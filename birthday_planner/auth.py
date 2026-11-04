"""Sign in with Google and Apple (OpenID Connect, authorization code flow).

Configuration comes from environment variables, so no secret ever lives in
the code:

  PUBLIC_URL               where the app is reachable, e.g. https://plan.example.com
  GOOGLE_CLIENT_ID         from Google Cloud console > Credentials > OAuth client (Web)
  GOOGLE_CLIENT_SECRET
  APPLE_CLIENT_ID          your Services ID, e.g. com.example.planner.web
  APPLE_TEAM_ID            10-character team ID from your Apple developer account
  APPLE_KEY_ID             ID of the "Sign in with Apple" key
  APPLE_PRIVATE_KEY_PATH   path to that key's .p8 file (or APPLE_PRIVATE_KEY with its contents)

A provider is offered only when all of its variables are set.

How a sign-in works:
  1. /auth/login/<provider> stores a one-time `state` and `nonce` (plus a PKCE
     verifier for Google), ties them to this browser with a short-lived
     cookie, and redirects to Google or Apple.
  2. The provider sends the person back to /auth/callback/<provider> with a
     code. We check the state and the browser cookie, swap the code for an
     ID token over TLS, and verify the token's signature against the
     provider's published keys, its issuer, audience, expiry and nonce.
  3. We start a session (random id in an HttpOnly cookie) for that person.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import jwt
from jwt import PyJWKClient

LOGIN_TTL = 10 * 60
SESSION_TTL = 30 * 24 * 3600
MAX_NAME = 30


class AuthError(Exception):
    """A sign-in that failed; the message is safe to show to the person."""


@dataclass
class Provider:
    name: str
    label: str
    client_id: str
    auth_url: str
    token_url: str
    jwks_url: str
    issuers: tuple
    scope: str
    client_secret: str = ""
    make_secret: object = None          # callable returning a fresh client secret (Apple)
    use_pkce: bool = False
    form_post: bool = False              # Apple returns to the callback with a POST
    extra_params: dict = field(default_factory=dict)

    def secret(self):
        return self.make_secret() if self.make_secret else self.client_secret


def google_from_env(env=os.environ):
    if not (env.get("GOOGLE_CLIENT_ID") and env.get("GOOGLE_CLIENT_SECRET")):
        return None
    return Provider(
        name="google", label="Google",
        client_id=env["GOOGLE_CLIENT_ID"], client_secret=env["GOOGLE_CLIENT_SECRET"],
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        jwks_url="https://www.googleapis.com/oauth2/v3/certs",
        issuers=("https://accounts.google.com", "accounts.google.com"),
        scope="openid email profile", use_pkce=True,
        extra_params={"prompt": "select_account"},
    )


def apple_client_secret(team_id, client_id, key_id, private_key, now=None):
    """Apple's client secret is a short-lived JWT signed with your .p8 key."""
    now = int(now or time.time())
    claims = {"iss": team_id, "iat": now, "exp": now + 3600,
              "aud": "https://appleid.apple.com", "sub": client_id}
    return jwt.encode(claims, private_key, algorithm="ES256", headers={"kid": key_id})


def apple_from_env(env=os.environ):
    key = env.get("APPLE_PRIVATE_KEY")
    if not key and env.get("APPLE_PRIVATE_KEY_PATH"):
        with open(env["APPLE_PRIVATE_KEY_PATH"], encoding="utf-8") as f:
            key = f.read()
    needed = ("APPLE_CLIENT_ID", "APPLE_TEAM_ID", "APPLE_KEY_ID")
    if not key or not all(env.get(k) for k in needed):
        return None
    team, client, kid = env["APPLE_TEAM_ID"], env["APPLE_CLIENT_ID"], env["APPLE_KEY_ID"]
    return Provider(
        name="apple", label="Apple", client_id=client,
        make_secret=lambda: apple_client_secret(team, client, kid, key),
        auth_url="https://appleid.apple.com/auth/authorize",
        token_url="https://appleid.apple.com/auth/token",
        jwks_url="https://appleid.apple.com/auth/keys",
        issuers=("https://appleid.apple.com",),
        scope="name email", form_post=True,
        extra_params={"response_mode": "form_post"},
    )


def providers_from_env(env=os.environ):
    found = [p for p in (google_from_env(env), apple_from_env(env)) if p]
    return {p.name: p for p in found}


def safe_next(path):
    """Only allow returning to a path on this site."""
    path = str(path or "/")
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


def clean_display_name(name, fallback):
    name = " ".join(str(name or "").split())[:MAX_NAME]
    return name if name and name not in ("Bot", "App") else fallback


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def post_form(url, fields, timeout=15):
    """POST a form and return the JSON reply (provider token endpoints)."""
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as err:
        detail = err.read()[:300].decode(errors="replace")
        raise AuthError("The sign-in provider rejected the login. Please try again.") from \
            RuntimeError(f"token endpoint {err.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        raise AuthError("Couldn't reach the sign-in provider. Please try again.") from err


class Auth:
    def __init__(self, providers, public_url, post=post_form):
        if not providers:
            raise ValueError("No sign-in providers are configured.")
        self.providers = providers
        self.public_url = public_url.rstrip("/")
        self.secure_cookies = self.public_url.startswith("https://") or \
            urllib.parse.urlsplit(self.public_url).hostname in ("localhost", "127.0.0.1")
        self.post = post
        self.jwks = {name: PyJWKClient(p.jwks_url, cache_keys=True) for name, p in providers.items()}
        self.lock = threading.Lock()
        self.pending = {}     # state -> login attempt
        self.sessions = {}    # session id -> {"user", "expires"}
        self.names = {}       # user id -> last known display name (Apple sends it only once)

    def public_info(self):
        return [{"name": p.name, "label": p.label} for p in self.providers.values()]

    def redirect_uri(self, provider):
        return f"{self.public_url}/auth/callback/{provider.name}"

    def origin(self):
        parts = urllib.parse.urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    # --- step 1: send the person to Google or Apple -------------------------

    def start(self, provider_name, next_path="/"):
        """Return (redirect_url, browser_binding) for a new sign-in."""
        provider = self.providers.get(provider_name)
        if provider is None:
            raise AuthError("That sign-in option isn't available.")
        state, nonce, binding = (secrets.token_urlsafe(32) for _ in range(3))
        verifier = secrets.token_urlsafe(64) if provider.use_pkce else None
        now = time.time()
        with self.lock:
            self.pending = {s: p for s, p in self.pending.items() if p["expires"] > now}
            self.pending[state] = {
                "provider": provider.name, "nonce": nonce, "verifier": verifier,
                "next": safe_next(next_path), "binding": hashlib.sha256(binding.encode()).hexdigest(),
                "expires": now + LOGIN_TTL,
            }
        params = {
            "client_id": provider.client_id, "redirect_uri": self.redirect_uri(provider),
            "response_type": "code", "scope": provider.scope, "state": state, "nonce": nonce,
            **provider.extra_params,
        }
        if verifier:
            params["code_challenge"] = _b64url(hashlib.sha256(verifier.encode()).digest())
            params["code_challenge_method"] = "S256"
        return f"{provider.auth_url}?{urllib.parse.urlencode(params)}", binding

    # --- step 2: the provider sends them back --------------------------------

    def finish(self, provider_name, params, binding):
        """Check the callback and return (user, next_path)."""
        if params.get("error"):
            raise AuthError("Sign-in was cancelled." if params["error"] in ("access_denied", "user_cancelled_authorize")
                            else "The sign-in provider reported a problem. Please try again.")
        state = params.get("state", "")
        with self.lock:
            attempt = self.pending.pop(state, None)
        if attempt is None or attempt["expires"] < time.time() or attempt["provider"] != provider_name:
            raise AuthError("This sign-in link has expired. Please try again.")
        if not binding or not hmac.compare_digest(hashlib.sha256(binding.encode()).hexdigest(), attempt["binding"]):
            raise AuthError("Please finish signing in from the same browser you started in.")
        code = params.get("code")
        if not code:
            raise AuthError("The sign-in provider didn't send a login code. Please try again.")

        provider = self.providers[provider_name]
        fields = {"grant_type": "authorization_code", "code": code, "client_id": provider.client_id,
                  "client_secret": provider.secret(), "redirect_uri": self.redirect_uri(provider)}
        if attempt["verifier"]:
            fields["code_verifier"] = attempt["verifier"]
        tokens = self.post(provider.token_url, fields)
        claims = self.verify_id_token(provider, tokens.get("id_token", ""), attempt["nonce"])
        return self.user_from(provider, claims, params.get("user")), attempt["next"]

    def verify_id_token(self, provider, id_token, nonce):
        if not id_token:
            raise AuthError("The sign-in provider didn't confirm who you are. Please try again.")
        try:
            key = self.jwks[provider.name].get_signing_key_from_jwt(id_token).key
            claims = jwt.decode(id_token, key, algorithms=["RS256"], audience=provider.client_id,
                                issuer=list(provider.issuers), leeway=60,
                                options={"require": ["iss", "aud", "exp", "iat", "sub"]})
        except jwt.PyJWTError as err:
            raise AuthError("Couldn't verify your sign-in. Please try again.") from err
        if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
            raise AuthError("Couldn't verify your sign-in. Please try again.")
        return claims

    def user_from(self, provider, claims, apple_user_json=None):
        uid = f"{provider.name}:{claims['sub']}"
        email = claims.get("email") or ""
        name = claims.get("name") or ""
        if apple_user_json:  # Apple sends the person's name only on their first sign-in
            try:
                n = json.loads(apple_user_json).get("name") or {}
                name = " ".join(x for x in (n.get("firstName"), n.get("lastName")) if x)
            except (ValueError, AttributeError):
                pass
        with self.lock:
            name = name or self.names.get(uid) or (email.split("@")[0] if email and "privaterelay" not in email else "")
            name = clean_display_name(name, f"{provider.label} user")
            self.names[uid] = name
        return {"id": uid, "name": name, "email": email, "provider": provider.name}

    # --- step 3: sessions ----------------------------------------------------

    def create_session(self, user):
        sid = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock:
            self.sessions = {k: v for k, v in self.sessions.items() if v["expires"] > now}
            self.sessions[sid] = {"user": user, "expires": now + SESSION_TTL}
        return sid

    def user_for(self, sid):
        if not sid:
            return None
        with self.lock:
            s = self.sessions.get(sid)
            if s and s["expires"] > time.time():
                return s["user"]
            self.sessions.pop(sid, None)
        return None

    def end_session(self, sid):
        with self.lock:
            self.sessions.pop(sid or "", None)

    # --- cookies -------------------------------------------------------------

    def cookie(self, name, value, max_age, same_site="Lax", path="/"):
        parts = [f"{name}={value}", f"Path={path}", f"Max-Age={max_age}", "HttpOnly", f"SameSite={same_site}"]
        if self.secure_cookies or same_site == "None":
            parts.append("Secure")
        return "; ".join(parts)

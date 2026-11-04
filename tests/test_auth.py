"""End-to-end sign-in tests against a fake OpenID Connect provider.

The fake provider behaves like Google/Apple: it has an authorize page that
redirects back with a code, a token endpoint that checks the client secret
and PKCE verifier and returns a signed ID token, and a JWKS endpoint with its
public key. Nothing here talks to the real Google or Apple.
"""

import base64
import hashlib
import json
import os
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import RSAAlgorithm

from birthday_planner import auth as auth_mod
from birthday_planner import web


def setUpModule():
    # Local test servers must not go through any outbound proxy.
    for var in ("no_proxy", "NO_PROXY"):
        os.environ[var] = ",".join(filter(None, [os.environ.get(var), "127.0.0.1", "localhost"]))


class FakeProvider:
    """A tiny OpenID Connect provider on 127.0.0.1."""

    def __init__(self, client_id="planner-client", client_secret="s3cret", pkce=True):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.client_id, self.client_secret, self.pkce = client_id, client_secret, pkce
        self.codes = {}                # code -> what the authorize request asked for
        self.claims = {"sub": "1234567890", "name": "Ana Silva", "email": "ana@example.com"}
        self.tamper = None             # "nonce", "audience", "key", "expired"
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body, ctype="application/json", headers=()):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path, _, query = self.path.partition("?")
                q = dict(urllib.parse.parse_qsl(query))
                if path == "/jwks":
                    jwk = json.loads(RSAAlgorithm.to_jwk(fake.key.public_key()))
                    jwk.update(kid="k1", use="sig", alg="RS256")
                    return self.reply(200, json.dumps({"keys": [jwk]}))
                if path == "/authorize":
                    code = os.urandom(8).hex()
                    fake.codes[code] = q
                    back = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
                    return self.reply(303, "", headers=[("Location", back)])
                self.reply(404, "{}")

            def do_POST(self):
                form = dict(urllib.parse.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                asked = fake.codes.pop(form.get("code"), None)
                if asked is None or form.get("client_id") != fake.client_id:
                    return self.reply(400, json.dumps({"error": "invalid_grant"}))
                if fake.client_secret and form.get("client_secret") != fake.client_secret:
                    return self.reply(401, json.dumps({"error": "invalid_client"}))
                if fake.pkce:
                    digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
                    if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != asked.get("code_challenge"):
                        return self.reply(400, json.dumps({"error": "invalid_grant", "detail": "pkce"}))
                now = int(time.time())
                claims = {"iss": fake.issuer, "aud": fake.client_id, "iat": now, "exp": now + 600,
                          "nonce": asked["nonce"], **fake.claims}
                key = fake.key
                if fake.tamper == "nonce":
                    claims["nonce"] = "something-else"
                elif fake.tamper == "audience":
                    claims["aud"] = "someone-elses-app"
                elif fake.tamper == "expired":
                    claims["exp"] = now - 3600
                elif fake.tamper == "key":
                    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                token = jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})
                self.reply(200, json.dumps({"id_token": token, "access_token": "x", "token_type": "Bearer"}))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.issuer = self.url
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def provider(self, name="google", form_post=False):
        return auth_mod.Provider(
            name=name, label=name.title(), client_id=self.client_id, client_secret=self.client_secret,
            auth_url=f"{self.url}/authorize", token_url=f"{self.url}/token", jwks_url=f"{self.url}/jwks",
            issuers=(self.issuer,), scope="openid email profile", use_pkce=self.pkce, form_post=form_post,
        )

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class Browser:
    """Follows one request at a time and keeps cookies by name, like a browser would."""

    def __init__(self, base):
        self.base = base
        self.cookies = {}

    def request(self, url, data=None, headers=None, form=False):
        if not url.startswith("http"):
            url = self.base + url
        body, hdrs = None, dict(headers or {})
        if data is not None:
            if form:
                body = urllib.parse.urlencode(data).encode()
                hdrs["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(data).encode()
                hdrs["Content-Type"] = "application/json"
        if self.cookies and url.startswith(self.base):
            hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if data is not None else "GET")
        opener = urllib.request.build_opener(NoRedirect)
        try:
            res = opener.open(req)
        except urllib.error.HTTPError as err:
            res = err
        for header in res.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in header:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        raw = res.read()
        try:
            payload = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return res.status, res.headers, payload

    def follow(self, url):
        """GET and follow redirects; return the final (status, location-path, payload)."""
        for _ in range(10):
            status, headers, payload = self.request(url)
            if status not in (301, 302, 303):
                return status, url, payload
            url = headers["Location"]
            if url.startswith(self.base) or not url.startswith("http"):
                if not url.startswith(self.base + "/auth/"):
                    return status, url.replace(self.base, ""), payload
        raise AssertionError("too many redirects")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class SignInTests(unittest.TestCase):
    def setUp(self):
        self.idp = FakeProvider()
        self.app = web.App(client=None)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.app))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.app.auth = auth_mod.Auth({"google": self.idp.provider("google")}, self.base)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.browser = Browser(self.base)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.idp.stop()

    def sign_in(self, browser=None, next_path="/?room=ABCDE"):
        b = browser or self.browser
        return b.follow(f"/auth/login/google?next={urllib.parse.quote(next_path)}")

    def test_full_google_sign_in_and_use_of_the_app(self):
        status, me = self.browser.request("/auth/me")[::2]
        self.assertIsNone(me["user"])
        self.assertEqual(me["providers"], [{"name": "google", "label": "Google"}])

        status, landed, _ = self.sign_in()
        self.assertEqual(landed, "/?room=ABCDE")
        self.assertIn(web.SESSION_COOKIE, self.browser.cookies)
        self.assertNotIn(web.LOGIN_COOKIE, self.browser.cookies)
        me = self.browser.request("/auth/me")[2]
        self.assertEqual(me["user"], {"id": "google:1234567890", "name": "Ana Silva", "provider": "google"})

        # The signed-in identity is used everywhere; a typed name is ignored.
        code = self.browser.request("/api/rooms", {})[2]["code"]
        self.assertEqual(self.browser.request(f"/api/rooms/{code}/join", {"name": "Somebody Else"})[0], 200)
        state = self.browser.request(f"/api/rooms/{code}")[2]
        self.assertEqual(state["members"], ["Ana Silva"])
        self.assertEqual(state["you"], {"id": "google:1234567890", "name": "Ana Silva"})

        # Signing out ends the session.
        self.assertEqual(self.browser.request("/auth/logout", {}, headers={"Origin": self.base})[0], 200)
        self.assertIsNone(self.browser.request("/auth/me")[2]["user"])
        self.assertEqual(self.browser.request(f"/api/rooms/{code}")[0], 401)

    def test_app_requires_sign_in(self):
        self.assertEqual(self.browser.request("/api/rooms", {})[0], 401)
        self.sign_in()
        code = self.browser.request("/api/rooms", {})[2]["code"]
        stranger = Browser(self.base)
        self.assertEqual(stranger.request(f"/api/rooms/{code}")[0], 401)
        self.assertEqual(stranger.request(f"/api/rooms/{code}/join", {"name": "Eve"})[0], 401)

    def test_cross_site_posts_are_blocked(self):
        self.sign_in()
        status = self.browser.request("/api/rooms", {}, headers={"Origin": "https://evil.example"})[0]
        self.assertEqual(status, 403)
        self.assertEqual(self.browser.request("/auth/logout", {}, headers={"Origin": "https://evil.example"})[0], 403)

    def test_two_people_with_the_same_name_are_told_apart(self):
        self.sign_in()
        code = self.browser.request("/api/rooms", {})[2]["code"]
        self.browser.request(f"/api/rooms/{code}/join", {})
        other = Browser(self.base)
        self.idp.claims = {"sub": "999", "name": "Ana Silva"}
        self.sign_in(other)
        other.request(f"/api/rooms/{code}/join", {})
        self.assertEqual(other.request(f"/api/rooms/{code}")[2]["members"], ["Ana Silva", "Ana Silva (2)"])

    def login_error(self, landed):
        self.assertTrue(landed.startswith("/?login_error="), landed)
        self.assertNotIn(web.SESSION_COOKIE, self.browser.cookies)
        return urllib.parse.parse_qs(landed[2:])["login_error"][0]

    def test_callback_from_a_different_browser_is_rejected(self):
        # Start in one browser, finish in another: the login-CSRF case.
        status, headers, _ = self.browser.request("/auth/login/google")
        _, back, _ = self.browser.request(headers["Location"])
        attacker_link = back["Location"]
        victim = Browser(self.base)
        _, landed, _ = victim.follow(attacker_link)
        self.assertIn("same browser", urllib.parse.parse_qs(landed[2:])["login_error"][0])
        self.assertNotIn(web.SESSION_COOKIE, victim.cookies)

    def test_state_cannot_be_reused(self):
        _, headers, _ = self.browser.request("/auth/login/google")
        _, back, _ = self.browser.request(headers["Location"])
        callback = back["Location"]
        login_cookie = self.browser.cookies[web.LOGIN_COOKIE]
        self.browser.follow(callback)
        self.browser.cookies = {web.LOGIN_COOKIE: login_cookie}
        _, landed, _ = self.browser.follow(callback)
        self.assertIn("expired", self.login_error(landed))

    def test_tampered_tokens_are_rejected(self):
        for tamper in ("nonce", "audience", "expired", "key"):
            with self.subTest(tamper=tamper):
                self.browser.cookies.clear()
                self.idp.tamper = tamper
                _, landed, _ = self.sign_in()
                self.assertIn("verify", self.login_error(landed))

    def test_cancelled_sign_in(self):
        _, headers, _ = self.browser.request("/auth/login/google")
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(headers["Location"]).query))["state"]
        _, landed, _ = self.browser.follow(f"/auth/callback/google?error=access_denied&state={state}")
        self.assertIn("cancelled", self.login_error(landed))

    def test_next_must_stay_on_this_site(self):
        for bad in ("https://evil.example/", "//evil.example/", "/\\evil.example"):
            self.browser.cookies.clear()
            _, landed, _ = self.sign_in(next_path=bad)
            self.assertEqual(landed, "/")

    def test_unknown_provider(self):
        _, landed, _ = self.browser.follow("/auth/login/myspace")
        self.assertTrue(landed.startswith("/?login_error="))


class AppleTests(unittest.TestCase):
    def setUp(self):
        self.p8 = ec.generate_private_key(ec.SECP256R1())
        from cryptography.hazmat.primitives import serialization
        self.pem = self.p8.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode()

    def test_client_secret_is_a_valid_es256_jwt(self):
        secret = auth_mod.apple_client_secret("TEAM123456", "com.example.web", "KEY1234567", self.pem, now=1_800_000_000)
        header = jwt.get_unverified_header(secret)
        self.assertEqual((header["alg"], header["kid"]), ("ES256", "KEY1234567"))
        claims = jwt.decode(secret, self.p8.public_key(), algorithms=["ES256"], audience="https://appleid.apple.com",
                            options={"verify_exp": False, "verify_iat": False})
        self.assertEqual((claims["iss"], claims["sub"], claims["exp"] - claims["iat"]), ("TEAM123456", "com.example.web", 3600))

    def test_apple_from_env(self):
        env = {"APPLE_CLIENT_ID": "com.example.web", "APPLE_TEAM_ID": "T", "APPLE_KEY_ID": "K", "APPLE_PRIVATE_KEY": self.pem}
        p = auth_mod.apple_from_env(env)
        self.assertTrue(p.form_post)
        self.assertEqual(p.extra_params, {"response_mode": "form_post"})
        self.assertTrue(jwt.get_unverified_header(p.secret())["alg"] == "ES256")
        self.assertIsNone(auth_mod.apple_from_env({**env, "APPLE_TEAM_ID": ""}))
        self.assertEqual(set(auth_mod.providers_from_env(env)), {"apple"})

    def test_apple_form_post_callback_uses_first_time_name(self):
        idp = FakeProvider(pkce=False)
        idp.claims = {"sub": "001234.abcd", "email": "x1y2@privaterelay.appleid.com"}
        app = web.App(client=None)
        server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(app))
        base = f"http://127.0.0.1:{server.server_address[1]}"
        app.auth = auth_mod.Auth({"apple": idp.provider("apple", form_post=True)}, base)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            b = Browser(base)
            _, headers, _ = b.request("/auth/login/apple")
            _, back, _ = b.request(headers["Location"])
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(back["Location"]).query))
            # Apple posts the code back as a form, with the name on first sign-in only.
            params["user"] = json.dumps({"name": {"firstName": "Ben", "lastName": "Okoro"}})
            status, headers, _ = b.request("/auth/callback/apple", params, form=True)
            self.assertEqual((status, headers["Location"]), (303, "/"))
            me = b.request("/auth/me")[2]["user"]
            self.assertEqual(me, {"id": "apple:001234.abcd", "name": "Ben Okoro", "provider": "apple"})

            # Next time Apple sends no name; we keep the one we learned, not the relay email.
            b2 = Browser(base)
            _, headers, _ = b2.request("/auth/login/apple")
            _, back, _ = b2.request(headers["Location"])
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(back["Location"]).query))
            b2.request("/auth/callback/apple", params, form=True)
            self.assertEqual(b2.request("/auth/me")[2]["user"]["name"], "Ben Okoro")
        finally:
            server.shutdown()
            server.server_close()
            idp.stop()


class ConfigTests(unittest.TestCase):
    def test_google_needs_both_values(self):
        self.assertIsNone(auth_mod.google_from_env({"GOOGLE_CLIENT_ID": "x"}))
        self.assertEqual(auth_mod.google_from_env({"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y"}).name, "google")

    def test_public_url_rules(self):
        env = {"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y"}
        self.assertIsNone(web.build_auth("", {}))
        with self.assertRaises(SystemExit):
            web.build_auth("", env)
        with self.assertRaises(SystemExit):
            web.build_auth("http://plan.example.com", env)
        self.assertEqual(web.build_auth("http://localhost:8000", env).public_url, "http://localhost:8000")
        self.assertTrue(web.build_auth("https://plan.example.com/", env).secure_cookies)

    def test_cookies_are_locked_down(self):
        a = web.build_auth("https://plan.example.com", {"GOOGLE_CLIENT_ID": "x", "GOOGLE_CLIENT_SECRET": "y"})
        c = a.cookie("bp_session", "abc", 60)
        for part in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
            self.assertIn(part, c)


if __name__ == "__main__":
    unittest.main()

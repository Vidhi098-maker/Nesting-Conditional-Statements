"""Group web app: friends join a room from their phones, chat with the
planner bot together, see real local venues, and vote.

    python3 -m birthday_planner.web            # http://localhost:8000
    python3 -m birthday_planner.web --port 9000

With Google and/or Apple credentials in the environment (see auth.py), people
sign in with those accounts and every action is tied to who they are.
Without them the app runs in a simple "type your name" mode for local testing.

Rooms and sessions live in memory, so they disappear when the server restarts.
"""

import argparse
import json
import os
import secrets
import socket
import threading
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .chatbot import (
    ATTACH_VENUES_TOOL, SYSTEM_PROMPT, TOOLS, WEB_SEARCH_TOOL, Planner, run_turn,
)
from .auth import SESSION_TTL, Auth, AuthError, providers_from_env
from .voting import tally, validate_ballot, winners

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 10_000
MAX_NAME = 30
MAX_TEXT = 1000
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SESSION_COOKIE = "bp_session"
LOGIN_COOKIE = "bp_login"

GROUP_PROMPT = SYSTEM_PROMPT + """

This is a group chat. Each user message starts with the sender's name in square \
brackets, e.g. "[Ana]: I'd love something outdoors". Several people may write at \
once. Collect preferences from each person who is chatting, using their name with \
add_friend, and address people by name.

Finding real places:
- After you show the 10 ideas, use web_search to find real places near where the \
birthday person lives for the top 3 ideas (and for any idea someone asks about).
- Pin what you find with attach_venues. Only attach places that actually appeared in \
your search results, with the result's URL. Never make up a venue.
- Mention one or two of the best finds briefly in your reply; the app shows the rest.

The vote happens in the app: when you call start_vote, everyone gets voting buttons."""

WEB_TOOLS = TOOLS + [ATTACH_VENUES_TOOL, WEB_SEARCH_TOOL]


class Room:
    def __init__(self, code, client):
        self.code = code
        self.client = client
        self.lock = threading.Lock()
        self.planner = Planner()
        self.history = []      # what Claude sees
        self.chat = []         # what people see: {"id", "sender", "text"}
        self.members = {}      # user id -> display name, in join order
        self.pending = []      # messages waiting for the bot
        self.bot_busy = False
        self.ballots = {}
        self.closed = False

    # Everything below that touches room state is called with self.lock held.

    def post(self, sender, text, uid=None):
        self.chat.append({"id": len(self.chat), "sender": sender, "uid": uid, "text": text})

    def phase(self):
        if self.closed:
            return "results"
        if self.planner.vote_requested:
            return "voting"
        return "planning"

    def results(self):
        if not self.closed:
            return None
        names = [s["name"] for s in self.planner.suggestions]
        results = tally(names, list(self.ballots.values()))
        return {"ranking": results, "tie": len(winners(results)) > 1}

    def snapshot(self, since, uid=None):
        venues = self.planner.venues
        return {
            "code": self.code,
            "phase": self.phase(),
            "you": {"id": uid, "name": self.members.get(uid)} if uid in self.members else None,
            "you_voted": uid in self.ballots,
            "members": list(self.members.values()),
            "bot_busy": self.bot_busy,
            "chat": self.chat[since:],
            "suggestions": [
                {**s, "venues": venues.get(s["name"], [])} for s in self.planner.suggestions
            ],
            "voted": [self.members[u] for u in self.ballots if u in self.members],
            "results": self.results(),
        }


class App:
    def __init__(self, client, auth=None):
        self.client = client
        self.auth = auth       # None: name-only mode for local testing
        self.rooms = {}
        self.lock = threading.Lock()

    def create_room(self):
        with self.lock:
            while True:
                code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(5))
                if code not in self.rooms:
                    room = Room(code, self.client)
                    room.post("Bot", "Hi everyone! Whose birthday are we planning, and where do they live?")
                    self.rooms[code] = room
                    return room

    def room(self, code):
        return self.rooms.get(str(code).upper())

    def join(self, room, uid, name):
        with room.lock:
            if uid not in room.members:
                taken = set(room.members.values())
                label, n = name, 2
                while label in taken:  # two people with the same name get told apart
                    label, n = f"{name} ({n})", n + 1
                room.members[uid] = label
                room.post("App", f"{label} joined the planning.")

    def send(self, room, uid, text):
        with room.lock:
            if room.phase() != "planning":
                raise ValueError("Planning is over. Time to vote!")
            if uid not in room.members:
                raise ValueError("Join the room first.")
            name = room.members[uid]
            room.post(name, text, uid)
            room.pending.append(f"[{name}]: {text}")
            if room.bot_busy:
                return  # the running bot worker will pick it up
            room.bot_busy = True
        threading.Thread(target=self.bot_worker, args=(room,), daemon=True).start()

    def bot_worker(self, room):
        """Answer pending messages one batch at a time until none are left."""
        while True:
            with room.lock:
                if not room.pending:
                    room.bot_busy = False
                    return
                batch = "\n".join(room.pending)
                room.pending.clear()
            turn_start = len(room.history)
            room.history.append({"role": "user", "content": batch})
            try:
                reply = run_turn(room.client, room.planner, room.history,
                                 system=GROUP_PROMPT, tools=WEB_TOOLS)
            except Exception as err:  # keep the room alive if the API call fails
                print(f"[room {room.code}] bot error: {err!r}")
                del room.history[turn_start:]
                reply = "Sorry, I couldn't reach my brain just now. Please try again in a moment."
            with room.lock:
                if reply:
                    room.post("Bot", reply)
                if room.planner.vote_requested:
                    room.post("App", "Voting is open! Pick your top 3.")

    def vote(self, room, uid, picks):
        with room.lock:
            if room.phase() != "voting":
                raise ValueError("Voting isn't open.")
            if uid not in room.members:
                raise ValueError("Join the room first.")
            error = validate_ballot(picks, len(room.planner.suggestions))
            if error:
                raise ValueError(error)
            room.ballots[uid] = picks
            if len(room.ballots) == len(room.members):
                room.closed = True

    def close_vote(self, room, uid):
        with room.lock:
            if uid not in room.members:
                raise ValueError("Join the room first.")
            if room.phase() != "voting":
                raise ValueError("Voting isn't open.")
            if not room.ballots:
                raise ValueError("Nobody has voted yet.")
            room.closed = True


def clean_name(value):
    name = " ".join(str(value or "").split())[:MAX_NAME]
    if not name or name in ("Bot", "App"):
        raise ValueError("Please choose a different name.")
    return name


class NotSignedIn(Exception):
    pass


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send_json(self, status, payload, cookies=()):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for c in cookies:
                self.send_header("Set-Cookie", c)
            self.end_headers()
            self.wfile.write(body)

        def redirect(self, location, cookies=()):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            for c in cookies:
                self.send_header("Set-Cookie", c)
            self.end_headers()

        def cookies(self):
            jar = SimpleCookie()
            try:
                jar.load(self.headers.get("Cookie") or "")
            except Exception:
                return {}
            return {k: m.value for k, m in jar.items()}

        def query(self):
            return dict(urllib.parse.parse_qsl(self.path.partition("?")[2]))

        def signed_in_user(self):
            return app.auth.user_for(self.cookies().get(SESSION_COOKIE)) if app.auth else None

        def identity(self, data=None):
            """(user id, display name) for this request."""
            if app.auth:
                user = self.signed_in_user()
                if user is None:
                    raise NotSignedIn()
                return user["id"], user["name"]
            name = clean_name((data or {}).get("name"))
            return f"name:{name}", name

        def same_origin(self):
            """Block cross-site form posts to the API when sign-in is on."""
            origin = self.headers.get("Origin")
            return not (app.auth and origin and origin != app.auth.origin())

        # --- sign-in routes ----------------------------------------------

        def auth_get(self, path):
            parts = path.strip("/").split("/")
            if path == "/auth/me":
                user = self.signed_in_user()
                self.send_json(200, {
                    "user": {"id": user["id"], "name": user["name"], "provider": user["provider"]} if user else None,
                    "providers": app.auth.public_info() if app.auth else [],
                })
            elif app.auth and len(parts) == 3 and parts[1] == "login":
                try:
                    url, binding = app.auth.start(parts[2], self.query().get("next", "/"))
                except AuthError as err:
                    return self.redirect("/?" + urllib.parse.urlencode({"login_error": str(err)}))
                self.redirect(url, [app.auth.cookie(LOGIN_COOKIE, binding, 600, same_site="None", path="/auth/callback")])
            elif app.auth and len(parts) == 3 and parts[1] == "callback":
                self.finish_login(parts[2], self.query())
            else:
                self.send_json(404, {"error": "Not found."})

        def finish_login(self, provider, params):
            clear = app.auth.cookie(LOGIN_COOKIE, "", 0, same_site="None", path="/auth/callback")
            try:
                user, next_path = app.auth.finish(provider, params, self.cookies().get(LOGIN_COOKIE))
            except AuthError as err:
                if err.__cause__:
                    print(f"[auth] {provider} sign-in failed: {err.__cause__!r}")
                return self.redirect("/?" + urllib.parse.urlencode({"login_error": str(err)}), [clear])
            sid = app.auth.create_session(user)
            self.redirect(next_path, [clear, app.auth.cookie(SESSION_COOKIE, sid, SESSION_TTL)])

        def auth_post(self, path):
            parts = path.strip("/").split("/")
            if app.auth and len(parts) == 3 and parts[1] == "callback":
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    return self.send_json(400, {"error": "Request too large."})
                form = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode(errors="replace")))
                return self.finish_login(parts[2], form)
            if path == "/auth/logout" and app.auth:
                if not self.same_origin():
                    return self.send_json(403, {"error": "Cross-site request blocked."})
                app.auth.end_session(self.cookies().get(SESSION_COOKIE))
                return self.send_json(200, {"ok": True}, [app.auth.cookie(SESSION_COOKIE, "", 0)])
            self.send_json(404, {"error": "Not found."})

        def read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError("Request too large.")
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                raise ValueError("Invalid JSON.")
            if not isinstance(data, dict):
                raise ValueError("Invalid JSON.")
            return data

        def route(self):
            """Return (room, action) for /api/rooms/<code>[/<action>] paths."""
            parts = self.path.split("?")[0].strip("/").split("/")
            if parts[:2] != ["api", "rooms"] or len(parts) < 3:
                return None, None
            return app.room(parts[2]), (parts[3] if len(parts) > 3 else "")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path.startswith("/auth/"):
                return self.auth_get(path)
            if path in ("/", "/index.html"):
                body = (STATIC_DIR / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            room, action = self.route()
            if room is None or action != "":
                self.send_json(404, {"error": "Room not found."})
                return
            query = self.path.partition("?")[2]
            since = 0
            for pair in query.split("&"):
                key, _, value = pair.partition("=")
                if key == "since" and value.isdigit():
                    since = int(value)
            try:
                uid = self.identity()[0] if app.auth else None
            except NotSignedIn:
                return self.send_json(401, {"error": "Please sign in first."})
            with room.lock:
                self.send_json(200, room.snapshot(since, uid))

        def do_POST(self):
            path = self.path.split("?")[0]
            if path.startswith("/auth/"):
                return self.auth_post(path)
            if not self.same_origin():
                return self.send_json(403, {"error": "Cross-site request blocked."})
            try:
                data = self.read_json()
                if path == "/api/rooms":
                    if app.auth:
                        self.identity(data)  # only signed-in people can start a plan
                    room = app.create_room()
                    self.send_json(200, {"code": room.code})
                    return
                room, action = self.route()
                if room is None:
                    self.send_json(404, {"error": "Room not found."})
                    return
                uid, name = self.identity(data)
                if action == "join":
                    app.join(room, uid, name)
                elif action == "messages":
                    text = str(data.get("text", "")).strip()[:MAX_TEXT]
                    if not text:
                        raise ValueError("Message is empty.")
                    app.send(room, uid, text)
                elif action == "vote":
                    picks = data.get("picks")
                    if not isinstance(picks, list) or not all(isinstance(p, int) for p in picks):
                        raise ValueError("Picks must be a list of numbers.")
                    app.vote(room, uid, picks)
                elif action == "close":
                    app.close_vote(room, uid)
                else:
                    self.send_json(404, {"error": "Not found."})
                    return
                self.send_json(200, {"ok": True})
            except NotSignedIn:
                self.send_json(401, {"error": "Please sign in first."})
            except ValueError as err:
                self.send_json(400, {"error": str(err)})

    return Handler


def lan_address():
    """Best guess at this machine's address on the local network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "localhost"


def build_auth(public_url, env=os.environ):
    """Sign-in for the configured providers, or None when none are set up."""
    providers = providers_from_env(env)
    if not providers:
        return None
    if not public_url:
        raise SystemExit("Set PUBLIC_URL (or --public-url) to the address people use, e.g. https://plan.example.com")
    parts = urllib.parse.urlsplit(public_url)
    if parts.scheme != "https" and parts.hostname not in ("localhost", "127.0.0.1"):
        raise SystemExit("Google and Apple sign-in need an https:// PUBLIC_URL (http is only allowed for localhost).")
    if "apple" in providers and parts.scheme != "https":
        raise SystemExit("Sign in with Apple only works on an https:// address. Use a public https URL or a tunnel.")
    return Auth(providers, public_url)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the group birthday planner web app.")
    parser.add_argument("--host", default="0.0.0.0", help="Address to listen on (default: all).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--public-url", default=os.environ.get("PUBLIC_URL", ""),
                        help="Address people open, needed for Google/Apple sign-in.")
    args = parser.parse_args(argv)

    import anthropic
    auth = build_auth(args.public_url)
    app = App(anthropic.Anthropic(), auth)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    if auth:
        names = " and ".join(p.label for p in auth.providers.values())
        print(f"Birthday Planner running at {auth.public_url} (sign in with {names})")
    else:
        print("No Google or Apple credentials set: running in name-only mode for local testing.")
        print(f"Birthday Planner running at http://localhost:{args.port}")
        print(f"Friends on the same Wi-Fi can join at http://{lan_address()}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

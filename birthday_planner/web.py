"""Group web app: friends join a room from their phones, chat with the
planner bot together, see real local venues, and vote.

    python3 -m birthday_planner.web            # http://localhost:8000
    python3 -m birthday_planner.web --port 9000

Rooms live in memory, so they disappear when the server restarts.
"""

import argparse
import json
import secrets
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .chatbot import (
    ATTACH_VENUES_TOOL, SYSTEM_PROMPT, TOOLS, WEB_SEARCH_TOOL, Planner, run_turn,
)
from .voting import tally, validate_ballot, winners

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 10_000
MAX_NAME = 30
MAX_TEXT = 1000
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

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
        self.members = []
        self.pending = []      # messages waiting for the bot
        self.bot_busy = False
        self.ballots = {}
        self.closed = False

    # Everything below that touches room state is called with self.lock held.

    def post(self, sender, text):
        self.chat.append({"id": len(self.chat), "sender": sender, "text": text})

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

    def snapshot(self, since):
        venues = self.planner.venues
        return {
            "code": self.code,
            "phase": self.phase(),
            "members": self.members,
            "bot_busy": self.bot_busy,
            "chat": self.chat[since:],
            "suggestions": [
                {**s, "venues": venues.get(s["name"], [])} for s in self.planner.suggestions
            ],
            "voted": sorted(self.ballots),
            "results": self.results(),
        }


class App:
    def __init__(self, client):
        self.client = client
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

    def join(self, room, name):
        with room.lock:
            if name not in room.members:
                room.members.append(name)
                room.post("App", f"{name} joined the planning.")

    def send(self, room, name, text):
        with room.lock:
            if room.phase() != "planning":
                raise ValueError("Planning is over. Time to vote!")
            room.post(name, text)
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

    def vote(self, room, name, picks):
        with room.lock:
            if room.phase() != "voting":
                raise ValueError("Voting isn't open.")
            if name not in room.members:
                raise ValueError("Join the room first.")
            error = validate_ballot(picks, len(room.planner.suggestions))
            if error:
                raise ValueError(error)
            room.ballots[name] = picks
            if len(room.ballots) == len(room.members):
                room.closed = True

    def close_vote(self, room):
        with room.lock:
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


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send_json(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

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
            with room.lock:
                self.send_json(200, room.snapshot(since))

        def do_POST(self):
            try:
                data = self.read_json()
                if self.path.split("?")[0] == "/api/rooms":
                    room = app.create_room()
                    self.send_json(200, {"code": room.code})
                    return
                room, action = self.route()
                if room is None:
                    self.send_json(404, {"error": "Room not found."})
                    return
                if action == "join":
                    app.join(room, clean_name(data.get("name")))
                elif action == "messages":
                    name = clean_name(data.get("name"))
                    text = str(data.get("text", "")).strip()[:MAX_TEXT]
                    if not text:
                        raise ValueError("Message is empty.")
                    if name not in room.members:
                        raise ValueError("Join the room first.")
                    app.send(room, name, text)
                elif action == "vote":
                    picks = data.get("picks")
                    if not isinstance(picks, list) or not all(isinstance(p, int) for p in picks):
                        raise ValueError("Picks must be a list of numbers.")
                    app.vote(room, clean_name(data.get("name")), picks)
                elif action == "close":
                    app.close_vote(room)
                else:
                    self.send_json(404, {"error": "Not found."})
                    return
                self.send_json(200, {"ok": True})
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the group birthday planner web app.")
    parser.add_argument("--host", default="0.0.0.0", help="Address to listen on (default: all).")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    import anthropic
    app = App(anthropic.Anthropic())
    server = ThreadingHTTPServer((args.host, args.port), make_handler(app))
    print(f"Birthday Planner running at http://localhost:{args.port}")
    print(f"Friends on the same Wi-Fi can join at http://{lan_address()}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

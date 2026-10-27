import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from birthday_planner import web
from tests.test_chatbot import LOCATION, FakeClient, text, tool


def scripted_client():
    return FakeClient([
        # Turn 1: save details, rank ideas, pin a venue found by search.
        ([tool("update_location", LOCATION, "t1"),
          tool("add_friend", {"name": "Ana", "vibe": "party", "place": "outdoor", "budget": 2}, "t2"),
          tool("get_suggestions", {}, "t3")], "tool_use"),
        ([tool("attach_venues", {"idea": "PLACEHOLDER", "venues": [
            {"name": "Real Spot", "url": "https://example.com/spot", "note": "Near the river"}]}, "t4"),
          tool("attach_venues", {"idea": "PLACEHOLDER", "venues": [
            {"name": "Sketchy", "url": "javascript:alert(1)"}]}, "t5")], "tool_use"),
        ([text("Here are your 10 ideas!")], "end_turn"),
        # Turn 2: open the vote.
        ([tool("start_vote", {}, "t6")], "tool_use"),
        ([text("Voting is open!")], "end_turn"),
    ])


class WebAppTests(unittest.TestCase):
    def setUp(self):
        self.client = scripted_client()
        self.app = web.App(self.client)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.app))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def wait_for_bot(self, code):
        for _ in range(100):
            status, state = self.call(f"/api/rooms/{code}")
            if not state["bot_busy"]:
                return state
            time.sleep(0.02)
        self.fail("bot never finished")

    def test_index_page_served(self):
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertIn(b"Birthday Planner", res.read())

    def test_full_group_flow(self):
        _, created = self.call("/api/rooms", {})
        code = created["code"]
        self.assertEqual(self.call(f"/api/rooms/{code}/join", {"name": "Ana"})[0], 200)
        self.assertEqual(self.call(f"/api/rooms/{code.lower()}/join", {"name": "Ben"})[0], 200)

        # Fill the attach_venues placeholders with a real top idea once we know it.
        def fill_placeholder():
            room = self.app.room(code)
            original = room.planner.tool_get_suggestions

            def patched():
                result = original()
                top = room.planner.suggestions[0]["name"]
                for content, _ in self.client.responses:
                    for block in content:
                        if getattr(block, "name", "") == "attach_venues":
                            block.input["idea"] = top
                return result
            room.planner.tool_get_suggestions = patched
        fill_placeholder()

        self.assertEqual(self.call(f"/api/rooms/{code}/messages",
                                   {"name": "Ana", "text": "It's Sam's birthday in Lisbon!"})[0], 200)
        state = self.wait_for_bot(code)
        self.assertEqual(len(state["suggestions"]), 10)
        self.assertEqual(state["suggestions"][0]["venues"][0]["name"], "Real Spot")
        self.assertEqual(state["chat"][-1], {"id": len(state["chat"]) - 1, "sender": "Bot",
                                             "text": "Here are your 10 ideas!"})
        # The bot saw the sender's name.
        self.assertEqual(self.client.requests[0]["messages"][-1]["content"],
                         "[Ana]: It's Sam's birthday in Lisbon!")
        # A non-http venue URL was rejected back to Claude as an error.
        results = self.client.requests[2]["messages"][-1]["content"]
        self.assertTrue(results[1]["is_error"])

        # Strangers can't post; then the bot opens voting.
        self.assertEqual(self.call(f"/api/rooms/{code}/messages", {"name": "Eve", "text": "hi"})[0], 400)
        self.call(f"/api/rooms/{code}/messages", {"name": "Ben", "text": "Let's vote"})
        state = self.wait_for_bot(code)
        self.assertEqual(state["phase"], "voting")
        self.assertEqual(self.call(f"/api/rooms/{code}/messages", {"name": "Ben", "text": "x"})[0], 400)

        # Bad ballots are rejected; results appear once everyone has voted.
        self.assertEqual(self.call(f"/api/rooms/{code}/vote", {"name": "Ana", "picks": [1, 1, 2]})[0], 400)
        self.call(f"/api/rooms/{code}/vote", {"name": "Ana", "picks": [2, 1, 3]})
        self.assertIsNone(self.call(f"/api/rooms/{code}")[1]["results"])
        self.call(f"/api/rooms/{code}/vote", {"name": "Ben", "picks": [2, 3, 1]})
        state = self.call(f"/api/rooms/{code}")[1]
        self.assertEqual(state["phase"], "results")
        self.assertEqual(state["results"]["ranking"][0]["name"], state["suggestions"][1]["name"])

        # Polling with ?since only returns new chat messages.
        total = len(self.call(f"/api/rooms/{code}")[1]["chat"])
        self.assertEqual(self.call(f"/api/rooms/{code}?since={total}")[1]["chat"], [])

    def test_errors(self):
        self.assertEqual(self.call("/api/rooms/NOPE1")[0], 404)
        _, created = self.call("/api/rooms", {})
        code = created["code"]
        self.assertEqual(self.call(f"/api/rooms/{code}/join", {"name": "  "})[0], 400)
        self.assertEqual(self.call(f"/api/rooms/{code}/join", {"name": "Bot"})[0], 400)
        self.assertEqual(self.call(f"/api/rooms/{code}/close", {})[0], 400)

    def test_api_failure_keeps_room_usable(self):
        class Broken:
            def __init__(self):
                self.beta = self
                self.messages = self

            def create(self, **kwargs):
                raise RuntimeError("network down")

        self.app.client = Broken()
        _, created = self.call("/api/rooms", {})
        code = created["code"]
        self.call(f"/api/rooms/{code}/join", {"name": "Ana"})
        self.call(f"/api/rooms/{code}/messages", {"name": "Ana", "text": "hello"})
        state = self.wait_for_bot(code)
        self.assertIn("couldn't reach", state["chat"][-1]["text"])
        self.assertEqual(self.app.room(code).history, [])


if __name__ == "__main__":
    unittest.main()

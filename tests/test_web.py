import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from birthday_planner import web
from tests.test_chatbot import FakeClient, text, tool


class WebAppTests(unittest.TestCase):
    """The group planner over HTTP, in name-only mode (sign-in is covered in test_auth)."""

    def start(self, client=None):
        self.app = web.App(client)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.app))
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def call(self, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, json.loads(res.read())
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read())

    def as_(self, name):
        """Helpers that act as one person."""
        def post(action, **body):
            return self.call(f"/api/rooms/{self.code}/{action}", {"name": name, **body})
        def view():
            return self.call(f"/api/rooms/{self.code}?name={name}")[1]
        return post, view

    def new_room(self, *names):
        self.code = self.call("/api/rooms", {})[1]["code"]
        for n in names:
            self.assertEqual(self.call(f"/api/rooms/{self.code}/join", {"name": n})[0], 200)

    def wait_for_bot(self):
        for _ in range(200):
            room = self.app.room(self.code)
            if not room.bot_busy:
                return
            time.sleep(0.02)
        self.fail("bot never finished")

    def test_index_page_served(self):
        self.start()
        with urllib.request.urlopen(self.base + "/") as res:
            self.assertIn(b"Group Planner", res.read())

    def test_full_group_plan_over_http(self):
        self.start()
        self.new_room("Devan", "Ana", "Ben")
        devan, ana, ben = (self.as_(n)[0] for n in ("Devan", "Ana", "Ben"))
        self.assertEqual(devan("details", kind="trip", title="Summer trip", place="London", currency="£")[0], 200)
        devan("preferences", vibe="chill", place="either", budget_max=800)
        ana("preferences", vibe="adventurous", place="outdoor", budget_max=650)
        ben("preferences", vibe="party", place="either", budget_max=900)
        for name, cost in (("4 days in Krakow", 450), ("Porto long weekend", 520), ("Dolomites hike", 780)):
            self.assertEqual(devan("idea", idea=name, cost_pp=cost, vibes=["chill"])[0], 200)

        # Voting is refused by the server until everyone agrees the £650 budget.
        status, body = devan("start-vote")
        self.assertEqual(status, 400)
        self.assertIn("agree the budget", body["error"])
        self.assertEqual(ana("agree", amount=700)[0], 400)          # stale amount
        for p in (devan, ana, ben):
            self.assertEqual(p("agree", amount=650)[0], 200)

        view = self.as_("Ana")[1]()
        self.assertEqual(view["budget"], {"limit": 650, "all_agreed": True, "agreed": 3, "taking_part": 3, "missing": 0})
        self.assertEqual(view["over_budget"], 1)
        self.assertEqual([p["name"] for p in view["people"]], ["Devan", "Ana", "Ben"])
        self.assertTrue(next(p for p in view["people"] if p["name"] == "Ana")["sets_budget"])

        self.assertEqual(devan("start-vote")[0], 200)
        finalists = [f["name"] for f in self.as_("Ben")[1]()["finalists"]]
        self.assertEqual(finalists, ["4 days in Krakow", "Porto long weekend"])
        devan("rate", ratings={"4 days in Krakow": 4, "Porto long weekend": 2})
        ana("rate", ratings={"4 days in Krakow": 3, "Porto long weekend": 0})
        self.assertIsNone(self.as_("Ben")[1]()["results"])
        ben("rate", ratings={"4 days in Krakow": 2, "Porto long weekend": 4})
        results = self.as_("Ben")[1]()["results"]
        self.assertEqual(results["winner"]["name"], "4 days in Krakow")
        self.assertEqual(results["rows"][1]["vetoed_by"], ["Ana"])

        for p in (devan, ana, ben):
            self.assertEqual(p("signoff", **{"in": True})[0], 200)
        view = self.as_("Devan")[1]()
        self.assertTrue(view["results"]["decided"])
        self.assertIn("Decided: 4 days in Krakow", view["chat"][-1]["text"])

    def test_only_the_host_can_sideline_someone(self):
        self.start()
        self.new_room("Devan", "Ana", "Ben")
        devan, ana, ben = (self.as_(n)[0] for n in ("Devan", "Ana", "Ben"))
        devan("details", kind="trip", place="London")
        for p in (devan, ana, ben):
            p("preferences", vibe="chill", place="either", budget_max=500)
        ben_id = next(p["id"] for p in self.as_("Devan")[1]()["people"] if p["name"] == "Ben")
        self.assertEqual(ana("active", person=ben_id, active=False)[0], 400)
        self.assertEqual(devan("active", person=ben_id, active=False)[0], 200)
        self.assertEqual(self.as_("Devan")[1]()["budget"]["taking_part"], 2)

    def test_strangers_and_unknown_actions(self):
        self.start()
        self.new_room("Devan")
        self.assertEqual(self.call(f"/api/rooms/{self.code}/messages", {"name": "Eve", "text": "hi"})[0], 400)
        self.assertEqual(self.call(f"/api/rooms/{self.code}/agree", {"name": "Eve"})[0], 400)
        self.assertEqual(self.call(f"/api/rooms/{self.code}/explode", {"name": "Devan"})[0], 404)
        self.assertEqual(self.call("/api/rooms/NOPE1")[0], 404)
        self.assertEqual(self.call(f"/api/rooms/{self.code}/join", {"name": "Bot"})[0], 400)

    def test_bot_plans_with_tools_and_can_only_act_for_senders(self):
        client = FakeClient([
            ([tool("set_plan", {"kind": "trip", "title": "Summer trip", "place": "London", "currency": "£"}, "t1"),
              tool("set_preferences", {"person": "Ana", "vibe": "adventurous", "place": "outdoor", "budget_max": 650}, "t2"),
              tool("agree_budget", {"person": "Devan"}, "t3"),          # Devan didn't write: refused
              tool("add_idea", {"name": "4 days in Krakow", "cost_per_person": 450, "vibes": ["chill"], "note": "Cheap flights"}, "t4"),
              tool("get_suggestions", {}, "t5")], "tool_use"),
            ([text("Saved! Krakow fits the budget.")], "end_turn"),
        ])
        self.start(client)
        self.new_room("Devan", "Ana")
        self.as_("Ana")[0]("messages", text="Trip from London! I'm adventurous, can do £650")
        self.wait_for_bot()

        view = self.as_("Ana")[1]()
        self.assertEqual(view["details"]["title"], "Summer trip")
        self.assertEqual(view["you"]["budget_max"], 650)
        self.assertEqual([i["name"] for i in view["ideas"]], ["4 days in Krakow"])
        self.assertEqual(view["chat"][-1]["text"], "Saved! Krakow fits the budget.")
        results = client.requests[1]["messages"][-1]["content"]
        self.assertTrue(results[2]["is_error"])
        self.assertIn("only act for people who wrote", results[2]["content"])
        self.assertIn('"ideas": [{"name": "4 days in Krakow"', results[4]["content"])
        # The bot saw the plan state and who wrote.
        first = client.requests[0]["messages"][-1]["content"]
        self.assertIn("(Current plan, from the app:", first)
        self.assertIn("[Ana]: Trip from London!", first)

    def test_api_failure_keeps_room_usable(self):
        class Broken:
            def __init__(self):
                self.beta = self
                self.messages = self

            def create(self, **kwargs):
                raise RuntimeError("network down")

        self.start(Broken())
        self.new_room("Ana")
        self.as_("Ana")[0]("messages", text="hello")
        self.wait_for_bot()
        self.assertIn("couldn't reach", self.as_("Ana")[1]()["chat"][-1]["text"])
        self.assertEqual(self.app.room(self.code).history, [])


if __name__ == "__main__":
    unittest.main()

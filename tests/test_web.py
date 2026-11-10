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
        self.new_room("Alex", "Ana", "Ben")
        alex, ana, ben = (self.as_(n)[0] for n in ("Alex", "Ana", "Ben"))
        self.assertEqual(alex("details", kind="trip", title="Summer trip", place="London", currency="£")[0], 200)
        alex("preferences", vibe="chill", place="either", budget_max=800)
        ana("preferences", vibe="adventurous", place="outdoor", budget_max=650)
        ben("preferences", vibe="party", place="either", budget_max=900)
        self.assertIn("agree the plan details", alex("agree", amount=650)[1]["error"])
        version = self.as_("Ana")[1]()["plan_agreement"]["version"]
        for p in (alex, ana, ben):
            self.assertEqual(p("agree-details", version=version)[0], 200)
        self.assertTrue(self.as_("Ana")[1]()["plan_agreement"]["all_agreed"])
        for name, cost in (("4 days in Krakow", 450), ("Porto long weekend", 520), ("Dolomites hike", 780)):
            self.assertEqual(alex("idea", idea=name, cost_pp=cost, vibes=["chill"])[0], 200)

        # Voting is refused by the server until everyone agrees the £650 budget.
        status, body = alex("start-vote")
        self.assertEqual(status, 400)
        self.assertIn("agree the budget", body["error"])
        self.assertEqual(ana("agree", amount=700)[0], 400)          # stale amount
        for p in (alex, ana, ben):
            self.assertEqual(p("agree", amount=650)[0], 200)

        view = self.as_("Ana")[1]()
        self.assertEqual(view["budget"], {"limit": 650, "all_agreed": True, "agreed": 3, "taking_part": 3, "missing": 0})
        self.assertEqual(view["over_budget"], 1)
        self.assertEqual([p["name"] for p in view["people"]], ["Alex", "Ana", "Ben"])
        self.assertTrue(next(p for p in view["people"] if p["name"] == "Ana")["sets_budget"])

        self.assertEqual(alex("start-vote")[0], 200)
        finalists = [f["name"] for f in self.as_("Ben")[1]()["finalists"]]
        self.assertEqual(finalists, ["4 days in Krakow", "Porto long weekend"])
        alex("rate", ratings={"4 days in Krakow": 4, "Porto long weekend": 2})
        ana("rate", ratings={"4 days in Krakow": 3, "Porto long weekend": 0})
        self.assertIsNone(self.as_("Ben")[1]()["results"])
        ben("rate", ratings={"4 days in Krakow": 2, "Porto long weekend": 4})
        results = self.as_("Ben")[1]()["results"]
        self.assertEqual(results["winner"]["name"], "4 days in Krakow")
        self.assertEqual(results["rows"][1]["vetoed_by"], ["Ana"])

        for p in (alex, ana, ben):
            self.assertEqual(p("signoff", **{"in": True})[0], 200)
        view = self.as_("Alex")[1]()
        self.assertTrue(view["results"]["decided"])
        self.assertIn("Decided: 4 days in Krakow", view["chat"][-1]["text"])

    def test_only_the_host_can_sideline_someone(self):
        self.start()
        self.new_room("Alex", "Ana", "Ben")
        alex, ana, ben = (self.as_(n)[0] for n in ("Alex", "Ana", "Ben"))
        alex("details", kind="trip", place="London")
        for p in (alex, ana, ben):
            p("preferences", vibe="chill", place="either", budget_max=500)
            p("agree-details")
        ben_id = next(p["id"] for p in self.as_("Alex")[1]()["people"] if p["name"] == "Ben")
        self.assertEqual(ana("active", person=ben_id, active=False)[0], 400)
        self.assertEqual(alex("active", person=ben_id, active=False)[0], 200)
        self.assertEqual(self.as_("Alex")[1]()["budget"]["taking_part"], 2)

    def test_strangers_and_unknown_actions(self):
        self.start()
        self.new_room("Alex")
        self.assertEqual(self.call(f"/api/rooms/{self.code}/messages", {"name": "Eve", "text": "hi"})[0], 400)
        self.assertEqual(self.call(f"/api/rooms/{self.code}/agree", {"name": "Eve"})[0], 400)
        self.assertEqual(self.call(f"/api/rooms/{self.code}/explode", {"name": "Alex"})[0], 404)
        self.assertEqual(self.call("/api/rooms/NOPE1")[0], 404)
        self.assertEqual(self.call(f"/api/rooms/{self.code}/join", {"name": "Bot"})[0], 400)

    def test_bot_plans_with_tools_and_can_only_act_for_senders(self):
        client = FakeClient([
            ([tool("set_plan", {"kind": "trip", "title": "Summer trip", "place": "London", "currency": "£"}, "t1"),
              tool("set_preferences", {"person": "Ana", "after_state": "spent", "place": "outdoor", "budget_max": 650}, "t2"),
              tool("agree_budget", {"person": "Alex"}, "t3"),          # Alex didn't write: refused
              tool("add_idea", {"name": "4 days in Krakow", "cost_per_person": 450, "after_states": ["unplugged"], "note": "Cheap flights"}, "t4"),
              tool("suggest_idea", {"idea": "4 days in Krakow"}, "t5"),  # feed isn't open yet: refused
              tool("get_suggestions", {}, "t6")], "tool_use"),
            ([text("Saved! Krakow fits the budget.")], "end_turn"),
        ])
        self.start(client)
        self.new_room("Alex", "Ana")
        self.as_("Ana")[0]("messages", text="Trip from London! I want to be worn out after, can do £650")
        self.wait_for_bot()

        view = self.as_("Ana")[1]()
        self.assertEqual(view["details"]["title"], "Summer trip")
        self.assertEqual((view["you"]["budget_max"], view["you"]["after"]), (650, "spent"))
        self.assertEqual([i["name"] for i in view["ideas"]], ["4 days in Krakow"])
        self.assertEqual(view["chat"][-1]["text"], "Saved! Krakow fits the budget.")
        results = client.requests[1]["messages"][-1]["content"]
        self.assertTrue(results[2]["is_error"])
        self.assertIn("only act for people who wrote", results[2]["content"])
        self.assertTrue(results[4]["is_error"])
        self.assertIn("agree the location", results[4]["content"])
        self.assertIn('"groupMostlyWantsToFeel": "spent"', results[5]["content"])
        # The bot saw the plan state and who wrote.
        first = client.requests[0]["messages"][-1]["content"]
        self.assertIn("(Current plan, from the app:", first)
        self.assertIn("[Ana]: Trip from London!", first)

    def test_feed_and_majority_over_http(self):
        self.start()
        self.new_room("Alex", "Ana", "Ben")
        alex, ana, ben = (self.as_(n)[0] for n in ("Alex", "Ana", "Ben"))
        alex("details", kind="outing", title="Saturday", place="Leeds", setting="city", month=6, hemisphere="north")
        for p, feel in ((alex, "unplugged"), (ana, "unplugged"), (ben, "buzzing")):
            self.assertEqual(p("preferences", after=feel, place="either", budget_max=50)[0], 200)
        # Location, then amount: suggesting is refused until both are agreed.
        self.assertIn("agree the location", alex("suggest", idea="Spa day")[1]["error"])
        for p in (ana, ben):
            p("agree-details")
        self.assertIn("agree the budget", alex("suggest", idea="Picnic in the park")[1]["error"])
        for p in (alex, ana, ben):
            p("agree", amount=50)

        view = self.as_("Alex")[1]()
        self.assertEqual((view["after"]["top"], view["after"]["answered"], view["majority"]), ("unplugged", 3, 2))
        top = view["ideas"][0]
        self.assertIn("leaves you unplugged", " ".join(top["reasons"]))
        self.assertEqual(alex("suggest", idea=top["name"])[0], 200)
        self.assertEqual(alex("suggest", idea=top["name"])[0], 400)       # already on the feed
        self.assertEqual(ben("idea", idea="Crazy golf", cost_pp=12, after=["buzzing"])[0], 200)   # own idea goes on the feed
        view = self.as_("Ana")[1]()
        self.assertEqual([f["name"] for f in view["feed"]], ["Crazy golf", top["name"]])    # newest first
        self.assertEqual(view["agreed_plan"], [])
        golf, first = (f["id"] for f in view["feed"])
        ana("feed-vote", id=golf, agree=True)          # 2 of 3: golf approved first
        ana("feed-vote", id=first, agree=True)         # then the other
        view = self.as_("Ben")[1]()
        self.assertEqual([f["name"] for f in view["agreed_plan"]], ["Crazy golf", top["name"]])   # order approved
        chat = " ".join(m["text"] for m in view["chat"])
        self.assertIn("Ben suggested Crazy golf", chat)
        self.assertIn("✓ Crazy golf is in the plan (2 of 3 agreed)", chat)
        # A change of mind drops it back out.
        ana("feed-vote", id=golf, agree=False)
        view = self.as_("Ben")[1]()
        self.assertEqual([f["name"] for f in view["agreed_plan"]], [top["name"]])
        self.assertEqual(next(f for f in view["feed"] if f["id"] == golf)["no_names"], ["Ana"])

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
        self.assertIn("hit a problem", self.as_("Ana")[1]()["chat"][-1]["text"])
        self.assertEqual(self.app.room(self.code).history, [])

    def test_no_api_key_explains_once_and_everything_else_works(self):
        self.start(None)
        self.new_room("Ana")
        post = self.as_("Ana")[0]
        post("messages", text="hello")
        post("messages", text="anyone there?")
        chat = self.as_("Ana")[1]()["chat"]
        notes = [m for m in chat if m["sender"] == "App" and "no Claude API key" in m["text"]]
        self.assertEqual(len(notes), 1)
        self.assertEqual(post("details", kind="trip", place="London")[0], 200)

    def test_clear_messages_for_api_problems(self):
        self.start()
        class Err(Exception):
            def __init__(self, status, msg):
                super().__init__(msg)
                self.status_code = status
        self.assertIn("no Claude API key", web.bot_problem(TypeError("Could not resolve authentication method.")))
        self.assertIn("rejected", web.bot_problem(Err(401, "invalid x-api-key")))
        self.assertIn("out of credit", web.bot_problem(Err(400, "Your credit balance is too low")))
        self.assertIn("rate limit", web.bot_problem(Err(429, "slow down")))

    def test_retries_without_web_search_or_fallback_when_the_account_lacks_them(self):
        class Err(Exception):
            status_code = 400

        class Picky:
            """Rejects web search and server-side fallback, like an account without them."""
            def __init__(self):
                self.beta = self
                self.messages = self
                self.calls = []

            def create(self, **kw):
                self.calls.append(kw)
                if any(t.get("name") == "web_search" for t in kw["tools"]):
                    raise Err("web search is not enabled for this organization")
                if "fallbacks" in kw:
                    raise Err("fallbacks is not available for this account")
                from types import SimpleNamespace as NS
                return NS(content=[NS(type="text", text="Hi! What are we planning?")], stop_reason="end_turn")

        client = Picky()
        self.start(client)
        self.new_room("Ana")
        self.as_("Ana")[0]("messages", text="hello")
        self.wait_for_bot()
        self.assertEqual(self.as_("Ana")[1]()["chat"][-1]["text"], "Hi! What are we planning?")
        self.assertEqual(len(client.calls), 3)
        self.assertFalse(self.app.web_search or self.app.fallbacks)
        # Later turns go straight to the working setup.
        self.as_("Ana")[0]("messages", text="again")
        self.wait_for_bot()
        self.assertEqual(len(client.calls), 4)


if __name__ == "__main__":
    unittest.main()

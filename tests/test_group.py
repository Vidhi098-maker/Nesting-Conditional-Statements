import json
import shutil
import subprocess
import unittest
from pathlib import Path

from birthday_planner.group import (
    CATALOG, GroupPlan, PlanError, consensus, suggest, summarize_group,
)

HOSTED = Path(__file__).resolve().parent.parent / "hosted" / "group-planner.html"


def plan_with(people, kind="trip", currency="£", **details):
    """A plan with these people, as {name: (vibe, place, budget)}; returns (plan, {name: uid})."""
    plan = GroupPlan()
    ids = {}
    for name, (vibe, place, budget) in people.items():
        uid = f"u:{name}"
        ids[name] = uid
        plan.join(uid, name)
        plan.set_preferences(uid, vibe, place, budget)
    plan.set_details({"kind": kind, "place": "London", "currency": currency, **details})
    for uid in ids.values():
        plan.agree_details(uid)
    return plan, ids


def add_trips(plan):
    for name, cost, vibes in [("4 days in Krakow", 450, ["chill", "party"]), ("Porto long weekend", 520, ["chill", "fancy"]),
                              ("Hiking in the Dolomites", 780, ["adventurous"]), ("Budapest baths", 480, ["party", "chill"]),
                              ("Lisbon and Sintra", 600, ["adventurous", "chill"])]:
        plan.add_idea(name, cost, vibes, by_bot=True)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.ids = plan_with({"Alex": ("chill", "either", 800), "Ana": ("adventurous", "outdoor", 650),
                                         "Ben": ("party", "either", 900)})

    def test_group_budget_is_the_lowest(self):
        self.assertEqual(self.plan.budget()["limit"], 650)

    def test_agreement_is_for_an_exact_amount(self):
        self.plan.agree_budget(self.ids["Alex"], 650)
        with self.assertRaisesRegex(PlanError, "just changed"):
            self.plan.agree_budget(self.ids["Ana"], 700)
        self.plan.agree_budget(self.ids["Ana"])
        self.plan.agree_budget(self.ids["Ben"])
        self.assertTrue(self.plan.budget()["all_agreed"])
        # Ben lowers his budget: everyone has to agree the new amount.
        self.plan.set_preferences(self.ids["Ben"], "party", "either", 500)
        b = self.plan.budget()
        self.assertEqual((b["limit"], b["all_agreed"], b["agreed"]), (500, False, []))

    def test_bad_budgets_are_rejected(self):
        for bad in (0, -5, 0.2, "50", True):
            with self.subTest(bad=bad), self.assertRaises(PlanError):
                self.plan.set_preferences(self.ids["Ana"], "chill", "either", bad)
        self.plan.set_preferences(self.ids["Ana"], "chill", "either", None)
        self.assertEqual(self.plan.budget()["missing"], [self.ids["Ana"]])

    def test_guests_count_towards_the_budget_but_dont_block(self):
        self.plan.add_guest("Cleo", "chill", "indoor", 300)
        self.assertEqual(self.plan.budget()["limit"], 300)
        for uid in self.ids.values():
            self.plan.agree_budget(uid)
        self.assertTrue(self.plan.budget()["all_agreed"])


class PlanAgreementTests(unittest.TestCase):
    def setUp(self):
        self.plan = GroupPlan()
        self.ids = {}
        for name in ("Alex", "Ana"):
            self.ids[name] = f"u:{name}"
            self.plan.join(self.ids[name], name)
            self.plan.set_preferences(self.ids[name], "chill", "either", 500)

    def test_saver_agrees_and_others_must_too(self):
        self.plan.set_details({"kind": "trip", "place": "London"}, self.ids["Alex"])
        d = self.plan.details_agreement()
        self.assertEqual((d["agreed"], d["all_agreed"]), ([self.ids["Alex"]], False))
        with self.assertRaisesRegex(PlanError, "agree the plan details before the budget"):
            self.plan.agree_budget(self.ids["Alex"])
        self.assertIn("waiting for Ana to agree the plan", self.plan.vote_blockers()[0])
        self.plan.agree_details(self.ids["Ana"], version=self.plan.details_version)
        self.assertTrue(self.plan.details_agreement()["all_agreed"])

    def test_changing_details_resets_agreements_and_budget(self):
        self.plan.set_details({"kind": "trip", "place": "London"}, self.ids["Alex"])
        self.plan.agree_details(self.ids["Ana"])
        self.plan.agree_budget(self.ids["Alex"])
        old = self.plan.details_version
        self.plan.set_details({"kind": "trip", "place": "London"}, self.ids["Ana"])   # no real change
        self.assertEqual(self.plan.details_version, old)
        self.plan.set_details({"place": "Leeds"}, self.ids["Ana"])
        self.assertEqual(self.plan.details_agreement()["agreed"], [self.ids["Ana"]])
        self.assertEqual(self.plan.budget()["agreed"], [])
        with self.assertRaisesRegex(PlanError, "just changed"):
            self.plan.agree_details(self.ids["Alex"], version=old)

    def test_a_cross_blocks_approval(self):
        self.plan.set_details({"kind": "outing", "place": "London", "setting": "city"}, self.ids["Alex"])
        self.plan.vote_plan(self.ids["Ana"], False)
        d = self.plan.details_agreement()
        self.assertEqual((d["agreed"], d["disagreed"], d["all_agreed"]), ([self.ids["Alex"]], [self.ids["Ana"]], False))
        self.assertIn("Ana doesn't agree the plan yet", self.plan.vote_blockers()[0])
        self.plan.vote_plan(self.ids["Ana"], True)                 # changes their mind
        self.assertTrue(self.plan.details_agreement()["all_agreed"])

    def test_tally_matches_everyone_in_the_plan(self):
        self.plan.join("u:Ben", "Ben")                               # joined, no preferences yet
        self.plan.set_details({"kind": "trip", "place": "London"}, self.ids["Alex"])
        self.plan.agree_details(self.ids["Ana"])
        d = self.plan.details_agreement()
        self.assertEqual((len(d["agreed"]), d["total"], d["all_agreed"]), (2, 3, False))
        self.plan.agree_details("u:Ben")                              # can agree before adding preferences
        self.assertTrue(self.plan.details_agreement()["all_agreed"])
        self.assertIn("waiting for Ben to add their preferences", self.plan.vote_blockers()[0])
        self.plan.set_active(self.ids["Alex"], self.plan.token("u:Ben"), False)   # host sidelines Ben
        self.assertEqual(self.plan.details_agreement()["total"], 2)
        self.assertNotIn("Ben", " ".join(self.plan.vote_blockers()))

    def test_view_shows_each_persons_tick_or_cross(self):
        self.plan.set_details({"kind": "trip", "place": "London"}, self.ids["Alex"])
        self.plan.vote_plan(self.ids["Ana"], False)
        view = self.plan.view(self.ids["Ana"])
        self.assertEqual({p["name"]: p["plan_vote"] for p in view["people"]}, {"Alex": "yes", "Ana": "no"})
        self.assertEqual({k: view["plan_agreement"][k] for k in ("agreed", "disagreed", "total", "you")},
                         {"agreed": 1, "disagreed": 1, "total": 2, "you": "no"})

    def test_nothing_to_agree_yet(self):
        with self.assertRaisesRegex(PlanError, "no plan details"):
            self.plan.agree_details(self.ids["Alex"])


class AfterStateTests(unittest.TestCase):
    """The kind of activity is worked out backwards from how people want to feel."""

    def ranked(self, feel):
        plan = GroupPlan()
        for name in ("Alex", "Ana"):
            plan.join(f"u:{name}", name)
            plan.set_preferences(f"u:{name}", None, "either", 60, after=feel)
        plan.set_details({"kind": "outing", "place": "Leeds", "setting": "city", "month": 5, "hemisphere": "north"})
        return plan, plan.ranking()[0]

    def test_each_feeling_points_to_a_different_kind_of_activity(self):
        tops = {feel: self.ranked(feel)[1][0]["name"] for feel in ("spent", "sharp", "unplugged", "buzzing")}
        self.assertEqual(len(set(tops.values())), 4, tops)
        self.assertIn(tops["spent"], ("Indoor rock climbing", "Trampoline park"))
        self.assertIn(tops["sharp"], ("Escape room challenge", "Museum or gallery day + cafe", "Board game and pizza night"))

    def test_reason_says_how_it_leaves_you(self):
        _, ideas = self.ranked("sharp")
        self.assertIn("leaves you mentally sharp", ideas[0]["reasons"][0])

    def test_group_top_feeling_and_own_ideas(self):
        plan, _ = self.ranked("unplugged")
        plan.join("u:Ben", "Ben")
        plan.set_preferences("u:Ben", None, "either", 60, after="spent")
        self.assertEqual(plan.group_after(), ({"unplugged": 2, "spent": 1}, "unplugged"))
        idea = plan.add_idea("Float tank session", 40, None, after=["unplugged"])
        self.assertEqual(idea["vibes"], ["chill"])
        self.assertIn("leaves you unplugged", " ".join(next(i for i in plan.ranking()[0] if i["name"] == "Float tank session")["reasons"]))
        with self.assertRaises(PlanError):
            plan.set_preferences("u:Ben", None, "either", 60, after="sleepy")


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.ids = plan_with({"Alex": ("chill", "either", 800), "Ana": ("adventurous", "outdoor", 650),
                                         "Ben": ("party", "either", 900), "Cleo": ("chill", "either", 700)})
        add_trips(self.plan)

    def open_feed(self):
        for uid, _ in self.plan.participants():
            self.plan.agree_budget(uid)

    def test_location_then_amount_gate_the_feed(self):
        with self.assertRaisesRegex(PlanError, "agree the budget"):
            self.plan.suggest_to_feed(self.ids["Alex"], "4 days in Krakow")
        self.open_feed()
        self.plan.set_details({"place": "Leeds"}, self.ids["Alex"])      # location changes: everyone agrees again
        with self.assertRaisesRegex(PlanError, "agree the location"):
            self.plan.suggest_to_feed(self.ids["Alex"], "4 days in Krakow")

    def test_majority_puts_ideas_in_the_plan_in_order_approved(self):
        self.open_feed()
        self.assertEqual(self.plan.majority(), 3)                         # more than half of 4
        a = self.plan.suggest_to_feed(self.ids["Alex"], "4 days in Krakow")
        b = self.plan.suggest_to_feed(self.ids["Ana"], "Lisbon and Sintra")
        self.plan.feed_vote(self.ids["Ben"], b["id"], True)
        self.assertEqual(self.plan.agreed_plan(), [])                     # 2 of 4 is not a majority
        self.plan.feed_vote(self.ids["Cleo"], b["id"], True)              # Lisbon reaches 3 first
        self.plan.feed_vote(self.ids["Ben"], a["id"], True)
        self.plan.feed_vote(self.ids["Ana"], a["id"], False)
        self.plan.feed_vote(self.ids["Cleo"], a["id"], True)              # then Krakow
        self.assertEqual([i["name"] for i in self.plan.agreed_plan()], ["Lisbon and Sintra", "4 days in Krakow"])
        view = self.plan.view(self.ids["Ana"])
        krakow = next(f for f in view["feed"] if f["name"] == "4 days in Krakow")
        self.assertEqual((krakow["yes"], krakow["no"], krakow["you"], krakow["approved"]), (3, 1, "no", True))

    def test_rules(self):
        self.open_feed()
        with self.assertRaisesRegex(PlanError, "over the £650 budget"):
            self.plan.suggest_to_feed(self.ids["Alex"], "Hiking in the Dolomites")
        with self.assertRaisesRegex(PlanError, "isn't on the list"):
            self.plan.suggest_to_feed(self.ids["Alex"], "A trip to the moon")
        item = self.plan.suggest_to_feed(self.ids["Alex"], "Porto long weekend")
        with self.assertRaisesRegex(PlanError, "already on the feed"):
            self.plan.suggest_to_feed(self.ids["Ana"], "Porto long weekend")
        self.assertEqual(item["votes"], {self.ids["Alex"]: True})         # suggesting counts as agreeing
        self.plan.feed_vote(self.ids["Alex"], item["id"], None)           # and can be withdrawn
        self.assertEqual(item["votes"], {})

    def test_majority_follows_who_is_in_the_plan(self):
        self.open_feed()
        item = self.plan.suggest_to_feed(self.ids["Alex"], "Porto long weekend")
        self.plan.feed_vote(self.ids["Ana"], item["id"], True)
        self.assertEqual(self.plan.agreed_plan(), [])                     # 2 of 4
        self.plan.set_active(self.ids["Alex"], self.plan.token(self.ids["Cleo"]), False)
        self.assertEqual([i["name"] for i in self.plan.agreed_plan()], ["Porto long weekend"])   # 2 of 3
        # A lower budget that prices it out takes it back off the plan.
        self.plan.set_preferences(self.ids["Ben"], "party", "either", 500)
        self.open_feed()
        self.assertEqual(self.plan.agreed_plan(), [])
        self.assertTrue(self.plan.view(self.ids["Ben"])["feed"][0]["over_budget"])

    def test_bot_suggestions_carry_no_vote(self):
        self.open_feed()
        item = self.plan.suggest_to_feed(None, "Budapest baths", by_bot=True)
        self.assertEqual(item["votes"], {})
        self.assertEqual(self.plan.view(self.ids["Alex"])["feed"][0]["by"], "Planner bot")


class VotingTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.ids = plan_with({"Alex": ("chill", "either", 800), "Ana": ("adventurous", "outdoor", 650),
                                         "Ben": ("party", "either", 900)})
        add_trips(self.plan)

    def agree_all(self):
        for uid in self.ids.values():
            self.plan.agree_budget(uid)

    def test_vote_needs_everyone_to_agree(self):
        with self.assertRaisesRegex(PlanError, "agree the budget"):
            self.plan.start_vote(self.ids["Alex"])
        self.agree_all()
        self.plan.start_vote(self.ids["Alex"])
        self.assertEqual(self.plan.current_phase(), "voting")
        names = [f["name"] for f in self.plan.finalists()]
        self.assertNotIn("Hiking in the Dolomites", names)  # over the £650 budget
        self.assertEqual(len(names), 4)

    def test_host_can_move_past_a_quiet_member(self):
        self.plan.agree_budget(self.ids["Alex"])
        self.plan.agree_budget(self.ids["Ana"])
        ben = self.plan.token(self.ids["Ben"])
        with self.assertRaisesRegex(PlanError, "Only the host"):
            self.plan.set_active(self.ids["Ana"], ben, False)
        self.plan.set_active(self.ids["Alex"], ben, False)   # Alex joined first, so is host
        self.plan.start_vote(self.ids["Alex"])
        # Ben can leave and rejoin himself.
        self.plan.set_active(self.ids["Ben"], ben, True)
        self.assertEqual(self.plan.current_phase(), "planning")  # paused until Ben agrees

    def test_changing_details_pauses_the_vote(self):
        self.agree_all()
        self.plan.start_vote(self.ids["Alex"])
        self.plan.set_details({"title": "Autumn trip"}, self.ids["Alex"])
        self.assertEqual(self.plan.current_phase(), "planning")
        for uid in self.ids.values():
            self.plan.agree_details(uid)
        self.agree_all()
        self.assertEqual(self.plan.current_phase(), "voting")

    def test_budget_drop_pauses_the_vote_and_drops_finalists(self):
        self.agree_all()
        self.plan.start_vote(self.ids["Alex"])
        self.plan.set_preferences(self.ids["Ben"], "party", "either", 500)
        self.assertEqual(self.plan.current_phase(), "planning")
        with self.assertRaisesRegex(PlanError, "Voting isn't open"):
            self.plan.rate(self.ids["Alex"], {})
        self.agree_all()
        self.assertEqual(self.plan.current_phase(), "voting")
        self.assertEqual([f["name"] for f in self.plan.finalists()], ["4 days in Krakow", "Budapest baths"])

    def test_ratings_are_validated(self):
        self.agree_all()
        self.plan.start_vote(self.ids["Alex"])
        names = [f["name"] for f in self.plan.finalists()]
        with self.assertRaisesRegex(PlanError, "every option"):
            self.plan.rate(self.ids["Alex"], {names[0]: 3})
        with self.assertRaisesRegex(PlanError, "0 \\(No way\\) to 4"):
            self.plan.rate(self.ids["Alex"], {n: 5 for n in names})

    def test_full_vote_and_sign_off(self):
        self.agree_all()
        self.plan.start_vote(self.ids["Alex"])
        names = [f["name"] for f in self.plan.finalists()]
        self.assertEqual(names, ["4 days in Krakow", "Lisbon and Sintra", "Budapest baths", "Porto long weekend"])
        with self.assertRaisesRegex(PlanError, "nothing to sign off"):
            self.plan.sign_off(self.ids["Alex"], True)
        for who, scores in (("Alex", [4, 3, 2, 3]), ("Ana", [2, 4, 0, 3]), ("Ben", [3, 2, 4, 2])):
            self.plan.rate(self.ids[who], dict(zip(names, scores)))
        rows, winner, decided = self.plan.result()
        self.assertEqual(winner["name"], "4 days in Krakow")
        self.assertEqual(rows[-1]["name"], "Budapest baths")   # Ana's "No way" rules it out
        self.assertFalse(decided)
        self.plan.sign_off(self.ids["Alex"], True)
        self.plan.sign_off(self.ids["Ana"], False)
        self.assertFalse(self.plan.result()[2])
        self.plan.sign_off(self.ids["Ana"], True)
        self.plan.sign_off(self.ids["Ben"], True)
        self.assertTrue(self.plan.result()[2])

    def test_everything_vetoed_means_no_winner(self):
        rows, winner = consensus([{"name": "A"}, {"name": "B"}],
                                 [{"uid": 1, "ratings": {"A": 0, "B": 4}}, {"uid": 2, "ratings": {"A": 4, "B": 0}}])
        self.assertIsNone(winner)

    def test_view_hides_account_ids_and_shows_people(self):
        view = self.plan.view(self.ids["Ana"])
        self.assertNotIn("u:", json.dumps(view))
        ana = next(p for p in view["people"] if p["is_you"])
        self.assertEqual((ana["name"], ana["budget_max"], ana["sets_budget"]), ("Ana", 650, True))
        self.assertEqual(view["budget"]["limit"], 650)
        self.assertEqual(view["over_budget"], 1)


class DetailsTests(unittest.TestCase):
    def test_local_plans_need_an_area(self):
        plan = GroupPlan()
        with self.assertRaisesRegex(PlanError, "kind of area"):
            plan.set_details({"kind": "dinner", "place": "Leeds"})
        plan.set_details({"kind": "dinner", "place": "Leeds", "setting": "city", "month": 12, "hemisphere": "north"})
        names = [i["name"] for i in plan.ranking()[0]]
        self.assertTrue(names)
        self.assertNotIn("Go-kart racing", names)            # not a dinner idea
        self.assertTrue(all(i["kinds"] for i in CATALOG))

    def test_bad_details(self):
        plan = GroupPlan()
        for bad in ({"kind": "party"}, {"kind": "trip", "place": "X", "month": 13}, {"kind": "trip", "place": "X", "currency": "¥"}):
            with self.subTest(bad=bad), self.assertRaises(PlanError):
                plan.set_details(bad)


@unittest.skipUnless(shutil.which("node") and HOSTED.exists(), "needs node and the hosted page")
class ParityTests(unittest.TestCase):
    """The hosted page and the server must rank and decide identically."""

    def test_same_ranking_and_winner_as_the_hosted_page(self):
        html = HOSTED.read_text()
        start, end = html.index("const KINDS = {"), html.index("/* ---------- Shared state")
        cases = [
            {"ctx": {"setting": "city", "features": ["beach"], "season": "summer", "limit": None, "currency": "£"},
             "kind": "birthday", "people": [{"vibe": "party", "place": "outdoor"}, {"vibe": "creative", "place": "either"}], "feedback": {}},
            {"ctx": {"setting": "rural", "features": ["lake", "mountains"], "season": "winter", "limit": 40, "currency": "£"},
             "kind": "outing", "people": [{"vibe": "chill", "place": "indoor"}], "feedback": {"Spa day": 1, "Lakeside camping trip": -1}},
            {"ctx": {"setting": "suburb", "features": [], "season": "autumn", "limit": 50, "currency": "$"},
             "kind": "dinner", "people": [{"vibe": "fancy", "place": "indoor"}, {"vibe": "chill", "place": "either"}] * 4, "feedback": {"Korean barbecue": 1}},
        ]
        script = html[start:end] + """
const cases = %s;
const out = cases.map((c) => {
  const guide = COST_GUIDE[c.ctx.currency];
  const cands = IDEAS.filter((i) => i.kinds.includes(c.kind)).map((i) => ({ ...i, costPP: guide[i.cost - 1] }));
  cands.push({ name: "Our own idea", vibes: ["chill"], outdoor: null, costPP: 10, custom: true, byBot: false, settings: ALL, needs: null, kinds: [] });
  return suggest(cands, c.ctx, summarizeGroup(c.people), c.feedback).ideas.map((x) => [x.name, x.score, x.reasons]);
});
const fin = [{ name: "A" }, { name: "B" }, { name: "C" }];
const votes = [{ uid: 1, ratings: { A: 4, B: 2, C: 3 } }, { uid: 2, ratings: { A: 2, B: 4, C: 3 } }, { uid: 3, ratings: { A: 3, B: 3, C: 0 } }];
out.push(consensus(fin, votes).rows.map((r) => [r.name, r.min, r.total]));
console.log(JSON.stringify(out));
""" % json.dumps(cases)
        js = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)

        from birthday_planner.group import COST_GUIDE
        py = []
        for c in cases:
            guide = COST_GUIDE[c["ctx"]["currency"]]
            cands = [{**i, "cost_pp": guide[i["cost"] - 1]} for i in CATALOG if c["kind"] in i["kinds"]]
            cands.append({"name": "Our own idea", "vibes": ["chill"], "outdoor": None, "cost_pp": 10, "custom": True})
            ideas, _ = suggest(cands, c["ctx"], summarize_group(c["people"]), c["feedback"])
            py.append([[x["name"], x["score"], x["reasons"]] for x in ideas])
        rows, _ = consensus([{"name": "A"}, {"name": "B"}, {"name": "C"}],
                            [{"uid": 1, "ratings": {"A": 4, "B": 2, "C": 3}}, {"uid": 2, "ratings": {"A": 2, "B": 4, "C": 3}},
                             {"uid": 3, "ratings": {"A": 3, "B": 3, "C": 0}}])
        py.append([[r["name"], r["min"], r["total"]] for r in rows])
        self.assertEqual(py, js)


if __name__ == "__main__":
    unittest.main()

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

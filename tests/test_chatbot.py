import contextlib
import io
import json
import unittest
from types import SimpleNamespace as NS

from birthday_planner import chatbot
from birthday_planner.planner import learn_weights, suggest, summarize_group


def text(t):
    return NS(type="text", text=t)


def tool(name, args, id_):
    return NS(type="tool_use", name=name, input=args, id=id_)


class FakeClient:
    """Plays back scripted Claude responses and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        content, stop = self.responses.pop(0)
        return NS(content=content, stop_reason=stop)


LOCATION = {"birthday_person": "Sam", "birthday_vibe": "party", "place": "Lisbon",
            "setting": "city", "features": ["beach"], "hemisphere": "north", "month": 7}


class FeedbackAlgorithmTests(unittest.TestCase):
    def setUp(self):
        self.location = {"setting": "city", "features": [], "season": "summer"}
        self.group = summarize_group([{"vibe": "chill", "place": "either", "budget": 3}])

    def test_learn_weights(self):
        w = learn_weights({"Spa day": 1, "Escape room challenge": -1})
        self.assertEqual(w["chill"], 1)
        self.assertEqual(w["adventurous"], -1)

    def test_disliked_idea_removed_and_similar_drop(self):
        before = [i["name"] for i in suggest(self.location, self.group)]
        disliked = before[0]
        after = [i["name"] for i in suggest(self.location, self.group, feedback={disliked: -1})]
        self.assertNotIn(disliked, after)
        self.assertEqual(len(after), 10)

    def test_like_pulls_similar_ideas_up(self):
        def rank(feedback):
            names = [i["name"] for i in suggest(self.location, self.group, count=100,
                                                feedback=feedback, diversity=0)]
            return names.index("Escape room challenge")
        # Liking another adventurous indoor idea moves the escape room up.
        self.assertLess(rank({"Indoor rock climbing": 1}), rank({}))

    def test_diversity_spreads_vibes(self):
        plain = suggest(self.location, self.group, diversity=0)
        varied = suggest(self.location, self.group, diversity=3)
        self.assertNotEqual([i["name"] for i in plain], [i["name"] for i in varied])


class PlannerToolTests(unittest.TestCase):
    def test_requires_location_and_friend(self):
        p = chatbot.Planner()
        with self.assertRaises(chatbot.ToolError):
            p.run_tool("get_suggestions", {})
        p.run_tool("update_location", LOCATION)
        with self.assertRaises(chatbot.ToolError):
            p.run_tool("get_suggestions", {})

    def test_rejects_unknown_idea_and_bad_values(self):
        p = chatbot.Planner()
        with self.assertRaises(chatbot.ToolError):
            p.run_tool("record_feedback", {"idea": "Moon trip", "liked": True})
        with self.assertRaises(chatbot.ToolError):
            p.run_tool("add_friend", {"name": "A", "vibe": "sleepy", "place": "indoor", "budget": 1})


class ChatLoopTests(unittest.TestCase):
    def test_full_chat_then_vote(self):
        client = FakeClient([
            ([tool("update_location", LOCATION, "t1"),
              tool("add_friend", {"name": "Ana", "vibe": "party", "place": "outdoor", "budget": 2}, "t2"),
              tool("get_suggestions", {}, "t3")], "tool_use"),
            ([text("Here are 10 ideas...")], "end_turn"),
            ([tool("record_feedback", {"idea": "Not a real idea", "liked": False}, "t4")], "tool_use"),
            ([tool("start_vote", {}, "t5")], "tool_use"),
            ([text("Let's vote!")], "end_turn"),
        ])
        answers = iter(["Sam in Lisbon, July, Ana loves parties", "looks great, let's vote", "1 2 3"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            results = chatbot.main(client=client, ask=lambda _p: next(answers))

        self.assertEqual(len(results), 10)
        self.assertIn("The winner is", out.getvalue())
        # The parallel tool results go back in one user message.
        tool_results = client.requests[1]["messages"][-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in tool_results], ["t1", "t2", "t3"])
        self.assertEqual(len(json.loads(tool_results[2]["content"])), 10)
        # A bad idea name comes back as an error result, not a crash.
        bad = client.requests[3]["messages"][-1]["content"][0]
        self.assertTrue(bad["is_error"])

    def test_refusal_is_handled(self):
        client = FakeClient([([], "refusal")])
        answers = iter(["something odd", "quit"])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(chatbot.main(client=client, ask=lambda _p: next(answers)))
        self.assertIn("can't help", out.getvalue())


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import unittest

from birthday_planner import cli
from birthday_planner.planner import SETTINGS, get_season, suggest, summarize_group
from birthday_planner.voting import tally, validate_ballot, winners


def member(vibe="chill", place="either", budget=2):
    return {"name": "x", "vibe": vibe, "place": place, "budget": budget}


class SeasonTests(unittest.TestCase):
    def test_north(self):
        self.assertEqual(get_season(1, "north"), "winter")
        self.assertEqual(get_season(7, "north"), "summer")

    def test_south_is_flipped(self):
        self.assertEqual(get_season(1, "south"), "summer")
        self.assertEqual(get_season(10, "south"), "spring")

    def test_tropical(self):
        self.assertEqual(get_season(1, "tropical"), "warm")


class SuggestTests(unittest.TestCase):
    def test_always_ten_for_every_setting_and_season(self):
        group = summarize_group([member()])
        for setting in SETTINGS:
            for season in ("winter", "spring", "summer", "autumn", "warm"):
                location = {"setting": setting, "features": [], "season": season}
                ideas = suggest(location, group)
                self.assertEqual(len(ideas), 10, (setting, season))
                self.assertEqual(len({i["name"] for i in ideas}), 10)

    def test_location_filters(self):
        group = summarize_group([member()])
        names = [i["name"] for i in suggest(
            {"setting": "rural", "features": [], "season": "summer"}, group, count=100)]
        self.assertNotIn("Rooftop dinner with a view", names)
        self.assertNotIn("Beach day and sunset dinner", names)
        self.assertNotIn("Ice skating and hot chocolate", names)

    def test_nearby_beach_and_vibe_boost(self):
        group = summarize_group([member("adventurous", "outdoor", 3)] * 3, "adventurous")
        location = {"setting": "town", "features": ["beach"], "season": "summer"}
        names = [i["name"] for i in suggest(location, group)]
        self.assertIn("Surf or paddleboard lesson", names[:3])

    def test_budget_uses_lowest(self):
        group = summarize_group([member(budget=3), member(budget=1)])
        self.assertEqual(group["budget"], 1)
        self.assertEqual(group["size"], 3)


class VotingTests(unittest.TestCase):
    def test_validate(self):
        self.assertIsNone(validate_ballot([1, 2, 3], 10))
        self.assertIsNotNone(validate_ballot([1, 1, 2], 10))
        self.assertIsNotNone(validate_ballot([1, 2], 10))
        self.assertIsNotNone(validate_ballot([1, 2, 11], 10))

    def test_tally_and_tiebreak(self):
        options = ["A", "B", "C", "D"]
        results = tally(options, [[2, 1, 3], [1, 2, 4]])
        # A and B both get 5 points and 1 first-place vote; A was suggested first.
        self.assertEqual([r["name"] for r in results[:2]], ["A", "B"])
        self.assertEqual(len(winners(results)), 2)

    def test_first_place_tiebreak(self):
        results = tally(["A", "B", "C"], [[2, 3, 1], [1, 2, 3], [2, 1, 3]])
        self.assertEqual(results[0]["name"], "B")


class CliTests(unittest.TestCase):
    def test_full_run_with_bad_input_retries(self):
        answers = iter([
            "Sam", "Lisbon", "city", "beach", "1", "7", "party",
            "2",
            "Ana", "9", "party", "outdoor", "2",
            "Ben", "creative", "either", "3",
            "1 1 2", "1 2 3",
            "1 3 2",
        ])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            results = cli.main(ask=lambda _prompt: next(answers))
        self.assertEqual(len(results), 10)
        self.assertIn("The winner is", out.getvalue())


if __name__ == "__main__":
    unittest.main()

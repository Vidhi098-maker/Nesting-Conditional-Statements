"""Scores the idea catalog against where the birthday person lives and what
the group wants, and returns the top suggestions."""

from collections import Counter

from .ideas import IDEAS

VIBES = ("chill", "adventurous", "fancy", "party", "creative")
SETTINGS = ("city", "suburb", "town", "rural")
FEATURES = ("beach", "mountains", "lake")
HEMISPHERES = ("north", "south", "tropical")
PLACE_PREFS = ("indoor", "outdoor", "either")
BUDGET_LABELS = {1: "cheap", 2: "moderate", 3: "splurge"}


def get_season(month, hemisphere):
    """Return the season for a month (1-12) in the given hemisphere."""
    if hemisphere == "tropical":
        return "warm"
    if month in (12, 1, 2):
        season = "winter"
    elif month in (3, 4, 5):
        season = "spring"
    elif month in (6, 7, 8):
        season = "summer"
    else:
        season = "autumn"
    if hemisphere == "south":
        flipped = {"winter": "summer", "summer": "winter", "spring": "autumn", "autumn": "spring"}
        season = flipped[season]
    return season


def summarize_group(members, birthday_vibe=None):
    """Combine everyone's answers into one group profile.

    Each member is a dict with "vibe", "place" and "budget". The birthday
    person's own favourite vibe (if given) counts double. The budget is the
    lowest anyone chose, so everyone can afford the plan.
    """
    vibes = Counter(m["vibe"] for m in members)
    if birthday_vibe:
        vibes[birthday_vibe] += 2
    places = Counter(m["place"] for m in members)
    budget = min(m["budget"] for m in members)
    return {"vibes": vibes, "places": places, "budget": budget, "size": len(members) + 1}


def score_idea(idea, location, group):
    """Return (score, reasons) for an idea, or None if it isn't possible."""
    season = location["season"]

    # Hard filters: things that make the idea impossible where they live.
    if location["setting"] not in idea["settings"]:
        return None
    if idea["needs"] is not None:
        if idea["needs"] not in location["features"]:
            return None
    if idea.get("winter_only"):
        if season != "winter":
            return None

    score = 0
    reasons = []

    # What the group wants.
    vibe_votes = sum(group["vibes"][v] for v in idea["vibes"])
    if vibe_votes > 0:
        score += 3 * vibe_votes
        top = max(idea["vibes"], key=lambda v: group["vibes"][v])
        reasons.append(f"matches the group's {top} vibe")

    if idea["outdoor"]:
        score += group["places"]["outdoor"]
        score -= group["places"]["indoor"]
    else:
        score += group["places"]["indoor"]
        score -= group["places"]["outdoor"]

    # Where they live: local features and the weather on the birthday.
    if idea["needs"] is not None:
        score += 3
        reasons.append(f"makes the most of the nearby {idea['needs']}")

    if idea["outdoor"]:
        if idea.get("winter_only"):
            score += 3
            reasons.append("perfect for a winter birthday")
        elif season == "winter":
            score -= 4
        elif season in ("summer", "warm"):
            score += 2
            reasons.append(f"great {season} weather activity")
    else:
        if season == "winter":
            score += 2
            reasons.append("cosy and indoors for winter")

    # Practical limits: budget and group size.
    if idea["cost"] > group["budget"]:
        score -= 4 * (idea["cost"] - group["budget"])
    else:
        reasons.append(f"fits a {BUDGET_LABELS[group['budget']]} budget")

    if group["size"] > idea["max_group"]:
        score -= 5
    elif idea["max_group"] >= 20:
        reasons.append("works for big groups")

    return score, reasons


def suggest(location, group, count=10):
    """Return the `count` best ideas as dicts with name, score and reasons."""
    scored = []
    for order, idea in enumerate(IDEAS):
        result = score_idea(idea, location, group)
        if result is not None:
            score, reasons = result
            scored.append((score, order, idea, reasons))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"name": idea["name"], "score": score, "reasons": reasons}
        for score, _, idea, reasons in scored[:count]
    ]

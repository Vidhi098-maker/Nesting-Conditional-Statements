"""Interactive command-line flow: location -> group survey -> 10 ideas -> vote."""

from .planner import (
    BUDGET_LABELS, FEATURES, HEMISPHERES, PLACE_PREFS, SETTINGS, VIBES,
    get_season, suggest, summarize_group,
)
from .voting import POINTS, tally, validate_ballot, winners


def ask_text(prompt, ask=input):
    while True:
        answer = ask(prompt).strip()
        if answer:
            return answer
        print("Please type something.")


def ask_choice(prompt, options, ask=input):
    """Ask until the user picks one of `options` (by number or by name)."""
    menu = ", ".join(f"{i}) {opt}" for i, opt in enumerate(options, 1))
    while True:
        answer = ask(f"{prompt} [{menu}]: ").strip().lower()
        if answer.isdigit():
            if 1 <= int(answer) <= len(options):
                return options[int(answer) - 1]
        elif answer in options:
            return answer
        print("Sorry, that isn't one of the options.")


def ask_int(prompt, low, high, ask=input):
    while True:
        answer = ask(prompt).strip()
        if answer.isdigit():
            if low <= int(answer) <= high:
                return int(answer)
        print(f"Please enter a number from {low} to {high}.")


def ask_features(ask=input):
    answer = ask("What's nearby? Any of beach, mountains, lake (comma separated, or blank for none): ")
    picked = {part.strip().lower() for part in answer.split(",") if part.strip()}
    return sorted(picked & set(FEATURES))


def ask_location(ask=input):
    print("\n--- Where does the birthday person live? ---")
    place = ask_text("City or town name: ", ask)
    setting = ask_choice("What kind of area is it?", SETTINGS, ask)
    features = ask_features(ask)
    hemisphere = ask_choice("Which climate zone?", HEMISPHERES, ask)
    month = ask_int("Birthday month (1-12): ", 1, 12, ask)
    season = get_season(month, hemisphere)
    return {"place": place, "setting": setting, "features": features, "season": season}


def ask_member(name, ask=input):
    print(f"\n{name}, what do you think?")
    vibe = ask_choice("  Ideal vibe", VIBES, ask)
    place = ask_choice("  Indoors or outdoors", PLACE_PREFS, ask)
    budget = ask_int("  Max budget (1 = cheap, 2 = moderate, 3 = splurge): ", 1, 3, ask)
    return {"name": name, "vibe": vibe, "place": place, "budget": budget}


def ask_ballot(voter, num_options, ask=input):
    while True:
        answer = ask(f"{voter}, enter your top {len(POINTS)} picks, best first (e.g. 4 1 7): ")
        parts = answer.replace(",", " ").split()
        if not all(p.isdigit() for p in parts):
            print("Please use numbers only.")
            continue
        picks = [int(p) for p in parts]
        error = validate_ballot(picks, num_options)
        if error is None:
            return picks
        print(error)


def main(ask=input):
    print("=== Birthday Planner ===")
    birthday_person = ask_text("Whose birthday is it? ", ask)
    location = ask_location(ask)
    birthday_vibe = ask_choice(f"What vibe does {birthday_person} love most?", VIBES, ask)

    print("\n--- Group survey ---")
    count = ask_int("How many friends are helping plan (1-20)? ", 1, 20, ask)
    members = []
    for i in range(count):
        name = ask_text(f"Friend #{i + 1} name: ", ask)
        members.append(ask_member(name, ask))

    group = summarize_group(members, birthday_vibe)
    ideas = suggest(location, group)

    print(f"\n--- Top {len(ideas)} ideas for {birthday_person} in {location['place']} "
          f"({location['setting']}, {location['season']} birthday, "
          f"{BUDGET_LABELS[group['budget']]} budget) ---")
    for i, idea in enumerate(ideas, 1):
        why = "; ".join(idea["reasons"]) or "a solid all-rounder"
        print(f"{i:>2}. {idea['name']}  -  {why}")

    print("\n--- Time to vote! (3 points for 1st, 2 for 2nd, 1 for 3rd) ---")
    ballots = [ask_ballot(m["name"], len(ideas), ask) for m in members]
    results = tally([idea["name"] for idea in ideas], ballots)

    print("\n--- Results ---")
    for r in results:
        print(f"{r['points']:>3} pts  ({r['first_place']} first-place)  {r['name']}")

    top = winners(results)
    if len(top) > 1:
        names = ", ".join(r["name"] for r in top)
        print(f"\nIt's a tie between: {names}.")
        print(f"The planner's higher-ranked suggestion breaks the tie: {results[0]['name']}!")
    else:
        print(f"\nThe winner is: {results[0]['name']}!")
    print(f"Happy birthday, {birthday_person}! 🎉")
    return results

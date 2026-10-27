"""Ranked voting: each voter picks their top 3 (3, 2 and 1 points)."""

POINTS = (3, 2, 1)


def validate_ballot(picks, num_options):
    """Return an error message for a bad ballot, or None if it is valid."""
    if len(picks) != len(POINTS):
        return f"Please pick exactly {len(POINTS)} options."
    if len(set(picks)) != len(picks):
        return "Each pick must be a different option."
    for pick in picks:
        if not 1 <= pick <= num_options:
            return f"Picks must be between 1 and {num_options}."
    return None


def tally(options, ballots):
    """Count the ballots (lists of 1-based option numbers, best first).

    Returns a list of result dicts sorted best first. Ties on points are
    broken by first-place votes, then by the original suggestion order.
    """
    results = [
        {"name": name, "points": 0, "first_place": 0, "rank": i}
        for i, name in enumerate(options)
    ]
    for ballot in ballots:
        for points, pick in zip(POINTS, ballot):
            results[pick - 1]["points"] += points
        results[ballot[0] - 1]["first_place"] += 1
    results.sort(key=lambda r: (-r["points"], -r["first_place"], r["rank"]))
    return results


def winners(results):
    """Return every option tied for the top on points and first-place votes."""
    best = results[0]
    return [
        r for r in results
        if r["points"] == best["points"] and r["first_place"] == best["first_place"]
    ]

"""Group planner: plan anything together (a birthday, a dinner, a night out,
a trip...) with a budget everyone agrees to and a vote designed so nobody
ends up somewhere they hate.

The rules, which the server enforces:
  - First everyone agrees what's being planned (the kind of plan, the place,
    the month...). Changing those details means everyone agrees again.
  - Everyone taking part gives the most they can spend per person. The group
    budget is the lowest of those, so nobody is priced out, and ideas above
    it are hidden.
  - Each person agrees the budget themselves. An agreement is for an exact
    amount, so if the budget changes everyone has to agree again.
  - The vote opens only once everyone has agreed. People rate each finalist
    from 0 ("No way") to 4 ("Love it"). Any "No way" rules an option out,
    and the winner is the option whose least-happy person is happiest.
  - It's decided once everyone taps "I'm in".

The ranking uses the same scoring as planner.py, extended to the group's own
ideas (which carry a cost per person instead of location rules). The hosted
page (hosted/group-planner.html) runs the same algorithm in JavaScript.
"""

import re
import secrets
import urllib.parse

from .ideas import IDEAS as BASE_IDEAS
from .planner import FEATURES, HEMISPHERES, PLACE_PREFS, SETTINGS, VIBES, get_season

KINDS = {
    "birthday": {"label": "Birthday", "place": "Town or city", "catalog": True},
    "outing": {"label": "Day or night out", "place": "Town or city", "catalog": True},
    "dinner": {"label": "Dinner or meal", "place": "Town or city", "catalog": True},
    "trip": {"label": "Trip or holiday", "place": "Travelling from", "catalog": False},
    "other": {"label": "Something else", "place": "Where", "catalog": False},
}
CURRENCIES = {"£": "£ Pound", "€": "€ Euro", "$": "$ Dollar", "₹": "₹ Rupee"}
COST_GUIDE = {"£": (15, 40, 90), "€": (18, 45, 100), "$": (20, 50, 110), "₹": (800, 2500, 6000)}
FACES = (("😣", "No way"), ("😕", "Meh"), ("🙂", "OK"), ("😀", "Good"), ("😍", "Love it"))
FINALISTS = 6
MAX_CUSTOM_IDEAS = 60
MAX_GUESTS = 30

ALL = ("city", "suburb", "town", "rural")
URBAN = ("city", "suburb")
FUN = ("birthday", "outing")
FUN_FOOD = ("birthday", "outing", "dinner")

# Map-search terms and plan kinds for the base catalog in ideas.py.
SEARCH = {
    "Rooftop dinner with a view": "rooftop restaurant", "Escape room challenge": "escape room",
    "Karaoke night in a private booth": "karaoke private room", "Chef's tasting menu": "tasting menu restaurant",
    "Comedy club show": "comedy club", "Live concert or gig": "live music venue",
    "Museum or gallery day + cafe": "museum", "Pottery or painting class": "pottery class",
    "Cooking class together": "cooking class", "Bowling and arcade night": "bowling alley",
    "Go-kart racing": "go kart track", "Trampoline park": "trampoline park",
    "Indoor rock climbing": "climbing gym", "Spa day": "day spa", "Food market crawl": "food market",
    "Themed house party": "party supplies", "Backyard barbecue": "butcher barbecue",
    "Board game and pizza night": "board game cafe", "Picnic in the park": "park picnic area",
    "Outdoor movie under the stars": "outdoor cinema", "Bonfire and s'mores night": "fire pit campground",
    "Stargazing trip": "stargazing spot", "Farm visit and fruit picking": "pick your own farm",
    "Horse riding trail": "horse riding", "Winery or brewery tour": "winery tour",
    "DIY craft party (tie-dye, candles)": "craft workshop", "Scavenger hunt around town": "scavenger hunt",
    "Beach day and sunset dinner": "beach restaurant", "Surf or paddleboard lesson": "surf school",
    "Sunset boat cruise": "sunset boat cruise", "Mountain hike with a summit picnic": "hiking trail",
    "Cabin weekend getaway": "cabin rental", "Ski or snowboard day": "ski resort",
    "Kayaking on the lake": "kayak rental", "Lakeside camping trip": "lakeside campground",
    "Ice skating and hot chocolate": "ice rink",
}
FOOD = {
    "Rooftop dinner with a view", "Chef's tasting menu", "Cooking class together", "Food market crawl",
    "Backyard barbecue", "Murder mystery dinner at home", "Picnic in the park",
    "Fancy dress-up dinner party", "Beach day and sunset dinner",
}


def _extra(name, vibes, settings, outdoor, cost, max_group, search, kinds):
    return {"name": name, "vibes": vibes, "settings": settings, "needs": None, "outdoor": outdoor,
            "cost": cost, "max_group": max_group, "search": search, "kinds": kinds}


EXTRA_IDEAS = [
    _extra("Pub with a beer garden", ("chill", "party"), ALL, True, 1, 25, "pub beer garden", FUN_FOOD),
    _extra("Cocktail bar crawl", ("party", "fancy"), URBAN, False, 2, 12, "cocktail bar", FUN),
    _extra("Tapas and sharing plates", ("chill", "party"), URBAN, False, 2, 14, "tapas restaurant", ("dinner", "birthday")),
    _extra("Korean barbecue", ("party", "adventurous"), URBAN, False, 2, 12, "korean bbq", ("dinner", "birthday")),
    _extra("Sushi or omakase", ("fancy",), URBAN, False, 3, 8, "sushi restaurant", ("dinner",)),
    _extra("Pizza and wine bar", ("chill",), ALL, False, 1, 16, "pizzeria", ("dinner",)),
    _extra("Curry house feast", ("chill", "party"), ALL, False, 1, 20, "indian restaurant", ("dinner", "birthday")),
    _extra("Bottomless brunch", ("party", "chill"), URBAN, False, 2, 16, "bottomless brunch", ("dinner", "birthday", "outing")),
    _extra("Potluck dinner at someone's home", ("chill", "creative"), ALL, False, 1, 20, None, ("dinner",)),
    _extra("Street food night", ("adventurous", "chill"), URBAN, True, 1, 15, "street food market", ("dinner",)),
]

CATALOG = [
    {**i, "kinds": FUN_FOOD if i["name"] in FOOD else FUN, "search": SEARCH.get(i["name"])} for i in BASE_IDEAS
] + EXTRA_IDEAS


class PlanError(ValueError):
    """An action that isn't allowed right now; the message is shown to people."""


def money(currency, amount):
    return "–" if amount is None else f"{currency}{round(amount):,}"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:60] or "idea"


# --- the ranking algorithm (mirrors hosted/group-planner.html) ---------------

def summarize_group(people):
    vibes, places = {}, {}
    for p in people:
        if p.get("vibe"):
            vibes[p["vibe"]] = vibes.get(p["vibe"], 0) + 1
        if p.get("place"):
            places[p["place"]] = places.get(p["place"], 0) + 1
    return {"vibes": vibes, "places": places, "size": len(people)}


def learn_weights(feedback, lookup):
    w = {}
    for name, signal in feedback.items():
        idea = lookup.get(name)
        if idea is None:
            continue
        for v in idea["vibes"]:
            w[v] = w.get(v, 0) + signal
        if idea["outdoor"] is not None:
            k = "outdoor" if idea["outdoor"] else "indoor"
            w[k] = w.get(k, 0) + signal
    return w


def score_idea(idea, ctx, group, w):
    """Return (score, reasons), or None if the idea can't happen here."""
    season, custom = ctx["season"], idea.get("custom", False)
    if not custom:
        if not ctx["setting"] or ctx["setting"] not in idea["settings"]:
            return None
        if idea["needs"] and idea["needs"] not in ctx["features"]:
            return None
        if idea.get("winter_only") and season != "winter":
            return None
    score, reasons = 0, []
    vibe_votes = sum(group["vibes"].get(v, 0) for v in idea["vibes"])
    if vibe_votes > 0:
        score += 3 * vibe_votes
        top = max(idea["vibes"], key=lambda v: group["vibes"].get(v, 0))
        reasons.append(f"matches the group's {top} vibe")
    if idea["outdoor"] is not None:
        out, ins = group["places"].get("outdoor", 0), group["places"].get("indoor", 0)
        score += out - ins if idea["outdoor"] else ins - out
    if idea.get("needs"):
        score += 3
        reasons.append(f"makes the most of the nearby {idea['needs']}")
    if season and idea["outdoor"] is not None and not custom:
        if idea["outdoor"]:
            if idea.get("winter_only"):
                score += 3
                reasons.append("perfect in winter")
            elif season == "winter":
                score -= 4
            elif season in ("summer", "warm"):
                score += 2
                reasons.append(f"great {season} weather activity")
        elif season == "winter":
            score += 2
            reasons.append("cosy and indoors for winter")
    if ctx["limit"] is not None:
        reasons.append(f"within the {ctx['currency']}{ctx['limit']} budget")
    if not custom and group["size"] > idea["max_group"]:
        score -= 5
    elif not custom and idea["max_group"] >= 20 and group["size"] > 6:
        reasons.append("works for big groups")
    learned = sum(w.get(v, 0) for v in idea["vibes"])
    if idea["outdoor"] is not None:
        learned += w.get("outdoor" if idea["outdoor"] else "indoor", 0)
    if learned:
        score += 2 * learned
        if learned > 0:
            reasons.append("similar to ideas you liked")
    if custom:
        reasons.append("suggested by the planner bot" if idea.get("by_bot") else "added by the group")
    return score, reasons


def overlap(a, b):
    s = len(set(a["vibes"]) & set(b["vibes"]))
    if a.get("needs") and a.get("needs") == b.get("needs"):
        s += 1
    if a["outdoor"] is not None and a["outdoor"] == b["outdoor"]:
        s += 1
    return s


def suggest(candidates, ctx, group, feedback=None, count=10, diversity=1):
    """Rank ideas: drop ruled-out and over-budget ones, score the rest, then
    pick greedily with a penalty for being too like ideas already picked."""
    feedback = feedback or {}
    lookup = {c["name"]: c for c in candidates}
    w = learn_weights(feedback, lookup)
    cands, over_budget = [], 0
    for order, idea in enumerate(candidates):
        if feedback.get(idea["name"], 0) < 0:
            continue
        if ctx["limit"] is not None and idea["cost_pp"] > ctx["limit"]:
            over_budget += 1
            continue
        r = score_idea(idea, ctx, group, w)
        if r is not None:
            cands.append((r[0], order, idea, r[1]))
    picked = []
    while cands and len(picked) < count:
        def key(c):
            pen = max((overlap(c[2], p[2]) for p in picked), default=0)
            return (c[0] - diversity * pen, -c[1])
        best = max(cands, key=key)
        cands.remove(best)
        picked.append(best)
    ideas = [{"name": i["name"], "score": s, "reasons": r, "cost_pp": i["cost_pp"],
              "custom": bool(i.get("custom")), "note": i.get("note", "")} for s, _, i, r in picked]
    return ideas, over_budget


def consensus(finalists, ballots):
    """An option anyone rated 0 is out; among the rest the one whose least
    happy person is happiest wins, then the higher total, then list order."""
    rows = []
    for rank, f in enumerate(finalists):
        scores = [b["ratings"][f["name"]] for b in ballots if isinstance(b["ratings"].get(f["name"]), int)]
        rows.append({**f, "rank": rank, "min": min(scores) if scores else None, "total": sum(scores),
                     "vetoes": [b["uid"] for b in ballots if b["ratings"].get(f["name"]) == 0]})
    rows.sort(key=lambda r: (len(r["vetoes"]) > 0, -(r["min"] if r["min"] is not None else -1), -r["total"], r["rank"]))
    winner = rows[0] if rows and not rows[0]["vetoes"] else None
    return rows, winner


# --- one group's plan --------------------------------------------------------

class GroupPlan:
    """All of one room's planning state. Callers hold the room's lock."""

    def __init__(self):
        self.details = {}      # kind, title, place, setting, features, hemisphere, month, currency
        self.details_version = 0   # bumped on every change; agreements are for one version
        self.phase = "planning"
        self.round = 0
        self.frozen = []       # finalists of the current vote
        self.people = {}       # uid -> {name, vibe, place, budget_max, budget_ok, active, signoff}
        self.guests = {}       # slug -> {name, vibe, place, budget_max} (mentioned in chat, not using the app)
        self.feedback = {}     # uid -> {idea name: +1 / -1}
        self.ratings = {}      # uid -> {"round": n, "ratings": {idea name: 0-4}}
        self.ideas = {}        # slug -> custom idea
        self.venues = {}       # idea name -> [{name, url, note}]
        self.host = None
        self.tokens = {}       # uid -> random id shown to others (account ids stay private)

    # --- derived facts ---

    @property
    def currency(self):
        return self.details.get("currency") if self.details.get("currency") in CURRENCIES else "£"

    def kind(self):
        return self.details.get("kind") if self.details.get("kind") in KINDS else None

    def members(self):
        """Everyone in the plan who hasn't left, with or without preferences."""
        return [(uid, p) for uid, p in self.people.items() if p.get("active", True)]

    def participants(self):
        return [(uid, p) for uid, p in self.people.items() if p.get("vibe") in VIBES and p.get("active", True)]

    def everyone(self):
        return [p for _, p in self.participants()] + [g for g in self.guests.values() if g.get("vibe") in VIBES]

    @staticmethod
    def budget_of(p):
        b = p.get("budget_max")
        return b if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0 else None

    def budget(self):
        parts = self.participants()
        budgets = [self.budget_of(p) for p in self.everyone() if self.budget_of(p) is not None]
        limit = min(budgets) if budgets else None
        missing = [uid for uid, p in parts if self.budget_of(p) is None]
        agreed = [uid for uid, p in parts if limit is not None and p.get("budget_ok") == limit]
        all_agreed = limit is not None and bool(parts) and not missing and len(agreed) == len(parts)
        return {"limit": limit, "missing": missing, "agreed": agreed, "all_agreed": all_agreed}

    def details_agreement(self):
        """Each person's tick or cross on the current plan details. Everyone in
        the plan counts, so the tally always matches the number of people."""
        members = self.members()
        ready = bool(self.kind() and self.details.get("place"))
        votes = {uid: (p.get("plan_vote") or {}) for uid, p in members}
        current = {uid: v.get("agree") for uid, v in votes.items() if ready and v.get("version") == self.details_version}
        agreed = [uid for uid, _ in members if current.get(uid) is True]
        disagreed = [uid for uid, _ in members if current.get(uid) is False]
        waiting = [uid for uid, _ in members if uid not in current]
        return {"version": self.details_version, "agreed": agreed, "disagreed": disagreed, "waiting": waiting,
                "total": len(members), "all_agreed": ready and bool(members) and len(agreed) == len(members)}

    def context(self):
        d = self.details
        season = get_season(d["month"], d.get("hemisphere") or "north") if d.get("month") else None
        return {"setting": d.get("setting"), "features": d.get("features") or [], "season": season,
                "limit": self.budget()["limit"], "currency": self.currency}

    def candidates(self):
        kind, guide = self.kind(), COST_GUIDE.get(self.currency, COST_GUIDE["$"])
        out = []
        if kind and KINDS[kind]["catalog"]:
            out += [{**i, "cost_pp": guide[i["cost"] - 1], "estimate": True} for i in CATALOG if kind in i["kinds"]]
        for c in self.ideas.values():
            out.append({**c, "custom": True, "settings": ALL, "needs": None, "kinds": (), "estimate": c.get("by_bot", False)})
        return out

    def group_feedback(self):
        total = {}
        for f in self.feedback.values():
            for name, v in f.items():
                total[name] = total.get(name, 0) + (1 if v > 0 else -1 if v < 0 else 0)
        return {k: (1 if v > 0 else -1) for k, v in total.items() if v}

    def ranking(self):
        kind = self.kind()
        if not kind:
            return [], 0, False
        if KINDS[kind]["catalog"] and not self.details.get("setting"):
            return [], 0, True
        ideas, over = suggest(self.candidates(), self.context(), summarize_group(self.everyone()), self.group_feedback())
        return ideas, over, False

    def current_phase(self):
        """The stored phase only holds while everyone agrees the plan and the budget."""
        if self.phase != "planning" and not (self.details_agreement()["all_agreed"] and self.budget()["all_agreed"]):
            return "planning"
        return self.phase

    def finalists(self):
        limit = self.budget()["limit"]
        return [f for f in self.frozen if limit is None or f["cost_pp"] <= limit]

    def ballots(self):
        parts = dict(self.participants())
        return [{"uid": uid, "ratings": r["ratings"]} for uid, r in self.ratings.items()
                if r.get("round") == self.round and uid in parts]

    def showing_results(self):
        phase = self.current_phase()
        if phase == "results":
            return True
        parts = self.participants()
        return phase == "voting" and bool(parts) and len(self.ballots()) == len(parts)

    def signoffs(self):
        out = {"in": [], "out": [], "waiting": []}
        for uid, p in self.participants():
            s = p.get("signoff") or {}
            if s.get("round") == self.round:
                out["in" if s.get("in") else "out"].append(uid)
            else:
                out["waiting"].append(uid)
        return out

    def result(self):
        rows, winner = consensus(self.finalists(), self.ballots())
        so = self.signoffs()
        decided = bool(winner) and bool(self.participants()) and not so["out"] and not so["waiting"]
        return rows, winner, decided

    def vote_blockers(self):
        parts, b = self.participants(), self.budget()
        out = []
        d = self.details_agreement()
        if not parts:
            out.append("someone needs to save their preferences")
        elif not d["all_agreed"]:
            if d["disagreed"]:
                out.append(f"{self.names(d['disagreed'])} doesn't agree the plan yet")
            if d["waiting"]:
                out.append(f"waiting for {self.names(d['waiting'])} to agree the plan")
        elif len(parts) < len(self.members()):
            waiting = [u for u, p in self.members() if p.get("vibe") not in VIBES]
            out.append(f"waiting for {self.names(waiting)} to add their preferences")
        elif b["missing"]:
            out.append(f"waiting for {self.names(b['missing'])} to add a budget")
        elif not b["all_agreed"]:
            out.append(f"waiting for {self.names([u for u, _ in parts if u not in b['agreed']])} to agree the budget")
        if len(self.ranking()[0]) < 2:
            out.append("there need to be at least 2 ideas")
        return out

    def names(self, uids):
        return ", ".join(self.people[u]["name"] for u in uids if u in self.people)

    # --- actions (each raises PlanError when it isn't allowed) ---

    def _participant(self, uid):
        p = self.people.get(uid)
        if not p or p.get("vibe") not in VIBES or not p.get("active", True):
            raise PlanError("Save your preferences to take part first.")
        return p

    def join(self, uid, name):
        if uid not in self.people:
            self.people[uid] = {"name": name, "active": True}
            self.tokens[uid] = secrets.token_hex(8)
            self.host = self.host or uid

    def token(self, uid):
        return self.tokens.get(uid)

    def uid_for(self, token):
        return next((u for u, t in self.tokens.items() if t == token), None)

    def set_details(self, fields, uid=None):
        """Change the plan details. Any real change means everyone agrees again
        (budget included); whoever made the change agrees to it."""
        d = dict(self.details)
        if "kind" in fields:
            if fields["kind"] not in KINDS:
                raise PlanError("Choose what kind of plan this is.")
            d["kind"] = fields["kind"]
        for key, limit in (("title", 60), ("place", 60)):
            if key in fields:
                d[key] = " ".join(str(fields[key] or "").split())[:limit]
        if "setting" in fields:
            if fields["setting"] not in SETTINGS + (None, ""):
                raise PlanError(f"Area must be one of {', '.join(SETTINGS)}.")
            d["setting"] = fields["setting"] or None
        if "features" in fields:
            d["features"] = [f for f in (fields["features"] or []) if f in FEATURES]
        if "hemisphere" in fields:
            if fields["hemisphere"] not in HEMISPHERES + (None, ""):
                raise PlanError("Climate must be north, south or tropical.")
            d["hemisphere"] = fields["hemisphere"] or None
        if "month" in fields:
            m = fields["month"]
            if m not in (None, "") and (not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= 12):
                raise PlanError("Month must be 1-12.")
            d["month"] = m or None
        if "currency" in fields:
            if fields["currency"] not in CURRENCIES:
                raise PlanError("Currency must be £, €, $ or ₹.")
            d["currency"] = fields["currency"]
        if not d.get("kind") or not d.get("place"):
            raise PlanError("Choose the kind of plan and fill in the place.")
        if KINDS[d["kind"]]["catalog"] and not d.get("setting"):
            raise PlanError("Choose the kind of area so the ideas fit.")
        if d != self.details:
            self.details = d
            self.details_version += 1
            for p in self.people.values():
                p.pop("plan_vote", None)
                p.pop("budget_ok", None)
        if uid in self.people and self.people[uid].get("active", True):
            self.people[uid]["plan_vote"] = {"version": self.details_version, "agree": True}

    def vote_plan(self, uid, agree, version=None):
        """A tick (agree) or cross (don't agree) on the current plan details."""
        p = self.people.get(uid)
        if not p or not p.get("active", True):
            raise PlanError("Join the plan first.")
        if not (self.kind() and self.details.get("place")):
            raise PlanError("There are no plan details to agree yet.")
        if version is not None and version != self.details_version:
            raise PlanError("The plan details just changed. Check them and answer again.")
        p["plan_vote"] = {"version": self.details_version, "agree": bool(agree)}

    def agree_details(self, uid, version=None):
        self.vote_plan(uid, True, version)

    def set_preferences(self, uid, vibe, place, budget_max):
        if uid not in self.people:
            raise PlanError("Join the plan first.")
        if vibe not in VIBES or place not in PLACE_PREFS:
            raise PlanError("Pick a vibe and indoors or outdoors.")
        if budget_max is not None:
            if isinstance(budget_max, bool) or not isinstance(budget_max, (int, float)) or not budget_max > 0:
                raise PlanError("The budget needs to be a number above zero.")
            budget_max = round(budget_max)
            if budget_max < 1:
                raise PlanError("The budget needs to be at least 1.")
        self.people[uid].update(vibe=vibe, place=place, budget_max=budget_max, active=True)

    def agree_budget(self, uid, amount=None):
        p, b = self._participant(uid), self.budget()
        if not self.details_agreement()["all_agreed"]:
            raise PlanError("Everyone needs to agree the plan details before the budget.")
        if b["limit"] is None:
            raise PlanError("There's no group budget yet.")
        if self.budget_of(p) is None:
            raise PlanError("Add the most you can spend first.")
        if amount is not None and amount != b["limit"]:
            raise PlanError(f"The group budget just changed to {money(self.currency, b['limit'])}. Check it and agree again.")
        p["budget_ok"] = b["limit"]

    def set_feedback(self, uid, idea, signal):
        self._participant(uid)
        if idea not in {c["name"] for c in self.candidates()}:
            raise PlanError("That idea isn't on the list.")
        mine = self.feedback.setdefault(uid, {})
        if signal:
            mine[idea] = 1 if signal > 0 else -1
        else:
            mine.pop(idea, None)

    def add_idea(self, name, cost_pp, vibes, outdoor=None, note="", by_bot=False, added_by=None):
        name = " ".join(str(name or "").split())[:70]
        vibes = [v for v in (vibes or []) if v in VIBES]
        if not name or not vibes:
            raise PlanError("An idea needs a name and at least one vibe.")
        if isinstance(cost_pp, bool) or not isinstance(cost_pp, (int, float)) or cost_pp < 0:
            raise PlanError("The cost per person needs to be a number.")
        key = slug(name)
        if key not in self.ideas and len(self.ideas) >= MAX_CUSTOM_IDEAS:
            raise PlanError("This plan has a lot of ideas already. Rule some out first.")
        self.ideas[key] = {"name": name, "cost_pp": round(cost_pp), "vibes": vibes,
                           "outdoor": outdoor if isinstance(outdoor, bool) else None,
                           "note": " ".join(str(note or "").split())[:140], "by_bot": by_bot, "added_by": added_by,
                           "search": name}
        return self.ideas[key]

    def add_guest(self, name, vibe, place, budget_max=None):
        name = " ".join(str(name or "").split())[:40]
        if not name or vibe not in VIBES or place not in PLACE_PREFS:
            raise PlanError("A friend needs a name, a vibe and indoors or outdoors.")
        if slug(name) not in self.guests and len(self.guests) >= MAX_GUESTS:
            raise PlanError("Too many friends added by chat.")
        b = round(budget_max) if isinstance(budget_max, (int, float)) and not isinstance(budget_max, bool) and budget_max >= 1 else None
        self.guests[slug(name)] = {"name": name, "vibe": vibe, "place": place, "budget_max": b}

    def set_active(self, actor, target_token, active):
        target = self.uid_for(target_token)
        if target not in self.people:
            raise PlanError("That person isn't in the plan.")
        if actor != target and actor != self.host:
            raise PlanError("Only the host can change who's taking part.")
        self.people[target]["active"] = bool(active)

    def start_vote(self, uid):
        self._participant(uid)
        blockers = self.vote_blockers()
        if blockers:
            raise PlanError("Can't vote yet: " + "; ".join(blockers) + ".")
        ideas = self.ranking()[0][:FINALISTS]
        self.frozen = [{"name": i["name"], "cost_pp": i["cost_pp"], "reasons": i["reasons"], "custom": i["custom"]} for i in ideas]
        self.round += 1
        self.phase = "voting"

    def rate(self, uid, ratings):
        self._participant(uid)
        if self.current_phase() != "voting":
            raise PlanError("Voting isn't open.")
        names = [f["name"] for f in self.finalists()]
        if not isinstance(ratings, dict) or set(ratings) != set(names):
            raise PlanError("Rate every option.")
        if any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 4 for v in ratings.values()):
            raise PlanError("Ratings go from 0 (No way) to 4 (Love it).")
        self.ratings[uid] = {"round": self.round, "ratings": dict(ratings)}

    def close_vote(self, uid):
        self._participant(uid)
        if self.current_phase() != "voting":
            raise PlanError("Voting isn't open.")
        if not self.ballots():
            raise PlanError("Nobody has rated yet.")
        self.phase = "results"

    def back_to_planning(self, uid):
        self._participant(uid)
        self.phase = "planning"

    def sign_off(self, uid, is_in):
        p = self._participant(uid)
        if not self.showing_results() or not self.result()[1]:
            raise PlanError("There's nothing to sign off yet.")
        p["signoff"] = {"round": self.round, "in": bool(is_in)}

    def attach_venues(self, idea, venues):
        names = {i["name"] for i in self.ranking()[0]} | {f["name"] for f in self.finalists()}
        if idea not in names:
            raise PlanError(f"{idea!r} isn't in the current list.")
        cleaned = []
        for v in list(venues or [])[:3]:
            url = str(v.get("url", ""))
            if not url.startswith(("https://", "http://")):
                raise PlanError("Each venue needs the http(s) URL of its source page.")
            cleaned.append({"name": str(v.get("name", ""))[:80], "url": url[:500], "note": str(v.get("note", ""))[:120]})
        self.venues[idea] = cleaned

    # --- what one person sees ---

    def map_url(self, name):
        idea = next((c for c in self.candidates() if c["name"] == name), None)
        place, kind = self.details.get("place"), self.kind()
        term = idea and (idea.get("search") or (idea["name"] if idea.get("custom") else None))
        if not term or not place:
            return None
        query = term if kind == "trip" else f"{term} near {place}"
        return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)

    def view(self, uid):
        cur, b, da = self.currency, self.budget(), self.details_agreement()
        parts = dict(self.participants())
        ballots = self.ballots()
        rated = {x["uid"] for x in ballots}
        so = self.signoffs()
        ideas, over, needs_area = self.ranking()
        mine = self.feedback.get(uid, {})
        estimates = {c["name"]: c.get("estimate", False) for c in self.candidates()}

        def idea_view(i):
            return {"name": i["name"], "cost_pp": i["cost_pp"], "estimate": estimates.get(i["name"], False),
                    "reasons": i.get("reasons", []), "note": i.get("note", ""), "mine": mine.get(i["name"], 0),
                    "map_url": self.map_url(i["name"]), "venues": self.venues.get(i["name"], [])}

        people = []
        for pid, p in self.people.items():
            bm = self.budget_of(p)
            people.append({
                "name": p["name"], "is_you": pid == uid, "is_host": pid == self.host, "id": self.token(pid),
                "joined": p.get("vibe") in VIBES, "active": p.get("active", True), "guest": False,
                "vibe": p.get("vibe"), "place": p.get("place"), "budget_max": bm,
                "agreed": pid in b["agreed"], "sets_budget": bm is not None and bm == b["limit"],
                "agreed_details": pid in da["agreed"],
                "plan_vote": "yes" if pid in da["agreed"] else "no" if pid in da["disagreed"] else None,
                "rated": pid in rated,
                "signoff": "in" if pid in so["in"] else "out" if pid in so["out"] else None,
            })
        for g in self.guests.values():
            bm = self.budget_of(g)
            people.append({"name": g["name"], "is_you": False, "is_host": False, "id": None, "joined": True,
                           "active": True, "guest": True, "vibe": g["vibe"], "place": g["place"], "budget_max": bm,
                           "agreed": False, "sets_budget": bm is not None and bm == b["limit"], "agreed_details": False,
                           "plan_vote": None,
                           "rated": False, "signoff": None})

        rows, winner, decided = self.result()
        me = self.people.get(uid)
        return {
            "details": {**self.details, "currency": cur, "season": self.context()["season"],
                        "kind_label": KINDS[self.kind()]["label"] if self.kind() else None},
            "phase": self.current_phase(), "paused": self.phase != "planning" and self.current_phase() == "planning",
            "round": self.round,
            "you": {"id": self.token(uid), "name": me["name"], "is_host": uid == self.host, "taking_part": uid in parts,
                    "agreed": uid in b["agreed"], "rated": uid in rated,
                    "my_ratings": self.ratings.get(uid, {}).get("ratings", {}) if uid in rated else {},
                    "vibe": me.get("vibe"), "place": me.get("place"), "budget_max": self.budget_of(me),
                    "active": me.get("active", True)} if me else None,
            "people": people,
            "plan_agreement": {"version": da["version"], "all_agreed": da["all_agreed"], "agreed": len(da["agreed"]),
                               "disagreed": len(da["disagreed"]), "total": da["total"], "taking_part": da["total"],
                               "you_agreed": uid in da["agreed"],
                               "you": "yes" if uid in da["agreed"] else "no" if uid in da["disagreed"] else None},
            "budget": {"limit": b["limit"], "all_agreed": b["all_agreed"], "agreed": len(b["agreed"]),
                       "taking_part": len(parts), "missing": len(b["missing"])},
            "ideas": [idea_view(i) for i in ideas], "over_budget": over, "needs_area": needs_area,
            "vote_blockers": self.vote_blockers(),
            "finalists": [idea_view(f) for f in self.finalists()] if self.current_phase() != "planning" else [],
            "rated": len(rated),
            "results": {
                "rows": [{"name": r["name"], "cost_pp": r["cost_pp"], "min": r["min"], "total": r["total"],
                          "vetoed_by": [self.people[u]["name"] for u in r["vetoes"]], "map_url": self.map_url(r["name"])} for r in rows],
                "winner": {"name": winner["name"], "min": winner["min"], "cost_pp": winner["cost_pp"],
                           "map_url": self.map_url(winner["name"])} if winner else None,
                "decided": decided,
                "signoff": {k: [self.people[u]["name"] for u in v] for k, v in so.items()},
            } if self.showing_results() else None,
        }

    def brief(self):
        """A compact summary for the planner bot."""
        b = self.budget()
        ideas = self.ranking()[0]
        return {
            "plan": {**self.details, "currency": self.currency, "phase": self.current_phase()},
            "people": [{"name": p["name"], "vibe": p.get("vibe"), "place": p.get("place"),
                        "maxPerPerson": self.budget_of(p), "agreedBudget": uid in b["agreed"]}
                       for uid, p in self.participants()]
                      + [{"name": g["name"], "vibe": g["vibe"], "place": g["place"],
                          "maxPerPerson": self.budget_of(g), "notUsingApp": True} for g in self.guests.values()],
            "planDetailsAgreedByEveryone": self.details_agreement()["all_agreed"],
            "groupBudgetPerPerson": b["limit"], "budgetAgreedByEveryone": b["all_agreed"],
            "ideas": [{"name": i["name"], "costPerPerson": i["cost_pp"]} for i in ideas],
            "voteBlockers": self.vote_blockers(),
        }

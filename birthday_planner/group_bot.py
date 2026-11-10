"""The planner bot for the group web app: Claude's tools over a GroupPlan.

Claude runs the conversation; group.py makes the decisions and enforces the
rules. Tools that act for a person (their preferences, agreeing the budget,
liking an idea) only work for people who wrote in the batch of messages the
bot is answering, so the bot can never agree a budget for someone who didn't
say so.
"""

import json

from .chatbot import ATTACH_VENUES_TOOL, WEB_SEARCH_TOOL, ToolError
from .group import AFTER_STATES, CURRENCIES, FEATURES, KINDS, PLACE_PREFS, SETTINGS, HEMISPHERES, PlanError, money

GROUP_PROMPT = """You are the planner bot in a group chat of friends deciding where to go and \
what to do together: a birthday, a dinner, a day or night out, a trip, anything. Be warm and brief \
(2-6 short sentences, or a short list). Reply in plain text without Markdown headings or bold.

Each user message starts with the sender's name in square brackets, e.g. "[Ana]: I'd love \
something outdoors". Several people may write at once; address people by name.

The app runs the decision in a fixed order, and enforces it. Steer the group through it:
1. LOCATION first. Where it happens is the most important thing; everyone ticks the plan details.
2. AMOUNT. Everyone gives the most they can spend per person; the group budget is the lowest, \
and each person agrees it themselves.
3. KIND OF ACTIVITY, by working backwards. Don't ask "what do you want to do?". Ask how each \
person wants to FEEL when it's over, and map their answer to one of:
   spent (physically spent, will sleep deeply), sharp (mentally sharp, solved something), \
unplugged (brain rested after a full-on week), connected (closer to each other), buzzing \
(a big one to remember).
   The after-feeling points to the category of activity; the app's search ranks ideas by it.
4. FEED. Ideas are suggested to a shared feed, where everyone ticks or crosses each one.
5. MAJORITY. An idea more than half the group ticks goes into the plan, in the order approved.

Tools (act only for people who wrote in this batch, and only on what they said themselves):
- set_plan: where and what (kind, name, place, area type, nearby beach/mountains/lake, month, \
climate, currency). Infer what you can (e.g. "Headingley, Leeds" = suburb, north, £). For trips, \
place is where people travel from.
- agree_plan: a sender's tick or cross on the plan details.
- set_preferences: a sender's after-feeling, indoor/outdoor preference and the most they can \
spend per person (a number in the plan's currency).
- agree_budget: ONLY when that sender clearly accepts the current group budget.
- add_friend: answers for someone mentioned who isn't using the app.
- get_suggestions: the search results ranked for this group, with reasons, plus the feed, the \
agreed plan and what's blocking the next step. Call it after changes, before talking about ideas.
- add_idea: a concrete new option with a realistic estimated cost per person and the \
after-feelings it gives. Use it for trips and anything the built-in list doesn't cover, inside \
the agreed budget. For trips include travel, stay, food and activities, and name a real \
destination and length (e.g. "4 days in Kraków").
- suggest_idea: put an idea from the search results on the feed. Pass the sender's name when \
they asked for it; leave it out to suggest it yourself. Suggest at most 3 of your own at a time.
- vote_on_suggestion: a sender's tick or cross on something on the feed.
- web_search: check real current prices before adding trip ideas, and find real places.
- attach_venues: pin real places from your search results to an idea. Never make up a venue.

Say which step the group is on and who it's waiting for. Costs you add are estimates; say so. \
You can't book anything."""

PERSON = {"type": "string", "description": "Name of the person, exactly as written in their [Name] tag"}
AFTER = {"type": "string", "enum": list(AFTER_STATES),
         "description": "How they want to feel when it's over"}

TOOLS = [
    {"name": "set_plan", "description": "Save or change where and what is being planned. Send only fields you know; others are kept.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(KINDS)}, "title": {"type": "string"},
         "place": {"type": "string", "description": "Town or city; for trips, where people travel from"},
         "setting": {"type": "string", "enum": list(SETTINGS)},
         "features": {"type": "array", "items": {"type": "string", "enum": list(FEATURES)}},
         "hemisphere": {"type": "string", "enum": list(HEMISPHERES)},
         "month": {"type": "integer", "minimum": 1, "maximum": 12},
         "currency": {"type": "string", "enum": list(CURRENCIES)}}}},
    {"name": "set_preferences", "description": "Save one sender's answers. budget_max is the most they can spend per person.",
     "input_schema": {"type": "object", "properties": {
         "person": PERSON, "after_state": AFTER,
         "place": {"type": "string", "enum": list(PLACE_PREFS)}, "budget_max": {"type": "number", "minimum": 1}},
         "required": ["person", "after_state", "place"]}},
    {"name": "agree_plan", "description": "Record a sender's tick (agrees) or cross (doesn't agree) on the current plan details. Only what they said themselves.",
     "input_schema": {"type": "object", "properties": {"person": PERSON, "agrees": {"type": "boolean"}}, "required": ["person"]}},
    {"name": "agree_budget", "description": "Record that a sender accepts the current group budget. Only when they said so themselves.",
     "input_schema": {"type": "object", "properties": {"person": PERSON}, "required": ["person"]}},
    {"name": "add_friend", "description": "Save answers for a friend who was mentioned but isn't using the app.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "after_state": AFTER,
         "place": {"type": "string", "enum": list(PLACE_PREFS)}, "budget_max": {"type": "number", "minimum": 1}},
         "required": ["name", "after_state", "place"]}},
    {"name": "add_idea", "description": "Add one concrete option to the search results, with a realistic estimated cost per person in the plan's currency.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Short and concrete, e.g. \"4 days in Kraków\""},
         "cost_per_person": {"type": "number", "minimum": 0},
         "after_states": {"type": "array", "items": {"type": "string", "enum": list(AFTER_STATES)},
                          "description": "How it leaves people feeling"},
         "outdoor": {"type": "boolean", "description": "Leave out if it's a mix"},
         "note": {"type": "string", "description": "One short line on why it fits"}},
         "required": ["name", "cost_per_person", "after_states"]}},
    {"name": "get_suggestions", "description": "The ranked search results with reasons, the budget status, the feed with votes, the agreed plan in order, and what's blocking the next step.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "suggest_idea", "description": "Put an idea from the search results (exact name) on the feed for the group to tick or cross.",
     "input_schema": {"type": "object", "properties": {
         "idea": {"type": "string"}, "person": {**PERSON, "description": "The sender suggesting it; leave out to suggest it yourself"}},
         "required": ["idea"]}},
    {"name": "vote_on_suggestion", "description": "Record a sender's tick (agrees) or cross on an idea that's on the feed (exact name).",
     "input_schema": {"type": "object", "properties": {"person": PERSON, "idea": {"type": "string"}, "agrees": {"type": "boolean"}},
                      "required": ["person", "idea", "agrees"]}},
    ATTACH_VENUES_TOOL,
    WEB_SEARCH_TOOL,
]


class GroupBot:
    """Runs Claude's tool calls against a room's plan, under the room's lock."""

    def __init__(self, room, senders):
        self.room = room
        self.senders = senders            # display name -> uid, for this batch only

    def run_tool(self, name, args):
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ToolError(f"Unknown tool {name}.")
        with self.room.lock:
            try:
                return handler(**args)
            except PlanError as err:
                raise ToolError(str(err)) from err

    def _sender(self, person):
        uid = self.senders.get(str(person or "").strip())
        if uid is None:
            if len(self.senders) == 1 and not person:
                return next(iter(self.senders.values()))
            names = ", ".join(self.senders) or "nobody"
            raise ToolError(f"You can only act for people who wrote in this batch: {names}.")
        return uid

    @property
    def plan(self):
        return self.room.plan

    def tool_set_plan(self, **fields):
        self.plan.set_details(fields)  # the bot's change needs everyone to agree it
        return json.dumps({"saved": self.plan.details, "season": self.plan.context()["season"]})

    def tool_set_preferences(self, person, after_state, place, budget_max=None):
        uid = self._sender(person)
        self.plan.set_preferences(uid, None, place, budget_max, after_state)
        return json.dumps({"saved": True, "groupBudget": self.plan.budget()["limit"]})

    def tool_agree_plan(self, person, agrees=True):
        self.plan.vote_plan(self._sender(person), agrees)
        return "Agreed to the plan details." if agrees else "Noted that they don't agree the plan details yet."

    def tool_agree_budget(self, person):
        uid = self._sender(person)
        self.plan.agree_budget(uid)
        return f"Agreed to {money(self.plan.currency, self.plan.budget()['limit'])} per person."

    def tool_add_friend(self, name, after_state, place, budget_max=None):
        self.plan.add_guest(name, None, place, budget_max, after_state)
        return f"Saved {name}."

    def tool_add_idea(self, name, cost_per_person, after_states, outdoor=None, note=""):
        idea = self.plan.add_idea(name, cost_per_person, None, outdoor, note, by_bot=True, after=after_states)
        limit = self.plan.budget()["limit"]
        if limit is not None and idea["cost_pp"] > limit:
            return f"Added, but it's over the {money(self.plan.currency, limit)} budget so it stays hidden."
        return "Added."

    def tool_get_suggestions(self):
        return json.dumps(self.plan.brief())

    def tool_suggest_idea(self, idea, person=None):
        if person:
            self.plan.suggest_to_feed(self._sender(person), idea)
        else:
            self.plan.suggest_to_feed(None, idea, by_bot=True)
        return "It's on the feed for everyone to tick or cross."

    def tool_vote_on_suggestion(self, person, idea, agrees):
        uid = self._sender(person)
        item = next((i for i in self.plan.feed if i["name"] == idea), None)
        if item is None:
            raise ToolError(f"{idea!r} isn't on the feed. Use suggest_idea first.")
        self.plan.feed_vote(uid, item["id"], bool(agrees))
        state = self.plan.feed_state(item)
        return json.dumps({"agree": len(state["yes"]), "against": len(state["no"]), "majorityNeeded": self.plan.majority(),
                           "inThePlan": item["approved_seq"] is not None})

    def tool_attach_venues(self, idea, venues):
        self.plan.attach_venues(idea, venues)
        return f"Attached {len(self.plan.venues[idea])} venue(s) to {idea}."

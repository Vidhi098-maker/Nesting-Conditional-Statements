"""The planner bot for the group web app: Claude's tools over a GroupPlan.

Claude runs the conversation; group.py makes the decisions and enforces the
rules. Tools that act for a person (their preferences, agreeing the budget,
liking an idea) only work for people who wrote in the batch of messages the
bot is answering, so the bot can never agree a budget for someone who didn't
say so.
"""

import json

from .chatbot import ATTACH_VENUES_TOOL, WEB_SEARCH_TOOL, ToolError
from .group import CURRENCIES, FEATURES, KINDS, PLACE_PREFS, SETTINGS, VIBES, HEMISPHERES, PlanError, money

GROUP_PROMPT = """You are the planner bot in a group chat of friends planning something together: \
a birthday, a dinner, a day or night out, a trip or holiday, or anything else. Be warm and brief \
(2-6 short sentences, or a short list). Reply in plain text without Markdown headings or bold.

Each user message starts with the sender's name in square brackets, e.g. "[Ana]: I'd love \
something outdoors". Several people may write at once; address people by name.

The goal is a plan everyone is happy with. The app enforces this: everyone gives the most they \
can spend per person, the group budget is the lowest of those, and every person must agree it \
themselves before voting opens. Voting is by happiness ratings; anything someone rates "No way" \
is out. Finally everyone taps "I'm in".

Tools:
- set_plan: what's being planned (kind, name, place, area type, nearby beach/mountains/lake, \
month, climate, currency). Infer what you can (e.g. "Headingley, Leeds" = suburb, north, £). For \
trips, place is where people travel from.
- set_preferences: a sender's vibe, indoor/outdoor preference and the most they can spend per \
person (a number in the plan's currency). Only for people who wrote in this batch, and only \
what they said themselves.
- agree_budget: ONLY when that sender clearly says they accept the current group budget. Never \
agree for anyone else.
- add_friend: preferences for someone mentioned who isn't using the app.
- add_idea: a concrete option with a realistic estimated cost per person. Use it for trips, \
holidays and anything the built-in list doesn't cover. For trips include travel, stay, food and \
activities, name a real destination and length (e.g. "4 days in Kraków"), and give a one-line \
note. Keep options inside the group budget when there is one. Add 3-5 varied options at a time.
- record_feedback: a sender likes or rules out a listed idea.
- get_suggestions: the ranked ideas plus budget status and what's blocking the vote. Call it \
after changes, before talking about ideas.
- start_vote: when the group says they're ready and the budget is agreed by everyone.
- web_search: check real current prices (flights, stays) before adding trip ideas, and find \
real places for the top ideas.
- attach_venues: pin real places from your search results to an idea on the list. Only places \
that appeared in your search results, with their URL. Never make up a venue.

Costs you add are estimates; say so. You can't book anything."""

PERSON = {"type": "string", "description": "Name of the person, exactly as written in their [Name] tag"}

TOOLS = [
    {"name": "set_plan", "description": "Save or change what's being planned. Send only fields you know; others are kept.",
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string", "enum": list(KINDS)}, "title": {"type": "string"},
         "place": {"type": "string", "description": "Town or city; for trips, where people travel from"},
         "setting": {"type": "string", "enum": list(SETTINGS)},
         "features": {"type": "array", "items": {"type": "string", "enum": list(FEATURES)}},
         "hemisphere": {"type": "string", "enum": list(HEMISPHERES)},
         "month": {"type": "integer", "minimum": 1, "maximum": 12},
         "currency": {"type": "string", "enum": list(CURRENCIES)}}}},
    {"name": "set_preferences", "description": "Save one sender's preferences. budget_max is the most they can spend per person.",
     "input_schema": {"type": "object", "properties": {
         "person": PERSON, "vibe": {"type": "string", "enum": list(VIBES)},
         "place": {"type": "string", "enum": list(PLACE_PREFS)}, "budget_max": {"type": "number", "minimum": 1}},
         "required": ["person", "vibe", "place"]}},
    {"name": "agree_budget", "description": "Record that a sender accepts the current group budget. Only when they said so themselves.",
     "input_schema": {"type": "object", "properties": {"person": PERSON}, "required": ["person"]}},
    {"name": "add_friend", "description": "Save preferences for a friend who was mentioned but isn't using the app.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "vibe": {"type": "string", "enum": list(VIBES)},
         "place": {"type": "string", "enum": list(PLACE_PREFS)}, "budget_max": {"type": "number", "minimum": 1}},
         "required": ["name", "vibe", "place"]}},
    {"name": "add_idea", "description": "Add one concrete option with a realistic estimated cost per person in the plan's currency.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Short and concrete, e.g. \"4 days in Kraków\""},
         "cost_per_person": {"type": "number", "minimum": 0},
         "vibes": {"type": "array", "items": {"type": "string", "enum": list(VIBES)}},
         "outdoor": {"type": "boolean", "description": "Leave out if it's a mix"},
         "note": {"type": "string", "description": "One short line on why it fits"}},
         "required": ["name", "cost_per_person", "vibes"]}},
    {"name": "record_feedback", "description": "Record that a sender likes or rules out one of the listed ideas (exact name).",
     "input_schema": {"type": "object", "properties": {"person": PERSON, "idea": {"type": "string"}, "liked": {"type": "boolean"}},
                      "required": ["person", "idea", "liked"]}},
    {"name": "get_suggestions", "description": "The ranked ideas with cost per person, the budget status, and what's blocking the vote.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "start_vote", "description": "Freeze the top ideas and open voting. Only works once everyone has agreed the budget.",
     "input_schema": {"type": "object", "properties": {"person": PERSON}, "required": ["person"]}},
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
        self.plan.set_details(fields)
        return json.dumps({"saved": self.plan.details, "season": self.plan.context()["season"]})

    def tool_set_preferences(self, person, vibe, place, budget_max=None):
        uid = self._sender(person)
        self.plan.set_preferences(uid, vibe, place, budget_max)
        return json.dumps({"saved": True, "groupBudget": self.plan.budget()["limit"]})

    def tool_agree_budget(self, person):
        uid = self._sender(person)
        self.plan.agree_budget(uid)
        return f"Agreed to {money(self.plan.currency, self.plan.budget()['limit'])} per person."

    def tool_add_friend(self, name, vibe, place, budget_max=None):
        self.plan.add_guest(name, vibe, place, budget_max)
        return f"Saved {name}."

    def tool_add_idea(self, name, cost_per_person, vibes, outdoor=None, note=""):
        idea = self.plan.add_idea(name, cost_per_person, vibes, outdoor, note, by_bot=True)
        limit = self.plan.budget()["limit"]
        if limit is not None and idea["cost_pp"] > limit:
            return f"Added, but it's over the {money(self.plan.currency, limit)} budget so it stays hidden."
        return "Added."

    def tool_record_feedback(self, person, idea, liked):
        self.plan.set_feedback(self._sender(person), idea, 1 if liked else -1)
        return "Noted."

    def tool_get_suggestions(self):
        return json.dumps(self.plan.brief())

    def tool_start_vote(self, person):
        self.plan.start_vote(self._sender(person))
        return "Voting is open in the app."

    def tool_attach_venues(self, idea, venues):
        self.plan.attach_venues(idea, venues)
        return f"Attached {len(self.plan.venues[idea])} venue(s) to {idea}."

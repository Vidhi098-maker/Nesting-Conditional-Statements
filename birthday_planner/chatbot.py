"""AI chatbot front end for the planner.

Claude handles the conversation; the algorithm in planner.py makes the
decisions. Claude can only change the plan through these tools:

  update_location  - where the birthday person lives and when the birthday is
  add_friend       - one friend's vibe / indoor-outdoor / budget answers
  record_feedback  - a thumbs up or down on a suggested idea
  get_suggestions  - run the scoring algorithm and get the ranked top 10
  start_vote       - hand over to the group vote

The web version (web.py) also gives Claude web search plus:

  attach_venues    - pin real local places found by search to a ranked idea

so every recommendation it makes is one the algorithm actually ranked.
"""

import json

from .cli import ask_ballot
from .ideas import IDEAS
from .planner import (
    FEATURES, HEMISPHERES, PLACE_PREFS, SETTINGS, VIBES,
    get_season, suggest, summarize_group,
)
from .voting import tally, winners

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """You are a friendly birthday-planning assistant for a group of friends.

Your job is to learn about the birthday person and the group through conversation, \
then use the tools to get tailored ideas. A scoring algorithm decides which ideas fit \
best; your role is to gather good inputs and explain the results.

The app has already greeted the user with: "Hi! Whose birthday are we planning, and \
where do they live?"

How to work:
- Ask short, natural questions, a couple at a time. You need: the birthday person's \
name and favourite vibe, where they live (city/suburb/town/rural, nearby beach, \
mountains or lake, climate zone) and the birthday month, plus each friend's vibe, \
indoor/outdoor preference and budget (1 cheap, 2 moderate, 3 splurge).
- Translate what people say into the tool fields yourself (e.g. "we're in Lisbon, \
near the sea" is a city with a beach in the north climate zone). Ask only when you \
genuinely can't tell.
- Save details with update_location and add_friend as soon as you learn them.
- Once you have the location and at least one friend, call get_suggestions and present \
all 10 ideas as a numbered list in the order returned, each with a one-line reason \
drawn from the reasons the tool gives.
- Only recommend ideas returned by get_suggestions. Never invent ideas of your own.
- When someone likes or dislikes an idea, call record_feedback, then get_suggestions \
again and show the updated list.
- When the group is happy with the list, call start_vote."""

TOOLS = [
    {
        "name": "update_location",
        "description": "Save or update where the birthday person lives and when the birthday is. "
                       "Call it with every field you know so far; it replaces the previous location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "birthday_person": {"type": "string", "description": "Name of the birthday person."},
                "birthday_vibe": {"type": "string", "enum": list(VIBES),
                                  "description": "The birthday person's favourite vibe."},
                "place": {"type": "string", "description": "City or town name."},
                "setting": {"type": "string", "enum": list(SETTINGS)},
                "features": {"type": "array", "items": {"type": "string", "enum": list(FEATURES)},
                             "description": "Natural features nearby. Empty list if none."},
                "hemisphere": {"type": "string", "enum": list(HEMISPHERES),
                               "description": "Climate zone: north or south of the tropics, or tropical."},
                "month": {"type": "integer", "minimum": 1, "maximum": 12,
                          "description": "Birthday month, 1-12."},
            },
            "required": ["birthday_person", "place", "setting", "features", "hemisphere", "month"],
        },
    },
    {
        "name": "add_friend",
        "description": "Save one friend's preferences. Calling it again with the same name updates them.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "vibe": {"type": "string", "enum": list(VIBES)},
                "place": {"type": "string", "enum": list(PLACE_PREFS)},
                "budget": {"type": "integer", "minimum": 1, "maximum": 3,
                           "description": "1 = cheap, 2 = moderate, 3 = splurge."},
            },
            "required": ["name", "vibe", "place", "budget"],
        },
    },
    {
        "name": "record_feedback",
        "description": "Record that the group liked or disliked a suggested idea. Disliked ideas are "
                       "removed and similar ideas move down; liked ones pull similar ideas up.",
        "input_schema": {
            "type": "object",
            "properties": {
                "idea": {"type": "string", "description": "Exact idea name from get_suggestions."},
                "liked": {"type": "boolean"},
            },
            "required": ["idea", "liked"],
        },
    },
    {
        "name": "get_suggestions",
        "description": "Run the scoring algorithm and return the top 10 ideas, best first, "
                       "with scores and the reasons each one was picked.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "start_vote",
        "description": "End the chat and start the group vote on the current top 10.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

ATTACH_VENUES_TOOL = {
    "name": "attach_venues",
    "description": "Pin real places found with web_search to one of the current suggestions, "
                   "so the group can see where they could actually do it. Only use places that "
                   "appeared in your search results. Replaces any venues already attached to that idea.",
    "input_schema": {
        "type": "object",
        "properties": {
            "idea": {"type": "string", "description": "Exact idea name from get_suggestions."},
            "venues": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "url": {"type": "string", "description": "Source page from the search results."},
                        "note": {"type": "string", "description": "One short line: area, price or why it fits."},
                    },
                    "required": ["name", "url"],
                },
            },
        },
        "required": ["idea", "venues"],
    },
}

IDEA_NAMES = {idea["name"] for idea in IDEAS}


class ToolError(Exception):
    pass


class Planner:
    """The state the tools read and write."""

    def __init__(self):
        self.location = None
        self.birthday_person = None
        self.birthday_vibe = None
        self.friends = {}
        self.feedback = {}
        self.suggestions = []
        self.venues = {}
        self.vote_requested = False

    def run_tool(self, name, args):
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ToolError(f"Unknown tool {name}.")
        return handler(**args)

    def tool_update_location(self, birthday_person, place, setting, features, hemisphere,
                             month, birthday_vibe=None):
        if setting not in SETTINGS:
            raise ToolError(f"setting must be one of {SETTINGS}.")
        if hemisphere not in HEMISPHERES:
            raise ToolError(f"hemisphere must be one of {HEMISPHERES}.")
        if not 1 <= int(month) <= 12:
            raise ToolError("month must be 1-12.")
        if birthday_vibe is not None and birthday_vibe not in VIBES:
            raise ToolError(f"birthday_vibe must be one of {VIBES}.")
        self.birthday_person = birthday_person
        self.birthday_vibe = birthday_vibe
        season = get_season(int(month), hemisphere)
        self.location = {
            "place": place,
            "setting": setting,
            "features": [f for f in features if f in FEATURES],
            "season": season,
        }
        return f"Saved. The birthday falls in {season}."

    def tool_add_friend(self, name, vibe, place, budget):
        if vibe not in VIBES:
            raise ToolError(f"vibe must be one of {VIBES}.")
        if place not in PLACE_PREFS:
            raise ToolError(f"place must be one of {PLACE_PREFS}.")
        if not 1 <= int(budget) <= 3:
            raise ToolError("budget must be 1, 2 or 3.")
        self.friends[name] = {"name": name, "vibe": vibe, "place": place, "budget": int(budget)}
        return f"Saved {name}. {len(self.friends)} friend(s) so far."

    def tool_record_feedback(self, idea, liked):
        if idea not in IDEA_NAMES:
            raise ToolError(f"No idea called {idea!r}. Use an exact name from get_suggestions.")
        self.feedback[idea] = 1 if liked else -1
        return f"Noted that the group {'liked' if liked else 'disliked'} {idea}."

    def tool_get_suggestions(self):
        if self.location is None:
            raise ToolError("Call update_location first.")
        if not self.friends:
            raise ToolError("Add at least one friend with add_friend first.")
        group = summarize_group(list(self.friends.values()), self.birthday_vibe)
        self.suggestions = suggest(self.location, group, feedback=self.feedback)
        return json.dumps(self.suggestions)

    def tool_attach_venues(self, idea, venues):
        if idea not in {s["name"] for s in self.suggestions}:
            raise ToolError(f"{idea!r} isn't in the current suggestions.")
        cleaned = []
        for venue in venues[:3]:
            url = str(venue.get("url", ""))
            if not url.startswith(("https://", "http://")):
                raise ToolError("Each venue needs the http(s) URL of its source page.")
            cleaned.append({"name": str(venue["name"]), "url": url, "note": str(venue.get("note", ""))})
        self.venues[idea] = cleaned
        return f"Attached {len(cleaned)} venue(s) to {idea}."

    def tool_start_vote(self):
        if not self.suggestions:
            raise ToolError("Call get_suggestions first.")
        self.vote_requested = True
        return "The vote will start once you finish this message."


def run_turn(client, planner, messages, system=SYSTEM_PROMPT, tools=TOOLS, max_steps=15):
    """Send the conversation to Claude, run any tools it calls, and return its reply text.

    The caller appends the new user message first. If Claude declines, that
    whole turn is rolled back so the history stays valid for the next one.
    """
    turn_start = len(messages) - 1
    for _ in range(max_steps):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "refusal":
            del messages[turn_start:]
            return "Sorry, I can't help with that one. Let's get back to the birthday plans!"
        if response.stop_reason == "pause_turn":
            continue  # a long web search paused; re-sending resumes it
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            try:
                output = planner.run_tool(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
            except (ToolError, TypeError, ValueError) as err:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": f"Error: {err}", "is_error": True})
        messages.append({"role": "user", "content": results})
    return "Sorry, I got a bit lost there. Could you say that again?"


def run_vote(planner, ask=input):
    names = [s["name"] for s in planner.suggestions]
    print("\n--- Time to vote! (3 points for 1st, 2 for 2nd, 1 for 3rd) ---")
    for i, name in enumerate(names, 1):
        print(f"{i:>2}. {name}")
    ballots = [ask_ballot(friend, len(names), ask) for friend in planner.friends]
    results = tally(names, ballots)

    print("\n--- Results ---")
    for r in results:
        print(f"{r['points']:>3} pts  ({r['first_place']} first-place)  {r['name']}")
    if len(winners(results)) > 1:
        print("It's a tie! The algorithm's higher-ranked idea breaks it.")
    print(f"\nThe winner is: {results[0]['name']}!")
    return results


def main(client=None, ask=input):
    if client is None:
        import anthropic
        client = anthropic.Anthropic()

    planner = Planner()
    messages = []
    print("=== Birthday Planner Chat ===  (type 'quit' to exit)\n")
    print("Bot: Hi! Whose birthday are we planning, and where do they live?")

    while not planner.vote_requested:
        text = ask("\nYou: ").strip()
        if text.lower() in ("quit", "exit"):
            return None
        if not text:
            continue
        messages.append({"role": "user", "content": text})
        print(f"\nBot: {run_turn(client, planner, messages)}")

    return run_vote(planner, ask)


if __name__ == "__main__":
    main()

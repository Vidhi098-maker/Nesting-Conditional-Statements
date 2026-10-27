# Nesting Conditional Statements

Python practice scripts (`file1.py`–`file4.py`) plus a bigger project built with
the same ideas: the **Birthday Planner**.

## Birthday Planner

A command-line app that suggests the 10 best birthday ideas for someone based on
**where they live** and **what the group thinks**, then lets the group **vote**
on a winner.

```bash
python3 -m birthday_planner
```

No extra packages needed (Python 3.8+). The chatbot version needs the `anthropic` package.

### How it works

1. **Location.** Enter the town or city, the kind of area it is (city, suburb,
   town, rural), what's nearby (beach, mountains, lake), the climate zone and
   the birthday month. The app works out the season, flipping it for the
   southern hemisphere.
2. **Group survey.** Each friend picks an ideal vibe (chill, adventurous, fancy,
   party, creative), indoors or outdoors, and a max budget. The birthday
   person's favourite vibe counts double, and the group budget is the lowest
   anyone chose so everyone can afford the plan.
3. **10 suggestions.** About 40 ideas in `birthday_planner/ideas.py` are
   filtered and scored with nested conditionals:
   - ideas that can't happen there are removed (no beach day without a beach,
     no ski day in summer, no rooftop bar in a rural area);
   - the score goes up for matching vibes and indoor/outdoor preferences, using
     nearby nature, and good weather;
   - it goes down for going over budget, too many people, or outdoor plans in
     winter.
   Each suggestion lists why it was picked.
4. **Voting.** Each friend ranks their top 3 picks (3, 2 and 1 points). Ties are
   broken by first-place votes, then by the planner's own ranking.

### AI chatbot version

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...   # your Claude API key
python3 -m birthday_planner.chatbot
```

Instead of answering a fixed questionnaire, you chat with Claude ("it's Sam's
birthday in July, we're in Lisbon by the sea, Ana loves a party..."). The
**algorithm** does the tailoring, not the language model:

1. **Structured extraction.** Claude turns what you say into a profile through
   tools (`update_location`, `add_friend`). The app checks every value, and
   Claude asks again if a value doesn't fit.
2. **Constraint filtering and scoring.** `get_suggestions` runs the same scoring
   as the command-line version. Claude can only present ideas the algorithm
   returned, never make up its own.
3. **Learning from feedback.** "We don't like karaoke" or "the climbing
   sounds fun" becomes `record_feedback`. A disliked idea is removed. Every
   vibe and indoor/outdoor trait of a liked or disliked idea gains or loses
   weight, so similar ideas move up or down too (a simple content-based
   recommender).
4. **Diversity re-ranking.** The top 10 are picked one at a time, with a
   penalty for being too similar to ideas already picked. This keeps the list
   from being ten versions of the same night out.
5. **Group vote.** When the group is happy, Claude calls `start_vote` and
   everyone ranks their top 3 as before.

### Group web app (chat from your phones)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python3 -m birthday_planner.web        # prints a link friends on your Wi-Fi can open
```

- One person taps **Start a new plan** and shares the invite link or the 5-letter room code.
- Everyone chats with the planner bot in one group chat. The bot sees who said
  what, so it can save each friend's preferences.
- The **Ideas** panel shows the algorithm's live top 10. Claude uses web search
  to find **real local venues** for the top ideas and pins them with links.
  It's only allowed to pin places that appeared in its search results.
- When the group is ready, the bot opens voting. Everyone taps their top 3 on
  their own phone, and results show when all have voted (or someone closes
  the vote early).

Rooms are kept in memory, so they disappear when the server restarts. Anyone
with the link can chat, and every message uses your Claude API credits, so only
share it with your group. To let friends join from outside your Wi-Fi, run
the server somewhere public or use a tunnelling tool.

### Tests

```bash
python3 -m unittest
```

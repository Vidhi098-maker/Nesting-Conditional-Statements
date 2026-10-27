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

No extra packages needed (Python 3.8+).

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

### Tests

```bash
python3 -m unittest
```

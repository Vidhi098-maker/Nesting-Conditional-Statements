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

### Group web app (plan anything, from your phones)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
python3 -m birthday_planner.web        # prints a link friends on your Wi-Fi can open
```

The same group planner as the hosted page, run on your own server:

- **Any kind of plan:** a birthday, a dinner, a day or night out, a trip, or
  anything else. Local plans use the built-in idea catalog; for trips and
  anything else the planner bot adds concrete options with estimated costs
  (it can web-search current prices and pin real venues), and anyone can add
  their own.
- **A budget everyone agrees:** each person gives the most they can spend per
  person; the group budget is the lowest. Everyone agrees that exact amount
  before voting opens, and has to agree again if it changes.
- **Voting built for agreement:** everyone rates the finalists from "No way"
  to "Love it". Any "No way" rules an option out, and the winner is the one
  the least-happy person likes most.
- **Sign-off:** it's decided once everyone taps "I'm in". The host (whoever
  started the plan) can mark someone who's gone quiet as not taking part.

The rules live in `birthday_planner/group.py` and are enforced by the server,
so nobody can open the vote early, agree a budget for someone else or rate
twice. The bot (`group_bot.py`) can only act for people who wrote in the
messages it's answering. Other people never see your Google or Apple account
ID, only a random per-room ID.

#### Sign in with Google and Apple

With credentials set, people sign in with their Google or Apple account
before starting or joining a plan. Their name comes from that account, and
chat messages, votes and membership are tied to it (a typed name can't
pretend to be someone else). Without credentials the app falls back to a
"type your name" mode for local testing.

Copy `.env.example` to `.env`, fill it in, then run:

```bash
set -a; . ./.env; set +a
python3 -m birthday_planner.web
```

**Google** (free):
1. In the Google Cloud console, create a project and set up the OAuth consent
   screen (app name, support email, audience "External"). While it's in
   testing, add your friends as test users, or publish the app.
2. Create an OAuth client of type **Web application** with:
   - Authorized JavaScript origin: `https://YOUR-DOMAIN`
   - Authorized redirect URI: `https://YOUR-DOMAIN/auth/callback/google`
   (for local testing also add `http://localhost:8000/auth/callback/google`)
3. Put the client ID and secret in `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.

**Apple** (needs the paid Apple Developer Program):
1. Under Certificates, Identifiers & Profiles, create an **App ID** with
   "Sign in with Apple" turned on.
2. Create a **Services ID** (this is `APPLE_CLIENT_ID`), turn on Sign in with
   Apple and configure it with that App ID, your domain (`YOUR-DOMAIN`, no
   `https://`) and the return URL `https://YOUR-DOMAIN/auth/callback/apple`.
3. Create a **Key** with Sign in with Apple, download the `.p8` file (you
   can only download it once) and note its Key ID and your Team ID.

Apple only allows real https domains, not localhost or IP addresses, so the
app must be reachable at a public https address: host it on a server behind
HTTPS, or use a tunnel with a fixed hostname for testing.

How it's secured: the standard OpenID Connect code flow with a one-time
`state` tied to the browser, a `nonce`, and PKCE for Google; ID tokens are
verified against Google's and Apple's published signing keys, issuer,
audience and expiry. Sessions are random IDs in `HttpOnly`, `Secure`,
`SameSite=Lax` cookies, and cross-site posts to the API are refused. See
`birthday_planner/auth.py`.

Rooms are kept in memory, so they disappear when the server restarts. Anyone
with the link can chat, and every message uses your Claude API credits, so only
share it with your group. To let friends join from outside your Wi-Fi, run
the server somewhere public or use a tunnelling tool.

### Hosted group planner (no server needed)

`hosted/group-planner.html` is published on claude.ai and goes beyond birthdays:
a birthday, a dinner, a day or night out, a trip, or anything else.

- **Ideas:** local plans use the built-in idea catalog (ranked by the same
  algorithm as `planner.py`). For trips and anything else, the planner bot adds
  concrete options with estimated costs per person, and anyone can add their own.
- **Budget everyone agrees:** each person gives the most they can spend per
  person. The group budget is the lowest of those, so nobody is priced out, and
  ideas above it are hidden. Every person has to agree it before voting opens.
- **Voting built for agreement:** everyone rates each finalist from "No way" to
  "Love it". Any "No way" rules an option out, and the winner is the option the
  least-happy person likes most (ties go to the higher total).
- **Sign-off:** it's only decided once everyone taps "I'm in".

### Tests

```bash
python3 -m unittest
```

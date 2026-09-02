# Star Conquest leaderboard

A small public board for challenge links. Win a game, press **Challenge a friend**,
paste the link here with a name, and the score joins the ranking for that map.

Plain HTML/CSS/ES modules with no build step, talking straight to Supabase's REST
API. It is a separate Netlify site from the game itself; nothing in the game or
its `web/` build depends on it.

## Pages

| | |
|---|---|
| [`index.html`](index.html) | every map with a posted score, newest first |
| [`game.html?key=…`](game.html) | one map's high-score table |
| [`user.html?u=…`](user.html) | one player's card — see below |
| [`submit.html`](submit.html) | paste a challenge link to post a score |

A player card takes **repeated `u` params**, not one comma-joined list, because a
name is free text and may contain a comma: `user.html?u=Ann&u=Bo` puts both on the
page and turns it into a comparison. Names fold to `users.name_key` (lower, trimmed)
the way the database does, so the case in the URL never matters, and four is the cap
— past that a comparison stops being one.

Comparing shows each player's **best** result per map with the maps they have both
played first, a green bar on whoever is ahead *within the comparison*, and the
placing against the whole board in the rank column — two different questions, which
is why a map both were beaten on still shows a leader. Ranks are computed here from
every score on those maps (`js/standings.mjs`), matching the game page's
`competitionRanks`: a dead heat shares a place and the next distinct score skips one.

Every name on a map's board links to that player's card. The browser also remembers
the name you last posted under ([`js/me.mjs`](js/me.mjs)), which is what puts **My
scores** in the marquee and an *Add my scores* button on someone else's card — the
one-click way to line yourself up against a rival. All of it is optional: with no
remembered name, nothing appears and every page works the same.

## How a link becomes a score

The game encodes a whole `Settings` object — map setup plus the result — as
`JSON → zlib → base64url` in the link's `#` fragment
(`starconquest/settings.py`, `Settings.to_token`). [`js/token-decode.mjs`](js/token-decode.mjs)
is the reading half in JavaScript.

Scores group by `Challenge.key`, the game's own checksum of the setup, which rides
along inside the token. So "the same map" means exactly what the game means by it,
and nothing has to be re-hashed here.

That checksum covers the whole setup, so adding a field to `Settings` changes it
for every map: links shared either side of such a change describe one map under
two keys. `KEY_ALIASES` in [`js/token-decode.mjs`](js/token-decode.mjs) folds an
incoming legacy key onto the current one, and
[`fold-game-key.sql`](fold-game-key.sql) moves scores already stored under it.

## Setup

1. **Create a Supabase project** (free tier is fine). Note its Project URL and
   `anon` public key from *Project Settings → API keys*.
2. **Run [`schema.sql`](schema.sql)** in the project's SQL editor. It creates
   `users`, `games`, `scores`, the `game_summary` view, and the row-level security
   policies that make everything append-only. The whole file is idempotent — paste
   it again after any change to it, and an existing board picks the change up
   without touching a row. [`fold-game-key.sql`](fold-game-key.sql) is the other
   script here, run only when two keys need merging (see above).
3. **Fill in [`js/config.mjs`](js/config.mjs)** with that URL and anon key. The anon
   key belongs in git — it is designed to be public, and RLS is the real boundary.
   The `service_role` key must never go in this repo. Optionally set `GAME_URL` to
   where the game is deployed, and each map page gains a "Play this map" link.
4. **Create a second Netlify site** from this repo with **Base directory** set to
   `leaderboard`. Netlify then reads `leaderboard/netlify.toml` and publishes these
   files as-is. The root `netlify.toml` and the game's own site are untouched.

## Local development

```sh
cd leaderboard && python3 -m http.server 8000   # then open localhost:8000
```

A real Supabase URL in `config.mjs` works from localhost with no CORS setup —
PostgREST accepts any origin for the anon key. Without one, every page says so
instead of failing obscurely.

## Tests

The decoder is pinned against real tokens from the Python encoder:

```sh
uv run python tools/dump_challenge_fixtures.py   # regenerate the fixtures
node --test leaderboard/tests/
```

Re-run the generator and commit `tests/fixtures/tokens.json` if the token format
in `settings.py` ever changes — that fixture file is what keeps the two encoders
from drifting apart.

`tests/standings.test.mjs` covers the player card's maths the same way — placings,
best-of-several attempts, who leads a comparison, and the card's own totals — which
is why that logic sits in a module with no DOM or fetch in it.

## Known limitations, accepted on purpose

- **Scores are unverifiable.** The token format is public and unsigned, so a
  hand-crafted impossible score would be accepted. RLS protects the database, not
  the plausibility of what is in a link. Catching that needs server-side
  re-simulation from the seed.
- **Names are not identities.** No auth, keyed by name, so two people typing the
  same name share a row — and so share a player card. Anyone can also post under
  your name, which is the same trade the board makes everywhere else.
- **Wins only.** The game only offers the challenge link when the human won and
  played at least one turn by hand, so nothing else can be posted.
- **Supabase pauses free projects after about a week idle**, which needs a manual
  unpause. A weekly scheduled request against the REST API would prevent it.

## Not built yet

Replaying each `models/` bot through the same seed to show how they would have
done in the player's seat. It needs the real Python engine, so it wants its own
small service — keyed by `game_key` + bot name and cached once computed, since the
result is deterministic.

# Star Conquest leaderboard

A small public board for challenge links. Win a game, press **Challenge a friend**,
paste the link here with a name, and the score joins the ranking for that map.

Plain HTML/CSS/ES modules with no build step, talking straight to Supabase's REST
API. It is a separate Netlify site from the game itself; nothing in the game or
its `web/` build depends on it.

## Pages

| | |
|---|---|
| [`index.html`](index.html) | every map with a posted score, newest first — toggle by game or by config, filterable by clicking a config badge or bot chip |
| [`game.html?key=…`](game.html) | one map's high-score table, sortable by turns or ships lost, plus how every bot did on it |
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

## Same setup, different seed

Every game with a `game_key` still gets its own row and its own board — a seed is
part of what `game_key` means. But a *config* (a map's settings with the seed set
aside) is worth naming: `public.sc_config_key` in [`schema.sql`](schema.sql) hashes
a game's stored `settings_json` with `seed` (and `autoplay`) removed, so every game
on the same tuned setup shares one badge on the main list regardless of which map
it rolled. That badge reads the setup's non-default knobs until someone gives it a
real name (`configLabel`, [`js/setup.mjs`](js/setup.mjs)) — free, since
`Settings.token_dict()` already prunes `settings_json` to the diff from defaults.

The main list's **By config** toggle switches from one row per map to one row per
config, rolled up from `public.config_summary` — how many maps and scores it has and
when it was last played, still newest-first. Clicking a config row (or a config
badge anywhere else) drills back into the ordinary per-game list filtered to that
one setup, which is where its "Name this setup" form and its own game board live.
There's no bot dropdown any more: a bot chip on a card *is* the filter, linking to
`index.html?bot=…` — clicking one narrows either list, by-game or by-config, to
setups/maps that included that opponent.

A config's page offers **Play a new seed**: the same setup handed back to the
game with its seed emptied, so the game rolls a fresh map from it
(`Settings.seed = None`) instead of replaying one of the maps already on the
board. [`js/token-encode.mjs`](js/token-encode.mjs) is the writing half of the
token format — the reverse of `token-decode.mjs`, dropping exactly the two keys
`sc_config_key` drops, so the link offers the setup the page grouped by and
nothing more. It needs `GAME_URL` set, like the map page's "Play this map"; that
button is the counterpart, pinning the seed and carrying the leader's score as
the target. A config has no single score to hand over — its games are different
maps of unequal difficulty — so this one carries the setup alone.

This key is computed here, on data the game already sends, rather than as a new
field on `Settings` — deliberately, so that adding it never moves `Challenge.key`
for a single existing map (see `docs/design-notes.md`, "Keys outlive the schema
that made them"). The trade is the opposite fragility: **naming a config freezes
it to `sc_config_key`'s current definition and to the game's current pruning
rules.** Change either — the function's body, or a `config.DEFAULT_*` balance
constant — and every config on the board rehashes, orphaning every name already
posted. There's no in-site remedy for that, only the SQL editor: re-derive the old
and new keys for the setups that matter and move the `configs` row across, the same
way `fold-game-key.sql` moves `scores` rows after a `Challenge.key` split.

Naming and tagging a config is **first name wins, permanently** — `configs` is
append-only like every other table here (no UPDATE policy), so a typo can only be
fixed from the SQL editor, the same trade `users.name` already makes. Since
`config_key` is derived rather than stored, there's also no link between it and
`games`: anyone can post a name for a config key nobody has played yet, or the
wrong hex string entirely. Harmless — `game_summary`'s join only ever surfaces a
name that actually matches a stored setup — but it's the same class of trust
already extended to every other free-text field on this board.

## How the bots did

Every map's page carries a second table: each `models/` bot replayed from the
player's seat on that exact map — same seed, same opponents in the same seats —
so a human score has something to be measured against. The verdict line above it
asks the only question worth asking of a high-score board, which is whether
anyone has outplayed the *best* machine answer to that map rather than where they
place among six of them.

None of it is computed here. A replay's result is a pure function of the stored
setup, the seed and the code, so it only ever needs computing once and there is
nothing to serve live: [`tools/bot_replay.py`](../tools/bot_replay.py) runs on a
schedule in GitHub Actions and caches its answers in `public.bot_scores`. See
[`.github/workflows/bot-replay.yml`](../.github/workflows/bot-replay.yml) for the
job and `docs/design-notes.md` ("Bot replays") for why it is a batch job rather
than the small service this file used to ask for.

`bot_scores` is the one table on the board the public cannot write: a read policy,
no insert policy, no insert grant, and the worker's `service_role` key bypassing
RLS as its only writer. Human scores are unforgeable only in the sense that
nobody bothers; these genuinely are.

A bot that never took the board still gets a row — `won` is the discriminator,
never `turns` — and is listed after every winner in plain name order, since
lasting 600 turns is not a better result than dying on turn 40. `js/standings.mjs`
(`botOrder`, `bestBot`, `humanVsBots`) holds that logic, and
`tests/standings.test.mjs` pins it.

## Setup

1. **Create a Supabase project** (free tier is fine). Note its Project URL and
   `anon` public key from *Project Settings → API keys*.
2. **Run [`schema.sql`](schema.sql)** in the project's SQL editor. It creates
   `users`, `games`, `scores`, `configs`, the `game_summary`/`config_summary` views, and
   the row-level security policies that make everything append-only. The whole file
   is idempotent — paste it again after any change to it, and an existing board
   picks the change up without touching a row. If a page 404s on a new table or
   view right after pasting, PostgREST's schema cache hasn't caught up yet; the
   file's own final statement (`notify pgrst, 'reload schema';`) normally makes
   that a non-issue. [`fold-game-key.sql`](fold-game-key.sql) is the other script
   here, run only when two keys need merging (see above).
3. **Fill in [`js/config.mjs`](js/config.mjs)** with that URL and anon key. The anon
   key belongs in git — it is designed to be public, and RLS is the real boundary.
   The `service_role` key must never go in this repo. Optionally set `GAME_URL` to
   where the game is deployed, and each map page gains a "Play this map" link
   (and each config page a "Play a new seed" one).
4. **Create a second Netlify site** from this repo with **Base directory** set to
   `leaderboard`. Netlify then reads `leaderboard/netlify.toml` and publishes these
   files as-is. The root `netlify.toml` and the game's own site are untouched.
5. **Optional — turn on the bot column.** Add two repository secrets under
   *Settings → Secrets and variables → Actions*: `SUPABASE_URL`, and
   `SUPABASE_SERVICE_KEY` set to the project's **service_role** key (*not* the
   anon key in `config.mjs` — that one is public on purpose, this one must never
   be). The hourly workflow skips itself cleanly while they are unset, so there
   is nothing to undo if you'd rather not. An hourly run also keeps a free
   Supabase project from idling into the pause noted under *Known limitations*.

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
node --test leaderboard/tests/*.test.mjs
```

(The glob is load-bearing on Node 22+: handed a bare directory, `--test` tries to
load it as a module and fails before running anything.)

Re-run the generator and commit `tests/fixtures/tokens.json` if the token format
in `settings.py` ever changes — that fixture file is what keeps the two encoders
from drifting apart.

`tests/standings.test.mjs` covers the player card's maths the same way — placings,
best-of-several attempts, who leads a comparison, and the card's own totals — which
is why that logic sits in a module with no DOM or fetch in it. It also covers the
map board's two rankings (`scoreComparator`, `displayOrder`); `tests/setup.test.mjs`
covers a config's derived label (`js/setup.mjs`). Neither `schema.sql` nor its
functions have a test harness — verify a change to `sc_config_key`/`sc_bots` by
pasting the file into a scratch Postgres or Supabase project and querying
`game_summary`/`config_summary` directly.

## Known limitations, accepted on purpose

- **Scores are unverifiable.** The token format is public and unsigned, so a
  hand-crafted impossible score would be accepted. RLS protects the database, not
  the plausibility of what is in a link. Catching that needs server-side
  re-simulation from the seed — which the bot-replay worker now does for the
  *machine* column, and could be extended to sanity-check a human's, since it
  already rebuilds the exact map from `settings_json` alone.
- **Names are not identities.** No auth, keyed by name, so two people typing the
  same name share a row — and so share a player card. Anyone can also post under
  your name, which is the same trade the board makes everywhere else.
- **Wins only.** The game only offers the challenge link when the human won and
  played at least one turn by hand, so nothing else can be posted.
- **A config's name is first-wins and permanent**, and a `config_key` can be
  squatted or posted for a setup nobody has played — see "Same setup, different
  seed" above.
- **Supabase pauses free projects after about a week idle**, which needs a manual
  unpause. A weekly scheduled request against the REST API would prevent it.

## Not built yet

Nothing outstanding. The bot column below was the last item here.

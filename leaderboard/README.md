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
two keys. Two things fold them back together, and a third repairs what is already
stored.

`findTwin` in [`js/submit.mjs`](js/submit.mjs) is the general one. Before a
submission opens a new map page, it looks for a row on the same
mode/players/nodes/seed whose stored `settings_json` *is* the setup being posted
(`setupIdentity`), and files the score there — newest matching row first, the
same direction [`fold-game-key.sql`](fold-game-key.sql) merges in, so the site
and the repair script never pull a map two ways. That works without anyone
noticing the split first, and keeps working across the next schema change,
because `Settings.token_dict` prunes every field still at its default — so a
field added since is simply absent from both rows.

It also catches two splits an alias structurally cannot: a digest that moved
without any field being added (`challenge_keys` changed how it treats a seat
beyond `players`, which no legacy drop can express, so those links carry a key
*no* build recomputes), and a player posting from a stale cached build, where
nobody changed anything at all. The gap it does not cover is a field joining
`AiParams`: seat dicts are pruned only whole, never field by field, so both
rows and both digests move at once — see the note by `_LEGACY_KEY_DROPS` in
`starconquest/settings.py`.

`KEY_ALIASES` in [`js/token-decode.mjs`](js/token-decode.mjs) is the narrower one:
it maps a specific superseded checksum onto the key that map is now filed under,
which is what an already-split board needs, since both keys exist there and the
stamped one still resolves. It is hand-kept, so its targets age — an entry written
before the next field joined `Settings` points at a digest nothing re-stamps.
`findTwin` is what makes that survivable.

[`fold-game-key.sql`](fold-game-key.sql) is the repair: it moves scores already
stored under a superseded key onto the current row — the bot column with them,
since `bot_scores` is keyed by `game_key` too and its foreign key would otherwise
refuse the merge outright. It carries a query for finding the maps that need it.
With `findTwin` in place a split can no longer *grow*, so this is tidying rather
than rescue, and the folded-away key still works as a link: `js/game.mjs` reads
an unknown key through `aliasFor` and forwards a bookmark to wherever its map
now lives.

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
for a single existing map (see `docs/system-design.md`, "Keys outlive the schema
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
job and `docs/system-design.md` ("Bot replays") for why it is a batch job rather
than the small service this file used to ask for.

The worker also lifts the bots' own per-decide wall-clock guards 100x. Those
exist so the single-threaded browser build never freezes mid-search; nothing waits
on a background job, and tripping one is the only thing that makes such a bot's
output depend on the machine it ran on — so a batch run that can never trip one is
*more* reproducible, which is exactly what a cached result needs. (Measured: a
runner only 2x slower would otherwise have cached a different answer for knower on
a 40-node map.)

`bot_scores` is the one table on the board the public cannot write: a read policy,
no insert policy, no insert grant, and the worker's secret key bypassing
RLS as its only writer. Human scores are unforgeable only in the sense that
nobody bothers; these genuinely are.

Each bot is replayed at its **best** profile, not its menu default: `REPLAY_AUX`
in [`tools/bot_replay.py`](../tools/bot_replay.py) currently runs `knower` at
search depth 12, the top of its own slider and a far stronger player than the
depth 1 an untuned seat gets. The setting in force is stored on the row and shown
beside the name ("knower · search depth 12"), so the board never quietly compares
two different versions of one bot — and changing it refills those rows on the next
ordinary run.

A bot that never took the board still gets a row — `won` is the discriminator,
never `turns` — and is listed after every winner in plain name order, since
lasting 600 turns is not a better result than dying on turn 40. `js/standings.mjs`
(`botOrder`, `bestBot`, `humanVsBots`) holds that logic, and
`tests/standings.test.mjs` pins it.

The main list carries the same verdict as a glance-able "Bot leads" badge on any
map's card, rather than making a visitor open the map to find out nobody has
beaten it yet. `game_summary`'s `bot_turns`/`bot_lost`/`bot_name` (schema.sql, a
lateral join on `bot_scores` mirroring the one already used for the human best
score) carry what the badge needs without a second per-game query;
`js/format.mjs`'s `botLeadBadge` decides whether to show it.

## Checked scores

A score in a link is a claim. The *replay* behind it is not: a match is fully
determined by its settings, its seed and, per turn, every seat's orders plus the
combat draws — feed those back through the engine and it either reproduces the
posted result or it does not. So the game uploads that replay when the player
posts a score, and the worker replays it.

The pieces, in the order a submission touches them:

1. `replay.GameLog` mints a `match_id` per match, and `main.challenge_settings`
   stamps it into the link as `Challenge.log`. That field rides on `Challenge`
   rather than `Settings` on purpose: `challenge_keys()` drops `challenge` before
   hashing, so unlike a new `Settings` field this moves no setup digest and
   invalidates no link anyone has already shared.
2. `starconquest/share.py` posts the log — deflated and base64url'd by
   `GameLog.encoded`, the same encoding the token itself uses — to
   `netlify/functions/log.mjs`, which stores it in `game_logs`.
3. `submit.mjs` writes the id onto the score row as `match_id`. The log and the
   score arrive by different routes and either can be first, which is why there
   is no foreign key between them.
4. `tools/verify_scores.py` (the same scheduled worker as the bot column) reads
   both, replays the log, and records one of four verdicts in `score_checks`:
   `verified`, `mismatch`, `unreadable`, or `missing` when no log was ever
   uploaded. `missing` is stored rather than left as an absence, so the board can
   tell "nobody has looked yet" from "we looked, and there is nothing to check".

**The verifier binds the replay to the setup** (`same_setup`), or an easy map's
log could be attached to a hard map's score and would verify perfectly.

What this does *not* do: prove a human played the game. A bot driving the seat
produces a log that verifies like any other — which is what `hand` discloses, and
the verifier recomputes it from the log's own per-turn autoplay flags rather than
trusting the number in the link.

### Shared replays, and where they are kept

Posting a score is one of two ways a replay reaches the board. The other is the
menu's **Share replays** checkbox, off until it is switched on: with it ticked, a
game also uploads as it goes — every 25 turns and again when it ends. That
cadence is the point of it. A match that is *abandoned* never reaches an end, and
abandoned and lost games are exactly what `scores` can never hold (the game only
offers the submit button on a win), while being the positions a bot is most worth
measuring against. A pure autoplay demo is skipped either way: a bot-versus-bot
game is reproducible from its seed, so storing one is bytes without information.

**`game_logs` is the one table the public can neither read nor write.** RLS is on
and it has no policies and no anon grants at all. Every other table here takes a
row from anyone, and for a hundred-byte score that is a fine trade; a replay is
5-14 KiB, so an open insert path is a storage bill rather than a nuisance. Writes
go through this site's own function, `netlify/functions/log.mjs`, which:

* answers only POST, and only for a body under the size cap;
* rate-limits the caller (in memory, per instance — enough for a runaway loop,
  not for an adversary, and the limits that actually hold are the size cap and
  the check constraint behind it; a durable limit would mean storing everyone's
  IP, which is a worse thing to own than the abuse it prevents);
* validates every field, `match_id` above all, since that is the column the
  verifier keys scores against;
* forwards the row under `SUPABASE_SERVICE_KEY`, which is the table's only
  writer. Set it in the Netlify site's environment alongside `SUPABASE_URL` — the
  same secret the Actions worker uses, and just as much not-in-git. With either
  unset the function answers 503 and stores nothing.

Reads belong to the worker alone, so **uploading a game does not publish it**,
and **nothing identifying is attached**: a row is a match id, a setup key and the
moves. Grouping one person's games would need a durable client id, which is a
tracking identifier by any other name.

Uploads are append-only like everything else here, so a game that checkpoints
repeatedly leaves several rows and the longest is the current one;
`tools/verify_scores.py --prune` clears the rest.

### Watching one back

A score whose replay is on the board gets a **Watch** link, and it goes back into
the *game* rather than to a player written here: the game already has the whole
reviewer — `reconstruct`, the fog replay and the scrubber — and its engine is
Python, so a JS viewer would be a second engine to keep in step with the first.
The link is `<GAME_URL>#log=<match id>`; the game fetches the replay from
`netlify/functions/replay.mjs` and opens history review on it.

**Posting a score is what publishes that replay.** `public_replays` (schema.sql)
is `game_logs` restricted to the matches a posted score points at, so a game that
merely uploaded itself because *Share replays* was on stays unreadable. That rule
lives in the view's `where` clause rather than in the function, which selects
from the view and has no condition of its own to drift. `submit.html` says so on
the form, since that is where the decision is actually made.

The link is only rendered for ids `public_replays` actually returns
(`watchableIds` in `js/game.mjs`), so it can never lead to a 404 — a score can
name a match whose upload never arrived. That query is asked by id rather than by
map, because a score's `game_key` and its log's are stamped by different code
paths and a folded key would make a `game_key` lookup quietly miss.

## Setup

1. **Create a Supabase project** (free tier is fine). Note its Project URL and
   `anon` public key from *Project Settings → API keys*.
2. **Run [`schema.sql`](schema.sql)** in the project's SQL editor. It creates
   `users`, `games`, `scores`, `configs`, `game_logs`, `score_checks`, the
   `game_summary`/`config_summary`/`public_replays` views, and the row-level security policies that
   make everything append-only. The whole file
   is idempotent — paste it again after any change to it, and an existing board
   picks the change up without touching a row. If a page 404s on a new table or
   view right after pasting, PostgREST's schema cache hasn't caught up yet; the
   file's own final statement (`notify pgrst, 'reload schema';`) normally makes
   that a non-issue. [`fold-game-key.sql`](fold-game-key.sql) is the other script
   here, run only when two keys need merging (see above).
3. **Fill in [`js/config.mjs`](js/config.mjs)** with that URL and the project's
   *publishable* key (`sb_publishable_…`; the older `anon` JWT is under Supabase's
   "Legacy API keys" tab and works too). That key belongs in git — it is designed
   to be public, and RLS is the real boundary. A **secret** key (`sb_secret_…`, or
   the legacy `service_role`) must never go in this repo. `GAME_URL_FALLBACK` is
   where the game is deployed; on a `.netlify.app` host it is usually not used at
   all — see "Finding each other" below.
4. **Create a second Netlify site** from this repo with **Base directory** set to
   `leaderboard`. Netlify then reads `leaderboard/netlify.toml` and publishes these
   files as-is. The root `netlify.toml` and the game's own site are untouched.
5. **Optional — turn on the bot column and score checking.** Add two repository secrets under
   *Settings → Secrets and variables → Actions*: `SUPABASE_URL`, and
   `SUPABASE_SECRET_KEY` set to the project's **secret** key `sb_secret_…` (*not*
   the publishable key in `config.mjs` — that one is public on purpose, this one
   must never be). Supabase moved the old `service_role` JWT to a "Legacy API
   keys" tab; it still works, under either that name or `SUPABASE_SERVICE_KEY`. The workflow skips itself cleanly while they are unset, so there is
   nothing to undo if you'd rather not. It runs every six hours — a cadence set
   by Actions minutes on a private repo rather than by how fresh the column needs
   to be, with *Run workflow* for when you want it sooner — and any run keeps a
   free Supabase project from idling into the pause noted under *Known
   limitations*.
6. **Optional — accept replay uploads.** Set the *same two* values as
   environment variables on this Netlify site (*Site configuration → Environment
   variables*): `SUPABASE_URL` and `SUPABASE_SECRET_KEY` (or `SUPABASE_SERVICE_KEY`). That is what
   both functions here read (`log.mjs` stores an upload, `replay.mjs` serves one
   back); with either unset they answer 503, and the game's uploads simply go
   nowhere and no replay is watchable — a working board without replays rather
   than a broken one. The game talks to `/api/log` and `/api/replay` on this site
   — see `paths.LEADERBOARD_LOG_URL`/`LEADERBOARD_REPLAY_URL` if it is deployed
   somewhere else.

## Finding each other

The game and the board are two Netlify sites whose names differ by exactly
`-leaderboard`, and Netlify names every other deploy `<context>--<site>.netlify.app`
— `deploy-preview-42--…` for a PR, `<branch>--…` for a branch deploy. Both sites
build from this one repository, so a PR produces the *same* context on each.

That makes the sibling derivable instead of configured. Each side edits the tag
into or out of its own hostname:

    deploy-preview-42--star-conquest.netlify.app
    deploy-preview-42--star-conquest-leaderboard.netlify.app

So a deploy preview of the game uploads to, and watches replays from, the deploy
preview of the board; a branch deploy pairs with its branch deploy; production
with production — with no URL edited by hand between them. `sibling_host` in
`starconquest/paths.py` is one direction (used by `webstore.leaderboard_origin`),
`siblingGame` in `js/config.mjs` the other, and `allowedOrigin` in
`netlify/functions/log.mjs` accepts the same shape so a preview is not refused by
CORS. Any host the rule cannot read — a custom domain, localhost, a desktop
build — falls back to the constant, which is the behaviour this always had.

**A preview shares production's database.** Netlify gives deploy previews the
site's environment variables, so a test upload from a preview lands in the real
`game_logs`. If that matters, set a different `SUPABASE_URL` for the *Deploy
previews* context (Netlify supports per-context values) and point it at a scratch
project.

## Local development

```sh
cd leaderboard && python3 -m http.server 8000   # then open localhost:8000
netlify dev                                     # ...or this, to run the function too
```

A real Supabase URL in `config.mjs` works from localhost with no CORS setup —
PostgREST accepts any origin for the publishable key. Without one, every page says so
instead of failing obscurely. A plain static server does not run
`netlify/functions/`, so uploads need `netlify dev` (with the two environment
variables set); `localhost:8000` is in the function's CORS allowlist for that.

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

`tests/functions.test.mjs` covers both Netlify functions: what `log.mjs`'s
`validate` accepts and how its `rateLimited` window behaves (which is why both
are exported rather than buried in the handler), and that `replay.mjs` reads
through the `public_replays` view, refuses a malformed id before it reaches a
query string, and answers "unpublished" and "missing" identically.

`tests/standings.test.mjs` covers the player card's maths the same way — placings,
best-of-several attempts, who leads a comparison, and the card's own totals — which
is why that logic sits in a module with no DOM or fetch in it. It also covers the
map board's two rankings (`scoreComparator`, `displayOrder`); `tests/setup.test.mjs`
covers a config's derived label (`js/setup.mjs`). Neither `schema.sql` nor its
functions have a test harness — verify a change to `sc_config_key`/`sc_bots` by
pasting the file into a scratch Postgres or Supabase project and querying
`game_summary`/`config_summary` directly.

## Known limitations, accepted on purpose

- **A score with no replay behind it is unverifiable.** The token format is
  public and unsigned, so a hand-crafted impossible score is accepted exactly as a
  real one is. What answers that is the replay the game now uploads when you post
  (see "Checked scores" above) — but only for scores that carry one. Anything
  posted by hand, or before that existed, checks as `missing`: unverified rather
  than suspect, and shown as such.
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

Nothing outstanding.

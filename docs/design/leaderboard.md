# Leaderboard design notes: bot column and checked scores

Why the board's two offline workers are shaped as they are: the bot column
(`tools/bot_replay.py`, the `bot_scores` table) and checked scores (`share.py`
uploads, `tools/verify_scores.py`, watching a replay). It also covers replay
versioning. `CLAUDE.md` states the rules under "Challenge links carry a score to
beat". The bot-side measurements behind the bot column (`REPLAY_AUX`,
`BUDGET_SCALE`, the seat-flag incident) are in [`bots.md`](bots.md),
"Replaying a bot for the leaderboard". The site's own setup is in
`leaderboard/README.md`. Related: [`core.md`](core.md) (challenge keys, the
replay log). Index: [`../README.md`](../README.md).

## Bot replays (`tools/bot_replay.py`)

The leaderboard's "how would each bot have done?" column. `leaderboard/README.md`
carried it under **Not built yet** for a long time with the note that it "needs
the real Python engine, so it wants its own small service". The service turned
out to be the wrong shape.

A replay's result is a pure function of three things — the stored `Settings`, the
seed, and the code — so it only ever has to be computed *once*. There is nothing
to serve live, and an always-on host would spend most of its life idle waiting to
recompute answers it already had. What the feature actually needs is a cache and
something to fill it, which is a scheduled job: `.github/workflows/bot-replay.yml`
writes into `public.bot_scores` on a schedule and leaves no service to keep up.
Netlify was never a candidate either way — its Functions run JavaScript and Go,
and there is no Python runtime to put the engine in.

The cadence answers to how fresh the column needs to be rather than to cost:
this repository is **public**, so Actions minutes are unmetered and the job runs
hourly, with `workflow_dispatch` for when it is wanted sooner. Most of those
runs find nothing to do and cost a few seconds of setup — the job installs no
dependencies, since the simulation core imports no pygame and nothing off PyPI.
Only a *private* repository makes the schedule a budget question: runs there
bill against the account's monthly allowance (2,000 minutes on the Free plan)
and GitHub rounds each job up to the whole minute, so hourly would spend 730+
minutes a month just asking whether there is work. Either way the cadence
comfortably covers the other thing the schedule buys: a free Supabase project
pauses after about a week idle, and any run touches the REST API.

The one capability given up is on-demand compute: a visitor cannot ask for a bot
that hasn't been run yet and watch it appear. That only starts to matter if the
column ever grows into something interactive — picking a bot and watching the
replay animate, or re-running it at a different `aux`. That would want a real
service; a cached table does not.

### Why it goes through `settings.build_state`

`tests/sim.play` takes mode/nodes/players and calls `mapgen.generate` directly.
That is the wrong door here: a posted setup carries tuned balance knobs, and
`build_state` is the only funnel that pushes them into `config` before generation
(`settings._apply_globals`). Replaying a 99-ships-per-homeworld map through
`generate` would quietly hand every bot the *default* map instead — the same
class of bug as `Settings.from_dict` returning a default `Settings()` when handed
something that isn't a dict, which is why `bot_replay._settings_for` checks the
shape rather than leaning on that function's usual tolerance. Tolerance is right
for a stranger's link and wrong for a worker that will post confident numbers
under a real map's key.

The map a bot inherits is identical to the human's, down to the star names
mapgen rolls last. The battles are not: once orders diverge so do the draws
taken from `state.rng`. That is the point — same board, its own war.

### What the replayed seat is tuned to

Slot 0 of `Settings.ai` belongs to the human, so whatever a menu left in it says
nothing about how a bot ought to play, and honouring it would let the same bot
score differently on two otherwise identical maps. The seat therefore gets a
default `AiParams` — with one exception.

That exception is `aux`, the one bot-defined knob. Every other field belongs to
the built-in heuristic's own tuning and says nothing about a drop-in's identity,
but `aux` is whatever that strategy decides it is, so "this bot at its best" is a
statement only the caller can make. `bot_replay.REPLAY_AUX` is where the board
makes it, and today it holds one entry: `knower` on Oracle: Search. Opponent
seats keep both the strategy and the params the setup gave them — those *are* the
map's difficulty, and changing them would answer a different question.

The `aux` in force is stored on the row, not implied by the code that happened to
be running. A reader comparing the board against a game they played from the menu
— where knower's seat defaults to depth 1 — is owed that, and `pending` reads it
back to notice when the policy has moved: an `aux` mismatch refills on an ordinary
run with no flag, because such a row answers a *different question* rather than
merely an older one. That is the distinction between it and `engine_rev`, where
`--stale` stays opt-in.

`bot_replay.BUDGET_SCALE` is the companion knob, lifting the bots' own per-decide
wall-clock guards 100x for a caller with nobody waiting on it. Both choices are
measured rather than assumed — see [`bots.md`](bots.md), "Replaying a
bot for the leaderboard", for the numbers and why a bigger budget is the *more*
reproducible option rather than a looser one.

### A loss is a result, not a score

`bot_scores.won` is the discriminator, never `turns`. A bot that never took the
board still reports how long it lasted and what it spent, which is worth showing,
but 600 turns of stalemate is not a better result than dying on turn 40 — and a
quick death is certainly not better than a slow victory. So `standings.botOrder`
puts every winner first, ranked by the game's own rule, and leaves the failures
after them in plain name order, which claims nothing. `bestBot` and `humanVsBots`
consider only winners for the same reason.

The verdict line measures a person against the *best* bot rather than counting
how many they beat. On a board of high scores the interesting question is whether
anyone outplayed the best machine answer to that map, not whether they placed
mid-table among six of them.

### A win stores its own replay

The Watch link beside a winning row used to hand the browser the same
ingredients the worker replayed (setup, seed, bot) and let it re-decide the whole
match live from turn one — which could disagree with the row it sat beside,
since a fresh re-decision depends on exactly which commit is deployed where.
Matching the two by hand (the harness treating the replayed seat exactly as the
token-driven link would) papered over that once at a real cost: it forced the
harness to measure a bot under an artificial handicap no other measurement here
grants it, since the roster's own ladder and swap tournaments let every seat see
every other clearly. See [`bots.md`](bots.md) ("Replaying a bot for the leaderboard"), "Storing the replay
removed the reason for the handicap, not just the mismatch", for the incident
and why storing the log let that handicap be lifted again rather than merely
tolerated.

`sim.play_settings` now takes a `log: replay.GameLog | None` parameter, filled
in turn by turn off the `TurnRecord` every `end_turn` call already returns —
exactly the shape `main.resolve_turn` builds one from in a live game.
`tools/bot_replay.py` builds one for every replay and keeps its encoded form
only on a win (`bot_scores.match_id`/`rules_version`/`log`; a loss stores
nothing, the rule a human's own posted score follows), and the Watch link
becomes `#log=<match_id>` — a human score's own mechanism, unchanged.
`replay.reconstruct` applies recorded orders and dice verbatim and asks no seat
to decide anything, so there is no second computation left that could disagree
with the first, whatever any seat was flagged during the run that produced the
log. `leaderboard/schema.sql`'s `public_watchable_replays` (a `union all` of
`public_replays` with a winning bot's own log, needing no further consent gate
since `bot_scores` is already fully public) is the one relation
`netlify/functions/replay.mjs` reads either kind through, keeping that
function's single, unconditional query. `standings.botWatchKind(row)` picks
between a current replay, an outdated one (stamped under rules this build has
moved past), the old reconstruct-it-live method as a fallback for a row with no
stored log yet, or nothing for a loss.

### `engine_rev` hashes the simulation, not the commit

Each row records a digest of what produced it, so `--stale` can find rows the
code has moved past. A git SHA would be the obvious choice and is the wrong one:
it moves on every commit, so a CSS change would invalidate the entire board. The
digest covers the outcome-determining core modules plus every `models/*.py`, and
nothing else. `starnames` is in that list despite being cosmetic — `_name_systems`
draws from `state.rng`, so changing the name list shifts every roll taken after
it (see **Star names** in [`core.md`](core.md)).

Nothing invalidates automatically. A changed bot leaves stale rows until someone
runs `--stale` on purpose, which is the same "a fix is a deliberate act" trade
`configs` already makes with its first-name-wins rule.

### The one table the public cannot write

`bot_scores` has a read policy and no insert policy, and no insert grant. The
worker's secret key bypasses RLS entirely, which makes it the only
writer. A human score carries no proof in itself — the token format is public and
unsigned, which is what **Checked scores** below answers — so it would be strange
to let the machine column be posted by hand too. It also means a rerun can *replace* a row, which is why this table is not
append-only the way the rest of the board is.

A per-decision wall-clock budget (`--bot-timeout`) is off by default, because a
blown budget forfeits that turn's orders and the result would then depend on how
fast the runner was that day. Where one is used, the count lands in
`bot_scores.bot_timeouts` and the page marks the row rather than presenting it as
reproducible alongside the others.

## Checked scores (`share.py`, `tools/verify_scores.py`)

The board's other pure-function-of-the-inputs job, and the answer to the oldest
entry under `leaderboard/README.md`'s **Known limitations**: a score in a
challenge link is a *claim*, since the token is public and unsigned and a
hand-crafted impossible result posts exactly like a real one.

A replay is not a claim. `replay.py` already records a match as its inputs —
settings, seed, and per turn every seat's orders plus the combat draws — and
`reconstruct` feeds them back through the engine without asking a single seat to
decide anything. So the evidence for a score already existed; it just never left
the player's machine. Uploading it is the whole feature.

**The id rides on `Challenge`, and that is what makes it free.** `challenge_keys()`
pops `challenge` before hashing (it is the score attached to a setup, not part of
the setup), so a field added there moves no digest and needs no
`_LEGACY_KEY_DROPS` entry — where the same field on `Settings` would have
invalidated every challenge link ever shared. `GameLog.match_id` is minted per
match from `settings.fresh_rng`, never `state.rng`: it must *not* be reproducible
from the seed, or every player of one shared map would mint the same id.

**Two things send, and both are consented to.** Pressing *Post to leaderboard*
uploads the match behind that score. Ticking *Share replays* on the menu
(`webstore.share_games`, off until switched on) also uploads a game as it goes —
every `share.CHECKPOINT_TURNS` turns and again when it ends. The cadence is the
whole point of the second one: a match that is *abandoned* never reaches an end,
and abandoned and lost games are exactly what a score can never carry and what a
bot is worth measuring against. Nothing else sends — `share_challenge` does not,
and neither does a pure autoplay demo (`hand_turns == 0`), which is reproducible
from its seed and so is bytes without information.

The preference is a local one (`paths.WEB_SHARE_GAMES_KEY`) rather than a
`Settings` field, for two independent reasons: it belongs to an installation and
not to a game setup, so it has no business in a save file or a shared link — and
a new `Settings` field would move `challenge_key()` for every map that has ever
existed.

`share.py` is a platform bridge in the `webstore`/`softkeyboard` style — guarded
everywhere, silent on failure, fire-and-forget on both sides (a `fetch` whose
promise is never read on the web, a daemon thread off it) so a POST can never
stall the frame. It keys a row by `GameLog.setup_key()`, which pins the seed
actually played: `main` resolves "roll a fresh seed" at game start and never
writes it back, so hashing the live `Settings` would file a random-seed game
under a key describing no particular map.

**`game_logs` is the one table the public can neither read nor write.** RLS is on
and it has no policies and no anon grants at all — every other table here takes a
row from anyone, and for a hundred-byte score that is a fine trade. A replay is
5-14 KiB, so an open insert path is a storage bill rather than a nuisance. Writes
go through the site's own function (`leaderboard/netlify/functions/log.mjs`),
which validates the row, caps its size and rate-limits the caller, and holds the
secret key that is the table's only writer. Reads are the worker's alone,
so uploading a game does not publish it.

The rate limit is honest about itself: Netlify functions run on ephemeral,
parallel instances, so an in-memory window stops a runaway loop and not an
adversary. What actually bounds the table is the size cap, the check constraint
behind it, and the fact that dropping the table is one statement in the SQL
editor. A durable limit would mean storing everyone's IP, which is a worse thing
to own than the abuse it prevents.

**Nothing identifying is attached.** A row is a match id, a setup key and the
moves. Grouping one person's games across sessions would need a durable client
id — a tracking identifier by any other name, and it buys nothing here.

Rows are append-only like the rest of the board, so a game that checkpoints
repeatedly lands several times and the *longest* row is the current one
(`verify_scores.best_logs`); `--prune` clears what it supersedes, using the one
delete path the public does not have.

**A rules change had never actually moved `RULES_VERSION` until the production/
combat reorder, so "the engine outran an old replay" had never been a real case
to handle — only a documented possibility.** The verifier already had the right
shape for it (`outdated` versus `mismatch`, decided *after* the replay so a
change that leaves most games alone does not flag them anyway), but nothing
outside `tools/verify_scores.py` ever asked the question: the game's own
`main.open_replay`/`resume_game` and `tools/position_suite.local_logs` would
reconstruct an outdated log through whichever engine happened to be running and
show the result with no caveat, and the board's Watch link had no way to know a
log's rules at all short of decoding its blob. `GameLog.is_current` is one
property shared by every one of those; the board's half of it
(`game_logs.rules_version`, exposed through `public_replays`) is deliberately
the same *claim, not evidence* shape as `finished`/`won`/`hand` — an index
letting `game.mjs` decide without paying for a blob it would otherwise have to
throw away unread.

The alternative — storing a full board snapshot per turn, so a replay survives
*any* future rules change rather than just being caught by one — was considered
and set aside for now, not ruled out. It reverses a deliberately argued design
choice (`replay.py`'s module doc: a match is never snapshotted, which is what
keeps a log 5-14 KiB and keeps a retuned bot from being able to move a stored
game), and backfilling every already-uploaded log would mean resurrecting the
exact engine each one was stamped under to re-simulate it once. The cheap fix
costs a column and a client-side comparison, and it is what `RULES_VERSION` was
already *for* — it just was not wired to anything but the verifier.

**The verifier binds the replay to the setup.** Without `same_setup`, an easy
map's log could be attached to a hard map's score and would replay perfectly.
Both setups are hashed in Python by the same code, so a key the *site* folded
(`KEY_ALIASES`) or computed itself for a hand-written link never has to be
reproduced in JS. `Settings.from_dict` is deliberately tolerant and answers a
non-dict with a default 18-node map, which here would verify a score against the
wrong map entirely — the same trap `bot_replay._settings_for` documents, and it
is checked for the same way.

The four verdicts land in `score_checks`, worker-written and publicly readable.
`missing` — no log was ever uploaded — is stored rather than inferred from
absence, because "nobody has looked yet" and "we looked, and there is nothing to
check" are different facts about a score, and it is the one verdict that is
retried without a flag: an upload can still arrive.

What none of this proves is that a *human* played the game. A bot driving the
seat produces a log that verifies like any other. That is what `hand` is for, and
the verifier recomputes it from the log's own per-turn autoplay flags rather than
trusting the number in the link.

Nor does it prove the game was played under the real rules of chance. The
verifier replays recorded orders and recorded dice. It does not check that the
dice came from the seed, or that the bots' orders are what those bots would
have played. A log with hand-picked dice or doctored bot moves verifies;
`tools/par_search.py`'s lucky lines are one example. Since `replay.reseed`
seeds each live turn from `(seed, turn)`, the dice half has become checkable
in part: a genuine turn's dice are a run of that turn's stream. The run's start
depends on how much the bots drew, though, which only re-running them would
show. The bot half stays open by design. What was measured, and what would
close the dice half, is in `docs/design/par.md`, "A gap this exposes in
`verify_scores`". A known limit, not yet addressed.

### Watching one back

A posted score names its replay, so the board can offer *Watch* — and the link
goes back into the **game**, not into a player written here. The reviewer already
exists: `reconstruct` rebuilds every turn, `build_history` folds the fog as it
stood, and the scrubber walks it. The engine is Python, so a JS viewer would be a
second engine to keep in step with the first, forever.

`#log=<match id>` is the launch URL (`--watch` off the web), and it is
distinguishable from a settings token by prefix alone: a token is base64url,
which cannot contain `=` except as the padding the encoder strips.
`main.open_replay` decodes the blob and goes through `resume_game`, so a watched
replay is the same object a resumed save is — which is what makes *rewinding out
of one* work for nothing: fork it at any turn and carry on playing from there. It
adopts the replay's own settings, so leaving review lands on that setup rather
than whatever the menu was showing.

`open_history` is the entry the H key and a watched replay share. They differ
only in how they came by the log, and must not differ in what review looks like —
with one exception, which is where the scrubber lands. Our own review opens on
the latest turn, because that is where the player is and looking back is a step
away from it. A watched replay opens at turn 0, the board as generated: the
question there is how the game was played, the answer runs forwards, and opening
on the final board would both give away the ending and make dragging the whole
way back the price of watching it.

**A watched result is not ours to post.** Reviewing somebody's win ends on the
same win overlay our own games end on, showing their turns and their ships lost —
so *Challenge a friend* and *Enter on leaderboard* would sit there offering to
send their score under our name, and a fraudulent entry would be a fetched link
and one press. `open_replay` marks the `Ui` (`Ui.watched`), `Ui.can_post` is the
one predicate render draws both buttons from and main fires both actions from,
and it also stops a watched game being filed as a personal best or checkpointed
back up under its original `match_id` — where, rows being keyed by that id and
the longest winning, our continuation would replace the very replay their score
is checked against.

It is a lock on the button, not a proof of authorship, and deliberately so:
rewinding a watched replay to a turn from its end and playing that turn out
builds a fresh `Ui` and a forked match that really is ours to post. Sealing that
would mean recording where a fork branched and carrying it through every later
rewind, to catch someone who has gone well out of their way — a bigger idea than
the problem.

**The download is polled, never awaited.** `share.fetch_log` starts it and hands
back a `Download` the frame loop asks once per frame; the browser build yields a
frame at a time, so blocking on a round trip would freeze the canvas before
anything had been drawn. Every failure is a *state* — pending, ok, error — rather
than an exception arriving on some arbitrary frame.

**What is watchable is decided in SQL.** `public_replays` is `game_logs` joined
to the scores that point at it, and it is the one view here deliberately *not*
`security_invoker`: the others exist so a tightened policy still binds their
callers, while this one exists to lend out a subset of a table nobody may read,
which only owner rights can do. Its `where` clause is the whole consent boundary,
so posting a score publishes that game and a checkpointed one stays private. The
submit page says so at the point the decision is made, which is the only place
saying it is worth anything.

### Versioning: bots are free to move, the engine is not

Keeping replays around raises an obvious worry — if the bots change, do the old
games still replay? — and the answer is that the question has the wrong subject.

**A stored log never consults a bot.** `end_turn(script=…)` applies the recorded
orders and deals the recorded dice; `decide` is not called. So retuning marshal,
rewriting knower or deleting a `models/` file outright cannot move a single
stored game. This is not an inference: `test_a_replay_does_not_consult_a_bot_even
_a_deleted_one` replaces every strategy with one that issues nonsense, removes one
from the registry, and asserts the reconstruction is unchanged. It is also exactly
why format version 2 exists — version 1 re-ran the AI, and a bot on a wall-clock
budget replayed into a different match.

So there is no case for a per-model "replay floor" gating which games are still
watchable, and adding one would cost the feature most of its value in exchange for
nothing. `bot_replay.replay_rev` is the same statement in code: `engine_rev` minus
`ai` and every `models/*.py`, so tuning a bot cannot mark a score verdict stale.

**The engine is the axis that does move a stored game**, and it is versioned by
hand. `engine.RULES_VERSION` is bumped in the same commit as a change to the phase
order, to how a fight resolves or how a map is drawn from a seed, and every log
carries the version it was played under. When a replay then fails to reproduce its
score, `verify_scores` reports `outdated` rather than `mismatch` — unverifiable,
not wrong. The check happens *after* the replay, so the many old games a bump does
not actually disturb keep verifying on their own merits; only the ones it broke
are set aside, and set aside rather than accused.

**The random pool is the other axis, and it gets the same verdict.** A seat left
to chance is dealt from `settings.RANDOM_POOL` and the log records what it was
dealt (`GameLog.strategies`; `docs/design/core.md`, "A seat left to the seed").
A score whose numbers reproduce but whose rival seats faced bots other than the
ones its setup deals today is `outdated`: played honestly, by a build from before
the pool was fixed or before an edit to it, against a lineup the map's other
scores did not face. It stays counted, without the verified tick. `outdated`
rather than `mismatch` because an edit to the pool would otherwise drop every
honest earlier score on a random map, and a tampered lineup gains nothing a
tampered score would not. `settings` is in `replay_rev`, so a pool edit makes
`--stale` re-decide. A log that records no lineup is judged on its numbers.

**Rewinding needs no versioning at all**, because it does not claim to reproduce
anything. A rewind replays the prefix exactly, then plays *on* from there — a
counterfactual by construction, which is why `fork` mints a new `match_id`. Both
rewinds re-stamp `rules_version`: the kept prefix has just been replayed under
today's rules to find that turn, so the log would be describing itself as older
than it is.

## The game and the board are one site

- **The game and the board are one site.** The root `netlify.toml` builds the
  game, and `tools/build_web.sh` stages the board's pages into `web/board/`
  from an explicit allow-list. The game itself is at `/game/`; the root is
  `tools/pwa/root.html`, a router that opens the board for a visitor and
  forwards a fragment (every challenge, replay and seat link the game ever
  shared is the root plus one) to `/game/`. The manifest, icons and service
  worker stay at the root, so old installs keep their scope and manifest `id`.
  The app is installable from either half: every board page links the
  manifest, and `js/nav.mjs` registers the worker. Its `start_url` is the
  router, which reopens whichever half the installed app was last on
  (`sc_app_last`, written only in standalone/fullscreen display, by `nav.mjs`
  and by the game page's `tools/pwa/inject.py` head), and the game if nothing
  is recorded. So an install from before this keeps opening the game until it
  visits the board. A fixed launch target either way would be wrong for
  someone: the board is a fine front page, but a player who installed to play
  wants the game. A per-device toggle would be one more control to find, for a
  choice the last-visited half already makes. The functions are bundled from
  `leaderboard/netlify/functions/` and answer at root `/api/`. On a
  `.netlify.app` page `webstore.leaderboard_origin` is the page's own origin,
  and `GAME_URL` in `leaderboard/js/config.mjs` mirrors it. So every deploy
  context, including a deploy preview, talks to itself with no URL edited by
  hand. Endpoints are still built at *call* time from `LEADERBOARD_*_PATH`,
  never stored as whole URLs. `paths.LEADERBOARD_ORIGIN` is the route for
  everything else (desktop, Android, localhost, a custom domain). Blanking it
  disables every leaderboard feature, which is what `render` tests, since
  resolving costs a DOM read it must not do once a frame. One origin means one
  localStorage: the lobby reads `sc_pbp_seats` and the posting name is
  `sc_pbp_name` (`test_leaderboard_sync` pins both keys). The site now holds
  `SUPABASE_SECRET_KEY` (unscoped on the free plan, so the build command
  unsets it first), and its sensitive-variable policy must stay on
  "Require approval" (the root `netlify.toml` header explains the fork-preview
  reasoning). The board's old host is a redirect shell (`legacy-board/`). It
  *proxies* `/api/`, because installed builds POST there and urllib won't
  follow a redirect on POST. `tools/pwa/sw.js` never touches `/api/` and
  fetches `/board/` network-first, both pinned by `tests/test_web_build.py`.

## Crowns, the weekly campaign and embargoes

- **Crowns reward stealing a record, not volume.** `crowns.html` ranks players
  by contested maps whose record they hold (`crown_holders`) and counts, per
  Monday-to-Monday UTC week, scores that strictly beat somebody else's record
  (`crown_steals`); a tie never steals, the earliest holder keeps it, as
  `game_summary` credits. Both read `counted_scores`, the one place the rule
  lives: every score but a `mismatch` replay counts, since the game uploads a
  log once with no retry and an offline or hand-pasted score could otherwise
  never count. Nothing is stored, so moderation recomputes them for free.
  `js/crowns.mjs` only orders rows; `tests/test_crowns_sql.py` runs the SQL
  against a real Postgres when `SC_TEST_PG` is set.
- **The weekly campaign stores its map and derives its state.**
  `tools/campaign.py` (the hourly worker; a no-op once the week's row exists)
  writes one `campaigns` row per Monday-to-Monday UTC week: field nodes laid
  out by `mapgen` with three times a game board's extra lanes
  (`FIELD_EXTRA_EDGE_FRACTION`, so few field nodes are cut points a single
  par-tight score can wall off), each an unplayed seed on an existing non-hand-drawn config
  of at most `config.STANDARD_MAX_NODES` systems (`FAMILY_MAX_NODES`: one
  120-system test game was enough to put big maps in a week, and a big map is
  a long sitting for one node) (sometimes its symmetric variant, plus one or two "?" nodes rolled with
  `settings.randomise_knobs`; a symmetric node whose config names no `layout`
  rolls one of `mapgen.SYMMETRIC_LAYOUTS`), and a ring of homes, one lane each off the edge
  nodes `mapgen.peripheral_starts` picks. A node's `settings` is stored in the
  pruned `token_dict` form `games.settings_json` holds, and `campaign_games`
  matches it to its game by seed plus jsonb *equality* — never
  `sc_config_key`, whose text digest tells Python's `0.0` from the `0` a
  browser posts, and so misses nearly every node. `campaign_scores` sits on
  that view, and so do the green campaign badge on a map's page and index row
  and the link back to `campaign.html?node=`. Who holds what is never stored: `js/campaign.mjs`'s `fold` replays the
  week's hand-played counted scores in posting order — a home goes to the
  first win from a player without one and can't be taken; a field node falls
  to a win posted while holding a neighbour (or within `GRACE_MS` of losing
  one, or the node itself), and a held one only to a strictly better score; see "The grace
  period" in this file. A bad week is remade by deleting its row
  (`tools/admin.py delete-campaign`, audited with the old graph) and letting
  the worker run again; scores already posted on its nodes stay on their maps
  but stop counting toward the week. The Advanced slider ranges live in `settings` (`ADV_*`) for
  this reason; `menu` aliases them.
- **A map can be registered with no score at all, and can carry a one-time
  reveal date over its board.** `js/submit.mjs`'s `ensureGame` accepts any
  Star Conquest link, not just a challenge one — `token-decode.mjs`'s
  `decodeToken` returns `challenge: null` for a plain settings-share link (or
  one carrying `Challenge`'s own `turns <= 0` sentinel) rather than rejecting
  it, and the site takes that as a setup to add rather than a score to post.
  That is what lets a setup be shared and played before anyone — its own
  author included — has a result on it to disclose. `games.embargo_until`
  rides along on that same insert, optionally, and only there: `games` is
  append-only like everything else on this board, so an embargo can only
  ever be decided the one moment a map's row does not yet exist, never
  retrofitted onto one already on the board.
  - **It gates the replay and the detail behind it, never the fact that a
    lead exists.** `public_replays` filters out a match whose map is still
    embargoed — the DB-level half, real for every caller, not just the
    site's own UI — and `game.mjs`'s `renderEmbargoed` is the other half: it
    never requests the per-score list at all while a map is embargoed, only
    `game_summary`'s own aggregate (`best_turns`/`best_user_name`/
    `best_holders`/`score_count`), so there is nothing for the page or its
    network tab to hand out beyond who is ahead and by how many turns. Lost,
    hand and submission time stay off the page entirely, along with every
    other score — a stricter cut would leave nothing to chase, which
    defeats a deadline built to be raced against. `home.mjs`'s card makes
    the same cut on the list. Scores and rankings themselves are otherwise
    unaffected by an embargo — they post and rank normally throughout,
    since there is no account system here to tell a submitter's own later
    read apart from anyone else's, so the only boundary that can be
    enforced for everyone alike, the setter included, is on *how* a score
    was made rather than on whether one exists.
  - **A campaign node is embargoed until its week ends, and that is derived,
    never stored.** `game_embargoes` (schema.sql) is the embargo in force:
    the later of `games.embargo_until` and the end of any live campaign week
    the map is a node of (matched through `campaign_games`). `public_replays`
    and `game_summary.embargo_until` both read it, so every page that honours
    an embargo honours this one with no JS of its own. It cannot be stamped on
    the row instead: a node's seed is fresh, so its `games` row is created
    mid-week by whoever posts first, and `games` is append-only after that.

## The grace period

One timer in `js/campaign.mjs`, derived like everything else in the
campaign: `fold` records when each player lost each node (`lostAt`), and
nothing about who holds what is stored. The principle: **a game started while
you had access to a node should count**, as long as you post it soon after
losing that access.

- **`GRACE_MS` (30 minutes).** A field win counts if the player held the
  node or a neighbour at any moment in the 30 minutes before posting it. It
  fixes the case that made the rule feel unfair: you start a node beside one
  you hold, somebody takes that neighbour mid-game, and your win counted for
  nothing. Thirty minutes is about one game. It only needs to cover the game
  in progress when the neighbour went, not a long campaign of play from
  memory.
- **The node itself counts too.** Holding the node is access just as holding
  a neighbour is. So a holder replaying their own node to raise the bar, who
  loses it mid-game and has no neighbour left, can still take it back inside
  the grace.
- **Only for a game started with access.** The grace runs from the loss, but
  a game *started* after the loss isn't the case it exists for, so it gets
  none. `fold` can't see when a game started, so the game says: at Start it
  stamps the answer `/api/campaign` gave (`campaign.stamp`,
  `Ui.campaign_start`), which rides on the score as `Challenge.campaign` and
  is stored in `scores.campaign_start`. `startedWithAccess` decides what a
  stamp means: `grace`, `late-start`, `not-adjacent` and `no-home` were no
  access; `adjacent` and `own` were access. Starting inside a grace is not
  access, or a restart five minutes after the loss would carry it.
- **A stamp is a claim, and safe as one.** The grace still runs from the loss,
  so a stamp can only narrow it: a forged one gets exactly the rule without
  stamps, never a later move. That is why it needs no signature, and why a
  blank or unknown stamp is taken on trust: every score from before the column,
  every desktop game (no lookup), a lookup that hadn't answered by Start, a
  resumed save, and a hand-written link. Trusting those also keeps past weeks
  folding the way they did.
- **Which game is asking.** `attemptStatus` takes the game's stamp
  (`start`), or `starting` for a game about to begin. Either one without access
  turns `grace` into `late-start`, not a move. The menu asks with
  `starting=1`; the game under way asks with its stamp; the page asks with
  neither, since it can't know about a game in progress, and its grace line
  says a game already started still counts and one started now won't.
- **Rewinds keep the stamp; a new start takes a new one.** A mid-game rewind
  is the same match (`apply_rewind` keeps `campaign_start`). A Retry, or
  playing on from a finished game's history, is a match begun now and is
  stamped from the current lookup. A match forked out of a watched replay gets
  a blank, since nothing looked its setup up while it was watched.

### Decided against: an "able to capture" flag on its own

Before the grace, the idea was to bake "could capture this node" into the link
when the game was started, and let the board honour it. It was dropped because
a link can be kept: start a game while next to a node, keep the link, and post
a win days after losing the neighbour. On its own a flag is a standing permit.
Combined with the grace (above) it can only narrow a clock that is already
running, which is what made it safe to add.
- **The edge minute counts on both sides.** `fold` accepts a win posted
  exactly `GRACE_MS` after the loss, and `attemptStatus` says yes at that
  minute too, so the page never says no to a win that would count.
- **`attemptStatus`** is the one place that says what a player may do on a
  node at `now` (`why`, `graceUntil`, `beat`). The page and the game both read
  it, so they can't disagree about a timer.

### Decided against: a cooldown between moves

Built and then removed, the same week. Each move started an hour's wait
before that player's next one, and a win posted during the wait was queued
and played when the hour was up. The aim was to slow a player sweeping the map
from Monday 00:00 UTC, and the queue meant no game was wasted.

It went because two timers together read as nonsense. A player inside their
grace with their cooldown still running was told "post within 12 minutes" and
"it plays in 40". That was consistent, since the grace was judged at posting
and the queue was the rule's doing, but nobody would read it that way. A
player who can't claim for 40 minutes should not look like they can take
the node at all. The remedies were a second rule (judge the grace when the
queued win plays) or more wording, and both made the campaign harder to
explain than the sweep made it unfair.

What paces a sweep now is only how long a game takes, roughly two nodes an
hour per player. If that ever turns out to be a real problem, look for a brake
that is *one* clock, not a second one beside the grace.

### In the game

The game and the board are one site, so the game can ask before it plays.

- **The game never holds the rules.** `netlify/functions/campaign.mjs` takes
  the setup's settings token and the player's name, finds this week's node by
  value (`setupIdentity`, as `campaign_games` matches by jsonb equality), and
  returns `attemptStatus` and the holder. A Python port of
  `fold` would be a second copy to drift, with nothing like
  `test_leaderboard_sync` able to pin a rule set. The function reads the public
  rows with the publishable key, so it holds no secret.
- **Times are the server's, counted on ours.** The answer carries the server's
  `now`, and `campaign.Status.left_ms` counts down from when it landed with
  `time.monotonic`. A device with its clock set wrong still shows the right
  minutes.
- **Web only, and never in the way.** The name is the board's own
  (`sc_pbp_name`, shared by origin), which a desktop build doesn't have. A
  lookup waits `SETTLE_S` after the last setup change, refreshes every
  `REFRESH_S` (so a neighbour lost mid-game starts its countdown), asks again
  at once when the game it asks for changes (`Watcher.pump`'s `start`), and a
  changed setup drops the old answer at once so Start never confirms from
  another map's answer. A Start pressed before the answer lands just starts,
  because a confirm is a courtesy, not a gate.
- **One modal, not two.** The confirm reuses the slow-setup modal
  (`menu._unless_slow`) with the node as its title, and puts the campaign's
  lines ahead of any bot warning. It asks only when a win wouldn't be a move
  (a node lost before Start now reads `late-start`), or would be one only
  inside the grace (`campaign.confirm`). An open node, your
  own node, or a player with no name yet starts straight away.
- **The top bar label is text `main` writes** (`main.campaign_tick` into
  `Ui.campaign_label`). `campaign` imports `pbp`, which imports `engine`, and
  `render` must never pull that in, even indirectly.
- **The board says it before the game does.** game.html on a live node shows
  the campaign page's own lines above "Play this map" (`attemptLines`, shared
  with campaign.html's node panel), so a player arriving from the board already
  knows whether a win would be a move. It is a note, not a confirm: a modal on
  the board would ask a second time for one game, since the game confirms on
  Start, and the link has no way to say the player already answered.
  `weekQueries` is the one copy of the two reads a week is folded from, for the
  two pages and the endpoint.

## Campaign fleets (proposed, not built)

A proposal for giving the campaign meta-map real-time lanes and fleets, written
up so it can be argued over and playtested before any of it is built. Nothing
here is in the code. The rules in force are in "Crowns, the weekly campaign and
embargoes" in this file.

Since this was written, "The grace period" in this file has dealt more
cheaply with the stolen neighbour. A cooldown for the Monday sweep was tried
and dropped (same section, "Decided against: a cooldown between moves"), so
fleets' remaining case is the sweep and the warning, and they still have to
justify a stored table and a per-person credential.

### The problem

`fold` (`js/campaign.mjs`) asks one question of a win on a field node: did this
player hold a neighbour *at the moment the score was posted*?

```js
if (![...links.get(nodeId)].some((other) => holds(key, other))) continue;
```

A score that fails is dropped with no trace. It is never banked against a later
neighbour, and `campaign.test.mjs` pins that ("a field node is taken only from
next to something you hold"). Three things follow:

- **A neighbour stolen mid-game voids the game.** You start a node while you
  hold its neighbour, someone beats your score on that neighbour before you
  post, and your win counts for nothing. Nothing you could have seen warned you.
- **No warning of an attack.** A holder learns their node has gone only when
  the score that took it is already posted.
- **The week can be swept at the start.** Nothing paces a run of captures but
  how fast you can play, so one keen player starting at Monday 00:00 UTC can
  take a chain of nodes before anyone else has opened the page.

### The model: a launch is a move, and a move is a row

Each player has a fleet, which is a claim in transit along one lane of the
meta-map.

- **A new append-only table**, `campaign_launches (week_start, from_node,
  to_node, user_id, launched_at default now())`. The server stamps
  `launched_at` and the client can never set it. Like `scores`, a row is a
  claim, not a fact: **any row is accepted, and `fold` ignores invalid ones.**
  That keeps "who holds what is never stored" true. The standing is still a
  replay, now of launches plus scores, so moderation still recomputes it for
  free.
- **A launch is valid** when, at `launched_at`, the player holds `from_node`,
  the two nodes share a lane, and the player has no other fleet in flight or
  in its window. One fleet per player is the main brake on a sweep.
- **Arrival** is `launched_at + hours` for that lane, and it opens a window of
  `WINDOW_HOURS`. A counted, hand-played win on `to_node` posted inside the
  window is a move. **Losing `from_node` after launch does not cancel it**, so
  the claim is locked at launch, and that fixes the stolen-neighbour case. You
  start a game knowing whether it can count.
- **A window that closes with no win just ends**, and the player may launch
  again. Recall and retreat are left out. They would add a rule without
  adding a choice anybody needs.
- **Homes stay as they are.** No fleet is needed to claim an empty home. A home
  is where a player's first launch starts, and it still can't be taken, so it
  is still the way back for anyone who loses the field.

What a fleet does *not* change: the bar to take a node. An empty node falls to
any qualifying win, and a held one only to a strictly better score
(`compareScores`, so a tie defends).

### Collisions fall out of the existing rules

Fold launches, arrivals, window closes and scores as one timeline, in time
order. When two players' windows overlap on one node, the first counted win in
either window takes it if it is empty. After that, a win in the other window
must strictly beat the new holder. In effect the best score wins, and the
earlier one wins a tie. That is the same thing that happens today when two
neighbours race for a node, so no new rule is needed.

### What the warning is, honestly

The campaign page would show every fleet in flight, with its source, target
and ETA. A holder's answer is the one the rules already give: better your own
score on the node to raise the bar an attacker must clear.

It warns of a *claim window*, not of play. An attacker can play the node's map
whenever they like, before launching or during the flight, and post when the
window opens. The embargo is what stops them copying the holder's replay, so
the warning hands over no information beyond the target and the time.

### Lane length

Hours per lane come from the node coordinates `tools/campaign.py` already
writes (`x`, `y`, in mapgen world units), scaled by `HOURS_PER_UNIT`. Store
them in the graph rather than having the page recompute them, so rounding is
decided once. Lanes become `[a, b, hours]`. `neighbours()` destructures only
the first two, so it reads the new form unchanged. Bump `GRAPH_VERSION` to 2,
and keep folding version-1 weeks under the current rules so past weeks don't
change.

The knobs to playtest are `HOURS_PER_UNIT`, `WINDOW_HOURS` and fleets per
player. Back of the envelope: with one fleet and a mean lane time of T hours, a
player makes at most about 168 / T moves a week. At T = 12 that is 14 moves on a
12-to-40 node field, few enough that latecomers still arrive to a contested
map. Measure the real distribution of lane lengths on a few generated weeks
before choosing the scale. Homes sit `HOME_OFFSET` out, which is longer than many
field lanes, so they may want their own fixed time.

### The open problem: identity

Names are not identities ("Known limitations, accepted on purpose" in
`leaderboard/README.md`), and today that is nearly harmless: a score forged in
your name can only help you. A forged *launch* does real damage. It spends your
one fleet and sends it where you didn't want it to go, and the window it opens
blocks your next launch.

A candidate, not a decision: a per-week campaign token, minted on your first
home claim, kept in browser storage the way the game keeps `sc_pbp_seats`, and
stored hashed on the server. Launches would go through a Netlify function that
checks it, holding the only write key the way `pbp.mjs` and `log.mjs` do. The
cost is that it would be the board's first per-person credential, with the
device-bound trade play-by-post already makes: a lost token is a lost fleet
until the week ends, unless `tools/admin.py` learns to reissue it.

### What building it would touch

For a later plan, not this one: `schema.sql` (the table, its RLS and grants, a
view for the page to read), a new function in `netlify/functions/`, `fold`
(the merged timeline, behind a version-2 branch) and `canAttempt` in
`js/campaign.mjs`, the campaign page (a launch control, fleets in flight),
`tools/campaign.py` (lane hours, `GRAPH_VERSION`), and tests in
`leaderboard/tests/campaign.test.mjs` and `tests/test_campaign.py`.

### Decided against: a play-by-post duel on a collision

When two fleets meet at one node, the obvious game answer is to make them
fight: open a play-by-post match between the two players and give the node to
its winner. Set aside, for three reasons:

- **It is a different game.** A node is a challenge against bots on one fixed
  map. A match between two people is another setup on another board, so its
  result says nothing about the node.
- **Somebody may never turn up.** A duel needs both players. The best-score rule
  needs neither to wait on the other.
- **It drags play-by-post's deadline machinery into the campaign**, where a
  week-long clock is already running.

The best-score rule settles a collision with what already exists.

## The bot column: the rules in full

- **The leaderboard's bot column is computed offline, never served.**
  `tools/bot_replay.py` replays every `models/` bot through the human's seat on
  each posted map and caches the answer in `bot_scores`; a scheduled GitHub
  Action (`.github/workflows/bot-replay.yml`) is the whole backend, since the
  result is a pure function of the setup, the seed and the code. It drives
  `tests/sim.play_settings`, which goes through `settings.build_state` rather
  than `mapgen.generate` — a posted setup carries tuned knobs, and that is the
  only funnel that pushes them into `config`. The replayed seat gets default
  `AiParams` (slot 0 is the human's) except for `aux`, the bot-defined knob —
  `bot_replay.REPLAY_AUX` names each bot's best profile there (`knower` on
  Oracle: Search) and the value in force is stored on the row; opponents keep
  theirs. **The seat it takes over is handed over outright** (`sim._hand_over` clears
  `is_human`, sets the strategy and params; plain `engine.end_turn(state,
  decide=decide)` then drives every seat, the replayed one included, just as
  `sim.play`'s own ladder/swap tournaments do). That is the same
  full-information footing every other bot-vs-bot measurement in this codebase
  already stands on: an oracle opponent resolves it through `ai.STRATEGIES`
  and simulates it exactly (`knower._model_for`), the way it would any other
  fielded bot, rather than guessing blind at a seat that was never actually a
  person. **The app agrees by construction, not by coincidence**: a match that
  *starts* in autoplay has no human seat at all (`settings.build_state` clears
  the flag `mapgen` stamps on pid 1), so an all-bot game has no preferred seat
  in either place and the in-app demo plays the identical game the column
  computes — pinned by
  `test_an_autoplay_demo_plays_the_same_game_the_bot_column_does`, which reads
  77 turns apart on its setup without it. `_hand_over` is still what does it
  in the harness, because a posted *human* setup carries `autoplay: False` and
  so has nothing for that stamp to fire on. Leaving the flag set once looked
  necessary — a token can only ever say "seat 1 is a bot" as `autoplay: true`,
  never as an actual flag, so a live, token-driven Watch link could only ever
  reconstruct the *handicapped* version. That is fixed from both ends now: the
  row is backed by a stored replay, and a token-driven reconstruction clears
  the flag too. `play_from` uses the same handover, and `engine_rev`
  hashes `tests/sim.py` (`_OUTCOME_HARNESS`) so changing how a replay is
  played marks every cached row stale; `replay_rev` must not, since a stored
  log's replay consults no seat at all. It also lifts the bots' own per-decide wall-clock guards 100x
  (`ai.set_budget_scale`, opt-in via a model's `BUDGET_SCALE`): those are sized
  so the browser tab never freezes, and tripping one is the only thing that
  makes such a bot's output depend on the clock — so a batch run that can never
  trip one is *more* reproducible, not less. `won`, never
  `turns`, says whether a bot took the board, and a loss is listed but never
  ranked (`standings.botOrder`). `bot_scores` is the one table with no public
  insert path: the worker's secret key is its only writer.
- **A winning replay is stored on the row, and the Watch link plays that back
  rather than re-deciding the match live.** That is what let the handover (previous section)
  go back to full information without reopening the original mismatch: a token
  can hand the browser a setup and ask it to re-decide from turn one, and that
  re-decision depends on exactly which commit is deployed where — the same
  class of risk regardless of which way the seat is flagged. `sim.play_settings`'s
  `log` parameter fills in a `replay.GameLog` turn by turn as the run happens
  (off the `TurnRecord` every `end_turn` call returns, the same shape
  `main.resolve_turn` records from); `tools/bot_replay.py` builds one for
  every replay and keeps its encoded form only on a win
  (`bot_scores.match_id`/`rules_version`/`log`), the same rule a human's own
  posted score follows. `replay.reconstruct` applies recorded orders and dice
  verbatim and asks no seat to decide anything, so it cannot drift from the
  row it backs — and, load-bearing for the handover above, is completely
  unaffected by what any seat was flagged during the run that produced it.
  `standings.botWatchKind(row)` picks the link: `#log=<match_id>` (exactly a
  human score's own mechanism) for a current replay, an outdated-replay
  disclosure for one stamped under rules this build has moved past, or the
  old reconstruct-it-live method (`token-encode.botWatchSetup`) as a fallback
  for a row with no stored replay at all — a loss, or one computed before
  this existed. `game_logs`'s human consent boundary (a posted score) is
  untouched; a bot's own log needs none of it, since `bot_scores` is already
  fully public, so it lives directly on that row rather than in `game_logs` —
  `public_watchable_replays` (`leaderboard/schema.sql`) is the `union all` of
  `public_replays` with a winning bot's own log that `netlify/functions/replay.mjs` actually reads,
  keeping that function's one-query, no-branching shape for either kind.

## A replay is never shown as if it still reproduced the game

- **A replay is never shown as if it still reproduced the game once the engine
  has moved past it.** `GameLog.is_current` (`rules_version == engine.
  RULES_VERSION`) is the same check on both sides of the wire, and both were
  silent about it until this was added — `main.open_replay`/`resume_game` would
  happily reconstruct an outdated log through today's engine and show whatever
  that produced, with nothing to say it might not be the game that was actually
  played. `replay.latest_log` and `tools/position_suite.local_logs` decline such
  a log the same way they already decline a version-1 one; `main.open_replay`
  returns `None` for one too (a dedicated status line, `WATCH_OUTDATED_MSG`,
  tells it apart from a genuinely unreadable blob). The board's half is
  `game_logs.rules_version` — one more claim stored alongside the blob, same as
  `finished`/`won`/`hand` (`share.row_for` sends it, `log.mjs` defaults a
  missing one to `1`, the only version there ever was before this column
  existed) — compared against `leaderboard/js/config.mjs`'s
  `CURRENT_RULES_VERSION` in `game.mjs`'s `watchableIds`, so an outdated replay
  simply has no *Watch* link rather than one that lies. That JS constant has no
  build step to keep it honest, only a hand bump alongside `RULES_VERSION` and
  `tests/test_leaderboard_sync.py` pinning the two together. This is a cheaper
  half-measure chosen over storing full board snapshots (which would let a
  replay outlive *any* future rules change, at the cost of the size and
  bot-independence properties `replay.py`'s module doc argues for) — worth
  revisiting if snapshotting ever happens, but not before.

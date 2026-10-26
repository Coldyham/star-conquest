# Leaderboard design notes: bot column and checked scores

Why the board's two offline workers are shaped as they are: the bot column
(`tools/bot_replay.py`, the `bot_scores` table) and checked scores (`share.py`
uploads, `tools/verify_scores.py`, watching a replay). It also covers replay
versioning. `CLAUDE.md` states the rules under "Challenge links carry a score to
beat". The bot-side measurements behind the bot column (`replay_aux`,
`BUDGET_SCALE`, the seat-flag incident) are in [`bots.md`](bots.md),
"Replaying a bot for the leaderboard". The site's own setup is in
`leaderboard/README.md`, and why each table and view in `schema.sql` is shaped
as it is in [`schema.md`](schema.md). Related: [`core.md`](core.md) (challenge
keys, the replay log). Index: [`../README.md`](../README.md).

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
The same workflow runs the score checks, the Playstyle readings and the weekly
campaign map as jobs of their own. They share nothing but the secrets check, so
a long bot replay never holds up the cheap three and one failing never stops
the others, and *Run workflow* runs any subset of them (2026-10-10; until then
they were steps of one job).
Netlify was never a candidate either way — its Functions run JavaScript and Go,
and there is no Python runtime to put the engine in.

The cadence answers to how fresh the column needs to be rather than to cost:
this repository is **public**, so Actions minutes are unmetered and the job runs
hourly, with `workflow_dispatch` for when it is wanted sooner. Most of those
runs find nothing to do and cost a few seconds of setup — the job installs no
dependencies, since the simulation core imports no pygame and nothing off PyPI.
Only a *private* repository makes the schedule a budget question: runs there
bill against the account's monthly allowance (2,000 minutes on the Free plan)
and GitHub rounds each job up to the whole minute. The workflow is five jobs
(a secrets check, then bot scores, score checks, Playstyle readings and the
campaign map), so hourly would spend some 3,600 minutes a month just asking
whether there is work. Either way the cadence
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
statement only the caller can make. `bot_replay.replay_aux` is where the board
makes it: every bot at the top of its own slider, since each strategy's slider
runs from its cheapest stop to its strongest. Today that is `knower` on Oracle:
Search and `actuary` on Style: Learning. Opponent
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
`leaderboard/netlify/functions/replay.mjs` reads either kind through, keeping that
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

### Estimated difficulty

The column already says, per map, which bots took it from the human's seat, so the
share that *didn't* is a difficulty estimate at no extra compute
(`game_summary.difficulty`, the "Difficulty (estimated)" sort and an Easy / Medium /
Hard / Very hard badge). It is for a new player looking for a ramp. They should not
need to know what the bot names mean or start on the latest fogged marshal map.
That is why it carries a plain word rather than "3 of 7 bots win", which goes in
the tooltip instead.

On the live board (2026-10-13, 137 maps, all 7 bots on each) maps spread out
rather than lumping at the ends: 0 to 7 bots winning covers 18, 31, 36, 26, 13,
9, 3 and 1 maps. The estimate follows the opponents. Maps against heuristic alone
average 4.2 bots winning, heuristic and rusherplus 4.6. Maps against knower,
marshal and thinker average 1.4, and actuary and marshal 1.2. It reads close to a
ladder: when two bots split on a map, the weaker one is the winner about 10% of
the time.

It is a **share, not a count**, so a newly added bot that has not reached every
map yet doesn't move the maps it has reached. It is **one game per bot**, so one
bot's difference is dice. And it measures the map *against the bots*: people have
won nearly every map that no bot wins. A config averages its maps' shares.

The levels (`format.mjs`'s `DIFFICULTY_LEVELS`) follow the roster's classes
rather than even steps. The oracle pair (knower at Search, actuary at
Learning) is the top. Next come marshal and plain actuary (only marshal is
replayed), then a gap to thinker, a similar gap to claudebot, and heuristic and
rusherplus at the bottom ([`ladder.md`](ladder.md)).
So on 7 bots, Hard was 1-2 winning, Medium 3-4 and Easy 5+. That made the live
board 13 Easy, 39 Medium, 67 Hard and 18 Very hard. Tiering on the weakest
class that won instead agrees on 119 of 137 maps. The rest are one weaker bot
winning against the trend, which a count is less swayed by.

Re-cut on 2026-10-18 for eight bots, with council in the column. council
joins the top class, so Hard is 1-3 winning (council, knower at Search, actuary
at Learning), Medium 4-5 (marshal and thinker too) and Easy 6+, at shares of
0.55 and 0.25. Those cuts read the same counts on seven runs, so a map the
newest bot has not reached yet is not moved a level by the gap.

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
repeatedly lands several times. A score is judged on the upload that verifies,
not the longest, since anyone can upload under a public `match_id`
(`verify_scores.judge`, `score_checks.log_id`; see
[`schema.md`](schema.md), "game_logs"). `--prune` clears the rest, using the one
delete path the public does not have.

**A rules change had never actually moved `RULES_VERSION` until the production/
combat reorder, so "the engine outran an old replay" had never been a real case
to handle — only a documented possibility.** The verifier already had the right
shape for it (an `outdated` flag on a `mismatch`, decided *after* the replay so a
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
have played. Until 2026-10-08 a log with hand-picked dice or doctored bot moves
verified; `tools/par_search.py`'s lucky lines are one example. Since then each
fight's dice are keyed from the board (`engine._Dice`), and the verifier rolls
every turn marked `keyed` again from its recorded orders (`replay.dice_follow`,
via `verify_scores._DiceAudit`) and calls a log whose dice differ a `mismatch`.
A real lucky line is caught at its first hand-picked roll. Turns from before
keying carry no marker and pass unchecked; the verdict's detail counts the
fighting turns that did. An unkeyed turn after a keyed one is a `mismatch`,
since a game is keyed from the moment keying arrived. Two gaps remain. A forged
log with every marker stripped reads as an old game, and only when it was
played could tell it apart. And the bot half stays open by design.

*Open: a cutover for unkeyed logs.* Once keyed dice have been live long enough,
a score submitted well after the deploy whose log is unkeyed could be treated as
unverified. `submitted_at` is server-stamped, so the date can be trusted. No
cutover is set yet, deliberately: the change may sit unpublished for a while,
and the service worker serves the cached build first (stale-while-revalidate),
so an honest player can run the old code for a session or two after a deploy.
Any cutover needs the real deploy date plus a grace period. What was
measured is in `docs/design/par.md`, "A gap this exposes in `verify_scores`".

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
the longest winning until their score is checked, our continuation would replace
the very replay their score is checked against.

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
score, `verify_scores` reports a `mismatch` flagged `outdated` — unverifiable,
not wrong. The check happens *after* the replay, so the many old games a bump does
not actually disturb keep verifying on their own merits; only the ones it broke
are set aside, and set aside rather than accused.

**The random pool is the other axis, and it gets the same flag.** A seat left
to chance is dealt from the pool its seed's era names (`settings.pool_for`)
and the log records what it was dealt (`GameLog.strategies`;
`docs/design/core.md`, "A seat left to the seed"). A score whose numbers
reproduce but whose rival seats faced bots other than the ones its setup deals
today is `verified` and `outdated`: played honestly, by a build from before the
pool was fixed or a stale one that did not know its seed's era, against a lineup
the map's other scores did not face. It stays counted and keeps the verified
tick. Not a `mismatch`, because an edit to the pool would otherwise drop every
honest earlier score on a random map, and a tampered lineup gains nothing a
tampered score would not. `settings` is in `replay_rev`, so a pool edit makes
`--stale` re-decide. A log that records no lineup is judged on its numbers.

**Rewinding needs no versioning at all**, because it does not claim to reproduce
anything. A rewind replays the prefix exactly, then plays *on* from there — a
counterfactual by construction, which is why `fork` mints a new `match_id`. Both
rewinds re-stamp `rules_version`: the kept prefix has just been replayed under
today's rules to find that turn, so the log would be describing itself as older
than it is.

## Playstyle readings (`tools/playstyle.py`, `tools/playstyle_worker.py`)

The player page (`user.html`) has a Playstyle panel showing how a name plays
next to the roster. It covers attack margin by ship share, how far into a game
two-thirds of the income and of the ships were first held, voluntary frontier
empties (how often covered, how often lost), and border losses. These are the
readings `tools/human_habits.py` already printed. The code moved into
`tools/playstyle.py`, and both tools now share it.

**Where it comes from.** The worker reads `public_replays` and nothing else.
Those are the logs a posted score already points at. Each one can already be
joined to a public name through `scores.match_id`, so a reading reveals nothing
a visitor couldn't already work out from the replay. Uploads that no score
points at, which is what *Share replays* sends, stay unread. That is the
consent boundary from "Checked scores".

**Keyed by match, never by name.** `playstyle_readings` has one row per
`match_id`, and nothing on the row identifies a person. The page finds a name's
rows through that name's own scores. This has three consequences:
- a rename carries over with nothing to update;
- a deleted score drops its reading from view, and `--prune` later deletes the
  row;
- no stored profile can drift from the scores it describes.

A per-name table would have needed every one of those cases written by hand.

**Every field sums.** A reading is counts and lists. Wave ratios are binned in
tenths, and a threshold that is never reached is counted, not left blank. So a
player's record is their rows added together (`pool`, in both
`playstyle.py` and `leaderboard/js/playstyle.mjs`), and the page does all the
pooling. The roster column is that same shape, pooled once:
`leaderboard/js/playstyle-baseline.mjs`. It comes from the winning seat of each
roster self-play game, because every posted game is a win, and it is generated
by `tools/playstyle.py --baseline`, never by hand. Game length is left off the
panel because the baseline's setups are not the ones people play.

**`rev` and `shape`.** `rev` is a digest of `playstyle.py` and
`models/actuary.py`, since `waves` prices a garrison with actuary's
`_effective`. A row stamped with another rev is read again while its log still
replays. A log that no longer replays keeps the row it has. `shape` is the
record's layout, and the page reads only the one it knows.

**What it does not claim.**
- Posted games are wins only, so this describes how a name wins, not how it
  plays.
- Names are typed, not owned, so anyone posting under a name adds to it.

The panel says both. It appears only once a name has `MIN_GAMES` readings.

### Decided against: warming actuary's learner from a profile

The first idea was to feed this profile to actuary at Style: Learning, so its
model of a person would not start cold. That was dropped before any of it was
built.
- The one curve actuary plays from (strike chance by ratio, player targets,
  unpressed) reads the same for every bot and for the person: `learner.md`,
  "The strike curve does not tell the bots apart" and "Predicting people".
- The traits that do tell players apart (guard, evacuation, all-in share, and
  the readings above) are not in actuary's model.
- A prior seeded from a profile would therefore move little.
- It would also make `decide` depend on a file or a fetch, not only on the
  board, which bot replays and play-by-post both rely on.
- Later measurement (2026-10-11, [`learning.md`](learning.md), "Memory off"
  and "The counter-strike") was against the roster only. There, Learning plays
  as well with no memory at all. A counter-strike keyed to the voluntary
  frontier empty, the one Playstyle reading that does separate players, did as
  well on the prior rate as on the learned one. Those priors were fitted on the
  roster, and only people have profiles, so this does not settle the profile
  question. It stays open, pending how far people's readings sit from the
  roster prior.

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

**Why one site.** The game and the board were two Netlify sites until the
merge, which went live in October 2026.
- **Deploys.** Both drew on one free-tier pool of about 20 production
  publishes a month, and most changes touched both. The pool ran out once,
  with a merged board fix waiting over a week for a slot.
- **One localStorage.** Separate origins meant the lobby's "your matches" was
  a hand-off from the game plus a lobby-side cache, and two name keys.
- **The PWA stays a PWA.** Leaving the fullscreen game for the board brought
  back the browser chrome, and on iOS lost the app's storage.
- **No hostname hacks.** Previews found each other by adding or removing
  `-leaderboard` in the URL (`sibling_host`/`siblingGame`); one origin needs
  none of it.

**Onto the game's origin, not the board's,** because localStorage doesn't
migrate between origins: the game's holds seat tokens, bests and preferences,
and the board's held only a posting name and the lobby cache. The board went
under `/board/`, which also settled the `index.html` and `favicon.png`
collisions. The cost: every change now builds the game with pygbag, so a board
typo fix is no longer cheap to preview.

**Decided against: keeping the functions on the old board site behind a
`/api/` proxy** (the merge brief's option B). It kept `SUPABASE_SECRET_KEY` off
the site that builds fork previews, but left two sites to deploy and put a
proxy between the rate limit and the client's IP. The functions moved instead,
and the fork-preview risk is held by the "Require approval" policy.

### The pages are templates

Every page carried an identical copy of the head and the menu, so adding a page
or a menu entry meant editing all of them. Each page is now a Jinja2 template
extending `leaderboard/templates/base.html`, rendered by
`tools/render_board.py` inside `build_web.sh`. A page sets `title`,
`description`, `main_class` and `script` (a list for more than one module), and
fills `main`, `before_main` and `footer`; the footer's flavour text stays per
page, and a page with no `footer` block has no footer.

- **At build time, because Netlify serves files.** There is no server to render
  on, so the include runs either before deploy or in the browser. The build
  already runs Python (`tools/pwa/inject.py`), and what reaches a visitor is the
  same complete HTML as before, menu and all, with no script needed to show it.
  Converting changed no rendered byte.
- **Decided against: a JS include** (an empty `<header>` a module fills on
  load). It kept "no build step", but the menu would vanish when the script
  failed and could pop in a frame late, and the markup would live in JS strings.
- **Decided against: copies pinned by a test.** It catches drift but still
  makes every menu change an edit to every page.
- **The cost** is that `leaderboard/` is no longer servable raw: local work
  renders first (`leaderboard/README.md`, "Local development"). The allow-list
  of servable files moved from `build_web.sh` into `render_board.PAGES`,
  `STATIC_FILES` and `STATIC_DIRS`, and `tests/test_web_build.py` checks that
  every page renders with exactly one, identical header.

The weekly campaign (crowns, embargoes, the grace period, campaign fleets) is
in [`campaign.md`](campaign.md).

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
  `bot_replay.replay_aux` puts each bot at the top of its own slider there
  (`knower` on Oracle: Council, `actuary` on Style: Hunt), one row per family
  ([`bot-families.md`](bot-families.md)), and the value in force is stored on the row; opponents keep
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
  `public_replays` with a winning bot's own log that `leaderboard/netlify/functions/replay.mjs` actually reads,
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

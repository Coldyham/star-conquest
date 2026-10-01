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

**Rewinding needs no versioning at all**, because it does not claim to reproduce
anything. A rewind replays the prefix exactly, then plays *on* from there — a
counterfactual by construction, which is why `fork` mints a new `match_id`. Both
rewinds re-stamp `rules_version`: the kept prefix has just been replayed under
today's rules to find that turn, so the log would be describing itself as older
than it is.

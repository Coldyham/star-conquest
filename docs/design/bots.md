# Bot design: the roster as a whole

Why the AI is shaped as it is across the whole `models/` roster: the parameter
space a tuning is only valid inside, how bots are measured, replaying bots for
the leaderboard's bot column, the break-even margins every bot prices a fight
with, and the two routes for bots written outside the repo. Single bots have
their own files: [`knower.md`](knower.md), and marshal in
[`marshal.md`](marshal.md) (overview and standing),
[`marshal-pricing.md`](marshal-pricing.md) (what a fight is priced against) and
[`marshal-flow.md`](marshal-flow.md) (where the surplus goes). `CLAUDE.md`
states *what* each rule is; these files say *why*. Every section, and every idea
that was measured and dropped, is indexed in [`../README.md`](../README.md).

**Every number here was measured, and the negatives are recorded as carefully as
the wins.** Don't re-add a deleted idea or re-tune a constant without a
measurement of your own, and read the next section before running one.

## Measuring a bot: what earlier sweeps got wrong

The method lessons from every bot file, gathered in one place. Each points at
the section holding the evidence.

- **Quote a result against a null measured at the same sample size.** A paired
  mirror harness (the two variants swapped between seats on the same seed) pins
  a deterministic null at exactly 50.0%. Free-for-all rotations do not cancel
  past two seats, and their nulls have read anywhere from 43% to 52%. See
  [`marshal.md`](marshal.md), "The 2026-09 tuning sweep" (its two
  methodological notes) and the wedge table in its first section.
- **Sweep node count and ship speed, not just the default cell.** A constant
  keyed off travel time is live in one regime of three. See "Lane length across
  the parameter space" below.
- **Check against other opponents, not only a copy of the bot.** `ENEMY_NEAR`
  gained 5 points in self-play and lost 7 against thinker. See
  [`marshal.md`](marshal.md), "The 2026-09 tuning sweep".
- **Small effects need n in the thousands. Re-run a surprising reading on fresh
  seeds before believing it.** Readings at n≈370-900 have looked real and then
  vanished at double the sample, several times. See
  [`marshal-pricing.md`](marshal-pricing.md), "Two doomed neighbours, and what a
  retreat is worth", "Holding a doomed system to sacrifice-and-retake" and
  "Re-tuned for production-before-combat and the garrison-fights-last pile-up".
- **A cell that times out more than about half the time cannot be read paired.
  Run the arms separately.** Symmetric maps and 1 ly/turn are the usual
  offenders. See [`marshal-pricing.md`](marshal-pricing.md), "Racing a third
  player for the same system", and [`marshal-flow.md`](marshal-flow.md), "Phase
  3b re-flooding an already-covered target…".
- **Assert that every roster name is in `ai.STRATEGIES` before the first
  game.** An unknown name silently falls back to heuristic, so a z of +17 is a
  bug, not a discovery. See [`marshal-pricing.md`](marshal-pricing.md), "Racing
  a third player for the same system".
- **Ablating one mechanism at a time hides interactions.** `FRONTIER_GUARD` read
  as dead weight until Phase 3b spent the ships it was hoarding. See
  [`marshal.md`](marshal.md), first section.
- **Win rate is not the whole objective.** Watch timeouts and game length too: a
  variant can hold its win rate while games stretch into stalemates. See
  [`marshal.md`](marshal.md), first section.
- **Check, rather than assume, that a variant with every new flag off is
  bit-identical to the base.** See [`marshal-pricing.md`](marshal-pricing.md),
  "Two doomed neighbours…", and [`marshal-flow.md`](marshal-flow.md), "The empty
  interior is the retreat…".
- **Measure candidate shares on contested decisions only.** During the land-grab
  every candidate ties, so the default wins by construction. See
  [`knower.md`](knower.md), "Borrowed candidates (`EXTERNAL_CANDIDATES`)".
- **To ask whether a bot plays differently, measure its distance from every
  bot, by phase.** A candidate share compares it with knower's default alone,
  and only on contested positions. See "How differently two bots play" below.
- **Time a bot on a quiet machine and record the load average.** A loaded
  machine read every cell ~1.9x high. The browser build has never been timed.
  See [`knower.md`](knower.md), "Cost per decide, and where the search guard
  trips".
- **Lift wall-clock guards for any reproducibility check.** A guard that can
  trip makes the result depend on the machine. See "Replaying a bot for the
  leaderboard" below.
- **`tools/sweep.py`'s smoke gate can fail a healthy cell deterministically**
  when a sweep has many cells. See [`marshal-flow.md`](marshal-flow.md),
  "Measurement: the combination, ablations, and cross-opponent checks".
- **A correctness fix may ship at a null result. A new tactic at a null is
  deleted.** See [`marshal-pricing.md`](marshal-pricing.md), "The attack side of
  the pile-up".
- **A neat explanation for a null result is itself a hypothesis.** "Phase 3b
  masks it" was tested and turned out false. See
  [`marshal-pricing.md`](marshal-pricing.md), "Re-tuned for
  production-before-combat…".
- **Bot-vs-bot play only visits positions bots create.** Real games add
  positions people built. See "Positions from real games" below.

## How differently two bots play (`tools/bot_distance.py`)

The first check that actuary plays differently was to make it one of knower's
borrowed candidates and count how often the search picked it
([`actuary.md`](actuary.md), "As one of knower's borrowed candidates"). That
answers a narrower question than it seems to. It compares the new bot with
knower's default only, so a bot could differ from knower and still copy marshal.
Any one-ship difference in a turn's orders counts as different. A candidate
listed last loses every tie. The share mixes "is it different" with "does
knower's rollout score it well", and the rollout runs an oracle rival as if it
were blind. It also measures nothing during the opening, where every candidate
ties.

`tools/bot_distance.py` asks every bot for its orders on the same positions,
seat by seat. The positions come from roster self-play by default, or from
recorded games with `--local`/`--supabase`. No game is played on them, and three
measures come back:

- **distance**: where each garrison ship goes this turn (held at home counts as
  a move), as a share of the seat's garrison, then half the L1 gap between two
  bots. 0 means the same orders to the ship, 1 means no ship goes the same way.
- **kappa**: each owned system's biggest send, classed as hold, own, neutral or
  rival. Agreement between two bots, corrected for chance (Cohen's kappa).
- **fingerprint**: share of the garrison launched, where it went, orders per
  position, how often an order empties its source, and decide cost.

Positions are split into **contested** (the seat holds a system next to a live
rival's) and **opening**, and each bot's nearest neighbour is reported in both.
Bots run at the leaderboard profile (`REPLAY_AUX`, guards lifted). `--null` asks
each bot twice; every current bot is at exactly 0 from itself.

First reading, 21 two-seat self-play games, 18 nodes, 6 ly/turn, every 5 turns
(612 contested and 316 opening positions; knower at Search):

    nearest neighbour      contested        opening
      knower             marshal  0.31   marshal  0.17
      actuary            marshal  0.37   marshal  0.19
      marshal            thinker  0.23   knower   0.17
      thinker            claudebot 0.11  claudebot 0.03
      claudebot          thinker  0.11   thinker  0.03
      heuristic          claudebot 0.20  claudebot 0.18
      rusherplus         knower   0.43   claudebot 0.25

    kappa against actuary, contested: knower 0.18, marshal 0.26,
    thinker 0.24, claudebot 0.20, heuristic 0.10, rusherplus 0.21

Contested, actuary agrees with no other bot on a system's move kind much beyond
chance. That confirms the candidate-share reading with every bot rather than
one. thinker and claudebot are close to the same bot, and in the opening they
are within 0.03. In the opening, knower, marshal and actuary are within 0.17-0.19
of each other: the roster's land-grab is one land-grab. actuary's planned
opening ([`actuary.md`](actuary.md), "The planned opening") was built for that
gap.

## New bot families (proposed 2026-10-05)

After actuary, a list of bot designs that should be at least claudebot's
strength and play moves the roster does not. The roster at the time had three
shapes: the phase bots (claudebot, thinker, marshal, knower at Off), actuary's
projected ledger, which assumes no rival launches again, and knower's oracle.
Nothing modelled a rival from what it can see, hedged against the rival's move
in the same turn, planned across several hops and turns at once, or took its
values from data. Six proposals, one per gap. Two were built and are recorded
in their own files. The other four are written up here, unbuilt.

**What the two built ones taught.** The opening is where the roster plays one
land-grab, and the planned opening paid there. convoy was the most distinct
bot in the opening but no better there. In the contested middle it played like
a phase bot and lost to actuary (convoy.md, "Where it loses to actuary").
Distinct is not better, so each proposal below still has to win games. The three
that target the contested middle (learner, duelist, riposte) go where convoy
lost.

**Built.**

- **surveyor**, the opening planned as a schedule: which neutrals, in what
  order, with which converging groups, priced by payback. Folded into actuary as
  Opening: Planned. See [`actuary.md`](actuary.md), "The planned opening".
- **convoy**, the turn as routing over time: supply at (system, turn), strikes
  as demand. Built, measured, not shipped. See [`convoy.md`](convoy.md).

**learner (proposed as reader): a rival modelled from the board, without running its code.**
Estimate each rival's attack margin from its fleets in flight (ships sent
against the target's garrison plus what it builds before landing), and its
reserve from its frontier garrisons. Then guard each border just above what
would trigger that rival's own rule, and commit the rest. No other non-oracle
bot changes its guards depending on the opponent: against rusherplus it would
hold almost nothing, against marshal much more. Unlike knower it reads a human
seat as readily as a bot. Built: the model with memory kept from turn to turn,
and then a bot that plays actuary's ledger on a board with the model's predicted
launches added. Both are in [`learner.md`](learner.md), with why memory has to be
keyed by the game's path rather than by an oracle flag. This reopens the ideas [`marshal.md`](marshal.md), "What the
measurements deleted", rejected as needing "a model of rivals, which is knower's
territory" (baiting, the offensive half of standing aside). The model here comes
from board facts, not from rival code. The host is actuary's ledger rather than
marshal's guard. The bot was built although the model had not passed the gate
set for it.

**duelist: the simultaneous move played as a matrix game.** At each contact,
list both sides' few options (hold, strike, reinforce, evacuate). Price each
pair with actuary's ledger, solve the small zero-sum game in pure Python, and
draw a move from the mixed strategy through `state.rng`. It is the one design
that assumes the rival moves this turn too and hedges against it. actuary
assumes it never launches, and knower best-responds to one predicted move. The
case for it: 86.7% of out-shipped garrisons are gone when the strike lands
([`marshal-pricing.md`](marshal-pricing.md), "Garrisons run away"), so strike
and evacuate are a guessing game, and any fixed rule loses a guessing game to
someone who has learned it, including a person who plays the same bot often.
Against knower, mixing hides duelist only from the lower seat: knower re-runs a
later seat on the real rng positioned where that seat will find it, and an
earlier one on a private rng. Report the two seatings apart. Risk: against a
fixed, predictable bot the best answer is not mixed, so mixing gives up value.
It combines with learner: best-respond to learner's model, and mix only where that
model is unsure. Its cost is actuary's or more, so it declares `decide_ms`.

**apprentice: an evaluator learned from data.** actuary's candidate moves and
a one- or two-turn projection, scored by a linear function whose weights are
fitted offline: on positions out of real games (`position_suite`'s corpus)
labelled with who went on to win, or by learning from self-play. The weights
ship as a constant table, so it stays deterministic and needs no numpy in
play; the fitting script lives in `tools/` and may use anything. Its values come
from data rather than from reasoning someone wrote down, including positions
only people create. Risk: the human corpus was 47 games at the first census
("The first census and setup sweep off the live board"), and a fit to bot
self-play learns the roster's habits. Clearing claudebot is likely; matching
actuary is not.

**riposte: the counter-punch.** When a rival launches, the system it launched
from is thin now. Strike that source with whatever lands before it refills, and
defend the target only where the trade loses ships. It answers in microseconds,
so it suits the browser and is cheap as a knower candidate (actuary was not
added there for its cost). Expect claudebot's strength. Its value is as a
distinct opponent, not a climber. It sits near two null results: striking
vacating targets first ([`knower.md`](knower.md), "Built, measured, removed":
an ordering change inside knower) and rushing the enemy
([`marshal-pricing.md`](marshal-pricing.md), "Rushing the enemy"). Check first
with `bot_distance.py` whether actuary already plays it: its ledger sees a
source thinned by its own launch.

**Considered and left out of the list.** Territory or chokepoint play
(betweenness lost at every weight; pocket-sealing was a constant offset), one
hammer stack (spearhead, 23-42%), arriving after a rival breaks a neutral (null),
all in "Decided against" in [`../README.md`](../README.md). A decoupled
simultaneous-move tree search would cost what knower's search does and overlap
it.

**Measurement ideas from the same list, not yet built.** `bot_distance.py`
covers distance, kappa and fingerprints. Two more were proposed. Fit one rating
per bot to the ladder grid (Bradley-Terry) and read the residuals: a bot that
plays differently beats or loses to someone its rating says it should not. And
when a bot is tried as a knower candidate, shuffle the candidate order so the
last one is not undercounted on ties, and measure whether knower wins more
with it, which was not measured for actuary.

## The person's habits

What the posted human games say about how their (one, mostly) player plays,
against roster self-play: which share marks a won game (2/3 of players' ships),
that their overkill is the endgame, and that they empty frontier systems with
relief covered far more often than any bot. It is in
[`learner.md`](learner.md), "The person's habits" (`tools/human_habits.py`),
beside learner's model of the same games.

## Lane length across the parameter space

`WORLD_SIZE` is a constant up to a standard board (`config.STANDARD_MAX_NODES`,
40), so the map is laid out in the same box and the *node count* sets how far
apart systems are: fewer nodes means physically longer lanes, not a smaller
board. Past 40 the box grows with the node count (`config.world_side`) and the
spread holds at the 40-node figures — measured 7/13/19, 7/13/20 and 8/13/21 at
40, 80 and 120 nodes and 1 ly/turn, identical from 3 ly/turn up. `config.SHIP_LY_PER_TURN` (1-30 on the
slider, 6 by default) then divides all of it. The two together move lane length
over more than an order of magnitude — measured over three seeds a cell,
min/median/max lane in turns:

    ly/turn      12 nodes    18 nodes    24 nodes    40 nodes
       1        14/22/36    11/18/29     9/16/26     7/13/18
       3          5/8/12      4/6/10       3/6/9       3/5/6
       6           3/4/6       2/3/5       2/3/5       2/3/3
      12           2/2/3       1/2/3       1/2/3       1/2/2
      30           1/1/2       1/1/1       1/1/1       1/1/1

**The consequence for tuning: a constant keyed off travel distance is live in
part of that space and unreachable in the rest, and the default sits in exactly
one regime.** `marshal._enemy_margin` is the worked example — it is
`max(edge_attacking + NEAR_PAD, min(ENEMY_FAR, ENEMY_NEAR + 0.1 * (dist - 1)))`,
so the cap needs `dist >= 7` and the break-even floor needs `dist == 1`. Which
of the three terms actually decides a strike, as a share of every strike Phase 3
priced:

    ly/turn    ENEMY_FAR cap    ramp interior    edge floor
       1              100%               --            --
       2            61-100%           0-39%            --
       3             0-60%           40-100%           --
       6                --              100%            --
      12                --            75-96%          4-25%
      18                --            0-72%          28-100%
      30                --            0-27%          73-100%

At the default 6 ly/turn only the middle term is ever selected, so a sweep there
reads both ends as dead code — and a margin fitted there is fitted for one third
of the slider. At the bottom end `ENEMY_FAR` *is* the margin; at the top the
ramp is inert and the live figure is whatever `combat.edge_attacking` returns.

So sweep `--nodes` and the speed knob before concluding that a constant does
nothing, or that a margin is tuned. This has produced a wrong "unreachable
constant" reading before.

## Positions from real games (`tools/position_suite.py`)

**Every other measurement in this file is a bot against another bot**, and that
is a real limit rather than a stylistic one. A roster playing itself only ever
visits positions bots create; whatever a bot is systematically bad at, its
opponents are bad at reaching, so the sweep never asks the question. The
2026-09 sweep's own warning about tuning to a copy of yourself
(`ENEMY_NEAR` gaining 5 points in self-play and losing 7 against thinker) is the
same failure one level up.

Stored replays answer it. The game keeps a match as its inputs, so
`replay.reconstruct` rebuilds the exact board after N turns of somebody's real
game — a position a *person* built, with a person's mistakes in it. Hand the seat
to a bot from there and it plays out the rest. `sim.play_from` is the mechanic,
`sim.positions` picks the turns, and `tools/position_suite.py` runs a whole
corpus (the local `games/` dir by default, or `--supabase` for the shared one —
see [`leaderboard.md`](leaderboard.md), "Checked scores", for how games get there).

**The comparison is paired, which is what makes it worth more than a win rate.**
We know exactly how long the player took from that same board, so each position
is a matched trial rather than an independent sample. One recorded game becomes
dozens of them.

Three numbers come out, and they must not be blurred together:

    faster      of positions where BOTH finished, how often the bot was quicker
    median gain turns saved against the person, over those same positions
    recovered   of positions from games the person LOST, how often a bot won

`recovered` is the one no leaderboard score can ever pose, because only wins are
postable — and it is the most interesting, since a lost or abandoned game is
precisely a position the player could not solve. It has no baseline, so it is a
raw rate rather than a comparison; never rank it against `faster`.

**Read the output as a direction, not a verdict.** The sample is whatever games
happen to exist, on whatever setups whoever played them chose — which, per "Lane
length across the parameter space" above, is very likely one regime of three. A
gap found here is a hypothesis; confirming it still wants a paired sweep with a
z-score, the same as everything else in this file.

**Each bot replays at its measured-best profile, not its menu default —
the same rule `bot_replay.REPLAY_AUX` follows for the leaderboard's bot
column, reused rather than re-decided here.** The first real run measured
knower at `aux=1.0` (search depth 1, the untuned default) purely because
nobody had wired the override through; its win rate and turn counts in any
run before this fix understate what knower actually does. `--aux BOT=VALUE`
overrides a single run and `--budget-scale` controls how far the bots' own
wall-clock guards are lifted (100x default, same reasoning as
`bot_replay.BUDGET_SCALE`: nothing here waits on a frame, and a guard that
never trips is what keeps a result reproducible rather than clock-dependent).

## The first census and setup sweep off the live board (2026-09)

`tools/config_census.py` and `tools/setup_sweep.py` ran for the first time
against the shared corpus once it grew past a handful of games (47 games, 87
scores at the time of this run) — answering "Lane length across the parameter
space"'s open question of which regime people actually sit in.

**They mostly sit in the regime the roster is fitted for.** Median lane length
clustered at 2-6 turns across the corpus (`ship_ly_per_turn` mostly 6-7.5,
`config`'s own default is 6) — the middle third of the three regimes that
section warns about, not either edge. Balance knobs otherwise sit close to
default (`combat_jitter` 0.08-0.10, `defender_advantage` 1.0-1.05). Mode is
overwhelmingly `random`, 3-5 players, 12-33 nodes.

`setup_sweep.py` then ran the most-played real setup found there (random, 3
players, 13 nodes, the knobs above, vs knower+marshal) through three arms —
defaults, +map size only, +every knob ("played") — 40 seeds a cell:

    bot          defaults    +map size    played
    marshal        37.5%       45.0%      40.0%
    knower         32.5%       30.0%      40.0%
    thinker        22.5%       10.0%      15.0%
    claudebot      15.0%        5.0%       0.0%
    heuristic       7.5%        2.5%       2.5%
    rusherplus      2.5%        5.0%       0.0%

Only one of the 18 defaults-vs-arm comparisons cleared |z| >= 1.96
(claudebot, defaults to played, z = -2.55), against ~0.9 expected from chance
alone across that many comparisons — so per the tool's own warning, this is
not yet a finding. It is a direction worth re-checking with a dedicated
paired sweep if it recurs: claudebot lost every one of 40 games on the setup
actually played, having won 15% at the defaults it was tuned against.

`position_suite.py --supabase` also ran, against 15 games sampled every 20
turns (113 positions), knower at its measured-best depth 12 and marshal — the
two the roster is currently being tuned against, not the full lineup above.
Real per-position cost turned out far more uneven than budgeted (most resolve
in under a second, a handful — a long game with a crowded AI roster, or
knower's own search — run 30-170s), which cost two earlier attempts their job
timeout before `position_suite.py` learned to report on whatever it had
finished rather than losing the run outright (see `tools/position_suite.py`'s
`_Cancelled`). The completed run:

    bot          positions    won      faster        median gain    recovered    timeouts
    knower            113   61.9%   50.0% of 104            -0     75.0% of 8            1
    marshal           113   42.5%   41.9% of 105           +17     50.0% of 8            0

Read per the rules above: `faster` and `median gain` say knower tracks a
human's pace roughly evenly from these positions (faster half the time,
median gain essentially zero) while marshal runs slower than the human more
often than not (+17 turns median, when both finish). `recovered` is the
sharper number — of the 8 sampled positions drawn from games the human
actually *lost*, knower took the board 75% of the time against marshal's 50%.
Eight positions is a thin sample for a rate on its own (see the caution
above), but it lines up with knower's oracle prediction being exactly the
tool no blind heuristic has, which is the mechanism this would be explained
by rather than a coincidence — worth widening the corpus before leaning on it
further, not before noting it.

## Replaying a bot for the leaderboard (`bot_replay.REPLAY_AUX`, `BUDGET_SCALE`)

The board's bot column replays each `models/` bot through the human's seat on a
posted map (`tools/bot_replay.py`; the infrastructure is in
[`leaderboard.md`](leaderboard.md), "Bot replays"). Two questions about the
roster fall out of that, and both were measured.

**Which version of a bot goes on the board.** Its best one, not its menu default.
`REPLAY_AUX` names the exceptions and today holds one: `knower` on Oracle: Search
(it was search depth 12 until the knob became three named stops; see [`knower.md`](knower.md), "How far to
look"). That is the top of knower's own slider (`SEARCH_DEPTH_MAX`). It is
not a small difference. On a 16-node map, same seed, same opponents, at the old
depth 12:

    knower @ 1 (default)     336 turns, 346 ships lost
    knower @ 12             121 turns, 137 ships lost

There is no point putting a deliberately hobbled version of the best bot on a
board whose whole purpose is to give a human score something to be measured
against. The cost is wall clock: depth 12 was roughly 100x depth 1, taking a
40-node six-seat game from well under a second to tens of seconds (both figures
from the branch-every-ply search; a root-only ply costs ~0.4x). The worker's
`--limit` and deadline exist for that, and it banks each result as it goes.

**Why the offline runner gets a bigger time budget, not a shallower search.**
knower's search is *iteration*-bounded, so the same board plans the same way —
except for `SEARCH_BUDGET_S`, a 150 ms per-decide catastrophe guard whose own
comment notes that tripping it makes the plan depend on the wall clock. knower's
cost table measured better than 2x headroom at depth 12. On a CI-class container
the largest configuration the menu can build — 40 nodes, 6 seats — measured only
**~1.1x**, and that is not a margin to cache results against. Shrinking
`BUDGET_SCALE` proportionally simulates a slower machine, and a runner just 2x
slower produced a different answer on the same map:

    scale 1.00  (as shipped)        123 turns, 311 lost
    scale 0.50  (~2x slower)        117 turns, 272 lost   <- guard tripped
    scale 0.25  (~4x slower)        113 turns, 206 lost   <- guard tripped
    scale 100   (the worker)        123 turns, 311 lost

Both of knower's guards exist because the WASM build is single-threaded and the
alternative to giving up mid-search is freezing the browser tab — a constraint a
background job does not have. So the worker lifts them 100x via
`ai.set_budget_scale`, turning 150 ms into 15 s against a measured worst case of
~131 ms. Read that as the opposite of a loosening: those guards are the only part
of the bot that is *not* iteration-bounded, so a run that can never trip one is
strictly more reproducible. It remains a guard — a wedged bot is stopped long
before the workflow's own `timeout-minutes` has to — and `BUDGET_SCALE` stays 1.0
for every ordinary caller, so a real game is untouched.

The suite's own reproducibility checks lift them too, and further — to `inf`
rather than 100x (`tests/test_sim.py::test_a_tuned_replay_is_still_reproducible`,
`::test_budget_scale_is_wired_and_only_matters_when_it_bites`). A test machine
running the rest of the suite beside them *is* the "2x slower runner" above, so
at the shipped scale the guard fires on one run of a pair and not the other and
the assertion measures the machine rather than the search. A guard that cannot
fire is the only version of "sized not to fire" a test can rely on; the one
assertion left at a finite scale is the 100x the worker actually uses, which is
there to pin that the worker's own setting is guard-free on this workload.

A bot that wants the same treatment declares `BUDGET_SCALE = 1.0` and multiplies
its own budgets by it at call time; see `models/README.md`. Bots without it are
left alone.

**A seat flag broke the column once, then nearly broke it a second, subtler
way.** A row on that column carries a *Watch* link. Originally that link could
only ever hand the game a setup with `autoplay` on (`botWatchSetup`,
`leaderboard/js/token-encode.mjs`) and let it re-decide the whole match live —
there is no field in a token that says "seat 1 is a bot", so the app kept seat 1
flagged human and drove it from outside no matter what was actually choosing its
orders. `sim.play_settings` used to clear `is_human` on the seat it took over —
the shortest way to make `engine._collect_orders` decide it — and an oracle
opponent reads that flag: a human seat is predicted blind and never trusted,
while an AI one is resolved through `ai.STRATEGIES` and simulated exactly
(`knower._model_for`, `_rollout_decide`). So the cached row was a game whose
opponents knew which bot was standing in, while the live Watch link — which
could only ever turn `autoplay` on, never clear the flag — played one whose
opponents did not. Two different games, one row. On a 3-seat, 18-node map with
knower opponents that was worth flipping the result outright:

    marshal, seat flagged as a bot   lost,  146 turns, 147 lost
    marshal, seat left human          won,  210 turns, 232 lost

The first fix matched the two by leaving the flag set: `sim._hand_over` set only
the strategy and params, and a companion `_step_seat` drove the seat the way
`main.resolve_turn` does under autoplay, so the harness computed the same
handicapped game the live link would if reconstructed. That closed the gap, at a
real cost measured nowhere on the board itself: it made a bot's replayed
performance depend on an artificial edge no other measurement in this codebase
grants it. `sim.play`'s own ladder and swap tournaments — the roster's other
yardstick — clear `is_human` on *every* seat, including whichever one starts
there, so oracle opponents there see a fielded bot exactly as they see each
other. Only the bot column singled one seat out as unpredictable, which is not a
fairer test of the bot, just a different and inconsistent one — an oracle
opponent that would have countered a bot cleanly in the ladder was, on this one
measurement, stuck guessing blind at it instead.

**Storing the replay removed the reason for the handicap, not just the
mismatch.** The bug the flag-matching fix closed was really one instance of a
bigger problem: a Watch link that *re-decides* the match is a second computation
of "the same" game, kept in sync with the cached row only by construction — by
matching harness to link today, with nothing stopping either from drifting
tomorrow (a stale deploy, a future engine change, a second bug in either
harness). The fix for that class of problem is to stop asking a Watch link to
compute anything at all, which is what storing the log actually does (see
below) — and once it does, the flag-matching fix has nothing left to protect.
`replay.reconstruct` applies a stored log's recorded orders and dice verbatim
and never asks any seat to decide anything, so what a seat was flagged during
the run that *produced* the log cannot affect how it plays back, regardless of
which way the flag was set.

`sim._hand_over` clears `is_human` again, restoring the roster-wide,
full-information footing: an oracle opponent reads the replayed seat exactly as
it would in the ladder. `_step_seat` is gone with it — with the flag cleared,
plain `engine.end_turn(state, decide=decide)` drives every seat, the replayed
one included, the same as `sim.play`'s own loop. `play_from` (the position
suite) goes through `_hand_over` too, for the same consistency reason it always
did: the two harnesses measure one bot one way.

The harness is still part of the answer, so `bot_replay.engine_rev` still hashes
`tests/sim.py` alongside the core and `models/` (`_OUTCOME_HARNESS`) — a change
to how a replay is played (this one included) marks every cached row stale. It
stays out of `replay_rev`, which covers a stored *log's* replay: that asks no
seat to decide anything, so no harness change can move it — exactly the property
that makes the flag safe to clear in the first place.

**Then the app was fixed to agree, rather than the column bent to match it.**
Everything above left one half standing: the *game* still flagged seat 1 human
in an all-bot match, so watching a setup autoplay in the app was a different
game from the one the column computed for it — the stored replay meant a Watch
link no longer showed the discrepancy, but the discrepancy was still there, and
it was the app that had it backwards. An all-bot game has no person in it and
therefore no seat that deserves to be unpredictable. `settings.build_state` now
clears the flag whenever a match *starts* in autoplay, which puts the app on the
same full-information footing as the ladder, the swap tournament, the position
suite and the column. On a 3-seat, 16-node map with a knower opponent the two
sides had drifted 77 turns apart (178 in-app against 101 offline); they are now
turn-for-turn identical, pinned by
`test_an_autoplay_demo_plays_the_same_game_the_bot_column_does`.

The seat comes back the moment a person actually plays it. What claims it is
**ending a turn under manual control**, not pressing Take control — which is
what lets Take control serve as the pause it is usually reached for, on a demo
somebody wants to stop and read rather than take over. `engine.end_turn`'s
`claim_seat` applies it as the turn's first phase, so that turn's own
predictions already treat the seat as a person's; `replay.reconstruct` re-applies
it from the per-turn `"ai"` flag the log already carried, so a claim survives a
resume and a rewind lands correctly at either side of it without storing
anything new. The residual is small and bounded: someone who ticks Autoplay on
an ordinary game and lets a knower opponent watch their opening is readable
until the first turn they play themselves — and `carry_autoplay` already frames
that checkbox as "out of a pure demo" rather than as a way to have the AI open
for you.

**What actually fixed the link.** `sim.play_settings` grew a `log` parameter:
filled in turn by turn off the `TurnRecord` every `end_turn` call already
returns, so a `replay.GameLog` comes out of a replay for free, in the same shape
`main.resolve_turn` builds one from live play. `tools/bot_replay.py` keeps it,
encoded, on a win (`bot_scores.match_id`/`rules_version`/`log` — a loss stores
nothing, the same rule a human's own posted score follows), and the Watch link
becomes `#log=<match_id>`: a human score's own mechanism, unmodified. There is
no second computation left to disagree with the first — watching the replay *is*
rewatching the exact game the row reports on, structurally, not by two things
happening to agree, and not by hobbling what the row measures to make them agree
either. `leaderboard/schema.sql`'s `public_watchable_replays` (a `union all` of
`public_replays` with a winning bot's own log, since `bot_scores` needs no
further consent gate to be public) is the one relation
`netlify/functions/replay.mjs` reads either kind through, so the function keeps
its single, unconditional query. `standings.botWatchKind` is what decides which
of "current" / "outdated" / "legacy" (no stored log — the old method, kept as a
transitional fallback) / "none" (a loss) a row gets.

## Break-even margins (`combat.edge_attacking`/`edge_defending`) and the roster back-port

Started as a marshal-only fix ([`marshal.md`](marshal.md), "Two bugs fixed on the way past") and generalised: `combat.edge_attacking`/
`edge_defending` are now the one place a fight's break-even multiple is computed,
and thinker, claudebot and knower all price their margins off it instead of a
private `1.1 / 0.9` constant. Motivation was a real bug, not tidiness — with
`DEFENDER_ADVANTAGE` a live menu slider, every bot but marshal was pricing fights
against a defender bonus that no longer existed, or under-pricing one that had
grown past 1.0.

Two properties the API has to hold for a bot pulling it in:

* **A knob can only ever *raise* a margin above the figure the bot was tuned at,
  never thin it.** `min_swing` floors the jitter half of the edge (not the
  advantage half) at the swing a margin was fitted against; every bot in
  `models/` passes its own `TUNED_SWING = 1.1 / 0.9`. Skipping this is a real
  regression, not a theoretical one: measured pre-floor, claudebot at
  `COMBAT_JITTER = 0.0` scored 8% (2-24, 34 unresolved) against its own
  pre-back-port self, 60 games, 24 nodes, both seatings — a gentler-than-default
  jitter thinned every margin below what the bot's absolutes were tuned to cover.
  With the floor, that cell and the jitter-0.10 default are both exact 50% nulls
  for all three bots.
* **Clearing the edge is not a promise of capture.** It only guarantees the
  defender loses the worst roll; near-matched forces can still round to zero
  survivors on both sides and hand the system to nobody. `preview_fight(1, 1,
  0.1, 0.75)` clears `edge_attacking()` (0.90 vs 0.825) and still annihilates.
  Every caller floors its ask at `target.ships + 1` for exactly this reason.

**The honest margin costs something above default jitter.** Re-measured after the
floor, thinker/claudebot/knower vs their pre-back-port selves (60 games a cell, 24
nodes, both seatings, `DEFENDER_ADVANTAGE` fixed at 1.0):

    COMBAT_JITTER   thinker   claudebot   knower
    0.10 (default)     50%        50%        50%    (exact nulls, by construction)
    0.15               61%        56%        49%
    0.25               33%        40%        46%
    0.50                0%         4%        14%

The old `1.1 / 0.9` hardcode was, by accident, a *gambling* policy: at high jitter
it kept sending at a margin that was no longer statistically safe, and won more
than a bot pricing the real odds does. This is why the finding belongs here and
not as a reason to cap the edge — the fix is correct, the number above is the
honest price of correctness, and a future change chasing that regression back
would be re-introducing the original bug. `DEFENDER_ADVANTAGE` moves the other,
unambiguous way for all three bots (0.75: 53/66/63%, 1.25: 69/94/79%, 1.5:
100/100/100%, same harness) — that direction was never in question, only whether
the bot was pricing it at all.

marshal's own head-to-head numbers move too, now that its rivals are no longer
handicapped by the stale constant — see the re-measured full-roster ladder under
"Where marshal stands" in [`marshal.md`](marshal.md).

## Defender advantage and the AI (`ai._frontier_order`)

`config.DEFENDER_ADVANTAGE` multiplies the defender's strength in combat, so
the AI's `expand_margin`/`attack_margin` have to be measured against the
*effective* garrison (`n.ships * DEFENDER_ADVANTAGE`), not the raw ship count.
Against the raw count the margins understate every target, and the AI simply
stops expanding — it waits forever for a surplus it already has. At 1.0 the
multiply is an exact identity, so nothing about a default game moves.

That fix is necessary but not sufficient, which is why the slider stops at
`config.DEFENDER_ADVANTAGE_MAX = 1.5` rather than the 2.0 first drafted.
Measured over `tests/sim` (40 seeds, 18 nodes, 3 players): 0.75 finishes 38/40,
1.0 → 36/40, 1.25 → 26/40, 1.5 → 19/40, 2.0 → **3/40** — and the 2.0 failures
are *hard* stalemates, not slow games, unresolved even at a 3000-turn cap. Both
sides produce symmetrically, so a fortress bonus that large grows the defence as
fast as any assault can be massed against it. One AI variant (an additive rather
than compounding cushion) moved 2.0 from 2/24 to 7/24 finished, which is not
enough to call it a tuning problem.

`Settings.from_dict` clamps the field to that ceiling, unlike the other balance
knobs, whose out-of-range values are merely odd rather than unplayable.


## The in-app bot maker: built, measured, not merged

Branch `bot-maker` (PR #20) is a visual IFTTT-style rule builder, so a player who
won't write Python has something between the AI tab's five sliders and a
`models/*.py` file. Both halves work: `botlang.py` is a rule language and
interpreter (a `Program` is a flat ordered list of `WHEN … THEN …` rules over 9
conditions, 7 actions and 5 amounts, run per owned system, first *usable* rule
fires, at most one order per system per turn), and `botmaker.py` is a third
`main.py` scene editing it. It is not merged. What follows is the part worth
keeping.

**The language works, and that was the open question.** The branch's own 400-game
pairwise ladder (`--ladder --trials 20`, 18 nodes, both seatings):

| | vs `heuristic` | vs `rusherplus` | ladder share |
| --- | --- | --- | --- |
| `blockturtle` | 76% | 85% | 27% (1st of 5) |
| `blockrush` | 59% | 21% | 22% |
| `blockheuristic` | 49% | 65% | 16% |

`blockheuristic` re-expresses `ai.compute_orders` and lands at parity with it, so
a flat rule list really is enough to say what the built-in says — unsurprising in
hindsight, since `compute_orders` is structurally a three-rule program. 29/400
timeouts against a ~20% natural stalemate rate for evenly matched bots at this
size.

**And it tops out exactly where the vocabulary says it must: 0% against `thinker`
and `knower`.** One order per system per turn cannot converge waves launched from
different distances so they land together, schedule a reinforcement by when a blow
lands, evacuate a doomed system, or predict a rival. Above `heuristic`,
`rusherplus` and `claudebot`; nowhere near the top of the roster.

**Why it is not merged is the audience, not the ceiling.** The feature's user is
someone interested enough to design bot behaviour but unwilling to write Python —
and `models/README.md` already reduces Python to a 30-line `decide` with a
copy-paste example. That intersection is close to empty, and what it buys the few
who are in it is a bot that loses to half the shipped roster. Against that:
~1,400 lines of core and shell, a third scene, three more dropdown entries, and a
`src`-template-beside-evaluator pairing in every spec-table entry that has to be
kept in step forever. The branch stays unmerged and undeleted; the numbers above
are the reason not to rebuild it from scratch on a hunch.

**Three findings outlive it.**

- **A vocabulary with no enemy-attack rule cannot win a game.** `blockturtle`
  first had hold / reinforce / expand-neutral / send-to-front — a
  complete-looking defensive bot that won **0 of 300** ladder games, because
  taking every enemy system is the win condition and no rule could take one. It
  went to 77% the moment one `attack_best` rule was added. An empty-handed
  program *looks* fine, which is why the editor grew a warning banner
  (`_has_win_path`) rather than a docs note.
- **Blending the two attack margins cost 13 points.** `blockheuristic` first
  folded neutrals and enemies into one `attack_best` at a split-the-difference
  1.4 and scored 35%; giving neutrals `AI_EXPAND_MARGIN` and enemies
  `AI_ATTACK_MARGIN` as separate rules took it to 48%. The heuristic gates the
  two differently for a reason and one blended number is not a substitute.
- **Nothing written to disk survives a web reload.** pygbag 0.9.3 mounts no
  IDBFS and calls no `syncfs`, and the bundle is re-unpacked from the `.apk` each
  load, so anything under `models/`, `saves/`, `games/` or `kv.json` is RAM-backed
  there. Generated Python *runs* fine — `ai.load_models()`'s `exec_module` already
  runs on every web boot — it just cannot be saved. Any future bot-authoring
  feature has to put its source of truth in `localStorage` via `webstore`, and
  that is what killed the branch's persistence phase before it started.

## Bots that aren't Python ([`bot-api.md`](../bot-api.md))

The successor idea, and a better-aimed one: instead of a second authoring
language inside the app, accept a bot in *any* language over a documented wire
protocol. The spec is [`bot-api.md`](../bot-api.md), and the schema, transport, one
ported bot and the parity test are built; this is why it is shaped the way it
is.

**Sell it on the ceiling, not on accessibility.** Writing a JSON-over-stdio loop
plus a payload decoder in Rust or Go is *more* work than editing
`models/mybot.py`, so nobody blocked by the latter is served by the former. What
it genuinely buys is depth: Python caps how far a searcher gets inside a
browser-safe budget, and native code could search well past `knower`. That is a
real prize, and it is a different feature from the one the bot maker was.

**Oracle parity is not required, by ruling.** `knower` reads `ai.STRATEGIES` and
drives `engine.end_turn` on cloned boards, and no out-of-process bot can do either
without an RPC back into the engine — which would roughly double the protocol.
That was initially read as the API's ceiling. It isn't: a tournament entrant is
*supposed* to be ignorant of its opponents' code, so `knower` is a test of the
simultaneous-resolution property rather than a standard to meet. Modelling what an
opponent might do stays fair game and remains a worthwhile thing for an entrant to
add. So: no `simulate` callback, `ai.STRATEGIES` off the wire, and rival strategy
*names* masked by default — knowing which of a published roster you face is
counter-programming, not prediction. Rival `ai_params` stay visible, being
documented-readable tuning rather than identity.

**The payload carries more than the board, and each addition has a reason.**

- **The balance knobs and the two break-even multiples.** An external bot cannot
  call `combat.edge_attacking()`, and pricing a fight off a live figure rather
  than a constant is the one thing every bot in this roster is required to do. The
  raw knobs *and* the derived multiples both go on the wire: re-deriving
  `(1+j)/(1-j)` in a second language is exactly the sort of duplicate that drifts
  with nothing failing.
- **The full setup, not a curated subset.** `nodes` and `ship_ly_per_turn`
  together move lane length over an order of magnitude ("Lane length across the
  parameter space", above), and a posted leaderboard map carries tuned knobs. A
  bot that cannot see which regime it is in cannot tune to it, and would repeat
  the mistake that section documents.
- **A per-turn seed, derived rather than drawn.** External bots cannot draw from
  `state.rng`, so they are handed a seed instead — computed from the game seed,
  the turn and the seat, *not* drawn from the stream. Drawing would make the
  engine's dice depend on which seats happen to be external; deriving keeps a seed
  reproducing the map and every battle, which is what the house rule actually
  protects.
- **A budget cap, sent as a number.** `ai.set_budget_scale`'s 100x lift cannot
  reach another process's clock, so the runner multiplies `budget_ms` by the
  manifest's `budget_scale` and sends the product. The bot never computes it, and
  `budget_ms` is the only clock read the purity contract permits.

**Two mechanical traps found while specifying it.** `bot_replay.engine_rev()`
digests `starconquest/`'s outcome modules and `models/*.py` — a compiled binary is
in neither, so without a manifest `version` on the row a recompiled bot serves its
stale cached score forever. And the failure policy has to invert the house
default: a bad strategy name falling back to `heuristic` silently is right in the
app and wrong in a tournament, where a run containing a fallback seat is not a
result and must be flagged rather than scored.

**Cost, measured.** A 24-node payload is ~5.3 KB and 136 µs to encode. Against a
150 ms budget that is nothing, which settles the schema's style in favour of keyed
objects over positional arrays. Against the *bot* it is not nothing: an entire
`compute_orders` is 9.5 µs at 18 nodes and 12.5 µs at 24, so encoding one payload
costs about eleven of them, and roughly twice what the engine spends resolving a
whole turn for one seat (~58 µs per decide-equivalent, from 3,474 decisions across
ten 2-seat 18-node games averaging 174 turns each). Hence the rule that only
external seats ever touch the wire — routing the Python roster through it would
make every batch measurement in this file slower for nothing.

**Build the schema before the transport, and the parity test before the second
bot.** A pure `botio.py` is testable with no child process in sight, and porting
one roster bot across the wire to demand *identical* orders is the only thing that
will catch a payload quietly missing a field — the same role
`test_export_round_trips_exactly` played on the bot-maker branch, and the same
failure mode: a schema gap looks exactly like a bot that plays slightly worse.

**And mutation-check that test rather than trusting it.** `bots/rusherwire`
matching `models/rusherplus` proves nothing until the comparison is shown to
fail: emptying `fleets` from the payload breaks it, and so does reversing the
order systems are listed in — the second because `rusherplus` tie-breaks with
`state.rng` inside a `min` key, so *ordering* is part of the contract and not
merely presentation. Which is also why the port is Python. Matching the original
exactly means matching its draw sequence, and the test swaps the reference's
`state.rng` for a `Random(rng_seed)` on the payload's own seed to make the two
comparable. A bot in another language cannot reproduce `random.Random` and does
not have to: the test's job is to prove the payload sufficient, not to make
determinism a cross-language requirement.

**Measured, the port plays.** `--external --ladder --trials 4` over
rusherwire/heuristic/marshal: marshal 67%, heuristic 21%, rusherwire 12%, with
rusherwire taking 38% off heuristic head to head and 0% off marshal — which is
roughly where `rusherplus` itself sits, and the point is that the wire changed
nothing about where it sits.

## External bots: the four rules

**Bots that aren't Python are subprocesses, and they compete without shipping.**
`botio.py` is the wire format (`hello` once, `turn_payload` per decision,
`orders_from` back) and `tests/botproc.py` the transport; a bot is a
`bots/<name>.bot.json` manifest naming a command, and `docs/bot-api.md` is the
protocol. Four rules hold it together:
- **`botio` is pure core and owns no process.** No `subprocess` import in the
  package, and none in the ordinary suite either — `tests/sim` imports
  `botproc` lazily, inside `--external`.
- **`bots/` lives outside `models/`, and that is load-bearing.**
  `build_web.sh` stages `models/`, and the web build is CPython on WASM: it
  cannot fork at all. So external bots run in `tests/sim` and (once decided)
  `tools/bot_replay`, never in the app or the browser, and the in-app Strategy
  dropdown stays Python. Registration is opt-in (`sim --external`) rather than
  automatic, unlike `ai.load_models()`, because `bot_replay`'s roster is
  `ai.available_strategies()`.
- **The bot's randomness is derived, never drawn.** `botio.decide_seed(seed,
  turn, pid)` hands a seat its own stream, so an external bot cannot shift the
  engine's dice and every other seat's battles roll as they did without it. It
  is the one sanctioned exception to "all randomness flows through `state.rng`",
  and it keeps what that rule protects: a seed still reproduces every fight.
- **A degraded seat is not a result.** A timeout holds for one turn; a dead bot
  or `botproc.FORFEIT_TIMEOUTS` timeouts falls the seat back to `heuristic` and
  records it (`degraded_runs`, printed by `sim`). The app's silent fallback for
  an unknown strategy name is right there and wrong in a tournament, where a run
  containing a fallback seat must never be scored or posted.

## `AiParams.aux`, the one bot-defined knob

- **`AiParams.aux` is the one bot-defined knob.** The core never interprets it
  (only the AI tab's aux slider writes it); each strategy assigns its own
  meaning. `config.AI_AUX` is `1.0` and that is the documented "untuned" value,
  so a bot's default behaviour must be what it does at 1.0 — a stale token or
  save with no `aux` key deserialises to it. Add per-bot knobs here rather than
  growing `AiParams` one field per strategy. A strategy names its knob with
  module-level `AUX_LABEL` (+ optional `AUX_RANGE`, `AUX_INT`, and `AUX_NAMES`
  naming each stop of a step-1 int knob), read by `ai.aux_spec`/`ai.aux_names`;
  `menu._ai_specs` appends that slider to `_AI_PARAMS` for the edited seat, so a
  strategy declaring nothing (the built-in heuristic, `thinker`, …) shows no aux
  slider at all. `models/knower.py` labels it *Oracle*, stops Off / Predict /
  Search, and clamps a stored value above 2 (its old 0-12 depths) to Search —
  narrowing a knob by clamping on read, never by rewriting what was stored, is
  what keeps every old link's digest. `bot_replay.aux_note` sends a named stop
  to the board as "Label: Stop", which `format.mjs`'s `botProfile` prints
  without a number. An `AUX_INT` slider stores an **int**, and `_ai_from_dict`
  preserves that — `aux` is the one field whose int/float form survives a
  decode, since `challenge_key` hashes the JSON and `12` is not `12.0`. Widen
  it and every link carrying an int aux reads as edited the moment it opens.
  The board cannot hold the distinction (its rows are browser-written — see
  "Whole-number floats" in [`core.md`](core.md)), so `verify_scores.same_setup` widens both sides
  through `_aux_widened` before hashing — drop that and every posted score with
  an aux reads as a different map.

## The `tests/sim` harness

- **`tests/sim.py` is both a demo harness and a test fixture.** Because it drives
  the pure core headlessly, the suite uses it to assert games actually terminate
  and never corrupt state (`check_invariants`). After changing `ai.py` or
  travel/combat balance, run a `--trials` batch and watch the timeout rate.
  `--film` adds the turn-playback oracle to every turn of every game (see Animated
  end of turn).
  It also hosts the two bot tournaments, sharing `_tally`/`_avg_turns`: `--swap`
  is a free-for-all (whole roster in one game, rotated through every seat via
  the cyclic `_rotations`), `--ladder` is a pairwise round-robin (`run_ladder`:
  every pair, both seatings, plus a head-to-head grid). `--external` adds the
  `bots/` subprocess bots to either (see "External bots: the four rules" above). Both default their
  roster to `ai.available_strategies()`, so a whole-`models/` ranking needs no
  arguments. `play_settings` is the third entry point — one bot through the
  human's seat on a stored `Settings`, going through `settings.build_state` so a
  posted setup's tuned knobs actually apply. It is what the leaderboard's bot
  column is made of (`tools/bot_replay.py`; see "Replaying a bot for the leaderboard" above).
  - **`play_from` is the fourth, and the only one that starts anywhere but the
    opening.** It branches a recorded match at turn N (`reconstruct` on a
    *truncated copy* — never the caller's log) and hands the seat to a bot, so a
    stored game yields a position every few turns instead of one number. The
    baseline comes free: we know what the person who was there then took.
    `sim.positions` picks the turns and `tools/position_suite.py` drives it over
    a corpus. Read its three numbers separately — *faster* is the only paired
    comparison, *recovered* (games the person lost, which no score can carry) has
    no baseline at all, and both are a direction rather than a verdict, since the
    sample is whatever games happen to exist. See `docs/design/bots.md`.
  - **Sweep the speed and node knobs, not just their defaults.** `WORLD_SIZE` is
    fixed up to a standard board, so a lane's length in light-years rises as the
    node count falls (past `config.STANDARD_MAX_NODES` the box grows instead, and
    lanes hold at a full standard board's spread), and
    `config.SHIP_LY_PER_TURN` (menu slider, 1-30) rescales every lane on top —
    lanes run 14-36 turns at 12 nodes and 1 ly/turn, and nearly all of them are
    a single turn from 18 ly/turn up. Any margin keyed off travel distance is therefore live in part of
    that space and unreachable in the rest, so a batch at the default 6 ly/turn
    measures one regime out of three and a knob can look like dead code purely
    because of where it was measured. See `docs/design/bots.md`.

## Per-seat AI and pricing a fight: the rules in full

**The engine never imports the AI.** The decision function is injected as the
`decide` parameter to `end_turn`; `main.py` and `tests/sim.py` pass `ai.decide`,
a per-seat dispatcher that routes each seat to its named strategy
(`ai.STRATEGIES`, keyed by `Player.ai_strategy`; `ai.decide` falls back to the
built-in `"heuristic"` for any unknown name, so a stale/missing strategy never
crashes). Keep this inversion — it is why the core has no AI dependency, and it is
the seam for user-written AIs (`ai.register(name, fn)`, `fn(state, pid) -> list[Order]`).

- **AI is per-seat and pluggable.** Each `Player` carries `ai_strategy` (a key
  into `ai.STRATEGIES`) and `ai_params` (`model.AiParams`, defaults mirroring
  the `config.AI_*` constants). `ai.compute_orders` reads the seat's params, so
  seats can play to different profiles; the menu's AI tab edits them per seat.
  `Settings` mirrors both per-seat lists (`ai: list[AiParams]`, `ai_strategy:
  list[str]`, indexed by seat-1), and `build_state` stamps each non-neutral
  `Player` with its `seat_strategy(...)` and a copy of its `seat_params(...)`.
  - **A seat may be left to the seed.** `settings.RANDOM_STRATEGY` (`"random"`,
    the last entry in the menu's Strategy dropdown) is not a key into
    `ai.STRATEGIES` at all — `settings.resolve_strategy` replaces it with a real
    bot inside `build_state`, so nothing downstream ever sees the placeholder.
    That is why it is resolved here rather than by a `models/` dispatcher bot:
    `knower._model_for` asks a module `is_oracle_seat(player)` and a dispatcher
    could not answer, having no seed on a `Player` — resolving one layer earlier
    keeps every oracle, `botio`'s seat reveal and the leaderboard's bot column
    looking at the bot that is really deciding, and so keeps the whole roster
    eligible. The pick is **derived, never drawn** (`random.Random(f"{seed}:
    strategy:{pid}")`, the rule `botio.decide_seed` follows): leaving a seat to
    chance must not shift `state.rng`, or the same seed would fight the same map
    differently depending on how many seats were left to it. A seed therefore
    reproduces the opponents as surely as it reproduces the map, which is what
    lets a challenge link on one be raced fairly. The pool is the fixed
    `settings.RANDOM_POOL`, not the loaded roster, so every build deals a seed
    the same bots; a new bot joins it only on purpose, since that re-deals every
    random map (`docs/design/core.md`). `Settings` keeps `"random"`, so
    a shared link stays a mystery to its recipient and the leaderboard's
    opponent chip (`sc_bots`, off `settings_json`) reads `random` rather than
    the bot that played — the setup's rule, not its outcome. Disclosing the
    resolved names would go on `Challenge` (excluded from `challenge_key`), not
    on `Settings`, which would move every digest ever shared. The win overlay
    *does* reveal it (`render._winner_label`, "Verdant (Knower) wins!") — the
    payoff, and free, since there is no turn left to play with the knowledge. It
    reads `Player.ai_strategy` directly, so it needs no `ai` import and names the
    resolved bot rather than the placeholder; neutral and any human seat (a
    claimed one included) are excluded, since nothing decided for them.
  - Those fields are readable for *every* seat — see `models/knower.py` for
    what that makes possible. Any such bot must keep three rules: never call
    `ai.load_models()` from inside a model (it re-`exec_module`s every file,
    including yours, unguarded); read `ai.STRATEGIES` *lazily* inside `decide`,
    since files load in sorted order and the registry is incomplete at your
    import time; and draw **nothing** from `state.rng` (`tests/test_knower.py`
    asserts both of the latter).
  - **Attack margins are measured against the *effective* garrison.**
    `ai._frontier_order` multiplies a target's ships by
    `config.DEFENDER_ADVANTAGE` before applying `expand_margin`/`attack_margin`,
    because that is what a fleet actually has to out-fight
    (`combat._apply_advantage`). Against the raw count the AI stops expanding
    entirely at a high setting. Identity at the 1.0 default — see "Defender advantage and the AI" above
    for why the knob's top end still turtles regardless.
  - **A bot prices a fight with `combat.edge_attacking()` /
    `edge_defending()`, never a constant.** They are the break-even multiples —
    what a fleet must beat the garrison by, and what a garrison must beat the
    incoming force by, to win the *worst* roll — read live off
    `config.COMBAT_JITTER` and `config.DEFENDER_ADVANTAGE`, both of which are
    menu sliders. They move in opposite directions, since the advantage belongs
    to whoever holds the system. Most `models/` bots' margins are pads/absolutes
    over those, with the edge's jitter half floored at each bot's `TUNED_SWING`
    (the swing it was fitted at), so a knob can only ever *raise* a margin above
    its measured figure — nothing in the roster moves at the 0.10/1.0 defaults.
    Clearing an edge is not a promise of capture: ties break to the defender and
    matched forces annihilate to neutral, hence the `target.ships + 1` floors.

- **A predicting bot advertises itself** with `IS_ORACLE = True` and, when
  prediction is per-seat rather than per-module, `is_oracle_seat(player)` —
  which callers prefer over the flag (`knower.is_oracle_seat` is "not Off", so
  its Off seats are predicted for real, and trusted, instead of approximated).
- **A bot can warn about a setup before it starts.** A module-level
  `setup_warning(settings, seats) -> list[str]` (read by `ai.setup_warning`,
  collected per strategy by `settings.setup_warnings`) raises the menu's
  "This setup may play slowly" confirm on Start, and on the play-by-post
  roster's Confirm — not the first press, since which seats are bots is only
  known once the roster is. `models/knower.py`'s is fitted from measured
  per-ply cost (`ply_ms`) and fires only when `SEARCH_BUDGET_S` would cut a
  Search seat's horizon (the setup's longest lane plus `LANE_CUSHION`, off the
  same lane survey the Advanced tab reports) short of its own longest lane, or
  a turn's knower thinking passes `WARN_TURN_MS`. It prices turn one, so with
  ship-speed growth on it also names the turn the clipping ends
  (`_sees_lanes_from`); see "Cost per decide" in [`knower.md`](knower.md).

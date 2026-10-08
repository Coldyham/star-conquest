# reader design notes

**Stage 1 only: a model, measured; not a bot.** `models/reader.py` has no
`decide`, so `ai.load_models` imports it and registers nothing, and it is in no
ladder, pool or bot column. It is the fourth proposal in
[`bots.md`](bots.md), "New bot families (proposed 2026-10-05)": a model of each
rival read off the board, kept from turn to turn, to be built into actuary's
ledger if it predicts well enough. It did not pass the gate set for that (see
"The gate"). Roster-wide rules and the measurement checklist are in
[`bots.md`](bots.md). Index: [`../README.md`](../README.md).

## Why the oracle flag does not make memory safe

The proposal was to mark reader `IS_ORACLE`, so knower would model its seat with
`_blind` rather than run its `decide`, and reader could then keep a model of
each opponent between turns. The flag does stop knower. It does not stop the
other callers that run a bot's `decide` on something other than the live board
in turn order:

- `tools/par_search.py` steps many cloned branches of one game, turns moving
  back and forth.
- `tools/bot_distance.py` and `tools/position_suite.py` hand a bot isolated
  positions from many games, interleaved.
- `tests/test_ai.py::test_a_gentler_jitter_never_thins_a_tuned_margin` calls
  every model three times a turn on one live board, and asserts the answers
  match.
- A rewind brings back a board whose memory holds turns that no longer
  happened. Rewind, retry and restart do not re-import models.
- `ai.load_models()` re-imports every model file, wiping module state. The app
  calls it on a new game, a resume, opening a replay or a play-by-post match,
  and in the menu.
- Play-by-post resolves each turn on whichever device happens to, and
  `tools/check_pbp.py` resolves one turn twice in a process and compares
  digests.
- `GameState` has no match id (`match_id` is on `GameLog`), and the same seed
  recurs in both seatings of every ladder pair.

So memory is keyed by the game's path, not by "the current game".

## The memo tree

Every board reader sees is a node, found by content: the turn, each system's
owner, ships and production progress, and every fleet in flight. A node's key
also carries a digest of the map (adjacency, production) and of the rules that
change what a board means (jitter, defender advantage, neutral production, in-lane
battles, speed growth). Seat strategies are left out. The model is a function of
boards alone, so two games that share boards share a path.

A new node's parent is a stored node one turn earlier that `_follows` accepts.
That check is strict:
- every fleet still flying moved one step;
- every fresh fleet left a system its owner held, with the ships to send;
- a system no fleet could have reached unseen (nothing landing, no one-turn
  lane) kept its owner, and changed by exactly its production less its
  launches.

Several candidates break on the smallest board key, so the pick is
deterministic. The node's models are its parent's plus what that turn showed.

The consequences, each pinned in `tests/test_reader.py`:
- the same board finds the same node, so repeated calls and copies answer
  alike;
- two branches from one board get their own nodes and models;
- a rewound board finds its original node;
- a board with no known parent (another game, a skipped turn, an evicted parent)
  starts from the prior.

Nodes are held least recently used first, `NODE_CAP` 512, and eviction can only
cause a cold start. In live self-play at 3, 6 and 18 ly/turn no board after the
first starts cold.

**What it costs.** Memory lost to a re-import, a resume or a play-by-post
device change starts cold from that turn. Output is a pure function of the path
the process has seen, and of the board alone when cold, so `check_bot`'s
reproducibility probe (fresh boards at turn 0) still passes.

## What a turn shows

A rival decides on the board as the turn began, and its decision is on the next
board: fresh fleets (`turns_remaining == turns_total - 1`). A one-turn fleet
lands inside the turn it leaves and is never on any board. Its ships are
recovered as what is missing from the source: production lands before arrivals,
so a source nothing landed on holds exactly its garrison plus production less
its launches. With one one-turn lane the residual has a destination. With
several it counts as launched, destination unknown.

For each rival system with ships, and each neighbour it does not hold, one
record: the ratio of its garrison to the neighbour's effective garrison on
arrival (garrison, hulls built by then, the holder's own fleets landing by then,
times `DEFENDER_ADVANTAGE`), split by neutral or player target and by whether
hostile ships were inbound to the source. It is a strike if ships went there,
else a pass. Also:
- the share of the garrison a strike sent;
- at a frontier system that launched, what it kept against its largest
  adjacent enemy garrison;
- when hostile ships inbound exceeded its garrison times the advantage,
  whether it left.

## The model and the prior

Counts per ratio bin, read as a strike chance with `PRIOR_WEIGHT` pseudo-counts
of the prior and fitted monotone (pool adjacent violators). `predict` gives each
rival source a chance per neighbour, scaled to sum to at most one.

A strike's size is two counts. One is whether it sent all-in (`ALL_IN`, 90% of
the garrison or more). The other is, for a strike that did not, what it sent
against the target's effective garrison. A predicted strike is the all-in share
times the garrison, plus the rest times the average sized ratio times the
target, capped at the garrison (`strike_ships`). The first version sized every
strike as the average share of the garrison; see "Sizing a strike to its
target" for why it changed.

The prior is the roster pooled in self-play: all four cells, seeds 1001-1040,
160 games (`tools/reader_check.py --fit-prior`). It was re-fitted on 2026-10-08
for actuary's `MIN_GAIN` of 1e-9 ([`actuary.md`](actuary.md)): 22,261 strikes
and 202,052 passes.

## The prediction check (`tools/reader_check.py`)

reader never plays. Roster games are played with every seat on its own bot, in
3-seat lineups drawn from claudebot, thinker, marshal, actuary, knower
(Predict), rusherplus and heuristic. Before each turn, for every seat on a
contested position, four predictors say what the rivals will launch at it:
- **none**: nobody launches (actuary's projection);
- **all**: every adjacent rival garrison comes whole (actuary's risk reach);
- **prior**: reader's model without memory;
- **reader**: reader's model with memory.

The turn is played and the rivals' real orders score them:
- **Brier**: per (rival source, our target) pair, strike chance against whether
  one came;
- **MSE**: per (rival, our target), the expected ships against the ships that
  came;
- **waste** and **miss**: the two sides of that gap.

Intervals are 95% bootstraps over games.

**Two measures in the plan were wrong and were changed before the first
reading:**
- **The ships metric was an absolute error.** chance × ships is a mean, and an
  absolute error rewards the median. With strikes on under 20% of pairs the
  median is 0, so "none" wins it by construction (the 4-game smoke run showed
  exactly that). It is a squared error now.
- **The separation statistic was "the lowest ratio with a strike more likely
  than not".** A source with several non-own neighbours strikes at most one,
  so a per-pair chance rarely reaches 0.5, and every bot read the same
  placeholder value. It is now the curve's value at ratios 1.0, 1.5 and 2.0.

### Results

The stage-1 model, under actuary's old `MIN_GAIN` of 0.05. Four cells, seeds
1-60 and then fresh seeds 61-120, 240 games each run, load average under 1. Brier and MSE over all rivals, none / prior / reader:

                     Brier, seeds 1-60          Brier, seeds 61-120
    18 nodes, 6 ly   0.132 / 0.095 / 0.090      0.118 / 0.086 / 0.080
    24 nodes, 3 ly   0.087 / 0.073 / 0.069      0.087 / 0.073 / 0.069
    40 nodes, 6 ly   0.169 / 0.114 / 0.105      0.157 / 0.106 / 0.099
    18 nodes, 18 ly  0.109 / 0.086 / 0.074      0.144 / 0.096 / 0.092

                     MSE, seeds 1-60            MSE, seeds 61-120
    18 nodes, 6 ly    68 /  53 /  44            170 / 136 /  91
    24 nodes, 3 ly    85 / 211 /  91            124 / 194 / 127
    40 nodes, 6 ly   323 / 232 / 183             77 / 127 /  83
    18 nodes, 18 ly   22 /  85 /  21             29 /  23 /  20

"all" is far behind both ways (Brier 0.83-0.91, MSE in the hundreds to
thousands), which is the cost of actuary's risk term read as a forecast. It
predicts 8-37 ships per contested (rival, target) pair at targets nobody struck,
against 0.6-2.0 for reader.

Against marshal, actuary and thinker separately, per run, each predictor's
difference from reader with its interval:

- **Will this source strike this target (Brier).** reader beats all three
  baselines in 35 of 36 comparisons on seeds 1-60 and 36 of 36 on seeds
  61-120. The exception is marshal against the prior at 18 ly
  ([-0.013, +0.001]). Memory helps everywhere, by 4-13% of the prior's score,
  and the gain grows with turns since contact (at 18 ly, seeds 1-60, 30+ turns
  in: prior 0.074, reader 0.056).
- **How many ships come (MSE).** 28 of 36 in each run. Five failures a run are
  against "none" and three against the prior. Over both runs, 8 of the 16
  failures have thinker as the rival and 5 actuary; 6 are in the 24-node,
  3 ly cell. The send size is the suspect: a
  strike is predicted at the average send share times the whole garrison, but
  the roster sizes a strike to its target, so a big garrison next to a small
  target is predicted to send far too much.

### What the model can tell apart

Median over rivals at turn 60, seeds 61-120. Seeds 1-60 read the same to
within 0.04 in every column except evacuation, which moved by up to 0.16
(heuristic 0.42, marshal 0.65):

                strike on a player at       neutral
                1.0    1.5    2.0           at 1.5    send   guard   evac
    actuary     0.05   0.20   0.32          0.30      0.88   0.05    0.57
    claudebot   0.04   0.16   0.32          0.35      0.68   0.55    0.28
    heuristic   0.04   0.16   0.32          0.11      0.74   0.45    0.26
    knower      0.08   0.16   0.32          0.36      0.81   0.05    0.55
    marshal     0.05   0.16   0.32          0.33      0.84   0.55    0.56
    rusherplus  0.05   0.16   0.32          0.33      0.88   0.05    0.52
    thinker     0.04   0.14   0.32          0.32      0.73   0.35    0.62

- **The strike curve does not tell the bots apart.** Every bot reads about 0.16
  and 0.32. A per-pair chance mixes "does this source strike" with "which of
  its targets", and the second dilutes the first by the number of neighbours.
- **The guard and evacuation columns do, and they agree with the code.**
  marshal's guard reads 0.55, and its `FRONTIER_GUARD` is 0.55. actuary,
  knower and rusherplus, which keep no standing guard, read 0.05. claudebot,
  which never evacuates ([`marshal-pricing.md`](marshal-pricing.md), "Garrisons
  run away"), reads 0.26 and 0.28, pulled up from 0 by the prior: the lowest on
  seeds 1-60, and 0.02 above heuristic on 61-120.

## Sizing a strike to its target, and splitting the strike from the target

The first reading's ship error pointed at the size, and its strike curve could
not tell the bots apart. Two changes were tried (2026-10-08), after actuary's
`MIN_GAIN` dropped to 1e-9 ([`actuary.md`](actuary.md)), so every figure in this
section is under that actuary. All of them are on fresh seeds 121-180 and
181-240, with the prior re-fitted on seeds 1001-1040 for each form.

- **Sizing a strike to its target**, as in "The model and the prior".
- **Splitting the strike in two.** First, does a source strike anything, read
  against the best ratio it has on offer and split by whether a player's system
  is next door. Then, which target, from eight classes: neutral or player, the
  best ratio on offer or not, a ratio of at least 1 or not. Each class is
  weighted by how often it was struck when offered.

Three forms on the same seeds: the stage-1 model (from a worktree, prior
re-fitted), both changes together, and sizing alone. All-rivals figures:

                       Brier, reader             ship error (MSE), reader
                       stage 1  both  sizing     none   stage 1  both  sizing
    seeds 121-180
    18 nodes, 6 ly     0.0779  0.0792 0.0779    193.5   102.3  103.0  102.5
    24 nodes, 3 ly     0.0641  0.0649 0.0641     65.2    65.5   60.1   57.7
    40 nodes, 6 ly     0.1026  0.1051 0.1026    105.1   183.8   78.4   73.9
    18 nodes, 18 ly    0.0937  0.0948 0.0937     46.6    41.5   37.7   36.8
    seeds 181-240
    18 nodes, 6 ly     0.0776  0.0789 0.0776     45.1    38.0   39.9   37.8
    24 nodes, 3 ly     0.0689  0.0701 0.0689     83.8    79.4   76.3   70.8
    40 nodes, 6 ly     0.1017  0.1044 0.1017    420.2   304.2  272.3  262.0
    18 nodes, 18 ly    0.0752  0.0778 0.0752     55.8    34.7   36.7   36.8

- **The split is worse.** Its strike chance is behind stage 1's in all 8
  cell-runs. Scored against sizing alone per rival, it is behind in all 24
  readings of marshal, actuary and thinker, and in 16 of them outside the
  interval. Its prior is worse too, so the structure costs and the fit does not
  rescue it. **Deleted.** The two steps also did not separate the bots: every
  rival read about the same chance to strike at a given best ratio, and a
  player-over-neutral pick weight of 0.98-1.07.
- **Sizing is the gain.** With the strike chance unchanged (it is stage 1's,
  bit for bit), ship error falls in 5 of the 8 cell-runs, is level in 2 and is
  2 points worse in 1. The fall is largest at 40 nodes (184 to 74, 304 to 262)
  and 24 nodes (66 to 58, 79 to 71). **Kept.**

What it can tell apart, at turn 60, median over rivals, seeds 181-240. Seeds
121-180 agree to within 0.04, except thinker's guard (0.20 there):

                strike on a player at       neutral   all-in  sized /
                1.0    1.5    2.0           at 1.5    share   target   guard   evac
    actuary     0.06   0.19   0.29          0.31      0.71    1.27     0.05    0.62
    claudebot   0.05   0.15   0.28          0.35      0.25    1.17     0.55    0.30
    heuristic   0.05   0.15   0.28          0.10      0.19    1.42     0.45    0.35
    knower      0.09   0.20   0.29          0.35      0.55    1.09     0.05    0.56
    marshal     0.05   0.15   0.28          0.34      0.64    1.30     0.55    0.62
    rusherplus  0.06   0.15   0.28          0.33      0.62    1.36     0.05    0.52
    thinker     0.04   0.15   0.28          0.34      0.28    1.18     0.05    0.66

The all-in share splits the roster in two. actuary, marshal, rusherplus and
knower send all-in on most strikes. thinker, claudebot and heuristic size
theirs to the target.

## The gate

Set before the first run: reader beats none, all and prior on Brier and on the
ships metric, outside the intervals, against marshal, actuary and thinker, in at
least 3 of the 4 cells, and the fitted parameters visibly separate the roster.

**It did not pass.** The stage-1 model read 1 of 4 cells on seeds 1-60 and 0
of 4 on 61-120. What failed was the ships metric and the strike curve's
separation. The strike-or-not prediction with memory cleared every baseline
almost everywhere, and the guard and evacuation estimates separated the bots
and recovered their constants. So memory buys something, and reader does not
reduce to a stateless model.

**With strikes sized to their target, it still does not pass:** 0 of 4 cells on
both seeds 121-180 and 181-240.
- Brier clears 35 of 36 comparisons in each run, and ship error 27 and 29.
- 16 of the 18 failures are ship error, and in 13 of those 16 the point
  estimate favours reader. They are mostly intervals that cross zero by a little
  (marshal against the prior at 18 ly: -143.50 [-304.73, +0.27]), not a model
  that predicts worse.
- Ship errors are heavy-tailed. One big strike missed or invented dominates a
  game, so 60 games a cell resolve them poorly.
- The comparisons against "prior" are harder than at stage 1, since the prior
  now sizes strikes too.

The gate also asked that the fitted parameters separate the roster. The guard,
evacuation and all-in share do, and the strike curve still does not.

**Open, not rejected.**
- Whether the remaining ship-error failures are a lack of power. That needs
  more games a cell, which would change the gate after seeing results, so it is
  a decision for whoever owns the gate.
- A strike curve that tells the bots apart.

Building on actuary's ledger (stage 2) waits on either.

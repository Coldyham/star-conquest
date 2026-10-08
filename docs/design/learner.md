# learner design notes

**A model of each rival, and a bot built on it.** `models/learner.py` reads each
rival's habits off the boards it has seen and keeps them from turn to turn. As a
bot it plays actuary's ledger on a board with the launches it expects added (see
"As a bot"). It is the fourth proposal in [`bots.md`](bots.md), "New bot families
(proposed 2026-10-05)". The model did not pass the gate set before building the
bot (see "The gate"), and the bot was built anyway, on 2026-10-08. It is not in
`settings.RANDOM_POOL` or `ai.LADDER_ORDER`. Roster-wide rules and the
measurement checklist are in [`bots.md`](bots.md). Index:
[`../README.md`](../README.md).

## Why the oracle flag does not make memory safe

The proposal was to mark learner `IS_ORACLE`, so knower would model its seat with
`_blind` rather than run its `decide`, and learner could then keep a model of
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

Every board learner sees is a node, found by content: the turn, each system's
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

The consequences, each pinned in `tests/test_learner.py`:
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
160 games (`tools/learner_check.py --fit-prior`). It was re-fitted on 2026-10-08
for actuary's `MIN_GAIN` of 1e-9 ([`actuary.md`](actuary.md)): 22,261 strikes
and 202,052 passes.

## The prediction check (`tools/learner_check.py`)

learner never plays. Roster games are played with every seat on its own bot, in
3-seat lineups drawn from claudebot, thinker, marshal, actuary, knower
(Predict), rusherplus and heuristic. Before each turn, for every seat on a
contested position, four predictors say what the rivals will launch at it:
- **none**: nobody launches (actuary's projection);
- **all**: every adjacent rival garrison comes whole (actuary's risk reach);
- **prior**: learner's model without memory;
- **learner**: learner's model with memory.

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
1-60 and then fresh seeds 61-120, 240 games each run, load average under 1. Brier and MSE over all rivals, none / prior / learner:

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
against 0.6-2.0 for learner.

Against marshal, actuary and thinker separately, per run, each predictor's
difference from learner with its interval:

- **Will this source strike this target (Brier).** learner beats all three
  baselines in 35 of 36 comparisons on seeds 1-60 and 36 of 36 on seeds
  61-120. The exception is marshal against the prior at 18 ly
  ([-0.013, +0.001]). Memory helps everywhere, by 4-13% of the prior's score,
  and the gain grows with turns since contact (at 18 ly, seeds 1-60, 30+ turns
  in: prior 0.074, learner 0.056).
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

                       Brier, learner             ship error (MSE), learner
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

## Predicting people (`--logs public`)

The case for learner over knower is a person, whose code knower cannot run. A
recorded game holds every seat's orders for every turn, so this can be scored
without playing. `--logs public` replays the logs a posted leaderboard score made
public (`public_replays`, read with the board's publishable key). On every turn
the person played by hand, each bot seat next to them predicts their launches,
and the prediction is scored against what the log says they did.

A fifth predictor joins the four: **knower**'s guess at a person, its blind plan
for their seat (`_blind`), read as certain. That is not how knower uses it.
`TRUST_HUMAN` is off, so the guess may only raise a threat, never relax a guard,
and launches it predicts are added back to the person's garrisons. The column
measures the forecast knower has to hand, not knower's play.

141 logs (of 145 under the current rules, 157 public) with 10 or more hand
turns, 24,359 (source, target) pairs, strikes on 10.6% (2026-10-08):

                 none     all     prior   learner   knower
    Brier       0.106    0.894    0.091    0.088    0.183
    ship error  130.5   1645.3    110.6    106.9    224.6

learner minus each, 95% interval over games:

    Brier       none -0.018 [-0.023,-0.015]   prior -0.0033 [-0.0041,-0.0025]
                knower -0.095 [-0.107,-0.084]
    ship error  none -23.6 [-43.7,-9.2]       prior -3.7 [-9.0,+0.8]
                knower -118 [-216,-49]

- **Memory reads a person, by a little.** Against the prior it gains 3.6% of
  Brier, clear of zero. That is at the low end of the 4-13% it gains on roster
  bots. The gain grows with turns since contact (30+ turns in: prior 0.089,
  learner 0.085). On ship error against the prior the interval crosses zero.
- **knower's guess at a person is worse than assuming they hold.** Its Brier,
  0.183, is behind "none" (0.106) and gets worse the longer the game runs
  (0.146 in the first 5 turns after contact, 0.199 from 30 on). So keeping
  `TRUST_HUMAN` off is right, and a calibrated forecast of a person is
  something knower does not have.
- **On small maps the ship error does not separate from "none".** Of the 141
  games, 87 are on 11 systems or fewer. There learner's Brier still clears every
  baseline, but its ship error against "none" is -2.4 [-6.0, +3.3]. On the 54
  larger maps every comparison clears except ship error against the prior.

What the model reads off people, at the end of each log, beside the bots in the
same games (median over seats):

                  all-in   sized /
                  share    target   guard   evac
    human (141)    0.66     1.52    0.05    0.35
    marshal (104)  0.63     1.35    0.05    0.68
    heuristic (56) 0.31     1.29    0.15    0.11
    knower (46)    0.64     1.15    0.05    0.65

The person sends all-in about as often as marshal and knower do, overkills a
sized strike by more than any bot, reads as keeping no standing guard, and stays
in a doomed system about twice as often as marshal does. See the caveats on the
guard and the overkill. marshal's guard reads 0.05 here where it
read 0.55 in self-play: these are short games on small maps, and the guard is
only recorded at a frontier system that launched.

**Caveats.**
- Rows carry no identity, and by the author's account almost all 141 games
  are theirs. So this measures how well learner reads one player, not people.
- The strike curves read the same for people as for every bot (0.17 at 1.5,
  0.28 at 2.0), as in self-play.
- **The guard ignores relief.** It is what a frontier system that launched kept
  home, against its largest adjacent enemy, with no account of our own ships
  landing there or able to reach it before an enemy could. The author's own
  rule is to empty a system when reinforcements are inbound or near enough to
  beat the enemy there. That would read as "no standing guard" here. It fits
  the earlier finding that the person loses fewer systems than bots do
  ([`marshal-flow.md`](marshal-flow.md), "What the board's human wins say").
  Untested.
- **The overkill may be the endgame.** "Sized / target" is, for a strike that
  did not send nine-tenths of its garrison or more, the ships sent over the
  target's effective garrison on arrival. Every posted score is a win, so the
  logs include the closing turns, where a winning side has force to spare.
  Untested.

## The person's habits (`tools/human_habits.py`)

Three questions the reading above raised, answered from the same posted games
(142 with 10+ hand turns, 140 of them won by the person). Roster self-play is
alongside them, since a bot that lost to the person is no baseline: 240 games
on 7 systems/2 seats, 11/2, 11/3 and 18/3 at the default settings, seeds 1-60,
23 hitting 400 turns. Every share leaves neutrals out unless it says otherwise
(2026-10-08).

**A replayed log rewrites `config`.** `replay.reconstruct` goes through
`build_state`, which writes that game's balance knobs into `config`. A worker
that replays a log and then plays self-play plays it under the log's settings,
so the tool resets to the defaults before each self-play game. The first draft
did not, and its self-play figures moved from run to run.

### Which share marks the endgame

The person's share of players' income, of players' ships, and of the whole
board's income (neutral systems counted), turn by turn:

                               players' income   players' ships   whole-board income
    first to reach 2/3 wins          90%               99%               95%
      (self-play, n 224/224/210)
    median turns left after          41                26                28
    reaches 2/3 at (person,          0.70              0.78              0.78
      share of the game)
    falls below half after,          10                 0                 3
      person's games
    never reaches 2/3                 2                 2                27

- **Players' income crosses first** (84 games to 31 at 2/3), about a tenth of a
  game ahead of ships. It is the earlier warning, not a reliable one: the first
  seat to 60% of it wins 73% of self-play games, against 91% for ships.
- **Players' ship share at 2/3 is the marker of a won game.** 99% at 2/3, and
  100% at 75% (221 games). In the person's 142 games a lead past 60% of ships
  fell back below half twice, and never past 2/3.
- **Whole-board income cannot be the marker.** Its denominator is larger, so it
  never crosses first, and 27 of the person's games never reach 2/3 of it,
  since they are won by elimination with neutrals standing.
- **At 50% the players-only shares mean nothing.** Every two-seat game starts at
  exactly half, and income swings during the land-grab, where whole-board
  income does not trip.

### Waves: the overkill is the endgame

Everything one side lands on a target it does not hold on one turn, over the
target's effective garrison on arrival, median, by the side's share of ships:

                      under 1/2   1/2 to 2/3   over 2/3
    person              1.38        1.63         1.79
    actuary (won)       1.50        1.48         1.33
    marshal (won)       1.56        1.48         1.67
    thinker (won)       1.43        1.43         1.50
    knower (won)        1.33        1.25         1.00

A whole wave, not one source's share, since several bots split a strike across
sources and one source's share understates what lands. Before the person holds
half the ships, their waves sit inside the winning bots' range (1.33-1.56). The
overkill the model read off the person in "Predicting people" is the closing
turns of won games, as the author suspected.

### Relief: emptying a frontier system with cover

A voluntary frontier empty sends 90% or more of a garrison with no hostile fleet
inbound bigger than it. It is *covered* when enough of the side's ships can land
before the earliest enemy to beat the threat (hostile ships inbound plus the
largest adjacent enemy garrison, over the advantage). Either they are already
flying there (*inbound*), or a neighbour that is not launching this turn can
get there first (*reachable*). Lost means lost within 5 turns:

                     empties   covered enough      lost within 5 turns
                               inbound  reachable  covered   short   uncovered
    person             1936      29%      16%       1-7%      14%       30%
    actuary (won)      1070      14%       8%       6-14%     40%       57%
    knower (won)        636       8%       7%       4-12%     50%       76%
    marshal (won)       165      55%       7%       6-9%      38%       54%
    rusherplus (won)    176      41%       9%       5-12%     22%       55%

The author's own rule is to empty a system when relief is inbound or can get
there before the enemy, and it shows. The person empties often and has it
covered 45% of the time, where actuary and knower manage 22% and 15%. Covered,
the systems almost never fall. The person's short and uncovered empties also
fall far less often than any winning bot's (14% and 30%, against 22-50% and
54-76%). The opponents in the person's games are bots losing to them, which may
punish an exposed system less than a winning bot would. marshal rarely empties
a frontier system voluntarily at all (165 times in its won games, against
actuary's 1070), and when it does it is usually covered.

**A bot could borrow the reachable half.** actuary's projection already counts
its own fleets landing on a system, so cover that is inbound is priced. A
neighbour that could get there first is not.

### Frontier losses

Systems lost per 100 frontier system-turns held: the person 3.0; in self-play
the winning seats heuristic 2.3, claudebot 3.0, thinker 4.1, marshal 4.4,
rusherplus 4.4, knower 4.5, actuary 5.0. The person's rate is at the low end of
a winning bot's. Per game they lose far fewer ([`marshal-flow.md`](marshal-flow.md),
"What the board's human wins say": 3.5 against marshal's 25) mostly because
they end games in about a third of the turns. Counted over every system they
hold rather than the frontier, the rate reads 0.75, but that measures the size
of a winner's interior.

## The gate

Set before the first run: learner beats none, all and prior on Brier and on the
ships metric, outside the intervals, against marshal, actuary and thinker, in at
least 3 of the 4 cells, and the fitted parameters visibly separate the roster.

**It did not pass.** The stage-1 model read 1 of 4 cells on seeds 1-60 and 0
of 4 on 61-120. What failed was the ships metric and the strike curve's
separation. The strike-or-not prediction with memory cleared every baseline
almost everywhere, and the guard and evacuation estimates separated the bots
and recovered their constants. So memory buys something, and learner does not
reduce to a stateless model.

**With strikes sized to their target, it still does not pass:** 0 of 4 cells on
both seeds 121-180 and 181-240.
- Brier clears 35 of 36 comparisons in each run, and ship error 27 and 29.
- 16 of the 18 failures are ship error, and in 13 of those 16 the point
  estimate favours learner. They are mostly intervals that cross zero by a little
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

## As a bot

`decide` finds this board's node (so the model keeps up), then copies the board
and puts each predicted launch at one of our systems onto the copy as a fleet of
its expected size, chance times ships, rounded. actuary's ledger plans against
that copy at its planned opening (`actuary.PLANNED`, whatever learner's own `aux`
says). The seat's `aux` is learner's *Trust* knob:

- **Off (0)**: nothing is added, so the seat plays exactly as actuary. Checked
  against actuary at Planned on 1,507 decisions (12, 18 and 40 systems, four
  seeds): no difference.
- **Raise (1, the default, and anything above)**: the fleets fly, their sources
  keep their ships. A prediction can only add a threat. This is knower's rule
  for an untrusted seat.

Two stops that also thinned the sources were built, measured and deleted; see
"Trusting a prediction". Nothing stored names them: they never left the branch.

It advertises `IS_ORACLE` and `is_oracle_seat` (always true), so knower models a
learner seat with `_blind` rather than run a `decide` whose answer depends on what
it remembers (`test_knower_never_runs_learners_decide`). It draws nothing from
`state.rng`. With actuary missing it falls back to the heuristic. `decide_ms` is
actuary's plus `READ_MS` (1 ms): over actuary's own decide learner adds a median
0.35 / 0.56 / 0.81 ms and a 75th percentile of 0.51 / 0.77 / 1.25 ms at 18 / 40 /
80 systems, timed at load 3.4, so read high.

### Against actuary

Each stop head to head with actuary, both seatings, seeds 1-50 (100 games a
cell, timeouts excluded), learner's win rate. Trust and Sure are the deleted stops
of the next section:

                    12 systems   18 systems   40 systems
    Off                50%          50%          50%
    Raise              48%          44%          51%
    Trust              45%          47%          41%
    Sure               47%          43%          49%

Off reads exactly 50%, the deterministic null. At 100 games a cell the interval
is about ±10 points, so Raise cannot be told from actuary. The predicted launches
do not make actuary plan better against actuary. That is what the gate
predicted: the model forecasts whether a strike comes, and not well enough how
big.

### Trusting a prediction (built, measured, deleted)

Raise never lets a prediction thin a rival's source, so the ledger never prices a
strike at a garrison that has left. Two ways of letting it were tried.

**Trust: thin every source by its expected launch.** Each fleet was launched on
the copy (`engine.apply_order`), deducting chance times ships from its source.
That lost: 44% against actuary pooled over 300 games, against Raise's 48%. An
expected value is the wrong thing to deduct. About 60% of the roster's strikes
send the whole garrison (see "Waves" and the all-in share above), so a source
mostly sends nearly everything or nothing. A calibrated 30% leaves the garrison
whole seven times in ten, and a strike priced against the thinned one fails.

**Sure: trust a confident call, once the model has earned it against that rival.**
The author's proposal: start at Raise and trust more as the model proves itself.
How often the model is confident, and how often it is right when it is, over
76,991 predictions at 18 and 40 systems (seeds 1-40, 3 seats, every roster bot as
a rival):

    chance        share of predictions   came true
    under 0.1           63.2%              4.2%
    0.3-0.4              7.9%             43.1%
    0.5-0.6              1.9%             60.9%
    0.7-0.8              0.45%            82.0%
    0.8 and up           0.28%            98%

    a call of 0.5 or more, by rival: came true
    rusherplus 86%   thinker 71%   marshal 70%   knower 56%   claudebot 52%
    actuary 44%   heuristic 31%

The model is calibrated, slightly under-confident. It is not sharper later in a
game: from 30 turns into contact on, the table is the same to a point. So a ramp
on game time would only trust the same forecasts more. What can ramp is a record
per rival. Sure kept one in the memo: every call of 0.5 or more (`SURE_P`) the
model made at a rival's move, and whether it came true. Once a rival's calls were
coming true 70% of the time (`SURE_HIT`, counted from a prior of 1 in 2), a
confident call against that rival thinned its source by the whole predicted
strike. Every other prediction stayed at Raise.

It changed nothing: 47 / 43 / 49% against actuary (Raise 48 / 44 / 51), 70%
against marshal (Raise 71%), 97% against knower (Raise 96%), at 60-100 games a
cell. How often it trusted a call, 2 seats, 18 systems, seeds 1-10 both seatings:

    against   turns with a trusted call   calls trusted
    actuary          0.3%                       12
    knower           1.6%                       32
    marshal         13.5%                      683

The gate stays shut against actuary, rightly, since the model's confident calls
about actuary come true 44% of the time. Against marshal it opens on one turn in
seven, and trusting those calls still buys nothing. So the model's confidence is
not where the strength is, against the bots it can read or the ones it cannot.
Deleted, with Trust.

### The roster ladder

`uv run python -m tests.sim --ladder --trials 30` (18 systems, 6 ly/turn,
default settings, 1680 games, 72 timed out). learner is first, 357 wins to
actuary's 343. The full grid is in [`marshal.md`](marshal.md), "Full roster
ladder". learner against each:

               knower  actuary  marshal  thinker  claudebot  heuristic  rusherplus
    learner       96%     47%      70%      97%      100%       100%        100%
    actuary      70%      -       64%      97%      100%       100%        100%

**Most of the lead is the oracle flag.** knower at Predict runs actuary's own
`decide` and reads it exactly. It cannot run learner, so it falls back to its blind
plan and keeps its guard. Off is actuary plus the flag, so it separates the two
(seeds 1-30, 60 games a cell, ±12 points):

    learner vs        Off (actuary + flag)   Raise    actuary itself
    knower                 90%               96%         70%
    marshal                64%               71%         64%

The flag is worth about 20 points against knower. Raise adds about 6 against
knower and 7 against marshal, inside the noise at 60 games, and gives 3 back
against actuary. So the ranking says more about knower than about learner.
**Any bot that advertises `IS_ORACLE` without predicting anyone gets the same
20 points**, since the flag is a claim knower takes on trust. The contract is
in `models/README.md`, "Predicting the other seats". learner's own claim rests on
its memory (`is_oracle_seat`), which is real, but nothing stops a bot claiming
it falsely.

**Not measured:** the other cells (slow, fast, large), the free-for-all, knower at
Search (the leaderboard's profile, `bot_replay.REPLAY_AUX`), and anything
against a person. learner joins the nightly bot column the moment `models/` on
`main` has it (`tools/bot_replay.py` replays every registered bot).

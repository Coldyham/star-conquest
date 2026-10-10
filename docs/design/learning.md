# Learning design notes

**actuary's third Style stop: the risk next door priced by each rival's learned
strike curve.** `aux` 2 in `models/actuary.py`. The model it reads (the memo
tree, the strike counts, the prior and the curve), how well it predicts, and the
gate it did not pass are in [`learner.md`](learner.md), which also records the
standalone bot this grew out of. actuary itself is in [`actuary.md`](actuary.md).
Roster-wide rules and the measurement checklist are in [`bots.md`](bots.md).
Index: [`../README.md`](../README.md).

## Why the curve goes in the risk term

Built 2026-10-09, from two observations by the author on why Raise was level with
actuary:

- **Assuming a rival will not strike is close to right.** Any one system is
  unlikely to strike any one neighbour on any one turn. 63% of the model's calls
  are under 0.1, and those come true 4% of the time ([`learner.md`](learner.md),
  "Trusting a prediction").
- **The two ways of being wrong do not cost the same.** A fleet sent is out of
  control until it lands. Ships kept home can do something else next turn. So
  acting on a predicted move that does not come costs more than waiting for one
  that does. And every launch shows the turn after: ships leave at launch and
  fleets in flight are on the board. A prediction only buys one turn's warning.

Raise could only add threat, so it could only make actuary more cautious, in a
game that is won close to the wire. But actuary's own risk term is already the
pessimistic case: every adjacent rival garrison counts as striking in full
(`_Ledger._risk`, `RISK_WEIGHT` 0.6). That is where the model has something to
say: most of that threat is never coming.

### What was measured, and what failed first

Duels against one opponent, seeds 101-300, both seatings (about 390 decided
games a cell), at 12, 18 and 40 systems, 6 ly/turn. A prototype ran in a scratch
harness (`ai.register`), so nothing here moves a stored record. Against marshal
and knower, each arm is paired by seed against actuary at Planned on the same
seeds: seeds it turned from loss to win, from win to loss, and z on those flips.
Against actuary the null is exactly 50%, so the win rate is read directly.

**Raise, and Raise kept to the strikes we could not answer** (no system of ours
next door could land relief in time after seeing the launch): 51.0 / 48.2 /
51.3% against actuary at 12 / 18 / 40, and 50.1 / 50.1% at 12 / 18. The lead
time is not the missing piece.

**Relax: the risk's reach scaled by the call.** Each rival garrison next door
counts at min(1, K x p) in `_risk`, where p is the model's chance that this
source strikes this target this turn, from `predict`. With K large every p>0
reads as 1, which is actuary exactly: that null read 38/76.

    against      K=1     K=2     K=4     K=8     K=16    K=32    K=64
    actuary 12   40.5%   41.4%   45.9%   46.2%
    actuary 18   42.5%
    marshal z            +0.85   +1.22   +2.65   +1.88   +1.19   +0.82
    knower Off z         -1.98   -1.37   -1.86

It gains against marshal, the rival the model reads best, and loses against
knower and actuary. A fixed K tuned on one opponent is knowledge of who the
opponent is, which is what a learner must not need. Against thinker every arm,
actuary included, wins 97-100%, so thinker cannot tell arms apart and was dropped.

**A per-rival calibration check does not separate them.** On calls under 1/8,
strikes seen against strikes the model expected, 40 games a rival at 18 systems:
knower Off 0.74, thinker 0.74, marshal 1.20, actuary 1.51. A correction would
relax harder against knower, the wrong way.

**The flaw: p is read at today's garrison.** Relaxing lets the ledger thin that
garrison, and a thin garrison is what an opportunist strikes. The model already
knows this: its strike curve is a function of the ratio of a rival's garrison
to the target's. So read the curve at the garrison we will hold.

### The stop

*As first built. Since 2026-10-09 the chance prices a whole strike rather than
a share of one: see "Strike and no-strike priced apart".* At Learning, `_risk`
counts a rival garrison of n ships next door, at turn t, as

    n x min(1, LEARN_REACH x curve[ratio bin of n / (our ships at t x DEFENDER_ADVANTAGE)])

where `curve` is that rival's strike curve on a player-held target, unpressed
(`strike_curve(model, PLAYER, 0)`), from the boards this seat has seen. The rest
of the ledger is actuary's. Thinning a garrison raises the ratio, so it raises
the chance the curve gives and gives back the threat. A rival that strikes only
at long odds costs nothing until we offer them. Every rival is read the same
way, a person included, from what it has done this game. `LEARN_REACH` large
reads every chance as 1 and is Planned exactly
(`test_learning_counting_every_garrison_in_full_plays_as_planned`).

Selection, same seeds as above, 18 and 40 systems pooled:

      K    marshal (paired)       knower Off (paired)    actuary         sum of z
      2    +78 / -74  z +0.32     +17 / -20  z -0.49     47.1%  z -1.61   -1.78
      4    +87 / -63  z +1.96     +17 / -10  z +1.35     49.5%  z -0.25   +3.05
      6    +86 / -54  z +2.70     +17 /  -6  z +2.29     50.7%  z +0.36   +5.36
      8    +81 / -48  z +2.91     +18 /  -9  z +1.73     54.1%  z +2.25   +6.89
     11    +86 / -46  z +3.48     +15 / -10  z +1.00     52.6%  z +1.42   +5.90
     13    +80 / -48  z +2.83     +16 /  -7  z +1.88     53.2%  z +1.78   +6.49

A plateau from 8 to 13, falling off below 6. `LEARN_REACH` is 8, the best of
the plateau on the sum and level with its neighbours within noise. These seeds
chose K, so they do not confirm it. The confirmation is the next table.

**Confirmation, fresh seeds 6001-6200**, the shipped code (`models/actuary.py`
at Learning against itself at Planned on the same seeds, so the knob alone
differs):

    against            18 systems             40 systems             pooled
    marshal            +46 / -16  z +3.81     +38 / -39  z -0.11     +84 / -55   z +2.46
    knower Off         +10 /  -7  z +0.73      +9 /  -3  z +1.73     +19 / -10   z +1.67
    actuary            49.1%      z -0.36     52.3%      z +0.92     50.7%       z +0.40
    knower Predict     +56 /  -4              +103 / -3              +159 / -7

    win rates          Planned   Learning     Planned   Learning
    marshal             69.2%     77.3%        68.5%     68.7%
    knower Off          94.3%     94.6%        96.0%     96.9%
    knower Predict      80.1%     94.5%        68.0%     95.1%

Against the gate written for Raise (next-to-last section): better against marshal
pooled (z +2.46, no cell at z -2 or worse), not worse against knower Off (it
leans the other way), and level with actuary (50.7%). The marshal gain is all at
18 systems on these seeds, where the selection seeds had it at both sizes, so
read it as real but smaller than the selection table says. Against actuary the
selection seeds' 54.1% did not hold: level, not better.

**Most of the knower Predict row is the oracle flag, not the model.** knower runs
actuary's `decide` at Planned and reads it exactly. A Learning seat claims
`is_oracle_seat`, so knower models it blind. To hold that equal, Planned was
run again with `is_oracle_seat` patched to always answer true, so knower models
both arms blind and only the model differs (same fresh seeds, Search on 100 of
them for cost):

    against           flagged Planned -> Learning         paired
    knower Predict    18: 92.6% -> 94.4%  (+17 / -10)     pooled +26 / -16  z +1.54
                      40: 94.2% -> 95.0%   (+9 /  -6)
    knower Search     18: 78.7% -> 77.7%  (+17 / -19)     pooled +40 / -33  z +0.82
                      40: 68.8% -> 73.4%  (+23 / -14)

The claim alone took Planned from 80.1 / 68.0% to 92.6 / 94.2% against Predict.
The model adds a lean on top, against both knower stops, that is not significant.
Search stops on a wall clock, so its moves shift with CPU load and its pairing is
noisier than the rest.

**Other cells and the free-for-all**, seeds 6001-6200 (FFA 6001-6100, every seat
rotation), Learning against Planned on the same seeds and seatings, paired:

    cell                   against        Planned -> Learning   paired
    slow (24, 3 ly/turn)   marshal        61.7% -> 69.4%        +47 / -22  z +3.01
                           knower Off     96.3% -> 97.5%         +7 /  -3  z +1.26
                           actuary        56.4% (Learning's)               z +2.37
    fast (24, 12 ly/turn)  marshal        65.6% -> 70.4%        +54 / -35  z +2.01
                           knower Off     91.3% -> 91.8%        +11 /  -9  z +0.45
                           actuary        49.4%                            z -0.25
    jitter 0.06 (18, 6)    marshal        70.4% -> 75.5%        +47 / -28  z +2.19
                           knower Off     95.3% -> 96.2%        +11 /  -8  z +0.69
                           actuary        51.6%                            z +0.62
    FFA, 3 seats at 18     with marshal, knower Off               63.5% -> 66.4%   +33 / -25  z +1.05
    FFA, 4 seats at 24     with actuary, marshal, knower Off      46.6% -> 49.0%   +51 / -42  z +0.93

No cell is worse. The marshal gain holds in all three (z +2.0 to +3.0), and the
slow cell is the one where Learning also beats actuary itself. The low-jitter
cell (0.06, a setting people play) is better against marshal, level elsewhere.
The free-for-all leans better in both lineups, not significantly. The slow cell
leaves 12-14% of games undecided at 500 turns, in both arms alike; those are
dropped.

**Cost.** Learning adds the memo read and a curve lookup per risk term: CPU ms a
decide against marshal, seeds 1-6, up to turn 150, Planned then Learning:

    systems    median          75th percentile
    18         2.40 -> 2.87    3.55 -> 4.00
    40         4.71 -> 5.43    7.57 -> 8.55
    80         10.1 -> 11.5    14.1 -> 16.3

About 15-20% more. `decide_ms` is unchanged, since only knower reads it, and knower
never runs a Learning seat.

### What else it changed

- **The knob.** actuary's `AUX_LABEL` went from *Opening* to *Style*, with
  stops Greedy, Planned (the default, unchanged) and Learning. Before, any `aux`
  from 1 up read as Planned. The menu could not set more than 1, so only a
  hand-edited link could hold 2 or more, and a stored game never re-runs a bot.
  The bot column first left actuary at 1 (Planned); it now replays every bot at
  the top of its slider (`bot_replay.replay_aux`), so it plays Learning.
- **The oracle claim is per seat.** `is_oracle_seat` is true at Learning only, so
  knower still runs actuary's `decide` at Greedy and Planned and models a
  Learning seat blind. actuary has no module-level `IS_ORACLE`. `decide_ms`
  prices Greedy and Planned, the stops knower runs.
- **The tools.** `tools/learner_check.py` keeps `predict` and the strike-size,
  guard and evacuation readings with their priors, counted by `Watcher` from
  `actuary.launches` on the turns actuary's memo links. On `--cells 18n6 --seeds
  1-4` its report and `--fit-prior` match the old learner's to the digit.
  `tools/playstyle.py` (behind `tools/human_habits.py` and the board's Playstyle
  panel) reads `_Snap` and `_effective` from actuary; its `reading_rev` hashes
  actuary for that reason.

**Not measured:** wide jitter (0.3), knower in a free-for-all at Predict, and
play against a person. Two of the proposed improvements in
[`learner.md`](learner.md) were then built as arms against Learning: evacuation
in the price of a capture (worse, deleted) and strike and no-strike priced apart
(kept). Both are below.

## Evacuation in the price of a capture (built, measured, deleted)

The evacuation half of [`learner.md`](learner.md)'s improvement 3, built
2026-10-09 as an arm of Learning behind a module flag. It is in the branch history (commit `2ee5191`, reverted). Nothing
stored names it.

- **The reading.** actuary's `Model` counted, for each rival, its doomed
  garrisons (hostile ships inbound over the garrison times the advantage) that
  left (launched half or more) or stayed, as `tools/learner_check.py` does, over
  `PRIOR_EVAC` 0.531.
- **The price.** In `_project`, a rival garrison that our landing dooms, on a
  strike of two turns or more (so it sees the fleet first), lost its evacuation
  rate times its ships before the fight. Those ships stayed on the ledger as
  the rival's, so the strike no longer took credit for killing them. They joined
  the rival's nearest system and counted in the risk to what we took.
- **What it touched.** 27% of Learning's decisions changed (629 decisions, 18 and
  40 systems, against marshal), at no extra cost per decide. Against actuary,
  marshal read about 0.8, far above the prior.

**The gate, set before the run:** better than Learning against marshal (z ≥ +2
pooled), and not worse than z -2 against knower Off or actuary at Planned.
Paired duels on fresh seeds 7001-7200, both seatings, 18 and 40 systems at 6
ly/turn, 4,800 games. Flips are seeds the evac arm turned from loss to win and
from win to loss:

    against          18 systems             40 systems             pooled
    marshal          +41 / -60  z -1.89     +24 / -63  z -4.18     +65 / -123  z -4.23
    knower Off       +19 / -36  z -2.29     +17 / -22  z -0.80     +36 /  -58  z -2.27
    actuary Planned  +44 / -75  z -2.84     +35 / -46  z -1.22     +79 / -121  z -2.97

Win rates pooled, Learning then evac: 68.6% to 61.4% against marshal, 91.6% to
88.9% against knower Off, 49.6% to 44.4% against Planned. Games were shorter
with it (135 turns against 149 at 18 systems). **It failed every clause and was
deleted.**

**Why it could only lose.** The reading is conditioned on a doomed garrison, so
the price applies only to a strike that already wins against the whole garrison.
It cannot make any capture cheaper, which was the proposal's case for it. All it
can do is take away the kill and add a threat, so every strike on an evacuator is
worth less. That is Raise's flaw again: a reading that can only add caution, on
the decisions that matter most. To use evacuation, a price would have to make
something cheaper: a strike sized below the full fight against a rival whose
garrisons leave even when the fight is not hopeless. The reading does not count
that case, and nothing here measured it.

## Strike and no-strike priced apart (built 2026-10-09, kept)

[`learner.md`](learner.md)'s improvement 2, built against Learning as it stood.

- **The flaw.** "The stop" counted a rival garrison of n ships at
  n x min(1, `LEARN_REACH` x p): a fleet of a chance-weighted size. A source
  sends nearly everything or nothing (the all-in share, 0.46 in the prior), so
  that fleet never flies. A 9-ship garrison at a quarter read as a 2-ship strike,
  which 4 of ours hold off, so it priced no risk at all, where the real case is
  a quarter's chance of meeting all 9.
- **The change.** Each rival garrison next door strikes whole or not at all,
  independently, at min(1, `LEARN_REACH` x p), with p read at the ratio to the
  garrison we will hold as before (`_Ledger._learned_shortfall`). For each turn,
  the shortfall is taken in every outcome and weighted by its chance. The worst
  turn's expected shortfall stands where the worst shortfall stood, so the rest
  of the ledger is unchanged. Outcomes are folded by total ships, so a rival with
  k garrisons next door costs at most 2^k sums, and usually far fewer. Every
  chance read as 1 is still Planned exactly
  (`test_learning_counting_every_garrison_in_full_plays_as_planned`).
- **What it touched.** 18% of Learning's decisions changed (629 decisions, 18
  and 40 systems, against marshal), at about 18% more CPU a decide (4.7 ms to
  5.5 ms mean). `decide_ms` is unchanged, since knower never runs a Learning seat.

**How it was measured.** Paired duels as in "The stop", each arm against plain
Learning on the same (cell, seed, seating), at 6 ly/turn. Flips are seeds the
arm turned from loss to win and from win to loss. `LEARN_REACH` was tuned for
the old form, so it was selected again first, on its own seeds.

**Selection**, seeds 8001-8100, 18 and 40 systems, `LEARN_REACH` 4, 8 and 16,
pooled z against Learning; the stop with the largest sum went on:

      K    marshal   knower Off   actuary Planned   sum
      4    +1.43       +0.17         +0.23         +1.84
      8    +0.51        0.00         +1.70         +2.21
     16    -0.24       +0.18         -0.87         -0.92

**Confirmation**, fresh seeds 8201-8400, K 8, against the gate set before it
ran: better than Learning against marshal at z ≥ +2 pooled with no cell at
z ≤ -2, and no worse than z -2 against knower Off or actuary at Planned:

    against          18 systems             40 systems             pooled
    marshal          +36 / -22  z +1.84     +43 / -31  z +1.39     +79 / -53  z +2.26
    knower Off       +15 / -11  z +0.78     +13 / -14  z -0.19     +28 / -25  z +0.41
    actuary Planned  +47 / -35  z +1.33     +37 / -29  z +0.98     +84 / -64  z +1.64

Win rates pooled, Learning then split: 69.9% to 73.1% against marshal, 92.6% to
93.0% against knower Off, 50.4% to 52.9% against Planned. **It passed, narrowly
against marshal**, and leans better everywhere else.

**Speed**, fresh seeds 8401-8550, 24 systems, against the same three, required
only to be no worse:

    against          slow (3 ly/turn)       fast (12 ly/turn)
    marshal          +29 / -29  z +0.00     +29 / -16  z +1.94
    knower Off       +15 / -11  z +0.78     +11 /  -8  z +0.69
    actuary Planned  +29 / -28  z +0.13     +30 / -19  z +1.57

Nowhere worse; the fast cell leans better and the slow one is level. The slow
cell leaves 15% of games undecided at 600 turns, in both arms alike.

**Kept as Learning's risk term.** The flag that carried it during the
measurement is gone, and so is the chance-weighted reach. No stored game moves,
since a stored game never re-runs a bot; the bot column recomputes, since it
replays actuary at Learning.

**knower and the free-for-all**, required only to be no worse (no pooled z at
-2 or below). Plain Learning here is the reach form from commit `e1dcc68` with
the flag off. Both arms are Learning seats, so both claim `is_oracle_seat` and
knower models both blind: only the model differs. Duels on fresh seeds 8601-8800
(Search 8601-8700, for cost), both seatings, 6 ly/turn. Free-for-alls on seeds
8601-8800, the arm in every seat by rotation, the others in a fixed order. An
undecided game at 600 turns counts as a loss for both arms (188 of 6,400, spread
evenly but for 20 against 9 in the 4-seat lineup).

    against             18 systems             40 systems             pooled
    knower Predict      +12 / -10  z +0.43     +16 /  -6  z +2.13     +28 / -16  z +1.81
    knower Search       +15 / -15  z +0.00     +13 / -20  z -1.22     +28 / -35  z -0.88

    free-for-all                                    Learning -> split   paired
    3 seats at 18, with marshal, knower Off         63.3% -> 62.5%      +57 / -62  z -0.46
    3 seats at 18, with marshal, knower Predict     61.2% -> 62.7%      +61 / -52  z +0.85
    4 seats at 24, with Planned, marshal, knower Off 45.4% -> 46.9%     +85 / -73  z +0.95

Win rates against knower, Learning then split: Predict 89.2% to 90.8%, Search
76.0% to 74.2%. Nowhere worse. Predict leans better (all of it at 40 systems),
Search leans the other way inside the noise, and the free-for-alls are level.
Search stops on a wall clock, as in "The stop", so its pairing is the noisiest
here. **Kept.**

**Not measured:** wide jitter (0.3) and play against a person.

## Memory off: the prior alone (built, measured, flag removed)

**The question.** "What the model can tell apart" ([`learner.md`](learner.md))
shows the strike curve reads about the same for every bot: 0.16 at ratio 1.5,
0.32 at 2.0. So Learning's gain over Planned might come from reading the
*prior's* curve at the garrison we will hold, not from anything it learned
about a rival.

**The arm.** A temporary `LEARN_MEMORY` flag (commit `10df678`). Off, `_curves`
gives every rival `strike_curve(EMPTY, PLAYER, 0)`, the prior alone, and the
rest of Learning is unchanged, `is_oracle_seat` included. So knower models both
arms blind, and only the memory differs.

**How it was measured.** Paired duels on fresh seeds 9001-9400, both seatings,
18 and 40 systems at 6 ly/turn: 9,600 games in all. Each game was played through
`tools/sweep.py`'s `play_duel`, with four processes and the sweep's
`BUDGET_SCALE`, and a repeat run matched every board digest. Each (cell,
opponent, seed, seating) pairs Learning with memory-off. Flips are seeds that
Learning won and memory-off lost, against the reverse. An undecided game at 600
turns counts as a loss for both arms (337 of 9,600). **The gate, set before the
run:** the memory earns its place if Learning beats memory-off at z ≥ +2 pooled
against some opponent, with none at z ≤ -2.

    Learning vs memory-off   18 systems             40 systems             pooled
    marshal                  +32 / -41  z -1.05     +39 / -36  z +0.35     +71 / -77   z -0.49
    knower Off               +14 / -19  z -0.87     +11 / -12  z -0.21     +25 / -31   z -0.80
    actuary Planned          +28 / -47  z -2.19     +33 / -42  z -1.04     +61 / -89   z -2.29

Win rates pooled, Learning then memory-off: 70.6% to 70.9% against marshal,
92.0% to 92.4% against knower Off, 51.2% to 53.0% against Planned. 85 to 206
games an opponent were identical move for move. The memory cost about 7% more
per turn (7.67 ms to 7.19 ms, both seats together).

**It failed.** Learning beats memory-off nowhere. Memory-off is level against
marshal and knower Off, and better against Planned (z +2.29, mostly at 18
systems). So what Learning adds over Planned is a fixed discount: the prior
curve read at the garrison we will hold. A rival's own record does not help.
That fits the prediction check: memory sharpens the forecast only a little,
and the curve does not tell the bots apart. The flag is removed, and Learning
still learns.

**What it leaves open, for the author.** Learning could read the prior alone
and keep no memory. Then `decide` reads only the board, so `is_oracle_seat`
could go false, and knower at Predict and Search would run it. That gives back
the 20-odd points the claim earns against knower ([`learner.md`](learner.md),
"The roster ladder"), and moves the bot column. It would also cut the memo tree
from the hot path. `tools/learner_check.py` and `tools/playstyle.py` still read
the memo tree and the models through `models_for`. The memory reads a person a
little better than the prior does ("Predicting people"), but play against a
person is not measured.

**Not measured:** knower at Predict and Search, the speed cells, wide jitter,
and play against a person.

## Five more arms, and three combinations (built, measured, deleted)

These are the ideas still open after "Memory off", each built as an arm of
Learning with memory on. Every arm was paired against Learning on the same
games as "Memory off": seeds 9001-9400, both seatings, 18 and 40 systems at
6 ly/turn, 4,800 games an arm, scored the same way. The flags (commits
`ae60f84`, `be4ef7b`) are removed. **The gate** was the one used for "Memory
off": better than Learning at z ≥ +2 pooled against some opponent, with none at
z ≤ -2. A combination chosen after seeing the singles would have needed fresh
seeds to confirm it. None came close.

- **Pressed.** A rival garrison with hostile ships inbound reads the curve's
  pressed row, which actuary already counts and never read. That row's prior
  is higher at low ratios (0.08 against 0.04): a garrison under attack launches
  more.
- **Size.** A strike is all-in at `tools/learner_check.py`'s prior share
  (0.464), or else sized at its prior ratio to our garrison (1.25 times what we
  hold). Each source then has three outcomes in place of two.
- **Guard.** learner's improvement 3, the half not built before. A rival keeps
  its learned guard home: the median of what a launching frontier system kept
  against its largest adjacent enemy, counted per rival in actuary's `Model` as
  learner_check counts it. Its strike from a garrison of n is n less that
  guard times our garrison. It reads true: marshal about 0.5 (its
  `FRONTIER_GUARD` is 0.55), actuary 0.
- **Joint.** A rival's garrisons next door strike together or not at all, at
  the largest of their chances, in place of independently.
- **Stronger memory.** `PRIOR_WEIGHT` 1 in place of 4, so a rival's own record
  outweighs the roster's prior four times sooner.

The table gives pooled z against Learning for each opponent, then the win rates
against marshal, Learning then the arm:

    arm                        marshal   knower Off   Planned    vs marshal
    pressed                     +1.57      +0.49       +1.52     70.6% -> 71.8%
    size                        -2.20      -1.15       -2.96     70.6% -> 68.3%
    guard                       -2.08      -0.56       -0.14     70.6% -> 68.8%
    joint                       -2.50      +0.59       -1.71     70.6% -> 68.2%
    PRIOR_WEIGHT 1              -0.38      -0.13        0.00     70.6% -> 70.2%
    pressed + guard             -2.30      -0.21       +0.75     70.6% -> 68.4%
    pressed + guard + size      -3.32      -0.84       -2.74     69.6% -> 65.2%   (2,875 games, stopped)
    pressed + guard + joint     -1.66      -0.59       -1.93     70.6% -> 68.9%

A combination of joint and size would be joint alone, since joint replaces the
independent sum that size splits. Pressed with memory off leans the same way
as pressed alone, but it was dropped on purpose: Learning has to learn.

**What they say.**
- **Pressed is the one lean.** It is better against every opponent, but
  nowhere near z +2, and adding guard wipes it out.
- **Every arm that thins the threat is worse against marshal.** Guard, joint
  and size each make a strike smaller or rarer than Learning does. marshal is
  the rival that punishes a thin garrison, as in "Evacuation in the price of a
  capture". Guard reads marshal's guard correctly and still loses. The guard is
  counted on every launch, but when marshal strikes us it sends what wins, not
  what is left over above its guard.
- **The memory is not drowned by the prior.** Weighting the prior a quarter as
  much changes nothing: 218 to 446 games an opponent were identical move for
  move. Together with "Memory off", this says the strike curve, read in actuary's
  risk, has nothing per rival to give.

**Not measured:** knower at Predict and Search, the speed cells, wide jitter,
and play against a person, for every arm here.

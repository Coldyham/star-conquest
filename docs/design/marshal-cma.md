# marshal design notes: tuning by CMA-ES

The fifth of marshal's files (overview and current standing:
[`marshal.md`](marshal.md); constants that move together and the hand-built
screen this continues: [`marshal-joint.md`](marshal-joint.md)). It hands the
search for joint moves to CMA-ES (`tools/const_train.py`): the run, the
pre-registered confirmations, the ablation that explained the first one's
failure, and what shipped. Roster-wide rules are in [`bots.md`](bots.md). Index:
[`../README.md`](../README.md).

## Tuning by CMA-ES (2026-10-20)

The screen in [`marshal-joint.md`](marshal-joint.md), "Screening against the
roster", found +4.7 points in combinations no one-at-a-time sweep could
reach, by five hand-built passes. This section hands the same search to CMA-ES,
the optimiser `tools/evo_train.py` runs for evolved, with the screen's objective
and twelve of marshal's constants as the genome. The tool is
`tools/const_train.py`.

### The tool

A candidate is a separate import of `models/marshal.py` (as `tools/sweep.py`
does it) registered under its own name, with every genome constant set on it
before each game, so the opponents read the stock module throughout. At stock
values the copy played game for game as marshal (36 of 36). The objective is the
screen's: duels against stock marshal, knower (Predict) and actuary (Field, the
stop that replaced Planned on 2026-10-18), both seatings of each seed, a timeout
scored 0.1 toward whoever was ahead, the three opponents weighted 1 / 1 / 1.25.
Every generation also plays stock on the same games, so a candidate's fitness
reads as paired points against it.

The genome is the twelve continuous constants live in a duel: `FRONTIER_GUARD`,
`POUR_GUARD`, `RELIEF_AWARE`, `RELIEF_IN_FLIGHT`, `DENY_SWAP`, `FEED`,
`DEFEND_PAD`, `NEUTRAL_PAD`, `NEUTRAL_MARGIN`, `OVERWHELM`, `BEYOND_DECAY` and
`RISK_PARITY`. The switches were each ablated on their own already, and the
duel-inert constants (`RIVAL_WEDGE`, `REMNANT_MAX_PLAYERS`) and the integer gates
were left out. A coordinate is `(value - stock) / scale`, one scale a plausible
step for that constant (0.15 for the guards and relief weights, 0.75 for `FEED`,
0.05 for the pads), and the search starts at the shipped values with sigma 1,
inside bounds that keep each constant meaningful (`OVERWHELM` at least 1, the
pads at least 0).

**Cost and noise.** A duel costs 0.18 worker-seconds on average over the four
training cells (13/3, 18/6, 11/11, 24/12) and three opponents: 0.03 against
marshal at 11/11, 0.79 against actuary at 13/3. On 22 workers that is about 120
games a second. A 100-seed pilot (2,400 games an arm) put the paired standard
error at 0.9 points; the bot before the 2026-10-17 ship read -6.3 ± 0.85, and
genomes one sigma from stock read +0.9, -1.3 and -2.4. So one step moves the
fitness by about the noise of one evaluation, the scale a population-based search
averages over.

### The run

Run `m1`: population 12, 50 seeds a generation in each of the four cells against
each opponent in both seatings (1,200 games a candidate, 15,600 a generation with
stock), fresh seeds every generation (6,100,000 + 1000 g), 60 generations, about
137 s each. The population's mean points against stock, which no selection
inflates since every candidate plays fresh paired seeds:

    generations   population mean   sigma
    1-6              +1.8           1.04
    7-12             +3.3           0.81
    13-18            +3.9           0.65
    19-24            +5.0           0.51
    25-30            +5.6           0.36
    31-41            +4.0           0.38
    43-60            +6.0           0.40 rising to 0.68

Sigma rose again over the last twenty generations: the search was still moving
when it stopped. `OVERWHELM`, `DEFEND_PAD` and `NEUTRAL_PAD` finished on their
bounds. (Generation 42's log line was lost to a machine crash; the CMA state
saved after it was intact, and the tool now fsyncs the log and replaces its state
atomically.) Each generation's best candidate read +6 to +11, which is the best
of twelve noisy draws and says nothing on its own.

### The first candidate fails on knower

Pre-registered before any of its games (`--confirm`): the mean of generations
51-60's means, to two places, passing on a weighted gain at z >= 2 with no
opponent 4 points or more worse, the same sign in both halves of the seeds
against every opponent, and neither free-for-all significantly worse. Seeds
7,000,000-7,000,299 in the six duel cells and 18/6 at defender advantage 1.25
and 1.5; a three-seat field at 18/6 against knower and actuary (seeds from
7,100,000) and a four-seat one at 24/6 against all three (from 7,200,000):

                     marshal        knower         actuary        weighted
    m1 candidate     +3.88          +0.58          +8.61          +4.68 (z +10.7)
      halves         +3.0 / +4.8    +2.5 / -1.4    +9.0 / +8.2

It fails the halves rule against knower, and only that: the gain is against
marshal and actuary, and knower is level. Better in both fields (+5.8, z +3.3;
+5.1, z +3.5).

Knower is not being fooled or handicapped here. At Predict it models the
candidate's seat through `_surrogate`, which calls the seat's registered
`decide`, the candidate's own; marshal is not an oracle, so the model is exact
and trusted. Predict borrows no marshal of its own: `EXTERNAL_CANDIDATES` runs at
Search and Council only. So knower sees the new marshal coming and the gain does
not reach it.

### The ablation

The candidate with each constant put back to stock, one at a time, and the three the run
pushed to their bounds (`DEFEND_PAD`, `NEUTRAL_PAD` and `OVERWHELM`) together: 150 fresh seeds
(7,500,000) in the eight duel cells against each opponent, 108,000 games. What
each change is worth, the candidate's points over the arm that reverts it:

                                weighted       marshal  knower  actuary  games unchanged
    the whole candidate         +5.14 ± 0.60   +4.74    +0.32   +9.32       224 / 7200
    NEUTRAL_MARGIN 1.3 -> 1.53  +1.86 ± 0.54   +3.16    +0.88   +1.60     2,058
    RELIEF_IN_FLIGHT 0.5 -> .87 +1.14 ± 0.31   -0.04    -0.70   +3.56     3,363
    DEFEND_PAD 0.05 -> 0        +0.87 ± 0.23   +1.35    +0.22   +1.02     4,461
    RELIEF_AWARE 0.65 -> 0.85   +0.49 ± 0.31   -0.81    -0.19   +2.09     2,911
    FEED 3.0 -> 3.73            +0.49 ± 0.31   +0.80    +0.74   +0.05     2,939
    FRONTIER_GUARD .55 -> .72   +0.45 ± 0.38   -0.19    -0.36   +1.61     2,003
    POUR_GUARD 0.85 -> 1.16     +0.38 ± 0.39   +0.28    -1.15   +1.68     1,171
    OVERWHELM 2.0 -> 1.08       +0.33 ± 0.26   +0.16    +0.12   +0.63     3,097
    BEYOND_DECAY .35 -> .64     +0.27 ± 0.29   +0.50    -0.43   +0.65     4,251
    RISK_PARITY 1.0 -> 0.94     +0.20 ± 0.10   +0.65    +0.02    0.00     6,565
    DENY_SWAP 1.0 -> 0.83       -0.01 ± 0.05   +0.11     0.00   -0.12     6,952
    NEUTRAL_PAD 0.05 -> 0       -0.53 ± 0.26   -1.19    -0.80   +0.22     5,754
    the three together          +0.50 ± 0.37   +0.18    -0.39   +1.46     1,935

The parts roughly add (+5.9 against +5.1 for the whole). Three carry most of it:
a pickier price on neutrals, more weight on relief already in flight when pricing
a rival target, and no defend pad on top of the tuned edge. `OVERWHELM` at 1.08
looks like a switch turned off but is the opposite, a cornered garrison sortieing
whenever out-numbered at all; it is worth about nothing. The pads cancel:
`DEFEND_PAD` 0 pays (z +3.8), `NEUTRAL_PAD` 0 costs (z -2.0), although the neutral
margin binds over the pad at the default advantage. `DENY_SWAP` 0.83 is inert. The
higher pour floor and the extra in-flight relief are where knower's points go:
both are large against actuary and cost against knower.

### Candidate B

The m1 candidate with `NEUTRAL_PAD`, `POUR_GUARD` and `DENY_SWAP` back at stock,
chosen on the ablation's seeds and pre-registered on unplayed ones under the same
rule: duels from 7,300,000, fields from 7,400,000 and 7,500,000.

                     marshal        knower         actuary        weighted
    B                +3.90          +1.71          +9.78          +5.49 (z +13.1)
      z              +6.55          +2.58          +13.68
      halves         +4.1 / +3.7    +1.0 / +2.4    +8.6 / +11.0
    as losses        +3.60          +1.12          +8.98          +4.91 (z +11.4)

    three-seat field, 18/6 vs knower, actuary     34.9% (stock 29.2%)  +5.7, z +3.5
    four-seat field, 24/6 vs all three            27.8% (stock 21.0%)  +6.8, z +4.6

B passes on every count, and the ablation's additive reading held: knower rose
from +0.3 to +1.7 and the weighted gain with it. By cell it gains against actuary
everywhere (+5.5 to +12.4) and against marshal everywhere but defender advantage
1.5 (-1.7; -4.0 scored as losses). Against knower it is weakest on the big slow
board, 24/3 (-4.4), and at advantage 1.25 (-1.4).

### Shipped (2026-10-20)

B shipped: `NEUTRAL_MARGIN` 1.53, `RELIEF_IN_FLIGHT` 0.87, `DEFEND_PAD` 0,
`RELIEF_AWARE` 0.85, `FEED` 3.73, `FRONTIER_GUARD` 0.72, `BEYOND_DECAY` 0.64,
`OVERWHELM` 1.08 and `RISK_PARITY` 0.94. The shipped file plays board for board
as the bot before it with B's constants set (336 of 336 games in seven cells
against knower, actuary and thinker). The guard and the pour floor are still
split (0.72 under 0.85).

**knower at Search.** Pending. Search and Council borrow marshal as one of
their candidate openings (`EXTERNAL_CANDIDATES`), so the ship changes them;
Predict, which every measurement above played, borrows nothing. The A/B running
is knower at Search against actuary, knower (Predict) and thinker, with the
registry's marshal the bot before the ship or after it, on the same seeds
(7,800,000 onward, five cells, both seatings).

**The roster ladder**, re-run with the bot before the ship beside it on the same
seeds ([`marshal.md`](marshal.md), "Where marshal stands"). At 18 nodes, marshal
308 wins to 305: 39% against actuary from 27%, level against knower (83%), and
three games lost to thinker (95% from 100%); 52 timeouts to 49. On the symmetric
board, 300 to 300: 33% against actuary from 28%, 78% against knower from 76%,
100% against thinker from 98%; 128 timeouts to 121. At 60 games a pair these are
directions; the paired confirmation above is the measurement.

### What this says about tuning marshal

- **A joint search is worth running against the roster.** CMA found as much as
  the hand screen did, on top of it, in about 2.5 hours of a 24-core machine,
  and on constants the screen had held still (the neutral margin, the pads).
- **The weighted objective hides opponent trades; the gate catches them.** The
  first candidate's fitness was better everywhere on average and level against
  knower. Without a per-opponent rule it would have shipped as a trade of
  knower for actuary.
- **Read the population, not the best.** The best of twelve was 2 to 4 points
  above the population mean every generation, which is selection, not skill.
- **An ablation of the found point is cheap and additive enough to edit by.**
  One 108,000-game run told which changes carry the gain, which cost against
  whom, and which do nothing; the edited candidate then confirmed as predicted.

Still open: the run had not converged (sigma rising), and three constants sat on
their bounds, so a second run from B with `DEFEND_PAD` allowed below 0 is the
obvious next step; training never played a free-for-all or the advantage cells,
where B's two soft spots are; and Council, which also borrows marshal, was not
measured.

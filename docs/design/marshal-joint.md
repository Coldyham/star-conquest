# marshal design notes: constants that move together

The fourth of marshal's files (overview and current standing:
[`marshal.md`](marshal.md); what a fight is priced against:
[`marshal-pricing.md`](marshal-pricing.md); where the surplus goes:
[`marshal-flow.md`](marshal-flow.md)). Every constant in those files was tuned
one at a time, each against the bot of its day, with the others held where they
were. This file asks which of them should move together: which act on the same
decision, what the order of `decide`'s phases does to that, and what joint sweeps
show. The screen is `tools/coupling_census.py`. Roster-wide rules are in
[`bots.md`](bots.md). Index: [`../README.md`](../README.md).

## Which constants move together (2026-10-16)

**The question.** Sweeping one constant up and down finds its best value *given
the others*. When two constants act on the same decision, that best value moves
with the other one, and a one-at-a-time sweep can settle on a point that is a
maximum only along its own axis. [`marshal.md`](marshal.md) already met one case
(`FRONTIER_GUARD` read as dead weight until Phase 3b spent what it hoarded), and
the 2026-09 sweep found a superadditive pair. Nobody had asked the question of
the whole bot.

**The answer, in short.**

- **The phase order couples the constants, and a census finds which.** `decide`
  spends one budget in a fixed order. Phase 0's guard is the floor every later
  phase spends down to, Phase 3's price is a gate that Phase 3b's pour follows,
  and two readings are reused across phases. Run over every pair of constants on
  positions from real games, `tools/coupling_census.py` leaves three groups to
  sweep together, with the guard in all three: the strike gate (the guard and
  `RELIEF_AWARE`), the long-lane pour (the guard, `DENY_SWAP` and the reflood
  gate) and the short-lane front (the guard and `FEED`).
- **Inside a group the pairs are substitutes.** The guard and `RELIEF_AWARE`
  (twist z -2.9), and `DENY_SWAP` and the reflood gate (z +3.3), each cover for
  the other, so a move of one moves the other's best value. Against stock
  marshal, stock sits at or near each pair's joint optimum; `FEED` was simply
  never tried above 1.0.
- **One constant is several.** Split by phase, the guard's floors want different
  values against stock marshal: none for the priced strike, 0.55 for the pour.
  That joint move gains 6.0 points (12.8 counting timeouts as losses) on fresh
  seeds, in every cell, and no sweep of `FRONTIER_GUARD` alone can reach it (*One
  guard, two floors*).
- **But where the optimum sits depends on the opponent.** The same split loses
  6.2 points against actuary (z -8.5) and is level against knower. Against
  actuary every lower guard loses and a higher one gains (1.0: +3.0, z +4.6,
  though mostly by turning losses into stalemates), and its floors want raising
  together rather than apart. Which constants are coupled is a property of the
  bot; where their joint optimum sits is a property of the matchup. A joint sweep
  against a copy of the bot finds a best response to that copy (*Against the
  roster, the optimum moves*).
- **Most constants are separable.** `OVERWHELM`, `RISK_PARITY`, `BEYOND_DECAY`,
  `CONSOLIDATE`, `RESERVE_PINCER`, `REMNANT`, `HOLD_FOLLOW` and the retreat flags
  share a decision with nothing on more than 1.5% of contested positions, and
  `RELIEF_AWARE` and `RELIEF_IN_FLIGHT` add even though they sum into one price.
  One at a time is fine for those.
- **How a timeout is scored changes what is best.** Every guard measurement since
  the first one was read on decided games. Counted as a board not taken (how the
  bot column scores a replay), a lower guard gains by ending stalemates in
  self-play (*A timeout is a board not taken*).

Nothing here is shipped. The split is a best response to stock marshal's
stalemates, not a better bot.

### The decision order is what couples them

`decide` spends one dict, `budget`, in a fixed order, and most constants act only
through a later phase:

    phase                    reads                                        does with budget
    0  guard                 FRONTIER_GUARD, FAST_GUARD_WEIGHT            sets it: ships above the guard
    1  defence               DEFEND_PAD, RISK_PARITY                      relief out of neighbours' budget
    2  consolidate/evacuate  CONSOLIDATE, AVOID_ABANDONED,                the rear's budget tops up a doomed
                             RETREAT_NEAREST, OVERWHELM, _required        neighbour; a doomed budget is zeroed
    3  priced strikes        _required (NEUTRAL_MARGIN, REMNANT,          a target is struck when inbound plus
                             RELIEF_AWARE, RELIEF_IN_FLIGHT), _richness   the sources' budget covers its price;
                             (BEYOND_DECAY, RIVAL_WEDGE), RESERVE_PINCER, the far wave launches
                             RIVAL_REFLOOD_MIN_TURNS
    3b surplus pour          COMMIT_SURPLUS, DENY_SWAP (x _defend_margin) every source at the strike's horizon
                             DENY_SWAP_MIN_TURNS                          sends the rest of its budget
    3c hold-follow           HOLD_FOLLOW (off)
    4  feed and flow         FEED, FEED_FRONT, FEED_MAX_TURNS,            fronts top up short neighbours, the
                             FLOW_AVOIDS_ABANDONED                        rear flows forward, and a live
                                                                          front's leftover stays home

Three couplings follow from that order, and the census below finds all three.

**One floor, spent in order.** Phase 0's guard is the floor that every later
spender spends down to: Phase 1's relief, Phase 2's top-up, Phase 3's strike,
Phase 3b's pour, Phase 4's top-ups. So a constant that changes what a later phase
may spend (a price, `DENY_SWAP`, the reflood gate, `FEED`) changes how much of the
guard's slack gets used, and the guard changes how much slack there is. By ships
sent, the pour is the largest spender at the default and the middle speeds (55%
of all ships sent at 11 nodes and 11 ly/turn, 33% at 18 and 6; 10 mirror games a
cell, an instrumented copy), so most of what the guard does, it does through the
pour.

**The price is a gate; the pour sets the size.** Phase 3 decides whether a target
is struck at all, and Phase 3b then sends everything above the floor
([`marshal-pricing.md`](marshal-pricing.md), "Racing a third player for the same
system", found this for contested neutrals). A price constant and the guard are
two sides of one inequality, inbound plus committable against required: one
raises the bar, the other lowers what can clear it. At the default sliders
`_enemy_margin()` is exactly 1.0, so for a strike on a rival the relief weights
are the whole of the margin. That is how a stalemate holds: each side keeps 0.55
of the stack it faces out of the gate, and prices a strike at that stack plus
half of whatever could relieve it, so neither side's committable reaches the
other's price (the split below ends four stalemates in five).

**One reading, used twice.** `_defend_margin()` sizes Phase 1's defence and
multiplies `DENY_SWAP`'s hold, so moving `DEFEND_PAD` silently re-tunes
`DENY_SWAP` (of the positions where either changes anything, the two interact on
69%). `_required` prices Phase 3's strikes and `_evacuate`'s step forward.
`_richness` sorts Phase 3's targets, seeds Phase 4's flow (`_front_pull`) and
breaks retreat ties. A rival stack is read twice: through `FAST_GUARD_WEIGHT` by
the guard, and raw by `FEED`'s shortfalls.

**When each was last swept, and with what missing.** Each value is a best
response to the bot of its day:

    constant                   value  last swept                              absent or different then
    FRONTIER_GUARD             0.55   2026-09 sweep, as a superadditive pair  ENEMY_NEAR (deleted since), RELIEF_AWARE,
                                      with ENEMY_NEAR 1.15; Stage 0 (0.3,     FAST_GUARD_WEIGHT, DENY_SWAP, the reflood
                                      0.7, ~190 games a cell)                 gate, FEED, REMNANT, RELIEF_IN_FLIGHT
    DEFEND_PAD, RISK_PARITY,   -      Stage 0 of the successor work           everything since Stage 0
      OVERWHELM, BEYOND_DECAY,
      NEUTRAL_MARGIN
    RELIEF_AWARE               0.5    Batch 1a: 0.25 / 0.5 / 1.0 level, 0.5   DENY_SWAP, the reflood gate, FEED,
                                      pre-registered                          RELIEF_IN_FLIGHT
    FAST_GUARD_WEIGHT          0.0    Batch 1a: 0.0 against 1.0, at 24:12     FEED, DENY_SWAP
    DENY_SWAP, _MIN_TURNS      1.0, 4 their own sweeps                        the reflood gate, FEED
    RIVAL_REFLOOD_MIN_TURNS    5      with DENY_SWAP shipped                  FEED
    FEED, FEED_MAX_TURNS       1.0, 2 0.75 and 1.0; caps 2 and 3              RELIEF_IN_FLIGHT
    RELIEF_IN_FLIGHT           0.5    0.25 / 0.5 / 1.0, RELIEF_AWARE at 0.5   -

Every row but the last was read on decided games, so a timeout counted for
neither arm; `RELIEF_IN_FLIGHT`'s counted a timeout as a loss for both.

**The regime gates split lane length between them.** `FAST_GUARD_WEIGHT` acts on
1-turn lanes, `FEED_MAX_TURNS` on boards whose median lane is 2 or less,
`DENY_SWAP_MIN_TURNS` on lanes of 4 or more and `RIVAL_REFLOOD_MIN_TURNS` on 5 or
more. Each was tuned in its own regime. The guard is the one constant live in all
of them, which is why it couples to each, and why its own best value moves by
regime.

### The census (`tools/coupling_census.py`)

**What it does.** Stock duels supply positions. At every fifth turn, up to 50 a
game, marshal's `decide` runs under stock, under each of 37 perturbations (each
constant one step either way from its shipped value; a flag switched off), and
under every pair of perturbations on different constants, about 690 pairs. A pair
**interacts** at a position when its joint change to the orders is not the sum of
its two single changes, `s11 - s10 - s01 + s00 != 0` over ships sent per (source,
destination). No game is played twice and nothing is random, so the census is
exact for the positions it sees. Run with the tool's defaults: the six
cells (`random:` 11:11, 13:3, 18:6, 24:3, 24:12, 18:18), stock marshal against
marshal and actuary (Planned), 8 seeds each, 2,409 contested positions.

**What changes the orders alone.** Share of contested positions, pooled, with
the ships moved per position:

    FRONTIER_GUARD 0.35 / 0.75        31.2% / 12.7%    21.40 / 1.50 ships
    COMMIT_SURPLUS off                20.5%             3.65
    RIVAL_REFLOOD_MIN_TURNS 0         13.3%             3.43    (20-31% on 3 ly/turn)
    FLOW_AVOIDS_ABANDONED off         12.2%             2.17
    RELIEF_AWARE 0.25 / 1.0            8.9% / 3.7%     11.44 / 0.70
    FEED 0.5 / 1.5                     3.9% / 4.6%      1.74 / 1.79 (17-30% at 18:18, 24:12)
    DEFEND_PAD -0.10 / 0.20            3.4% / 3.1%      0.35 / 0.19
    RISK_PARITY, OVERWHELM, RESERVE_PINCER, CONSOLIDATE, REMNANT,
      FAST_GUARD_WEIGHT 1.0            under 1.2% each

**The pairs.** Share of contested positions where the pair interacts, then that
share of the positions where either changes anything:

    COMMIT_SURPLUS x FRONTIER_GUARD               19.0%    46.9%
    FRONTIER_GUARD x RELIEF_AWARE                 13.7%    41.6%    24 ships a position
    COMMIT_SURPLUS x RIVAL_REFLOOD_MIN_TURNS      13.3%    42.3%
    DENY_SWAP_SURPLUS_ONLY x FRONTIER_GUARD       11.8%    36.4%
    COMMIT_SURPLUS x RELIEF_AWARE                  6.2%    22.0%
    FEED x FEED_MAX_TURNS                          6.1%    64.3%
    DENY_SWAP x FRONTIER_GUARD                     5.9%    17.4%
    FRONTIER_GUARD x RIVAL_REFLOOD_MIN_TURNS       5.7%    13.6%
    DENY_SWAP_MIN_TURNS x FRONTIER_GUARD           5.0%    15.4%
    FEED_FRONT x FEED_MAX_TURNS                    4.8%    59.8%
    FRONTIER_GUARD x RELIEF_IN_FLIGHT              4.6%    14.4%
    DENY_SWAP x RELIEF_AWARE                       4.0%    32.3%
    DEFEND_PAD x DENY_SWAP                         3.6%    68.8%
    RELIEF_AWARE x RELIEF_IN_FLIGHT                3.6%    34.5%
    DENY_SWAP x RIVAL_REFLOOD_MIN_TURNS            3.2%    19.9%
    FEED x FRONTIER_GUARD                          2.3%     7.0%    (18.1% at 24:12)

**Masks and enables say how.** Switching the pour off masks the lower guard on 219
positions (it changes the orders alone, and nothing once the pour is off), so
about a third of what the guard does, it does through the pour. The guard and
`RELIEF_AWARE` enable each other (126 and 156 positions: each changes nothing
alone and something once the other has moved), which is what two terms of one
inequality look like.

**The groups.** At 2% the census joins twelve constants into one component, held
together by the guard and the pour switch, which touch nearly everything. Without
those two it falls into the rival strike (the relief weights, `DENY_SWAP` and its
lane gate, the reflood gate, `DEFEND_PAD`) and the front feed (`FEED`, `FEED_FRONT`,
`FEED_MAX_TURNS`). Joined to nothing: `AVOID_ABANDONED`, `BEYOND_DECAY`,
`CONSOLIDATE`, `FAST_GUARD_WEIGHT`, `FLOW_AVOIDS_ABANDONED`, `HOLD_FOLLOW`,
`NEUTRAL_MARGIN`, `OVERWHELM`, `REMNANT`, `RESERVE_PINCER`, `RETREAT_NEAREST`
and `RISK_PARITY`.

**Decision-level coupling is necessary, not sufficient.** Two pairs the census
links measured close to additive in games: the guard and `FEED` (18% of contested
positions at 24:12; twist z -1.6 in outcomes) and the two relief weights (z -0.6).
A pair joined here earns a joint sweep; it is not yet a finding.

### Measuring a group

Every grid below plays each arm against stock marshal on the same seeds
(40000-40299) and cells, both seatings a seed, so any arm compares with any other
seed by seed, across experiments. Arms that change a constant are separate
imports of `models/marshal.py` (as `tools/sweep.py` does it), through a parallel
loop over `sweep.py`'s own `parse_arm` and `play_duel`. Arms that split a floor
run scratch copies of marshal carrying the new constants; at their defaults the
copies rebuilt stock marshal's games board for board (480 games), and the
copies' split arms rebuilt each other's (240). The null is exact: all 1,800 of
stock's cell-seed pairs split 1-1.

Each comparison is per (cell, seed): an arm's score over its two games minus
another's, or a 2x2 interaction contrast `s11 - s10 - s01 + s00`. Points are
win-rate points, z is the mean over its standard error. On a 3x3 grid the
*slope* and *curvature* of each axis and the *twist* (the linear-by-linear
interaction) describe the surface: stock is a joint maximum when both curvatures
are negative and the twist is small beside them, and a negative twist between two
"tighter" axes means the two are substitutes. Every result is scored two ways: a
timeout as half a game (draw), and as nothing (loss).

Every grid plays stock marshal, the opponent most of the earlier sweeps used.
*Against the roster, the optimum moves* then plays the main result against
knower and actuary, and it does not survive the move.

### One guard, two floors

**The guard and the relief weight first** (guard 0.40 / 0.55 / 0.70 by
`RELIEF_AWARE` 0.25 / 0.5 / 1.0, 32,400 games). Points against stock, draw
(loss):

                      relief 0.25      0.5             1.0
    guard 0.40        -2.0 (+2.3)      -0.2 (+3.0)     -1.9 (+0.1)
          0.55        -1.3 (-0.2)       stock          -2.0 (-3.2)
          0.70        -2.4 (-5.4)      -2.2 (-6.1)     -4.2 (-9.6)

    draw: curvature guard -1.04 (z -3.27), relief -1.50 (z -7.75); twist -0.48 (z -2.92)

The twist is real and has the sign of two knobs on one gate: both tighter, or
both looser, is worse than the sum. But the curvature dominates, and scored as
draws stock is the joint optimum of this plane (the fitted maximum, guard 0.50
and relief 0.48, is 0.15 points above it). Scored as losses the guard wants to be
lower (0.40: +3.0, z +4.8), and it gains by ending stalemates (413 timeouts to
stock's 644), not by winning more of the games that end (49.8%). By cell the
guard's best value moves: lower at 24:12 (+3.4 scored as draws), higher at 11:11.

**The guard is the floor for several phases, and they want different floors.**
A variant gave each spending phase its own floor, each defaulting to
`FRONTIER_GUARD`: relief (Phases 1 and 2), the priced strike (Phase 3), the pour
(Phase 3b) and the front top-ups (Phase 4). Phase 0 sets the budget at the lowest
floor in use, and each spender stops at its own. 21,600 + 21,600 + 32,400 +
14,400 games, all on the same seeds:

    arm against stock                          draw            loss            decided  timeouts
    guard 0.40, every floor                    -0.18 (-0.36)    +3.03 (+4.80)   49.8%    413
    guard 0.55, pour floor 0.75                +0.08 (+0.20)    -0.22 (-0.44)   50.1%    666
    guard 0.55, pour floor 1.0                 -0.21 (-0.50)    -0.81 (-1.59)   49.7%    687
    FEED before the pour (pour floor at FEED,  -1.40 (-4.72)    -1.39 (-4.28)   48.3%    643
      raw, on FEED's boards only)
    guard 0.40, pour floor 0.55                +1.56 (+3.27)    +4.53 (+7.66)   51.8%    430
      ...only the strike floor 0.40            +1.19 (+2.56)    +4.08 (+6.94)   51.4%    436
      ...only the relief floor 0.40            +0.08 (+0.41)    +0.22 (+0.82)   50.1%    634
      ...only the top-up floor 0.40            +0.67 (+2.47)    +0.72 (+2.27)   50.8%    640
    guard 0.25, pour floor 0.55                +2.58 (+4.64)    +7.75 (+11.30)  52.8%    272
    guard 0.10, pour floor 0.55                +4.28 (+7.35)   +10.81 (+14.67)  54.5%    174
    guard 0.05, pour floor 0.55                +3.82 (+6.58)   +10.89 (+14.68)  54.0%    135
    guard 0,    pour floor 0.55                +6.22 (+10.33)  +13.53 (+17.72)  56.4%    118
    guard 0.10, pour floor 0.45                +3.78 (+6.55)   +10.39 (+14.25)  54.0%    168
    guard 0.10, pour floor 0.70                +5.26 (+9.02)   +11.47 (+15.30)  55.6%    197
    guard 0.25, every floor                    +0.53 (+0.93)    +5.89 (+8.23)   50.6%    258
    guard 0,    every floor                    -0.07 (-0.10)    +7.00 (+8.42)   49.9%    135

Read down the middle. The strike floor carries the gain (alone it is within 0.4 of
the whole split, z -1.3); relief is null and the front top-ups add a little on
short lanes. Each step down helps, to a floor of 0: the split beats the 0.25 split
by 1.7 (z +2.5) at 0.10 and by 1.9 more (z +2.7) at 0, where every cell gains,
from +3.2 (11:11) to +9.3 (18:6) scored as draws. 0.05 is level with 0.10 (-0.5,
z -0.9), plausibly because `ceil` still keeps a ship at every front that faces
any stack. Games shorten: the median decided game falls from 129 turns to 98,
and the mean over all games from 252 to 159.

**Why the one-constant sweep cannot see it.** The same move made to the guard
alone (the last two rows) pools to nothing scored as draws, because it also
empties fronts through the pour: at 0 it reads -21.2 at 11:11 and -4.9 at 18:6,
and +9.2 and +8.7 at 24:12 and 18:18. Stage 0's guard rows (0.3, 0.7) and the
2026-09 sweep moved both floors together, so both found the compromise.

**Why it wins against marshal.** The guard's ships never entered the gate: a
target the whole front could take, but not the front above its guard, was never
struck. With the strike floor at 0 the gate opens whenever the sources can pay
the price, and the far wave still sends only the shortfall, so a strike spends
into the guard only when it needs those ships. The pour, which sends everything
else, still stops at the floor, so whatever the strike did not need stays home as
the retake reserve the guard census found it to be ([`marshal.md`](marshal.md),
"Is the guard a pool?"). Raising the pour floor alone does nothing (rows 2 and 3),
so against marshal the cost was never the pour but the gate reading the pour's
floor. What the same move costs against a bot that punishes the garrison a strike
thins is the next section.

**The relief weight stays where it is.** A grid crossed the split's guard (0.10 /
0.25 / 0.40) with `RELIEF_AWARE` 0.35 / 0.5 / 0.75, the pour floor at 0.55
(28,800 games). Relief is flat around the new point (slope +0.21, z +1.29;
curvature -0.07) and the twist is small (draw -0.22, z -1.21; loss -0.42, z
-2.20), so the strike floor moved without dragging the relief weight with it.

**The pour floor is regime-shaped.** At a strike floor of 0.10, 0.70 leans better
than 0.55 overall (+1.0, z +1.89), on the slow and middle cells (13:3, 18:6,
24:3), and worse on the fast ones (24:12, 18:18), where 0.45 leans better. On
short lanes `FEED` refills what the pour leaves, and holding the front before the
pour (the `FEED`-first row) costs up to 5 points there, so the shipped order,
pour then feed, is right on those boards.

**On fresh seeds.** The split at 0 / 0.55 was pre-registered as the candidate and
played on seeds no grid had used (41000-41399), in the six cells and at 18:6 with
advantage 1.25 and 1.5 (32,000 games; the null again exact, 3,200 of 3,200):

    arm against stock marshal         draw             loss             decided  timeouts
    split, six cells                  +6.01 (+11.60)   +12.83 (+19.71)  56.2%    147 (stock 802)
      ...first / second half of seeds +5.50 / +6.52    +12.67 / +13.00
    split, advantage 1.25 and 1.5     +1.09 (+1.23)    +15.31 (+12.37)  51.2%    133 (stock 588)
    split + FEED 1.5, six cells       +7.51 (+14.16)   +14.33 (+21.85)  57.7%    147
    split, pour floor 0.70            +6.80 (+12.78)   +13.67 (+20.65)  57.0%    143
      ...advantage 1.25 and 1.5       -0.72 (-0.78)    +13.75 (+11.11)  49.2%    125
    guard 0, every floor, six cells   -0.89 (-1.55)     +5.81 (+8.22)   49.1%    159
      ...advantage 1.25 and 1.5      -11.78 (-12.59)    +3.06 (+2.61)   37.3%    113

Every cell gains (scored as draws, +2.3 at 24:12 to +9.4 at 24:3) and both halves
of the seeds agree. At a high advantage the split holds level on the games that
end and ends three stalemates in four, where the same move to the guard alone
loses 11.8 points. `FEED` 1.5 adds 1.5 more (z +4.66), all of it in the two cells
where `FEED` runs.

### Against the roster, the optimum moves

Every grid above played stock marshal, as the sweeps in marshal's other files
mostly did. The split was then played against knower and actuary, each at its
default (Predict, Planned), on fresh seeds in the six cells, with stock marshal
on the same seeds as the baseline.

**Against knower** (seeds 43000-43299, 18,000 games): every split floor is level
or a little worse on the games that end (-0.5 to -1.6) and gains only by ending
stalemates. It is worse at 11:11 at every floor and better at 24:3.

    arm, against stock marshal's result   draw            loss            decided  timeouts
    stock                                       -               -         58.0%    391
    split 0 / 0.55                        -0.50 (-0.66)   +2.94 (+3.50)   56.9%    143
    split 0.10 / 0.55                     -1.57 (-2.15)   +1.33 (+1.67)   55.8%    182
    split 0.25 / 0.55                     -0.49 (-0.71)   +1.86 (+2.46)   57.0%    222
    split 0 / 0.55 at 11:11               -6.33 (-2.80)   -5.50 (-2.42)
    split 0 / 0.55 at 24:3                +4.25 (+2.62)   +9.67 (+5.02)

**Against actuary** (seeds 42000-42299, 43,200 games): the further the gate
reaches into the guard, the worse, and the guard alone wants to go *up*.

    arm, against stock marshal's result   draw            loss            decided  timeouts
    stock                                       -               -         29.0%    245
    guard 1.0, every floor                +3.00 (+4.57)   +0.72 (+1.04)   31.3%    409
    guard 0.85, every floor               +2.43 (+3.81)   +0.56 (+0.82)   30.8%    380
    guard 0.70, every floor               +1.51 (+2.51)   +0.86 (+1.38)   30.4%    292
      ...only the strike floor 0.70       +0.96 (+1.84)   +0.50 (+0.90)   29.8%    278
      ...only the pour floor 0.70         +0.43 (+0.81)   +0.25 (+0.46)   29.4%    258
      ...only the relief floor 0.70       -0.11 (-1.63)   -0.11 (-1.63)   28.9%    245
    guard 0.40, every floor               -1.88 (-2.90)   -1.11 (-1.67)   27.4%    190
    split 0.40 / 0.55                     -1.17 (-1.99)   -0.25 (-0.41)   28.2%    179
    split 0.25 / 0.55                     -3.10 (-4.68)   -1.36 (-2.02)   26.6%    120
    split 0 / 0.55                        -6.24 (-8.54)   -3.36 (-4.59)   23.9%     38
      ...first / second half of seeds     -6.78 / -5.69   -4.11 / -2.61

The split loses worst on slow lanes (-15.1 at 13:3, -13.8 at 24:3) and only the
fastest cells are level. Raising every floor beats raising any one of them
(the strike floor alone falls 0.6 short of the whole guard at 0.70, z -1.0; the
pour floor alone 1.1, z -2.0; the relief floor alone is inert), so against
actuary the floors pull together, where against marshal they pulled apart. And
the higher guard's gain is mostly losses traded for stalemates: at 1.0 it gains
3.0 scored as draws and 0.7 scored as losses, with 409 timeouts to stock's 245.

**So the split is a best response to stock marshal, not a better bot.** In mirror
games marshal loses to its own stalemates, each side holding a guard the other's
gate cannot clear, and the split ends them. actuary punishes a thinned garrison
the turn it can ([`seat-play.md`](seat-play.md), "What decided the games"), and a
strike that may spend into the guard thins exactly that garrison.
The coupling did not move between opponents: the guard is still several floors,
and the floors still want different values. What moved is where each wants to be.
This is the 2026-09 sweep's `ENEMY_NEAR` lesson again (5 points gained in
self-play, 7 lost against thinker), at a larger size: +6 against marshal, -6
against actuary, for one joint move.

### The long-lane pour: three restraints that cover for each other

On 13:3 and 24:3, where `DENY_SWAP` and the reflood gate are live, a grid crossed
`DENY_SWAP` 0.5 / 1.0 / 1.5 with the gate at 4 / 5 / off, and the guard at 0.40
with each (14,400 games, 600 cell-seeds):

                      gate 4           5               off
    DENY_SWAP 0.5     -3.5 (-4.1)      -4.7 (-6.0)     -8.5 (-9.8)
              1.0     -0.4 (-0.1)       stock          -3.1 (-1.9)
              1.5     -0.5 (-0.3)      -0.8 (-0.8)     -2.1 (-0.8)

    draw: curvature DENY_SWAP -2.19 (z -6.43), gate -1.17 (z -3.57); twist +0.84 (z +3.28)

The two are substitutes: a tight one covers for a loose one (`DENY_SWAP` 0.5 with
the gate at 4 is 1.5 better than the sum of its axes, z +2.29; 1.5 with the gate
off, 1.9 better, z +2.41). Both curvatures are steep, and the fitted joint
maximum (`DENY_SWAP` about 1.2, the gate between 4 and 5) is under a point above
stock, so one at a time did not trap these two. The guard at 0.40 interacts with
both in the same direction (loosening the guard and a pour restraint together
compounds: -1.2 with `DENY_SWAP` 0.5, -2.0 with the gate off), but no reading
clears |z| 2 at this size. Once the floors are split, the pour's third restraint
is the pour floor, so that is the one to re-tune with these two.

### The short-lane front: the pour floor and `FEED`

On 11:11, 24:12 and 18:18, where `FEED` and `FAST_GUARD_WEIGHT` are live, a grid
crossed the guard with `FEED` 0.5 / 1.0 / 1.5 and with `FAST_GUARD_WEIGHT` (18,000
games, 900 cell-seeds):

    arm against stock                draw            loss            decided
    FEED 0.5                         -3.78 (-6.06)   -3.22 (-4.64)   45.9%
    FEED 1.5                         +1.69 (+2.75)   +2.00 (+2.88)   51.9%
    guard 0.40, FEED 1.5             +2.47 (+3.21)   +4.56 (+5.02)   52.6%
    FAST_GUARD_WEIGHT 0.5            -3.53 (-6.93)   -3.61 (-6.62)   46.1%
    FAST_GUARD_WEIGHT 1.0            -5.33 (-9.29)   -5.61 (-9.08)   44.1%

    guard x FEED, draw: twist -0.44 (z -1.62); loss: -0.61 (z -2.11)

`FEED` was shipped at 1.0 having been tried only at 0.75 and 1.0; on these boards
1.5 is better (+3.8 at 18:18, z +3.13) and the fitted best is about 1.6, so its
curve is unfinished rather than coupled. The guard and `FEED` roughly add here.
`FAST_GUARD_WEIGHT` 0 is confirmed in both cells with 1-turn lanes, with no
significant interaction with the guard. `FEED_MAX_TURNS` gates `FEED` outright,
so the two move together whenever either does (6.1% of contested positions in
the census, and on those the gate decides whether `FEED` acts at all).

### The price: relief now and relief in flight

A grid crossed `RELIEF_AWARE` and `RELIEF_IN_FLIGHT` at 0.25 / 0.5 / 1.0 in all six
cells (21,600 games). They sum into one price and still barely interact (twist
draw -0.07, z -0.64): `RELIEF_AWARE` has a sharp optimum at 0.5 (curvature -1.50,
z -6.08) and `RELIEF_IN_FLIGHT` is flat (curvature -0.24). Each can be tuned with
the other held.

### A timeout is a board not taken

The bot column counts only `won` ([`leaderboard.md`](leaderboard.md), "Bot
replays"), so a stalemate there is a board not taken. `tools/sweep.py`'s rate and
most tables in marshal's files read decided games only (its seed pairs counted a
timeout as a draw, and since 2026-10-17 tilt it 0.1 toward whoever was ahead; the
`RELIEF_IN_FLIGHT` measurement counted it as a loss). The first
guard table ([`marshal.md`](marshal.md), first section) held the guard at 0.3
because higher guards doubled timeouts; the 2026-09 sweep then moved it to 0.55 on
decided games (its own timeout column rose from 22 to 39). Mirror games of stock
marshal time out 18% of the time, 30-38% at 3 ly/turn. Under the loss scoring the
guard alone
wants to come down (0.40: +3.0; 0.25: +5.9), and every split row gains more.
Against actuary the split loses under both scorings (*Against the roster, the
optimum moves*), so the scoring is not what decides it. Score both anyway:
`sweep.py`'s own rule is that a better arm must beat the baseline on rate and on
share.

### What to tune together

The groups, with the cells where each is live. A significant twist means the
partner's best value moves when this one does: re-sweep it, along the ridge
between them, whenever either changes. Every optimum named here was found
against stock marshal, and the guard's moved the other way against actuary, so
treat each as a starting point to re-sweep against the roster.

- **The strike floor and the pour floor** (`FRONTIER_GUARD`, split by phase).
  Every cell. Sweep them as two constants, and against actuary first: against
  stock marshal the best pair found is 0 and 0.55, against actuary every step
  down loses and the guard wants raising (the whole guard at 1.0 gains 3.0
  scored as draws, mostly by turning losses into stalemates). The pour floor's
  best value is regime-shaped too (higher on slow lanes, lower on fast ones).
- **The strike gate: the strike floor and `RELIEF_AWARE`.** Every cell.
  Substitutes (twist z -2.9 at stock). At a lower strike floor the relief weight
  stays at 0.5 against marshal, but the two still lean on each other (loss twist
  z -2.2). Against actuary the relief weight wants 0.65-0.75, about 4 points
  (*Screening against the roster*).
- **The long-lane pour: the pour floor, `DENY_SWAP` and
  `RIVAL_REFLOOD_MIN_TURNS`**, with `DENY_SWAP_MIN_TURNS`. Cells of 3 ly/turn.
  Three restraints on one pour that cover for each other (twist z +3.3 between
  the last two). Whenever the guard or a pour floor moves, re-sweep these two
  with it.
- **The short-lane front: the pour floor and `FEED`, with `FEED_MAX_TURNS`.**
  Boards whose median lane is two turns or less (24/12, 18/18), and three once
  the gate is at three (91% of 11/11 boards, 31% of 18/6). `FEED` refills what
  the pour leaves. Against the roster its curve rises to about 3
  and is flat to 6, and `FEED_MAX_TURNS` is its complement (three turns: null at
  `FEED` 1.0, +0.9 at 2.0, +1.7 at 3.0), so move the two together
  (*Screening against the roster*).
- **`DEFEND_PAD` with `DENY_SWAP`** (and `HOLD_FOLLOW`, if it is switched on). A
  product rather than a pair: both holds are multiples of `_defend_margin()`, so
  a move of the pad re-tunes them. Hold the product when one moves.
- **Separable**: the twelve the census joins to nothing (`AVOID_ABANDONED`,
  `BEYOND_DECAY`, `CONSOLIDATE`, `FAST_GUARD_WEIGHT`, at 0 confirmed,
  `FLOW_AVOIDS_ABANDONED`, `HOLD_FOLLOW`, `NEUTRAL_MARGIN`, `OVERWHELM`,
  `REMNANT`, `RESERVE_PINCER`, `RETREAT_NEAREST`, `RISK_PARITY`), and
  `RELIEF_IN_FLIGHT`, which the census ties to the price but which is flat and
  adds to `RELIEF_AWARE` in games. One at a time is fine.

**How to sweep a group.** Run `tools/coupling_census.py` first, to see which
constants share decisions. Then grid the pair, 3x3 about stock, in the cells
where the group is live, on fixed seeds so that every arm pairs with every other
seed by seed. Play it against actuary and knower, not only stock marshal: a grid
against a copy of the bot finds the best response to that copy. Score it both
ways, read the slope, curvature and twist, and confirm the best point on fresh
seeds against every opponent before believing it.

**A floor per phase, for the next sweep.** The smallest form of the split is one
constant: a `POUR_GUARD` beside `FRONTIER_GUARD`, where Phase 3b's pour from a
frontier source stops at `ceil(POUR_GUARD * _max_adjacent_enemy(...))` less what
the source has already sent. About ten lines in the pour's loop; with
`POUR_GUARD` equal to `FRONTIER_GUARD` it changes nothing. It played board for
board the same as the per-phase scratch copy at 0.40 / 0.55 (120 games) and 0 /
0.55 (60). Any edit to `models/marshal.py` moves the bot column's `engine_rev`,
and knower borrows marshal for its rollouts and candidates
([`knower.md`](knower.md), "Borrowed candidates"), so a shipped change moves
knower too and wants the ladder in [`marshal.md`](marshal.md) re-run.
Free-for-alls, where `_wedge` and the pile-up rules are live, were not measured
for the split; the screen's candidate was (*Confirmation*, below).

## Screening against the roster (2026-10-17)

The grids above mostly played stock marshal, and the last of them showed the best
point moving with the opponent. This section looks for joint moves against all
three opponents at once: quick passes on small samples, each built from the last
one's survivors and their combinations, then one confirmation on fresh seeds,
pre-registered.

### The objective

Every arm plays stock marshal, knower (Predict) and actuary (Planned), and stock
marshal plays the same opponent on the same cell, seed and seating as its
baseline; every seed is played in both seatings. A win scores 1 and a loss 0. A
timeout scores 0.55 if the arm's seat was ahead when the clock ran out (most
systems, then most ships including fleets in flight), 0.45 if it was behind and
0.5 if level: 0.1 of a win or a loss on the ±1 scale (`tools/sweep.py`'s `leader`
and `TIMEOUT_LEAD`). Winning sooner mostly comes with winning, so game length is
reported, not scored. Points are win-rate points against the baseline, seed by
seed. An arm's rank is its points against the three opponents averaged with
actuary weighted 1.25 and the others 1, and an arm 4 or more points worse against
any one opponent is vetoed: a lean toward the rival that punishes marshal's
mistakes hardest, without letting a gain there buy a loss elsewhere. The
confirmation also scores a timeout as a loss, the bot column's reading. The games
ran on `tools/sweep.py`'s own primitives (its arm and cell specs, `play_duel`,
`leader`) in a parallel scratch runner, and the pour floor from a copy of marshal
with a floor per phase, which at stock values played board for board as marshal
(64 of 64 games).

### Five quick passes

Each pass played its own fresh seeds in four cells, one in each lane regime where
marshal's gates switch (13 nodes at 3 ly/turn, 18 at 6, 11 at 11, 24 at 12),
against all three opponents: 138,240 games in all.

    pass  seeds         arms  games    asked
    1     50000-50039    20   19,200   single constants, the guard's floors, two pairs
    2     51000-51079    16   30,720   pass 1's survivors, their neighbours and combinations
    3     52000-52079    16   30,720   pass 2's four best changes as a full 2x2x2x2 factorial
    4     53000-53079    15   28,800   one step each way from the factorial's best corner
    5     54000-54079    15   28,800   one step each way from pass 4's two best moves combined

Pass 1, weighted, then against marshal, knower and actuary (standard error
0.5-1.2 weighted and 1-1.5 against each opponent):

    relief weight 0.75             +0.98     -1.6   +0.8   +3.2
    FEED 2.0                       +0.96     -0.6   +1.4   +1.9
    FEED 1.5                       +0.87     +0.3   +0.1   +1.9
    pour floor 0.85                +0.82     +0.6   +0.6   +1.2
    DENY_SWAP 1.25                 +0.30     +0.4   +0.6    0.0
    reflood gate 4                 -0.38     +0.5   -0.5   -0.9
    relief weight 0.35             -1.11     +0.2    0.0   -3.0
    split 0.25 / 0.55              -1.37     +3.0   -2.8   -3.8
    guard 0.85, every floor        -0.26     -4.1   -3.0   +5.0   vetoed
    guard 1.0, every floor         -2.63     -8.1   -5.8   +4.3   vetoed
    strike floor 0.85, pour 0.55   -2.31     -7.6   -0.4   +0.4   vetoed

The guard's level trades one opponent for another in both directions (up for
actuary, down for marshal), and no level of it survives the weighting. What
survives changes where ships go once the guard is set: what a pour leaves home
(the pour floor), which strikes go at all (the relief weight), and where the
leftover flows (`FEED`). Pass 2:

    pour 0.85, relief 0.75, FEED 2.0   +3.10     +0.3   +3.8   +4.8
    pour 0.85, FEED 2.0                +2.44     +2.6   +2.4   +2.4
    FEED 2.0, FEED_MAX_TURNS 3         +2.36     +2.8   +4.2   +0.6
    pour 0.85, relief 0.75             +2.22     +0.0   +1.6   +4.5
    pour 0.85                          +1.97     +1.8   +1.3   +2.7
    pour 1.0                           +1.87     +1.1   +1.8   +2.6
    relief 0.75, FEED 2.0              +1.65     -1.1   +2.7   +3.0
    pour 0.70                          +0.96     +1.0   +1.3   +0.7
    relief 0.75                        +0.91     -1.2   +1.2   +2.3
    FEED 3.0                           +0.58      0.0   +2.1   -0.2
    FEED 2.0                           +0.46     +0.3   +2.2   -0.8
    relief 1.0                         +0.35     -3.2   +0.5   +3.0

### The factorial: what adds, and what interacts

Pass 3 played all sixteen combinations of four changes: P, the pour floor at 0.85
(stock: the guard's 0.55); R, `RELIEF_AWARE` 0.75 (0.5); F, `FEED` 2.0 (1.0); K,
`FEED_MAX_TURNS` 3 (2). An effect is, seed by seed, the mean of the eight arms at
its high level less the mean of the other eight, so every game counts in every
estimate; an interaction is half the difference between one change's effect at
the other's two levels. Points (z):

    effect     weighted        marshal         knower          actuary
    P          +1.65 (+3.20)   +1.25 (+1.65)   +2.27 (+2.53)   +1.47 (+1.53)
    R          +0.97 (+2.37)   -1.75 (-2.63)   +0.26 (+0.42)   +3.71 (+4.76)
    F          +0.96 (+2.44)   +0.98 (+1.76)   +2.32 (+3.28)   -0.15 (-0.21)
    K          +0.42 (+1.11)   +0.64 (+1.12)   +0.25 (+0.33)   +0.38 (+0.61)
    P x R      -0.64 (-2.06)   -0.58 (-1.30)   -0.44 (-0.88)   -0.85 (-1.40)
    F x K      +0.46 (+2.54)   +0.37 (+1.08)   +0.59 (+1.69)   +0.42 (+1.66)
    R x K      +0.20 (+1.41)   +0.59 (+2.77)   +0.04 (+0.18)   +0.01 (+0.02)
    P x F, P x K, R x F: 0.26 or less weighted; three- and four-way: 0.07 or less

Mostly additive: the interactions that clear |z| 2 anywhere are under 0.7 points,
against main effects of up to 3.7. Two of them matter.

- **`FEED` and `FEED_MAX_TURNS` are complements.** K is worth -0.04 at `FEED`
  1.0 and +0.88 at 2.0. Three turns was measured at `FEED` 1.0 and deleted,
  correctly for that `FEED` ([`marshal-flow.md`](marshal-flow.md), "Shipped:
  feed what was just taken"); at a stronger `FEED` the same gate pays. The gate
  decides where `FEED` runs, so its value is in proportion to what `FEED` does
  there: the census's short-lane front group, now in outcomes, and a constant
  that one-at-a-time tuning deleted at the wrong level of its partner.
- **The pour floor and the relief weight are substitutes.** Both hold ships back
  from a rival target, one by what a pour leaves home and one by which strikes
  are priced in at all; with either in place the other is worth less.

And one change depends on the opponent: R gains 3.7 against actuary and loses 1.8
against marshal (z -2.6). The grid that confirmed 0.5 (*The price*, above)
played only marshal, and the weight was first chosen against thinker and
claudebot.

The best corners, raw (weighted; marshal, knower, actuary): PRFK +4.03 (+0.6,
+4.9, +6.1); PFK +3.57 (+3.2, +5.7, +2.2); PRF +2.82; PF +2.67.

### Levels around the corner

Pass 4 moved one constant at a time from C = PFK; pass 5 from D = C with `FEED`
3.0 and relief 0.65, pass 4's two best moves (0.65 rather than 0.75 for its
smaller cost against marshal), which pass 3 says add. "guard" is
the stock guard, the floor for every phase but the pour, which stays at 0.85.
Standard error about 0.85 weighted.

    pass 4, from C                 weighted   marshal  knower  actuary
    C                              +1.88      +2.8     +2.6    +0.6
    relief 0.75                    +3.75      +2.1     +3.9    +5.0
    relief 0.65                    +3.62      +2.7     +3.6    +4.4
    FEED 3.0                       +3.07      +4.6     +4.9    +0.4
    reflood gate 4                 +2.03      +3.0     +2.9    +0.6
    guard 0.65                     +1.88      +0.4     +3.6    +1.7
    FEED 1.5                       +1.83      +2.6     +1.1    +1.8
    reflood gate 7, DENY_SWAP 0.75 or 1.25, pour 0.70 or 1.0,
      guard 0.45                   +1.45 to +1.70
    FEED on every board            +1.32      +2.7     +2.1    -0.4

    pass 5, from D                 weighted   marshal  knower  actuary
    D                              +5.83      +3.0     +7.4    +6.8
    relief 0.75                    +6.46      +3.2     +8.3    +7.6
    FEED 4.0                       +6.43      +3.4     +8.6    +7.2
    FEED on every board            +6.22      +3.4     +7.0    +7.8
    pour 0.70                      +6.17      +3.7     +6.2    +8.2
    FEED 6.0                       +6.14      +3.6     +8.0    +6.7
    guard 0.45                     +6.03      +4.8     +5.3    +7.6
    top-up floor 0.40              +5.83      +2.7     +8.0    +6.6
    pour 1.0                       +5.10      +2.7     +6.6    +5.9
    FEED 2.0                       +4.89      +3.3     +5.1    +6.0
    relief 0.5                     +4.46      +2.6     +8.3    +2.9
    FEED_MAX_TURNS 2               +4.11      +2.5     +4.5    +5.1
    C                              +3.80      +3.2     +5.5    +2.9
    FEED_FRONT off                 +2.48      +0.4     +3.6    +3.2

- `FEED` rises to 3 and is flat to 6 (next section).
- Relief 0.65 gains 3.8-3.9 against actuary from both centres, and 0.75 gains
  4.4-4.7; each moves marshal and knower by 1.3 points or less in either
  direction.
- `FEED_MAX_TURNS` 3 beats 2 by 1.7 at `FEED` 3, all of it on the boards three
  turns admits: 91% of 11/11 boards and 31% of 18/6 have a median lane of three
  turns, so the shipped gate never feeds there (11/11: +4.3, +9.1, +2.8 against
  marshal, knower, actuary). Feeding on every board is level with 3: the slow
  boards it adds (median lane 4-9 turns) read within noise.
- `FEED_FRONT` off costs 3.4.
- Nothing in the long-lane pour group moves with the higher pour floor:
  `DENY_SWAP` 0.75-1.25, the reflood gate at 4 or 7, the floor itself at
  0.70-1.0 and the guard at 0.45 or 0.65 all read within noise of the centre.
  The guard at 0.45 ends a quarter of the stalemates against marshal (92 timeouts
  to D's 125) and leads scored as losses (+6.4 to D's +4.8), but against knower
  it read +0.8 in one pass and -2.2 in the next.

Each centre was re-measured on the next pass's seeds, which shows how much of a
pass's best reading was the selection: P+R+F +3.10, then +2.82; PFK +3.57, then
+1.88 and +3.80; C with relief 0.65 +3.62, then +4.89; C with `FEED` 3.0 +3.07,
then +4.46. Within the noise, in both directions.

### `FEED` is a ratio that stops binding

At the start of every turn of 32 games (D against stock at 18/6 and 24/12), a
front facing a rival held, with our fleets inbound, a median 0.93-0.95 of the
largest rival stack next door (stock: 0.97-1.00). `FEED` f calls a front short
below f, and the share of such fronts that are short is 45-61% at 1, 88-90% at 2,
92-94% at 3 and 95-98% at 6. From about 2 up nearly every front facing a rival is
short whatever `FEED` is, and Phase 4 reads: the rear's surplus flows only toward
fronts facing a rival, never to a front facing only neutrals, and a front facing
only neutrals (never short) hands its leftover to a short neighbour up to a
shortfall that no longer binds. The fronts do not get thicker (the median ratio
barely moves); different fronts get the surplus. That is why the curve flattens
where the short share saturates, and why `FEED_FRONT` is worth more at 3 than it
was at 1.

### Confirmation

Pre-registered before any of its games: D as the candidate, passing on a weighted
gain at z >= 2 with no opponent 4 points or more worse, the same sign in both
halves of the seeds, and neither free-for-all significantly worse. Two variants
measure D's parts: D without the pour floor (constants only: `FEED` 3.0,
`FEED_MAX_TURNS` 3, `RELIEF_AWARE` 0.65), and D without the relief change. Seeds
60000-60299 in the six duel cells and 18/6 at defender advantage 1.25 and 1.5:
2,400 cell-seeds against each opponent, 57,600 games. Points against stock
scored with the tilt, and in brackets with a timeout as a loss:

                                     marshal         knower          actuary         weighted
    D                                +2.85 (+1.08)   +3.79 (+3.38)   +6.83 (+6.08)   +4.67 (+3.71)
      z                              +7.03 (+1.98)   +6.75 (+5.43)  +11.67 (+10.05) +15.07 (+10.75)
      halves of the seeds            +3.6 / +2.1     +3.7 / +3.9     +7.3 / +6.4
    D without the pour floor         +2.54 (+1.65)   +2.55 (+2.35)   +5.86 (+5.44)   +3.82 (+3.32)
    D without the relief change      +3.05 (+2.29)   +2.69 (+2.44)   +3.04 (+2.92)   +2.94 (+2.58)

    what each part adds at D
    the pour floor                   +0.30 (-0.56)   +1.24 (+1.02)   +0.97 (+0.65)   +0.85 (+0.39)
      z                              +0.70 (-1.10)   +2.58 (+1.88)   +1.92 (+1.24)   +3.05 (+1.28)
    the relief change                -0.20 (-1.21)   +1.10 (+0.94)   +3.79 (+3.17)   +1.73 (+1.13)
      z                              -0.64 (-3.05)   +2.82 (+2.12)   +9.09 (+7.29)   +7.79 (+4.59)

By cell, D gains against actuary everywhere (+3.4 at 18/6 with advantage 1.5 to
+12.0 at 11/11), against knower everywhere but advantage 1.5 (-2.0, z -1.3), and
against marshal everywhere, least at 13/3 (+0.3). It decides 53.8% of games
against marshal (stock 50.0%), 65.1% against knower (60.7%) and 34.5% against
actuary (27.0%), and the median decided game against marshal shortens from 154
turns to 143. It times out more often against marshal (1,214 games to 1,044,
ahead in as many as behind), so scored as losses its gain there is +1.1 (z +2.0,
and -0.3 in the second half of the seeds); the relief change carries that (-1.2
as losses, z -3.1).

**A three-seat field** at the default setup (18 nodes, 6 ly/turn), each arm
against knower and actuary in rotating seats, seeds 61000-61299, 3,600 games:

                                   won     timeouts   against stock   z
    stock                          27.8%   36
    D                              32.9%   24         +4.47 (+5.11)   +3.41 (+3.87)
    D without the pour floor       30.4%   28         +2.26 (+2.67)   +2.22 (+2.58)
    D without the relief change    31.0%   26         +2.71 (+3.22)   +2.03 (+2.41)

**A four-seat field** at 24 nodes, 6 ly/turn, each arm against marshal, knower
and actuary in rotating seats, seeds 62000-62199, 3,200 games:

                                   won     timeouts   against stock   z
    stock                          14.8%   19
    D                              18.1%   24         +3.69 (+3.38)   +2.69 (+2.44)
    D without the pour floor       16.4%   17         +1.54 (+1.62)   +1.28 (+1.35)
    D without the relief change    17.4%   29         +3.25 (+2.62)   +2.37 (+1.88)

The pour floor is worth more in the fields than in duels: +2.2 in each, seed by
seed (z +1.65 and +1.68). D passes on every count it was registered against:
weighted z +15.1, no opponent below +2.8, both halves of the seeds ahead against
every opponent, and better in both fields (z +3.4 and +2.7).

### What would ship

Nothing in `models/` has changed. D, as measured, is two pieces.

- **Three constants, no code:** `FEED` 1.0 to 3.0, `FEED_MAX_TURNS` 2 to 3 and
  `RELIEF_AWARE` 0.5 to 0.65. Alone they are "D without the pour floor" above:
  +3.8 weighted, ahead against every opponent under both scorings, and in both
  fields (+2.3, z +2.2; +1.5, z +1.3).
- **The pour floor:** a `POUR_GUARD` of 0.85 beside `FRONTIER_GUARD`, so that
  Phase 3b's pour from a frontier source stops at
  `ceil(POUR_GUARD * _max_adjacent_enemy(...))` less what the source has already
  sent, as sketched in *A floor per phase, for the next sweep*: about ten lines.
  It adds +0.85 weighted at D in duels (z +3.05; +0.4 scored as losses, z +1.3),
  most of it against knower and actuary, and +2.2 in each free-for-all.

The relief change is the opponent-dependent part: +3.8 against actuary and +1.1
against knower, but more stalemates against marshal itself (-1.2 scored as
losses). The weighting here favours it; a tuning against marshal alone would
not.

Shipping either moves the bot column's `engine_rev`, and knower borrows marshal
for its rollouts and as one of its `EXTERNAL_CANDIDATES` ([`knower.md`](knower.md),
"Borrowed candidates"), so knower moves too: re-run the ladder in
[`marshal.md`](marshal.md), "Where marshal stands", update the constants'
comments and the docstring's `FEED_MAX_TURNS` paragraph in `models/marshal.py`
(at `FEED` 3 the boards three turns admits gain against all three opponents),
and run `tests/test_marshal.py`. Still open: the guard at 0.45 under the 0.85 pour
floor (a quarter fewer stalemates against marshal, mixed against knower); D's one
losing cell, defender advantage 1.5 against knower (-2.0, z -1.3); and `FEED`
as a rule rather than a ratio ("feed every front facing a rival"), which the
plateau suggests but which was not measured.

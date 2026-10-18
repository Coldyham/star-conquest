# hunter design notes

`models/hunter.py` is a fork of actuary built to beat it, written without
running actuary's code. It keeps actuary's projection, ledger and greedy, and
replaces the risk term: a threat counts only if a rival would actually launch it.
Every switch has an off position, and with all of them off hunter is actuary
exactly (`tests/test_hunter.py`), which is what every measurement below is
paired against.

## How it was measured

`tools/hunt_bench.py` plays a bot against one opponent in two-seat games, both
seatings on the same map, so a seed scores 1, 0.5 or 0 and z is taken over seed
scores. Wall-clock guards are lifted, so a result is the same on any machine
(6,000 games played on a Kaggle kernel and on a Mac matched game for game). A
game still unfinished at turn 600 scores a half; about 9% of mirror games do. The
five default cells are nodes:ly/turn 7:6, 11:6, 18:6, 24:3 and 18:18, chosen to
span lane length (median lane 5.5, 4.5, 4, 6 and 2 turns); 13:3 (8 turns) and
40:6 (2) were added for the headline. `--variant NAME=VALUE` sets hunter's
module constants for one run (`hunter.configure`), and `tools/hunt_sweep.py`
runs a list of them.

Seed blocks, each used once for one purpose: 9,100,000 screens, 9,200,000,
9,400,000, 9,500,000 and 9,600,000 confirms, 9,300,000 the REINFORCE sweep,
9,700,000 the headline, 9,800,000 and 9,900,000 timing. The unchanged fork read
exactly 50.0% in every cell before anything was switched on.

## Which strikes actuary launches

`tools/hunt_predict.py` plays a bot and, at every one of its decides, asks a
hand-written rule which systems it will strike, then compares that with the
orders it really issues from the same board. The rule: a target is struck if
one of the bot's systems next door, or several whose lanes land on the same
turn, beat its garrison on arrival (production and its owner's inbound fleets
included) on the worst roll (`combat.edge_attacking`).

Against actuary, 30 games across the five cells, the rule caught 2,714 of 2,714
strikes on rival-held systems in total and 98-100% in every cell (recall); it
also named about as many that actuary did not take (precision 0.48), since the
ledger declines half of what it could. Adding the defender's relief (0.3 of its
garrisons next door) changed nothing. So actuary never strikes a rival system
it cannot take on the worst roll, and a garrison above that line is safe from
it.

The same measurement on the other bots (`--ai X --rival hunter`), as the share
of strikes on rival systems by ships sent over garrison on landing (the worst
roll is 1.22 at jitter 0.1):

| bot | below 1.0 | 1.0-1.22 | 1.22 and up |
|---|---|---|---|
| actuary | 0% | 1% | 99% |
| marshal | 10% | 13% | 78% |
| thinker | 16% | 1% | 82% |
| knower | 40% | 6% | 54% |
| claudebot | 14% | 57% | 29% |
| rusherplus | 40% | 14% | 46% |
| heuristic | 27% | 14% | 60% |

The rule is actuary's habit, not a law. A strike below 1.0 mostly loses if the
garrison stays; knower's are snipes of garrisons it knows are leaving.

## Edge risk (`EDGE_RISK`)

actuary's `_risk` sums every rival ship next door over every arrival turn and
asks our garrison to beat it at the nominal roll. hunter's `_edge_risk` asks,
for each launch turn k up to `EDGE_LOOK` and each lane length d, whether the
rival's garrisons at distance d (one system, or several on equal lanes) beat
what we hold at k + d on the worst roll; our own relief counts as before
(`SELF_RELIEF`, landing first only). The worst shortfall share, times
`EDGE_RISK_WEIGHT`, times actuary's stake, is the risk.

This is not the rejected "pricing threats at the worst roll" in
[`actuary.md`](actuary.md) ("What the measurements changed"). That made our
garrison survive the attacker's *best* roll, and hoarded. Edge risk counts a
threat only when the attacker wins its *worst* roll, so it guards less, not
more.

| arm (on top of REINFORCE 0.45 unless noted) | screen 9.1M | confirm |
|---|---|---|
| EDGE_LOOK 1, weight 0.6, REINFORCE 0.3 | 39.6% (z -4.2) | |
| EDGE_LOOK 4 / 40, weight 0.6, REINFORCE 0.3 | 50.6 / 49.2 | |
| EDGE_LOOK 40, weight 1.0 / 1.5 / 2.5, REINFORCE 0.3 | 53.4 / 53.4 / 47.0 | |
| EDGE_LOOK 40, weight 1.0 | 56.8 (z +2.85) | 9.2M 55.5% (z +4.70); 9.4M 55.3% (z +4.79) |

On 9.2M it was +3.8 (z +2.77) over REINFORCE 0.45 alone. A one-turn look was a
disaster: a front thins faster than the rear can refill it.

## Gathering on fast boards (`EDGE_GATHER_LANE`)

Edge risk sat below 50% at 18:18 on every block (43-46%). On one- and two-turn
lanes a rival can bring ships next door in one turn and strike the next, so a
rival system next door also counts, at launch turn k, the rival garrisons
beside it that could have joined it by k. On every board that cost elsewhere
(9.4M: -3.2 pooled, z -2.64, all four other cells down); gated to boards whose
median lane is at most 2 turns, 9.5M read 18:18 44.5 to 52.8% (+8.2, z +2.92)
and 40:6 unchanged (~50% either way). On the headline block 18:18 read 48.8%, so
the gain at 18:18 is real on two blocks of three and fragile.

## Softer strikes on slow boards (`CAPTURE_SWING`, `CAPTURE_LANE`)

actuary guards to the nominal roll next door (`_risk` reads `reach / advantage`),
and hunter, like actuary, struck only on a worst-roll win, so against a
well-guarded actuary system it seldom could. `CAPTURE_SWING` prices a strike on
a rival-held system at a softer swing (1.0 = the nominal roll). On every board
it was null pooled (9.1M at 1.0 / 1.1 / 1.15) but the same shape at each value:
24:3 +7 to +11, 7:6 -7 to -13. On long lanes on fresh seeds (9.6M), 1.0 added
+4.8 (z +2.50): 13:3 +7.8 (z +2.98), 24:3 +1.8. It ships gated to boards whose
median lane is at least 6 turns.

## REINFORCE_WEIGHT

The only actuary constant that screened positive against actuary: 0.45 read
54.4% on the screen, 51.7% (z +2.35) on the confirm, and the sweep on 9.3M put
0.45-0.7 on a plateau of about +2. FRONT_DECAY 0.9 leaned the same way (51.5%)
but did not add. A sweep of 43 single-constant variants of the fork on 9.1M is
in `results/hunter/` (local); the large losers were FRONT_BONUS 0 (14.4%),
TAIL_TURNS 12 (36.4), RISK_WEIGHT 0.3 (40.7) and 0.1 (30.1), MIN_GAIN 0.5-2
(28-35) and TUNED_SWING 1.35-1.5 (31-39). RISK_WEIGHT 0.9 read 54.8% (z +4.78),
which is edge risk's heavier weight again: edge risk replaces that term, so the
two do not stack.

## Look-ahead (`LOOKAHEAD`, on by default)

Each decide builds hunter's plan and the same ledger at other postures (edge
risk weight x0.4 and x2.5, and the nominal-roll strike on every board),
dropping duplicates; on most turns they agree and nothing is rolled. Each
distinct plan is played on a private board for `LOOK_PLIES` (6) turns. Every
rival, and our own seat after the first ply, plays hunter's plain plan from
its own seat: a self-model, so no other bot's code is run. Because hunter is
actuary's fork, that self-model is unusually close to actuary's real play,
which is likely why the gain against actuary is twice the gain against
marshal. The board rolls its own dice (`dice_salt` + `:hunter-rollout`), the
same for every line, so no line sees the game's dice and a score gap is the
plan's. The score is the ledger's worth of the final board from our seat. The
look-ahead is bounded by work, not time: a rollout ply costs systems^1.3 x seats
units, a decide may spend `LOOK_WORK` (4,000), and a line that would pass it is
not rolled. So a seat plays the same on any machine, and a browser only takes
longer. That is four lines on every duel up to 24 systems, two at 40 systems
with two seats, and none at 40 with four seats or at 80 and up. A wall clock
(`LOOK_BUDGET_S`, 1 s, x `BUDGET_SCALE`) is checked before every rollout ply as
a catastrophe guard only; the base plan is rolled first, so running out of
either falls back to plain hunter.

The first version had the clock as the budget (150 ms), checked only between
lines. With the guard live that cost a 75th percentile of 200-730 ms at 40-80
systems and up to 0.99 s, since one line already outran it; and a guard that
trips on most turns makes play depend on the machine (`tools/check_bot.py`'s
reproducibility check flaked). The figures below were measured with the guard
lifted, and the work-bounded version replays them exactly (100 of 100 games on
9.2M across the five cells).

Screen (9.1M, 25 seeds, paired against plain hunter): actuary 70.0% (+11.8,
z +4.63), marshal 78.6% (+5.4, z +2.06), knower at Predict 85.8% (+0.6; knower
runs hunter's own decide, look-ahead included). Confirm against actuary (9.2M,
100 seeds): **68.2% (z +15.5), +11.4 over plain hunter (z +8.61)**, every cell
up:

| 7:6 | 11:6 | 18:6 | 24:3 | 18:18 |
|---|---|---|---|---|
| 67.5% (+7.8) | 71.0% (+12.2) | 64.8% (+16.2) | 73.0% (+7.5) | 64.5% (+13.2) |

Against marshal (9.2M, 100 seeds): 81.2%, +3.5 over plain hunter (z +2.71),
every cell up. Under a browser-sized guard (`BUDGET_SCALE` 0.5 on the Mac, so
wall-clock dependent): 65.7% against actuary, +8.9 (z +7.23), about 80% of the
unlimited gain; 24:3, where rollouts cost most, kept only +1.8.

Stalemates fell to 5.7% of games (plain hunter about 10%).

The browser-budget figure above was measured under the first, clock-bounded
version, so it is a pessimistic reading of a guard that no longer decides
anything below 24 systems.

## Where it stands

Headline, 9.7M, 100 seeds a cell, against actuary: **55.1% (z +5.52)**.

| 7:6 | 11:6 | 18:6 | 24:3 | 18:18 | 13:3 | 40:6 |
|---|---|---|---|---|---|---|
| 59.8% | 55.8% | 49.2% | 64.5% | 48.8% | 59.0% | 48.5% |

It wins where lanes are four turns or longer and is level on fast or big boards.
Single cells move 3-5 points between blocks (18:6 read 59.5% on 9.4M); the
pooled figure has held at 55.1-55.5% on three blocks.

Against the rest of the roster, paired with actuary on the same seeds (9.1M, 100
seeds, five cells):

| opponent | hunter | actuary | difference |
|---|---|---|---|
| marshal | 77.4% | 71.7% | +5.7 (z +4.04) |
| knower, Predict | 86.8% | 83.1% | +3.6 (z +3.29) |
| thinker | 97.2% | 97.0% | +0.1 |
| knower, Search | 38.3% | 27.0% (10 seeds) | 60 seeds for hunter (24:3 52.9%, 18:18 23.3%) |

## What it costs

`tools/hunt_cost.py`: 75th-percentile CPU ms per decide on native CPython (M3),
hunter against actuary, 3-18 ly/turn, 2 or 4 seats. Plain hunter: 2-10 ms at
18 systems, 13-29 at 40, 15-47 at 80-120; fitted 10.2 ms at 40 systems,
exponent 1.28. With the look-ahead under its work budget: 27-79 ms at 18
systems with two seats and 89-208 with four (worst 0.53 s), 27-304 at 40 with
two and 19-76 with four, 15-49 at 80 (no look-ahead). `decide_ms` adds
`COST_LOOK_MS` (0.04) per unit of `_look_work` to the plain fit, so it peaks on
the mid-sized boards the look-ahead still runs on. actuary's fit is 8.3 ms, exponent 0.77: edge
risk reads every launch turn of the horizon. Median and 95th percentile against
actuary in the same games: 7:6 0.5/2.2 ms (actuary 0.2/0.8), 40:6 3.1/20.2
(1.8/6.8).

## Built, measured, removed

- **A predicted counter-punch.** When actuary empties a system it sends 96-99%
  of it, but "this system could take a target alone" picks out the ones that
  empty at precision 0.32, recall 0.28: most emptying is a move to its own
  front. The ledger already strikes a system emptied last turn.
- **Learning each rival's threshold.** `EDGE_SWING` 1.0 and 1.1 (guard to the
  nominal roll, or between) against actuary, marshal, knower and claudebot, 25
  seeds: all within noise (1.1 leaned +4.2 against marshal, +2.2 against
  knower, -1.2 against actuary). The 1.0-1.22 band other bots strike in costs
  little, since those strikes mostly lose the exchange. The switch stays, at 0.
- **A strike this turn decided on our start-of-turn garrisons** (a rival that is
  no oracle cannot see this turn's launches): actuary -1.6, marshal -0.8,
  knower -2.8, 25 seeds. Deleted.
- **Fast-board tuning on the shipped package** (18:6 and 18:18, 50 seeds):
  edge weight 1.5 -3.8, FRONT_BONUS 1.0 -6.0, FRONT_DECAY 0.9 and gathering
  at lanes up to 4 level; CAPTURE_SWING 1.1 everywhere +2.2 and REINFORCE 0.6
  +2.0, both under the bar.
- **A stalemate breaker and an endgame closer.** About 10% of games against
  actuary reach turn 600 undecided (18% at 7:6). 47 of 48 sampled were
  deadlocks: two stacks within 1.5x of each other on one chokepoint, neither
  winning the worst roll. Pricing strikes at the nominal roll once the board
  banks 80-300 turns of income (`STALE_BANK`) broke the deadlock, but the
  beaten side's stack then escaped into thin systems and the game became a
  chase; weighting edge risk 4-8x once 2x ahead on ships (`ENDGAME_RATIO`)
  did not close it. 206 of 400 games changed, stalemates fell 57 to 49, and
  the pooled score moved -0.2 to +0.5 (4 cells, 50 seeds). Deleted.
- **actuary's Style: Learning** is not carried over: its learned risk would have
  replaced edge risk rather than combined with it, and without it hunter keeps
  no memory between turns and is no seat an oracle has to model.

## Open

- Fast boards (median lane 2) and knower at Search are where hunter does not
  win. Both look like they need look-ahead rather than a better price: on
  fast lanes actuary's short horizon is close to right already, and knower at
  Search runs hunter's own `decide`.
- Kernel `hunter-knower-e` measures knower at Search on 60 seeds.

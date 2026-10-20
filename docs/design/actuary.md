# actuary design notes

`models/actuary.py` is the one bot in the roster that is not built on the
shared skeleton. Every other bot from claudebot up walks the systems it owns in
phases (budget, defend, strike, flow the rest forward). actuary has no phases.
It projects the whole board forward, values it in ships, and commits whichever
launch raises that value most until none does. The *Style* knob picks the pull
on its ships. *Greedy* pulls them to every front alike. *Field*, the default,
pulls them to where the balance of force is closest; it is in
[`field.md`](field.md). *Learning* is Field that also prices the risk next door
by a model of each rival kept from turn to turn; it is in
[`learning.md`](learning.md). Until 2026-10-18 stop 1 was *Planned*, the
land-grab played by a planner; it is retired ("The planned opening"). Roster-wide rules and the measurement checklist are in
[`bots.md`](bots.md). Index: [`../README.md`](../README.md).

## The ledger and the greedy

**A timeline per system.** For every system, owner and garrison at each turn up
to a horizon (the longest lane in current travel turns plus `HORIZON_PAD`),
from three things: fleets already in flight, production, and the engine's own
pile-up fold (garrison pooled with its side's arrivals, attackers strongest
first, the survivor against the garrison last). Lanes never interact, so a
system's timeline depends only on what lands there. A candidate launch
re-projects its source and its destination and nothing else, which is what
makes pricing hundreds of candidates a turn affordable.

**Fights are priced against us, but only the verdict.** Who wins a fight that
involves us is decided at the worst roll: our side low, theirs high, the holder
of the system with the defender advantage, and a strike must outnumber the
garrison. Both halves come out of `combat.edge_attacking`/`edge_defending` with
the jitter half floored at `TUNED_SWING`, as the roster rule requires. What we
*keep* when we win is the nominal roll's survivors. See "Pricing the survivors
at the worst roll" below for why.

**One number.** At the horizon:

- a ship we hold is worth 1, plus `FRONT_BONUS` on a system that touches
  anything not ours, decaying by `FRONT_DECAY` per turn of travel back through
  our own systems;
- a system we hold is worth `TAIL_TURNS` turns of its income (`1/production`
  ships a turn);
- a rival's ships and systems count the same, against us;
- income earned before the horizon is already in the garrisons it built, so
  capturing sooner is worth more without a separate discount.

**Risk.** For each system we hold and each turn, the ships a single rival could
land there from its adjacent garrisons (as projected), against our projected
garrison with the defender advantage. The worst shortfall, as a fraction, times
`RISK_WEIGHT`, times what the system is worth to each side. It is an expected
loss, not a hard guard, so a garrison is kept home only where the ledger says
the risk outweighs what the ships would earn elsewhere.

**The greedy.** Candidates are single launches from each source to each
neighbour (all spare ships, half of them, or exactly enough to change who holds
the destination at the horizon) and same-arrival coalitions on targets no
single source can take. Each is priced as the change in the ledger. The best one
above `MIN_GAIN` is committed and the projection updated; repeat until none
pays. Defence, relief, evacuation, strikes and the flow to the front are not
written anywhere. They are what the margin picks. In practice almost every
commit moves a system's whole garrison: over 599 positions, 879 own-to-own
moves and 186 strikes were whole garrisons, 61 were half, 10 were "exactly
enough" and 9 were coalitions.

**It is not an oracle.** The look-ahead extrapolates public state (fleets in
flight, production, adjacent garrisons). It never runs another seat's `decide`
or reads `ai.STRATEGIES`, so it is not flagged `IS_ORACLE`, and knower predicts
it by running it like any other bot.

## Cost, and the caches that make it affordable

A decide re-prices only what a commit could have changed. Every cache entry
(a candidate's price, a source-destination pair's candidate list, a target's
coalitions) is stamped with version counters of the systems it read, within
two hops of either end, since risk next door reads one hop further. A commit
bumps the versions of the systems it touched. `_idle` skips a launch between two
quiet systems of ours toward one no nearer a front, which provably cannot pay
(note that a garrison about to be attacked is never quiet: by the square law a
bigger stack keeps more than the ships added to it). `tests/test_actuary.py`
checks both shortcuts change no answer, and that a priced gain equals the
ledger recomputed from scratch.

Per decide, native CPython, load average ~2 (2026-10, at `MIN_GAIN` 0.05, before
the sweep below; for today's cost see its "What it costs"):

    18 nodes, 6 ly/turn    median 1.6 ms   p99  8.6 ms
    24 nodes, 3 ly/turn    median 1.8 ms   p99 11.0 ms
    40 nodes, 6 ly/turn    median 3.9 ms   p99 15.1 ms
    40 nodes, 18 ly/turn   median 2.1 ms   p99  7.2 ms

That is two to three orders of magnitude above the phase bots (microseconds).
It matters to knower: a Search seat runs every non-oracle rival's `decide` on
each rolled turn of each line, so against actuary its 150 ms `SEARCH_BUDGET_S`
trips sooner and the search stops shallower. actuary declares its cost
(`decide_ms`: 75th-percentile ms = `6.4 * (nodes / 40) ** 0.77`. It was first
fitted as `5.0 * (nodes / 40) ** 0.55` to a grid of 18-120 systems, 3-18 ly/turn
and 2-5 seats at load 0.05, where seats and ship speed barely moved it, and was
refitted for the lower `MIN_GAIN` in "What it costs" below), and knower's setup warning counts it; see "Cost per
decide" in [`knower.md`](knower.md). Offline runs (`bot_replay`, `tests.sim`
with guards lifted) are unaffected. The oracle's single call per turn is well
inside its 50 ms.

## Where actuary stands

Measured before `MIN_GAIN` came down from 0.05 (see "The 2026-10 sweep"), so these
understate it, most of all at 24 nodes 3 ly/turn.

Head to head, both seatings, seeds 301-340 (80 games a cell, timeouts left
out), win rate is actuary's:

                            vs marshal   vs knower-0   vs thinker   vs claudebot
    18 nodes, 6 ly/turn       63%          90%               96%          100%
    18 nodes, 18 ly/turn      73%          91%               99%           98%
    24 nodes, 3 ly/turn       32%          83%               92%          100%
    40 nodes, 6 ly/turn       57%          91%               99%          100%

The first row's marshal and knower-0 cells are seeds 201-300 (200 games each),
and its thinker and claudebot cells come from the default-cell ladder (seeds
1-30). Against knower at Search,
default cell, seeds 401-450: **37%** (37-63). The full roster ladder, with
actuary first in all three cells, is in [`marshal.md`](marshal.md), "Full
roster ladder".

Free-for-all (`--swap`, seeds 301 on): with marshal and thinker, actuary 70 of
120 (59%), marshal 41, thinker 8. With claudebot added as a fourth seat, 58 of
100 (60%), marshal 24.

**The slow regime is the weak one.** At 24 nodes and 3 ly/turn marshal beats it
two games in three. A guess, not tested: lanes there run to 10+ turns, so the
horizon is long and the projection, in which no rival ever launches again, is
furthest from what will happen.

Across the combat sliders (default map, seeds 301-330):

                            vs marshal   vs knower-0   vs thinker   vs claudebot
    advantage 1.5             58%          92%               95%          100%
    advantage 0.75            57%          76%               83%          100%
    jitter 0.0                68%          93%               98%          100%
    jitter 0.3                29%          60%               68%           95%

Jitter 0.3 is the other weak cell; see the next section for what it was before.

## As one of knower's borrowed candidates (measured, not shipped)

As a measure of how different it plays, actuary was appended to knower's
`EXTERNAL_CANDIDATES` in a scratch harness only (`models/knower.py` is
unchanged). Last in the list, it is picked only when it outscores every other
candidate. knower at Search against thinker and marshal, guards lifted, seeds
1-20, both seatings:

    18 nodes, 5628 contested decisions
      default 46.4%  rusherplus 10.6%  actuary 10.4%  heuristic 9.4%
      marshal 9.4%   timid 9.3%       claudebot 4.5%
      actuary's move differs from knower's default on 91.0% of them

    40 nodes, 3201 contested decisions (seeds 1-10)
      default 34.6%  marshal 14.1%  actuary 13.1%  rusherplus 11.8%
      timid 9.3%     heuristic 8.9% claudebot 8.3%
      differs on 97.7%

Contested means we hold a system next to a live rival ("Measure candidate
shares on contested decisions only", [`bots.md`](bots.md)). It is not shipped as
a candidate: it would add its decide cost to every knower Search root.

Whether knower plays better with it was measured on 2026-10-19
(`tools/candidate_bench.py --borrow actuary`, at Field): head to head against
knower at Search, on fresh seeds after knower's `_flee`, +13.4 points pooled (z
+8.69), every cell positive, +16.7 in three seats. That is more than twice what
evolved adds in the same place. Council has borrowed it since it was built.
See [`knower.md`](knower.md), "evolved as a borrowed candidate (2026-10-19)".

## What the measurements changed

**Pricing the survivors at the worst roll.** The first version priced the whole
fight at the worst roll, survivors included. At jitter 0.3 the swing is 1.86x,
so a 12-ship capture of a 6-ship neutral was booked as keeping 4 ships where
the expected outcome keeps 10. Almost no capture paid. It sat on its home
system for 40 turns and lost every game to marshal and thinker (0 of 40 each).
Keeping the worst roll for the verdict and the nominal roll for the survivors:
13% / 35% / 36% against marshal / knower-0 / thinker at jitter 0.3, and the
default cell moved too (54% / 88% / 95%, from 47% / 81% / 91%), seeds 1-30.

**Pricing threats at the worst roll.** The risk term first demanded the worst
roll (`swing / advantage`) against every adjacent rival ship at once, which at
wide jitter hoards every frontier garrison against attacks that mostly never
come. At the nominal roll: 22% / 42% / 59% at jitter 0.3 and 63% / 89% / 97%
at default, same seeds. Kept.

**The constants are on a plateau.** One at a time, against marshal / knower-0 /
thinker, seeds 101-130 at the default cell and 101-120 at jitter 0.3, the
baseline read 62% / 91% / 98% and 31% / 54% / 56%. Every variant fell inside the
noise except one:

    TAIL_TURNS 14 / 36          53-66% vs marshal, inside the noise
    RISK_WEIGHT 0.3 / 1.0       62% / 54% vs marshal, inside the noise
    REINFORCE_WEIGHT 0 / 0.6    52% / 65% vs marshal; 0 a little worse at the default cell
    HORIZON_PAD 1 / 6           68% / 58% vs marshal, inside the noise
    FRONT_BONUS 0.1 / 0.5       57% / 76% vs marshal

`FRONT_BONUS` was re-run on fresh seeds (201 on) at 0.25, 0.5 and 0.75: 0.5 was
at least as good as 0.25 in five of six cells (+4 points against marshal over
200 games at the default cell, +12 against knower-0 at 24 nodes 3 ly/turn, +7
and +9 at 40 nodes), and 0.75 was no better than 0.5. Shipped at 0.5. None of
the others had been tuned then. The plateau was a small-sample reading: see the
next section, where two of these constants are a long way off it.

## The 2026-10 sweep: off the plateau

`tools/sweep.py --strategy actuary`, every arm duelled against stock **marshal**
(the baseline), both seatings, Planned opening throughout. Self-play was tried
first and dropped: two actuaries stalemate to the 600-turn cap about half the
time. Stock actuary is itself an arm, so each variant is compared with it on the
same (cell, seed, seating). The diff counts a timeout as half a win, and the z
is paired. "Inert" is the share of games bit-identical to stock's. Five cells:
18 nodes @6 ly/turn, 24 @3, 40 @6, 24 @12, and 18 @6 at combat jitter 0.3. Seeds
1-353 (about 700 games a cell, 3,400 an arm). The run was planned for 1,000 seeds
and stopped at 58,400 games.

Win rate against marshal; diff and paired z against stock:

                       18@6    24@3    40@6    24@12   18@6 j0.3   pooled         z
    stock              65.9    40.5    58.7    63.0    23.3        50.6
    MIN_GAIN 0         +2.0   +18.6    +9.6    +3.1    +4.0        +7.4   (58.4)  +11.7
    MIN_GAIN 0.5       -6.7    -7.0   -15.2    -5.2    -3.1        -7.4           -10.0
    FRONT_DECAY 0.75   +2.8   +12.3    +7.0    +3.4    +2.3        +5.6   (56.4)   +8.7
    FRONT_DECAY 0.25   -5.9    -5.8   -10.9    -4.2    -2.9        -5.9            -8.8
    RISK_WEIGHT 1.0   -12.3   -11.6    -5.0    -0.8    -4.3        -6.8            -9.3
    RISK_WEIGHT 0.3    -5.9    -1.1    -6.5     0.0    -0.1        -2.7            -3.9
    TAIL_TURNS 14      -5.9    -3.4    +0.7    +1.0    -3.1        -2.1            -2.9
    TAIL_TURNS 36      -1.8    +4.9    -3.1    +1.8    +2.4        +0.8            +1.3
    REINFORCE 0        -3.4    -2.5    -3.0    -1.8    +1.6        -1.8            -3.3
    REINFORCE 0.6      -1.8    -1.1    +2.1    +1.5    -0.4        +0.1            +0.1
    HORIZON_PAD 1      +1.1    +2.4    -0.8    +1.0    +2.0        +1.1            +1.7
    HORIZON_PAD 6      -1.6    +0.5    -2.5    +0.1    +2.5        -0.2            -0.3
    OPENING_CLOCK 3    -1.4    +1.5    +0.9    +2.6    +0.9        +0.9            +1.4

**Two constants were off the plateau, and both bite hardest in the slow cell.**
`MIN_GAIN` and `FRONT_DECAY` are monotonic over the three values tried. Each is
better in every cell at the lower `MIN_GAIN` or higher `FRONT_DECAY`, and worse
in every cell the other way. At 24 nodes 3 ly/turn, `MIN_GAIN` 0 takes actuary
from 40.5% to 60.8% against marshal and `FRONT_DECAY` 0.75 to 53.8%, which closes
the slow-regime weakness in "Where actuary stands". A guess, not tested: in
the slow cell a single launch moves the ledger less per turn of horizon, so a
fixed 0.05 floor refuses moves that pay. A slower decay keeps the front's pull
alive across long interior lanes.

**`RISK_WEIGHT` 0.6 and `REINFORCE_WEIGHT` 0.3 are where they should be.** Both
directions cost. Nothing else moved clearly. `OPENING_CLOCK_SCALE` 3, the open
question from "The clock is about twice the earliest strike", is +1.5 in the
slow cell and +0.9 pooled (z +1.4): not a finding.

**The horizon cap could not be tested at 12 or 20.** The horizon is the longest
lane plus `HORIZON_PAD`, a median of 11 turns at 24 nodes 3 ly/turn (10-13), 8 at
18 @6, 6 at 40 @6 and 5 at 24 @12. Both arms were inert. A cap that bites in the
slow cell would be 6-8.

**Confirmed on fresh seeds; the two do not stack.** Same harness and cells, seeds
2001-2600 (about 1,150 games a cell, 5,750 an arm), pooled diff against stock and
paired z:

                       18@6    24@3    40@6    24@12   18@6 j0.3   pooled         z
    stock              64.4    43.1    56.3    65.8    24.8        51.2
    MIN_GAIN 0         +3.4   +16.7   +12.5    +2.9    +2.2        +7.5   (59.1)  +15.1
    MIN_GAIN 0.02      +2.2    +3.5    +3.7    +1.5    +1.2        +2.4            +5.1
    FRONT_DECAY 0.75   +1.8   +10.8    +9.7    +1.6    +1.9        +5.2           +10.5
    FRONT_DECAY 0.85   +2.8   +14.0   +10.0    +1.3    +1.5        +5.9           +11.7
    both (0, 0.75)     +3.8   +15.7   +11.8    +2.2    +2.0        +7.1           +14.0
    both (0, 0.85)     +2.0   +14.5   +10.7    +2.0    +1.5        +6.2           +11.8
    HORIZON_MAX 8      -0.0    -0.2    +0.0    +0.0    +0.0        -0.1            -0.2

`MIN_GAIN` 0 reproduces to the decimal and is positive in every cell. Most of
it is lost at 0.02, so the floor itself is the problem, not its size.
`FRONT_DECAY` reproduces too, and 0.85 is at least as good as 0.75. Combined,
they are no better than `MIN_GAIN` 0 alone. A guess, not tested: both let the
greedy keep moving interior ships toward the front, one by valuing the front
from further back and the other by accepting the small gains such a move books.
A cap of 8 on the horizon binds only in the slow cell (it is inert in 95-100% of
games elsewhere) and does nothing there either. A long horizon is not the
slow-regime weakness.

**What it costs.** `MIN_GAIN` 0 commits more launches, and each commit
re-prices. Timed on the machine idle (load under 1), one process, every variant
on the same positions (stock actuary against marshal, seeds 5001-5006, up to 250
turns), p75 ms per decide and mean orders:

                    stock           MIN_GAIN 0      FRONT_DECAY 0.75   both (0, 0.75)
    18 @6        2.97   2.5       3.22   3.5       3.18   3.1         3.21   3.5
    24 @3        4.54   2.3       5.99   5.8       5.58   4.7         6.02   5.8
    40 @6        3.98   2.8       4.26   3.7       4.20   3.5         4.23   3.7
    24 @12       2.37   2.4       2.68   3.8       2.66   3.2         2.68   3.8
    80 @6       11.70   7.1      17.71  19.1      15.06  13.0        17.79  19.1

That is 7-13% on the default-sized maps, 32% in the slow cell and 51% at 80
nodes. The cost grows with size faster than `decide_ms`'s exponent of 0.55
assumes. Timeouts in the slow cell also rise, from 7.8% to 10.8%.

**The `decide_ms` refit.** The first fit's machine is not this one, so only a
ratio is carried over, as knower's "Cost per decide" does. The grid was 18, 24,
40, 80 and 120 systems x 3/6/18 ly/turn x 2/5 seats, with every seat stock actuary
and seeds 1-2 to turn 200. Every live seat was timed every 5 turns with both
constants on the same position, using thread CPU time, at load under 1. The p75
ratio, `MIN_GAIN` 0 over 0.05, ran 1.0-1.25 at 18-24 systems, 1.0-1.8 at 40,
1.2-1.6 at 80 and 1.4-2.2 at 120. Fitted:

    ratio  1.33 x (systems/40)^+0.22 x (seats/2)^-0.07 x (ly/6)^-0.03
    systems only:  1.28 x (systems/40)^+0.22

Seats and speed barely move it, as before. Folded into the old fit, that gives
`COST_DECIDE_MS` 6.4 (5.0 x 1.28) and `COST_NODES_EXP` 0.77 (0.55 + 0.22): 7.2 ms
at 64 systems where it was 6.5, and 14.6 at 120 where it was 9.1.

**Against the rest of the roster it holds.** Same harness, stock and `MIN_GAIN` 0
as the only arms, seeds 3001 on (150 seeds, 300 paired games a cell; knower at
Search 100 seeds, cut short at 40 nodes). Win rate stock → `MIN_GAIN` 0:

                        vs thinker      vs knower-1      vs knower-2 (Search)
    18 @6               94.2 → 95.5     70.5 → 78.2      41.0 → 43.5  (z +0.9)
    24 @3               87.8 → 95.8     65.8 → 76.8
    40 @6               97.3 → 99.7     50.7 → 70.8      37.1 → 45.7  (n 70, z +1.9)
    24 @12              96.5 → 96.5     51.0 → 53.8
    18 @6 jitter 0.3    63.3 → 68.3     43.7 → 45.7
    pooled diff         +3.3 (z +5.9)   +8.7 (z +8.9)

Against marshal across the advantage sliders, pooled +7.2 (z +6.6): at 18
nodes 59.5 → 64.3 (advantage 0.75) and 54.3 → 61.8 (1.5), at 40 nodes 56.7 →
69.0 and 58.7 → 63.0. Free-for-all (`tests.sim --swap`, actuary + marshal +
thinker, seeds 301-400, 300 games each, the variant as a scratch copy of the
module): actuary's share of wins 61% → 63% at 18 nodes and 61% → 69% at 40,
taken mostly from marshal. No cell got worse. knower at Search is the one
opponent where the gain is not yet clear of the noise, at a small sample.

**Shipped** (2026-10) as `MIN_GAIN` 1e-9, with `FRONT_DECAY` left at 0.5 and
`decide_ms` refitted. The floor is an epsilon, not exactly 0. At 0 a launch the
`_idle` prune skips as unable to pay could still price at +4e-15 of float noise
when priced, so the cached and uncached plans disagreed (`test_the_caches_change_no_answer`).
The two floors choose different orders on 4.5% of decides. Paired against each
other through marshal, seeds 4001-5200 in the same five cells (12,000 games), 1e-9
reads -0.12 points (z -1.1), and no cell is significant: the same bot.

## Behind on income, ahead on ships (measured, not built)

The ledger values ships, and gives ground rather than lose them: a doomed
garrison leaves, and while it is behind no strike clears the worst roll. Asked
from two games against learner (2026-10-08), the idea was a switch for one
standing: behind the top-income rival on income but ahead on ships. That ship
lead is a wasting asset, gone in *ship lead ÷ income gap* turns (the clock).
Holding still lets it run out, and when the expected outcome is a loss, variance
is worth buying. While the clock is short, the switch would price strikes at the
nominal roll and drop `RISK_WEIGHT` towards 0.

**How often the standing arises** was counted first, since a switch that changes
rare decisions measures null on the ladder whether or not the idea is right
(learner's Trust, [`learner.md`](learner.md)). The method:

- **Games:** 280 roster self-play games, the eight `models/` bots in every
  pairing, both seatings, at 6 ly/turn: 11, 18 and 24 nodes with two seats, and
  11 and 18 with three (56 games a cell). Also the 142 posted human games.
- **Standing:** each seat on each turn after contact (it borders a rival), against
  its top-income rival, with ships counted in garrisons and in flight.

Share of post-contact seat-turns, and how often that seat went on to win:

                                  self-play          posted human games
    ahead on both                 37%   79%          27%   90%
    ahead on income only           6%   39%           5%   52%
    behind on income, ahead ships  7%   31%           7%   31%
    behind on both                48%    4%          56%    4%

**The standing is mostly parity, and a real lead is already converted.**
- **Short episodes, tiny leads.** Episodes last a median of 2 turns. On a short
  clock (under 10 turns) the median lead is 1-2 ships, and the episode mostly
  ends with the lead lost in one exchange, not worn away by income.
- **A real lead is rare and already won.** With a lead of 20%+ of the rival's
  ships, actuary has 76 such episodes in its 84 games and wins 87% of them;
  learner has 68 and wins 74%.
- **The switch's case is a handful.** A real lead on a short clock happens 4
  times in actuary's 84 games, 7 in learner's, and twice for actuary across the
  posted games. Every posted game is a win for the person, so the bots' win
  rate there says nothing.

Not built: the switch would change too few decisions to measure. The standing
where seats actually lose is behind on both, about half of all post-contact
turns, and it was the real one in the game that prompted this. Variance-seeking
there is a separate idea, unmeasured. A seat short of ships has less to gamble
with.

## The planned opening (Opening: Planned; retired 2026-10-18)

Stop 1 was Planned until the field replaced it: by then the plan ran only on
24+ systems with lanes of two turns or less, and fulcrum, with no plan at all,
scored 64.3% against it at 24 nodes and 12 ly/turn, the one cell of five where
the plan was on ([`field.md`](field.md)). The code is in git history, up to the
commit that folded fulcrum in. What follows is the record of it.

The seat's `aux` knob (`AUX_LABEL` *Style*, *Opening* until 2026-10-09) has
three stops. *Planned* (1, the default, and anything unreadable) runs
`opening(state, pid)` until the seat first borders a rival, and the ledger from
then on. Since 2026-10-13 it does so only where `_plans` allows it: a map of
`OPENING_GATE_SYSTEMS` (24) or more whose median lane is `OPENING_FAST_LANE` (2)
turns or less. Elsewhere Planned opens with the ledger, so it plays exactly as
Greedy does (see "Planned against Greedy again" and "The gate, shipped"). *Learning* (2, and anything above) plays the same gated opening, and its
ledger prices the risk with each rival's learned strike curve
([`learning.md`](learning.md)). *Greedy* (0) is the
ledger from the first turn, exactly as actuary played before the knob existed:
0 of 930 decisions differed. Planned became the default before actuary was
published, so no shared challenge link ever carried Greedy as actuary's
untouched setting. It was built as a standalone bot, surveyor, and folded in;
Planned's orders equal surveyor's on all 930 of the same decisions, so the
measurements below apply to it unchanged. Every figure in "Where actuary stands"
and "As one of knower's borrowed candidates" above was measured at Greedy.

### Why the opening

On opening positions (no system next to a rival) the roster plays one land-grab.
`tools/bot_distance.py` puts knower, marshal and actuary within 0.17-0.19 of each
other at 18 nodes, and thinker and claudebot within 0.03
([`bots.md`](bots.md), "How differently two bots play"). The right land-grab
depends on the map, though. On a big map with few players the first stage of the
game is taking as many neutrals in as few turns as possible. On a small one, the
fight arrives before a neutral has repaid the ships it cost, and the player who
took it is behind on ships even while ahead on income. Until a seat borders a
rival nothing it does can be contested, so the opening is a one-player puzzle,
and a plan for the whole of it can be searched.

### The plan

- **The region.** The neutrals strictly nearer us than any rival, in lane turns.
  A neutral equally near both is where contact happens, and is left to the
  ledger.
- **The clock.** The earliest turn a rival's ships could land on anything on our
  side (owned or region), times `OPENING_CLOCK_SCALE`, capped at
  `OPENING_CLOCK_MAX`.
- **The score.** Ships held at the clock (garrisons and in flight), plus the
  income rate at the clock times `OPENING_INCOME_CLOCKS` × the clock. A capture
  costs the ships lost at the nominal roll (the launch is sized for the worst
  one) and earns its income from landing to the clock, plus the weighted rate.
- **The search.** Twelve policies, `OPENING_ORDERS` × `OPENING_SENDS` ×
  `OPENING_SKIPS`: take the most valuable, nearest or cheapest adjacent neutral
  first; send just enough or everything; skip a capture that does not pay, or
  not. Each is played forward to the clock on our side of the map alone, with
  continuous ships and production. A system with nothing to take sends its
  ships one hop towards the nearest of ours that has. The first turn of the
  best-scoring policy is played, and the search runs again next turn.
- **The hand-over.** The opening ends when an owned system borders a live
  rival's, when a rival fleet heads for one of ours, or when our side of the map
  (what we hold plus the region) is under `OPENING_MIN_SIDE`, 7 systems. On a
  small map that is from the first turn. See "A side too small to plan".

Cost per opening decide, native CPython: median 0.5 ms at 18 nodes, 0.3 ms at
40, 2.9 ms at 120 (max 7.3), all below the ledger's. `decide_ms` is unchanged.

### The clock is about twice the earliest strike

Between two Greedy actuaries, the median earliest turn a rival could reach us,
against the turn the first held system actually fell (20 seeds a cell):

    6 nodes   14.5 against 26.5
    18 nodes  22.5 against 44
    40 nodes  27.5 against 51.5

Planned at each setting against Greedy, paired, both seatings, seeds 1-30:

                        8n    18n   40n
    clock 1, weight 1   48%   43%   40%
    weight 2            57%   46%   45%
    weight 4            52%   51%   54%
    clock 2             50%   55%   57%
    clock 2, weight 2   53%   62%   59%
    region slack 2      43%   32%   40%

Taking neutrals up to 2 turns past the halfway line costs a lot. Lengthening the
clock and weighting income both help. On fresh seeds (101-160, 120 games a
cell), the neighbours of the best:

                        12n   18n   24n@3  40n
    clock 2, weight 2   53%   52%   54%    64%
    clock 2, weight 4   51%   53%   55%    64%
    clock 3, weight 2   49%   48%   60%    63%
    clock 3, weight 3   50%   48%   60%    66%

The 18-node 62% did not replicate. 40 nodes did, at every setting (z ~3), and
the slow cell leans the same way. Shipped at clock 2, weight 2. The slow regime
may want clock 3; it times out a third of the time there, so it was not chased.

### Where Planned stands

Measured before the side floor (next section), which changes nothing on a map
where our side starts at 7 or more.

**Against the roster, the gain is on big maps** (seeds 101-130, 60 games a cell
less timeouts, win rate against the column):

                        vs marshal          vs knower-1          vs thinker
                     Planned  Greedy      Planned  Greedy      Planned  Greedy
    12 nodes            74%     63%          72%     79%          92%     95%
    18 nodes            59%     76%          83%     75%          95%     98%
    24 nodes @3         42%     45%          65%     61%          93%     91%
    40 nodes            62%     53%          56%     45%         100%     98%
    80 nodes            43%     25%          31%     16%         100%    100%

Pooled over 40 and 80 nodes against marshal and knower, 48% against 35%, about
three standard errors. Below that it is inside the noise either way, except
18 nodes against marshal, which reads worse. This matches the map-size split it
was built for: the plan pays when the opening is long.

**On the board's own maps it finishes faster more often than it is slower.** Each
of the 103 generated challenge maps (`game_summary`, 2026-10-05), with the bot
through the human's seat as the bot column does: Planned won 61, the same as
Greedy. It was faster on 31 maps, slower on 23 and the same on 49, and at or
under the best human score on 5 (Greedy 2, marshal 2, knower at Search 12, which
wins 77). By size it was faster/slower on 10/8 (seven systems or fewer), 10/6
(8-13), 5/5 (14-24) and 6/4 (25+).

**Its opening is a variant, not a new family.** On 40-node opening positions it
is 0.18 from Greedy and 0.22 from marshal, the same spacing those two have from
each other. More of what it launches goes between its own systems (60% against
Greedy's 44%): ships with nothing to take move towards the edge of the
expansion.

### Planned against Greedy again (2026-10-13)

Re-measured after `MIN_GAIN` 1e-9 shipped and with `SELF_RELIEF` in hand, because
Greedy gained more from it than Planned at first sight. `tools/sweep.py`, fresh
seeds 6001 on (7001 on where marked), four arms against one baseline in both
seatings: Planned and Greedy, each stock and with `SELF_RELIEF` 0.8 lane-aware
("+0.8"). Paired is games Greedy did better than Planned in, against worse, on
the same cell, seed and seating, so a positive z favours Greedy.

    against marshal         Planned  Greedy   paired        Planned+0.8  Greedy+0.8  paired
    12@6   (6001+)            68.8    72.3   z+1.98          73.6        78.8      z+2.92
    12@6   (7001+)            64.6    68.6   z+2.06          71.5        73.8      z+1.04
    18@6   (6001+)            66.3    77.3   z+2.89          79.3        83.2      z+1.77
    18@6   (7001+)            67.2    68.6   z+0.69          74.0        73.8      z-0.25
    24@3                      61.9    63.8   z+0.74          73.4        73.4      z-0.48
    40@6                      67.5    66.7   z 0.00          72.5        77.5      z+1.60
    80@6                      77.9    76.7   z-1.18          78.5        81.0      z+0.46
    18@12                     71.8    72.5   z+0.12          75.8        75.9      z 0.00
    24@12  (6001+)            65.1    65.8   z+0.23          74.1        68.7      z-2.46
    24@12  (7001+)            68.0    67.4   z-0.20          71.3        68.3      z-1.22
    40@12                     65.0    60.3   z-1.69          67.9        59.4      z-2.71
    against knower-1
    12@6, 18@6 pooled         80.7/76.8  84.9/78.8  z+2.12   83.9/82.6   85.9/86.6  z+1.50
    40@6, 80@6 pooled         64.5/74.8  63.5/72.5  z-0.63   80.3/84.4   78.0/81.6  z-1.00

Style: Learning opens with the plan too. `LEARNING_PLANS` (a measurement flag,
on) lets it open with the ledger instead. Same seeds, against marshal, paired
favouring the ledger opening:

    Learning                 plan    ledger   paired        plan+0.8    ledger+0.8  paired
    12@6                     74.1     77.4   +19/-14        80.9        82.1      +15/-9
    18@6                     70.0     77.2   +40/-22        81.0        81.5      +29/-22
    24@3                     70.5     70.5   +33/-29        75.9        80.3      +34/-26
    40@6                     68.4     70.5   +32/-24        73.9        79.8      +31/-15
      pooled                                 z+2.40                               z+2.75
    24@12                    72.1     65.0   z-2.19         74.4        67.0      z-1.91
    40@12                    64.8     60.4   z-1.54         66.3        62.8      z-1.09

**The plan pays only when lanes are fast.** At 6 and 3 ly/turn the ledger opening
is level or better at every size, at both stops and against both opponents. On
small maps it is better outright (12 and 18 nodes against marshal and knower), and at
Learning it is better pooled. At 12 ly/turn on 24 and 40 nodes the plan is better
at both stops, by 5-8 points, and most clearly with `SELF_RELIEF` on. At 18
nodes and 12 ly/turn they are level. The earlier table above, which credited
the plan at 40 and 80 nodes, was 30 seeds before `MIN_GAIN` shipped and does not
reproduce. Why fast lanes favour the plan is not established. One guess, not
tested: with 1-2 turn lanes the ledger's horizon is only 4-5 turns, too short to
see an expansion pay, while the plan scores a whole land-grab.

**So deleting the plan would cost actuary at fast speeds.** Gating it would
not. The proposal: open with the plan only where lanes are fast (the median lane
of 2 turns or less, which is what 12 ly/turn gives on 24-40 nodes) and the side
is big enough to plan, at either stop, and with the ledger everywhere else. With
the gate, Planned and Greedy differ only by that one rule, so the stop between
them could go: `AUX_RANGE` (1, 2, 1) with two names, and `_stop_of` reading any
stored value under 2 as the gated opening plus ledger. That narrows the knob on
read, without rewriting a stored `aux`, so no challenge key moves. The gate's
threshold is unmeasured.

### The gate, shipped (2026-10-13)

Shipped together with `SELF_RELIEF` 0.8 lane-aware: Planned and Learning open
with the plan only where `_plans` allows (24+ systems, median lane of 2 turns or
less), and with the ledger elsewhere. At the default 6 ly/turn no map under 24
systems qualifies, nor most 40- and 80-node maps, so there Planned plays exactly
as Greedy. The three stops stay, so no stored `aux` reads differently. Measured
against marshal on fresh seeds 8001-8100, both seatings. "old" is the bot as it
shipped before (`SELF_RELIEF` 0, gate off: 687 positions checked identical).

                     shipped  ungated  Greedy   old      Learning  Learning old
    12@6              77.4     76.3     77.4    72.3      82.1      77.0
    18@6              78.7     74.3     78.7    67.7      78.6      78.0
    24@3              71.1     65.6     71.1    58.9      81.5      66.7
    40@6              74.2     72.5     70.9    62.5      73.5      69.3
    18@12             73.8     77.3     73.8    72.2      78.5      74.0
    24@12             67.7     67.7     70.4    66.7      72.2      67.0
    40@12             67.5     67.5     64.0    55.6      62.6      56.3

    paired, shipped over old:          +226/-110  z+6.3  (every cell positive)
    paired, Learning over Learning old: +201/-120 z+4.5
    paired, shipped over ungated:       +116/-95  z+1.45
    paired, shipped against Greedy:     identical where the gate is shut; +57/-52 where it opens

Nearly all of the gain is `SELF_RELIEF`. The gate is a small, consistent lean
over running the plan everywhere, worth having mainly because it makes the
default play what measured best on the maps people play.

### A side too small to plan

Found on a 7-system board (key `7e42b0d745b15833`, two seats, claudebot), where
the bot column, computed before Planned existed, showed actuary winning in 19
turns; Planned took 55.
One game says little: claudebot breaks ties on `state.rng` and every fight draws
from it, so one ship's difference moves the rest of the game. The figures here
re-seed `state.rng` right after `build_state`, so each map is played several
times with different dice. Over 40 such streams on that board Greedy's median
win was 22 turns (13-165) and Planned's 36.5 (16-105).

**Where Planned lost.** Through the human's seat, Planned against Greedy, against
each of the roster, seeds 1-40 and five dice streams each (win rate, paired z):

    2 seats   5 nodes   72.7 -> 68.3  (z -5.0)
              6 nodes   72.1 -> 69.1  (z -3.1)
              7 nodes   81.9 -> 77.5  (z -4.7)
              8+ nodes  level (z within +-1.1)
    3 seats   7 nodes   63.2 -> 57.7  (z -4.7), level from 15
    4 seats   9 nodes   56.0 -> 53.3  (z -2.0), level from 16

**What predicts it** is how much of the map is ours to plan. Binned per map by
the region at turn 0, Planned minus Greedy in win points: 0-2 neutrals -1.1 to
-3.4 (z -1.6 to -4.0), 3 -1.4, 4-5 -0.7, 6-8 +0.7, 9 and more about +3. Lanes
from home to the nearest rival reads the same way (2-3 lanes -2.3 to -3.3, 7 or
more positive). The earliest strike in turns barely separates them.

**Handing over earlier by distance does not work.** Ending the opening when an
owned system is within 2 or 3 lanes of a rival's (rather than bordering one)
fixed the small maps, but lanes to a rival shrink as the seat expands, so on a
big map it cut the end off every opening (win rate, vs the roster):

                        Greedy   1 lane   2 lanes   3 lanes
    2 seats, 80 nodes    70.1     78.5     76.4      72.9
    3 seats, 40 nodes    74.2     79.6     77.1      76.2

**Our side of the map does.** Held plus region stays roughly constant through an
opening, as the region turns into systems we hold, so a floor on it stops only
the openings that were too small from the start. At turn 0 it is a median 3
systems on 7 nodes with two seats, 8 on 18, 19 on 40 and 38 on 80; 4 on 40
nodes with six seats. Seeds 101-140, against Planned without the floor, pooled
over two runs, one of 5-24 nodes and one of 10-80:

    floor     5-24 nodes           10-80 nodes
      4     +0.8  (z +3.5)
      5     +1.5  (z +5.1)
      6     +1.7  (z +5.4)
      7     +1.9  (z +5.5)     -0.1  (z -0.3)
      9                        -0.7  (z -1.7)
     12                        -0.6  (z -1.1)
     16                        -0.7  (z -1.3)

Shipped at 7. Confirmed on fresh seeds 301-330 against the previous actuary
loaded as a separate bot, so it is also measured against itself (21 cells, 2-6
seats, 5-80 nodes, 8,778 paired games): +1.9 points at 24 nodes and under
(z +5.4), +0.8 at 40 and 80 (z +1.8), +1.8 overall (z +5.6). By opponent: marshal
+3.9, knower +3.1, thinker +1.8, the previous actuary +1.5, the rest +0.2 to
+1.4. On that 7-system board it now plays Greedy's game exactly.

**Not a bot-column fix.** The board's bot column could instead try both stops
on every map and keep the better. On this board that would mostly have picked
the luckier dice, not the better opening, and it would choose the profile with
hindsight per map where `bot_replay.replay_aux` chooses it once per bot. Fixing
the default fixes the board and the menu alike.

**Open:** with two seats on 10-18 nodes Planned still trails Greedy a little on
seeds 301-330 (-1.3 at 10, -3.2 at 18, z about -2.3), though not on seeds
101-140. A higher floor costs the 40-80 node gain, so it would need another
signal.

### In front of marshal it costs

As a standalone bot the opening could hand over to any delegate. With marshal
in place of the ledger, against plain marshal, seeds 101-160:

                        8n    12n   18n   24n@3  40n
    clock 2, weight 2   41%   48%   40%   41%    45%
    clock 1, weight 1   48%   38%   41%   34%    42%

A guess, not tested: marshal is a rusher ([`marshal-pricing.md`](marshal-pricing.md),
"Rushing the enemy") and wins the contested middle that the region leaves alone.
Whether an opening helps depends on what plays after it, so measure it in front
of each host before folding it into another bot.

### The first attempt: rules about contested neutrals (deleted)

surveyor's first version was not an opening planner. It edited actuary's plan
with rules about neutrals a rival might also take: land the turn after a rival
racing for one, since whoever fights a garrison first pays its strength squared
(12² - 9² - 4² = 47 against 12² - (9² - 4²) = 79); veto a capture the rival
could take straight back, and keep a strike-back force beside it; veto a capture
that does not repay before the rival's earliest possible arrival. It won the
5-system board (key `06a74fc834bdf656`) exactly as its best player did, in 17
turns, where every other bot took 64 or more. But paired against actuary it lost
in every cell, 20-50%. The landing-after rule alone was null-to-worse (43-50%),
as the remnant tactic was inside marshal
([`marshal-pricing.md`](marshal-pricing.md), "Racing a third player"). Against
the rest of the roster it was level or worse than actuary, and on the board's
maps it won 46 to actuary's 61. Waiting pays against a rival that races for
neutrals (heuristic) and loses against one that does not, so whether to wait is
a question about the rival.

## Crossing transfers (measured, deleted)

Found playing by hand (`seat-play.md`): the greedy sometimes commits A->B and
then, the projection changed, B->A between two of our own systems on the same
turn. Both stacks fly a lane's length and both garrisons sit thin meanwhile,
where one launch of the difference reaches the same final split with more held
on the way. Instrumented over 40 duels against marshal at 24 nodes: 273 crossings
in 22,582 launches, about 7 a game, 3.4 ships a side over a 2.8-turn lane.

`NET_CROSSINGS` netted each pair into one launch after the greedy finished.
`tools/sweep.py`, actuary mirror, 150 seeds, three cells (18 and 24 nodes at 6
ly/turn, 24 at 12):

    arm   18n@6   24n@6   24n@12   pooled                 seed pairs
    net   48.3%   48.1%   49.8%    48.7% (424-446)        +17/-28, z -1.64

Null, leaning worse in both halves. A plausible reading is that a stack in flight
cannot be struck and a crossing is the greedy rotating its front stack with the
fresh one behind it. Either way it is not waste worth removing. Deleted.

## Neutrals at less than the worst roll (measured, deleted)

Also from hand play: actuary books a capture only if it wins at the worst roll
(1.222x at the default jitter), and a static neutral can neither flee nor be
relieved. In game 404 of `seat-play.md` it spent ten turns saving up 10 ships for
an 8-ship neutral, and at 1.10x an attacker wins 86% of the time, at
1.15x 95%. `NEUTRAL_SWING` priced a capture of a neutral at that swing instead, in
the ledger's `_fight` and the opening's `need` alike. Same harness and cells as
above:

    arm             18n@6   24n@6   24n@12   pooled               seed pairs
    swing 1.10      47.5%   47.5%   47.6%    47.5% (407-449)      +69/-96, z -2.10  REPRODUCED worse
    swing 1.15      48.4%   48.9%   51.5%    49.6% (424-430)      +70/-73, z -0.25

The same answer marshal got from `NEUTRAL_MARGIN` 1.2 (47.1%,
[`marshal-flow.md`](marshal-flow.md)). The lost roll costs a whole fleet and the
tempo with it, and over every neutral in a game that outweighs the turns saved.
Deleted.

## A front stack pins the whole side (`SELF_RELIEF`, shipped 2026-10-13)

Found playing by hand (game 707 in `seat-play.md`): with one of my stacks parked
beside each of actuary's two frontier systems, actuary held 3 systems for 52
turns and never took the neutrals behind it. Its ledger priced 3->10 (7 ships at
a 5-ship neutral) at -5.25, nearly all of it the risk at 3. `_risk` sets
everything a rival could land against the garrison alone. It never counts what
our own neighbouring garrisons could send in time, so a rival stack next door
reads as a shortfall even when two of our stacks stand one lane behind.

`SELF_RELIEF` adds that share of our neighbouring garrisons to the garrison the
risk term compares against, per turn, by when each could land (`_own_relief`).
Two exactness costs come with it. A launch between our own systems now moves a
neighbour's risk, so `_gain` re-reads the risk around any of ours it touches.
And `_idle` no longer prunes a move next to a system with priced risk. The
cache test passes with it on. Learning's per-rival path is untouched, so
`test_learning_counting_every_garrison_in_full_plays_as_planned` fails with the
flag on, as it should.

Mirror against stock actuary, 120 seeds, three cells:

    arm     18n@6        24n@6        24n@12       pooled               seed pairs
    0.5     56.1%        56.5%        46.8%        53.1% (369-326)      +58/-35 z+2.38  REPRODUCED better
    1.0     46.5%        48.5%        38.9%        44.6% (313-389)      +37/-77 z-3.75  REPRODUCED worse
    1.0 on fresh seeds (9000+, 80):                44.3% (208-261)      +18/-44 z-3.30  REPRODUCED worse

Seed pairs for 0.5 by cell: +27/-12 (z +2.40) and +25/-9 (+2.74) at 6 ly/turn,
+6/-14 (-1.79) at 12. Half the relief helps where lanes are 2-4 turns and hurts
where they are 1-2. A plausible reason is that a strike across a 1-turn lane is
never seen before it lands, so no relief answers it (marshal's
`FAST_GUARD_WEIGHT` is the same observation). Counting all of it (1.0) is worse
everywhere, and much worse at 12 ly/turn.

Two follow-ups, both on fresh seeds (9500+, 80 each):

    arm                     cells                      pooled              seed pairs
    0.5                     18n@6, 24n@6, 24n@3        57.0% (258-195)     +49/-19 z+3.64  REPRODUCED better
                            by cell: 58.6% / 53.2% / 59.2% (3 ly/turn: +18/-4, z +2.98)
    0.5, REACTS             18n@6, 24n@6, 24n@12       55.8% (251-199)     +39/-12 z+3.78  REPRODUCED better
                            by cell: 56.8% / 57.0% / 53.6% (12 ly/turn: +9/-3, z +1.73)
    0.5 (same run)          18n@6, 24n@6, 24n@12       53.2% (244-215)     +40/-27 z+1.59
                            12 ly/turn: 47.7%, +9/-12

`SELF_RELIEF_REACTS` counts a neighbour only if its lane is shorter than the
quickest rival lane into the system, because only then can relief launched after
a strike is seen still land first. It keeps the gain at 6 and 3 ly/turn and
removes the loss at 12, where plain 0.5 lost. Head to head the two are not
separated (+48/-59 on the same games), so the case for the reacting form is the
fast cell alone.

### The full measurement (2026-10-13)

Run as "The 2026-10 sweep" was: `tools/sweep.py`, every arm duelled against a
fixed baseline in both seatings, with stock actuary as an arm so each variant is
compared with it on the same (cell, seed, seating). "Paired" is PAIRED ARMS:
games the variant did better than stock in, against games it did worse, on
the same cell, seed and seating. "r" is the lane-aware form (`SELF_RELIEF_REACTS`).
With both flags off the bot is bit-identical to the pre-flag actuary: 2,074
positions over four cells, no order differs.

**The dose, against marshal, Planned (seeds 1-200).**

                     18@6    18@6 j0.3   40@6    24@3    24@12
    stock            69.5    29.9        66.7    58.8    68.3
    r 0.3            71.6    32.0        71.3     -       -
    r 0.5            73.8    28.9        73.1    64.7    67.8
    r 0.8            74.9    30.6        74.0     -       -
    plain 0.5        74.6    30.0        70.5    69.7    64.5

    paired against stock, pooled over the cells each ran in:
    r 0.3 +107/-74 (z+2.45), r 0.8 +135/-79 (z+3.83), r 0.5 +204/-140 (z+3.45),
    plain 0.5 +258/-198 (z+2.81) with 24@12 at +37/-54 (z-1.78)

The lane-aware form gains in every cell but jitter 0.3 and 24@12, where it is
level. Plain 0.5 is as good at 18 nodes and better at 24@3, but it costs at
24@12, which is what the lane gate is for. 0.3 is weaker. 0.5 and 0.8 are not
separated against marshal.

**Confirmed on fresh seeds (5001-5150), against marshal, Planned.**

                     18@6    18@6 j0.3   24@3    40@6    24@12   paired vs stock
    stock            66.6    25.2        57.1    68.5    65.4
    r 0.5            77.8    28.0        67.6    74.8    68.0    +187/-92  z+5.7
    r 0.8            78.4    27.7        69.5    75.8    71.4    +203/-85  z+7.0

Every cell is positive for 0.8, and only jitter 0.3 is short of significance
(+30/-22). 0.8 against 0.5 is +134/-113.

**Every Style stop** (against marshal, lane-aware, paired against that stop's stock):

    Greedy   seeds 1-150    18@6 69.0 -> 75.9 / 78.6 (0.5 / 0.8), 40@6 70.6 -> 74.6 / 73.4,
                            24@3 57.0 -> 68.9 / 67.4, 24@12 69.0 -> 72.5 / 72.5
                            paired 0.5 +154/-78 (z+5.0), 0.8 +156/-76 (z+5.2)
    Learning seeds 1-150    0.5: 18@6 73.7 -> 78.7, 24@3 70.4 -> 77.8, 40@6 71.5 -> 77.1,
                            24@12 74.6 -> 73.4; paired +126/-75 (z+3.60)
    Learning seeds 5001+    0.5: paired +74/-62 (z+1.03); 0.8: 76.6 -> 80.3, 71.8 -> 75.7,
                            80.1 -> 83.0, 72.9 -> 77.2, paired +88/-59 (z+2.39)

The opening plan never reads the risk term, so Planned and Learning change only
from first contact on, and Greedy from the first turn. Learning prices risk
through `_learned_shortfall`, which counts our relief the same way. On fresh
seeds 0.5 is level at Learning while 0.8 holds. That and the advantage sliders
below are where 0.8 separates from 0.5.

**Against the rest of the roster** (Planned, seeds 3001-3120, four cells):

                       18@6         24@3         40@6         24@12        paired vs stock
    vs knower-1        72.4 -> 80.2 / 81.7   83.8 -> 91.5 / 91.0   72.6 -> 77.5 / 77.4   58.0 -> 67.2 / 65.4
                                                                       0.5 +115/-43 z+5.73, 0.8 +116/-45 z+5.60
    vs thinker         97.9-100 in every arm and cell (a ceiling)      0.5 +11/-8, 0.8 +13/-10
    vs knower-2        18@6 only, 100 seeds: 22.6 -> 27.3 (0.5)        +28/-19 z+1.31

**The advantage sliders** (against marshal, seeds 4001-4100):

                       adv 0.75             adv 1.5
    18@6               60.8 -> 62.0 / 65.8   65.0 -> 66.2 / 72.1
    40@6               66.5 -> 65.5 / 68.5   60.3 -> 69.2 / 70.7
    paired             0.5 +94/-78 (z+1.22), 0.8 +106/-65 (z+3.14); 0.8 over 0.5 +80/-56 (z+2.06)

**Free-for-all** (actuary, marshal, thinker, every seat rotation, each position
played once per arm in one process with the flags toggled, so it is paired):

                       actuary's share of wins (stock / 0.5 / 0.8)   paired vs stock
    18@6 seeds 301-450      62.9 / 67.1 / -                           0.5 +55/-36 (z+1.99)
    18@6 seeds 601-750      60.0 / 64.2 / 64.0                        0.5 +50/-31 (z+2.11), 0.8 +50/-32 (z+1.99)
    40@6 seeds 501-580      68.3 / 67.5 / 67.9                        level (+20/-22, +17/-18)

The gain comes from marshal's and thinker's wins alike. Timeouts and game
length do not move: in the 18-node runs 8-19 timeouts per 450 positions in every
arm, and mean length 144-158 turns.

**What it costs.** Thread CPU time per decide, every arm on the same positions
(stock actuary against marshal, seeds 5001-5006, up to 250 turns), load under 1.
p75 ms and mean orders:

                  stock          r 0.5          r 0.8
    18@6       2.58  2.2      3.05  2.2      3.00  2.1
    24@3       6.14  4.1      7.50  4.2      7.44  4.1
    40@6       6.99  6.1      9.36  6.1      9.25  6.0
    24@12      2.63  3.1      3.56  3.2      3.62  3.2
    80@6      19.93 17.4     24.88 17.4     24.95 17.4

That is 16-36% more, about 1.3x at 40 systems, and it hardly grows with size
(1.16 at 18, 1.25 at 80). The extra cost is the relief sum in every risk read
and the `_idle` prune switched off next to a system with priced risk.
`decide_ms` would need `COST_DECIDE_MS` taken from 6.4 to about 8.3 with the
exponent left alone, so knower's `setup_warning` keeps counting it honestly.

**Shipped (2026-10-13): `SELF_RELIEF = 0.8` with `SELF_RELIEF_REACTS = True`,** with
`COST_DECIDE_MS` 8.3 and the gated opening (see "The gate, shipped"). It
is better than stock in every cell and against every opponent that is not at a
ceiling, at every Style stop, and on fresh seeds. It is never worse anywhere
measured, and it is at least as good as 0.5 everywhere, better at Learning and
across the advantage sliders. Shipping it needs `decide_ms` refitted, moves
actuary's `engine_rev` (so the bot column replays), and leaves
`test_learning_counting_every_garrison_in_full_plays_as_planned` to be re-read
with the flag on (it passes). Not measured: the board's own maps through
`tools/setup_sweep.py`, knower at Search beyond 200 games, and the browser's
timing.

## A fleet landing next door is relief too (`RELIEF_IN_FLIGHT`, shipped 2026-10-16)

The relief a struck rival can call on (`_relief`, weighted by `REINFORCE_WEIGHT`)
counted only garrisons on its neighbouring systems. A fleet of its on a lane into
a neighbour, which can land, take the system if it must, and turn round onto the
target before our strike lands, counted for nothing, and a person used exactly
that to lure every bot on the roster into a losing strike. `_relief_in_flight`
now adds such fleets from the turn they could land on the target, thinned by
`_fight` against the landing system's garrison, at weight `RELIEF_IN_FLIGHT`
0.3. `_relief` returns the two sums apart, so weight 0 is bit-identical to the
old bot. +1.2 points against old actuary (z +3.86), +1.4 against old marshal on
fresh seeds (z +4.20), +1.1 at Learning (z +2.06), null against knower Predict;
no cell worse, and no measurable cost. The full write-up, the marshal half and
what it does not fix are in [`marshal-pricing.md`](marshal-pricing.md), "A fleet
landing next door is relief too".

## Relief only the same stack can pin (`SELF_RELIEF_SHARED`, built, measured, deleted 2026-10-16)

From a position the author described. Take a triangle with lanes of 4, 3 and 3:
we hold one end of the 4 and the corner of the two 3s, and the rival holds the
other end of the 4. Only the corner needs to match the rival. A strike on the
4-end is seen with 3 turns to spare, and relief from the corner takes 3. The
corner is safe once it is empty, because the ships that would take it are the
ones already on their way to the 4-end. A launch cannot be recalled, and a split
strike is answered by a matching split. In general, systems of ours that are
nearer each other than the rival need their total to cover the threat, not each
one.

`SELF_RELIEF_REACTS` already prices the triangle: the corner's 3-turn lane is
shorter than the rival's 4, so the 4-end counts 0.8 of the corner. What it does
not check is the second half, whether another stack could take the corner once
it leaves. That double count was a guess at why `SELF_RELIEF` 1.0 lost (*A front
stack pins the whole side*).

The arm: `_own_relief` counted a neighbour at `SELF_RELIEF_SHARED` when every
rival-held system it borders is one of the threatening rival's stacks next to
the target and no rival fleet is landing on it, and at `SELF_RELIEF` otherwise.
Both tests read start-of-turn facts, so the caches stayed exact. At 0.8/0.8 it
was bit-identical (968 positions over six setups and all three Style stops, no
order differs). It held on 56% of neighbour reads, mostly interior systems with
no rival next door, and the arms changed 4-8% of decides.

Measured with the paired runner from *A fleet landing next door is relief too*
(`marshal-pricing.md`), in six cells (11:11, 13:3, 18:6, 24:3, 24:12, 18:18), 500
seeds each, both seatings, actuary at Planned. Points are against the 0.8/0.8
arm on the same games:

    shared / rest           vs actuary (500k)     vs marshal (600k)
    1.0 / 0.8               -0.6  z -1.64          -0.6  z -1.24
    1.0 / 0.5               -0.8  z -2.09          -1.4  z -2.99
    0.8 / 0.5               -0.1  z -0.53          -0.6  z -1.66
    1.0 / 0                 -1.2  z -3.08          -2.6  z -5.15
    1.0, front only         +0.4  z +1.13          -0.2  z -0.51

"Front only" kept interior neighbours at 0.8, so the only neighbour counted in
full was the author's case: one that borders the rival, and only the stacks
threatening the target. No arm gains pooled, and no front-only cell is
significant (24:12 is the best against actuary, z +1.27, and the worst against
marshal, z -1.44).

What it says: the double count is not why 1.0 lost. Counting the neighbours
another stack could pin for less is worse, and worse the less they count, so
0.8 for them is right or too low. A neighbour only the same stacks can pin is not
worth more than 0.8 either. Two possible reasons, neither tested: the ledger can
spend the reliever on a strike in a later turn, before the relief is needed; and
the rival's stack can be reinforced from behind during the warning window. Not
run against knower or at Learning, since neither opponent measured hinted at a
gain. Deleted.

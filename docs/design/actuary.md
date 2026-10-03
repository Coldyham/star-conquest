# actuary design notes

`models/actuary.py` is the one bot in the roster that is not built on the
shared skeleton. Every other bot from claudebot up walks the systems it owns in
phases (budget, defend, strike, flow the rest forward). actuary has no phases.
It projects the whole board forward, values it in ships, and commits whichever
launch raises that value most until none does. Roster-wide rules and the
measurement checklist are in [`bots.md`](bots.md). Index:
[`../README.md`](../README.md).

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

Per decide, native CPython, load average ~2 (2026-10):

    18 nodes, 6 ly/turn    median 1.6 ms   p99  8.6 ms
    24 nodes, 3 ly/turn    median 1.8 ms   p99 11.0 ms
    40 nodes, 6 ly/turn    median 3.9 ms   p99 15.1 ms
    40 nodes, 18 ly/turn   median 2.1 ms   p99  7.2 ms

That is two to three orders of magnitude above the phase bots (microseconds).
It matters to knower: a Search seat runs every non-oracle rival's `decide` on
each rolled turn of each line, so against actuary its 150 ms `SEARCH_BUDGET_S`
can trip in a browser game and the search stops shallower. Unmeasured in the
browser. Offline runs (`bot_replay`, `tests.sim` with guards lifted) are
unaffected. The oracle's single call per turn is well inside its 50 ms.

## Where actuary stands

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
a candidate: it would add its decide cost to every knower Search root, and
whether knower plays better with it was not measured.

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
the others has been tuned. A real sweep should start with the slow regime.

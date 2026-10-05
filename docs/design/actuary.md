# actuary design notes

`models/actuary.py` is the one bot in the roster that is not built on the
shared skeleton. Every other bot from claudebot up walks the systems it owns in
phases (budget, defend, strike, flow the rest forward). actuary has no phases.
It projects the whole board forward, values it in ships, and commits whichever
launch raises that value most until none does. By default the land-grab, up to
first contact, is played by a planner instead; see "The planned opening". Roster-wide rules and the measurement checklist are in
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

Per decide, native CPython, load average ~2 (2026-10):

    18 nodes, 6 ly/turn    median 1.6 ms   p99  8.6 ms
    24 nodes, 3 ly/turn    median 1.8 ms   p99 11.0 ms
    40 nodes, 6 ly/turn    median 3.9 ms   p99 15.1 ms
    40 nodes, 18 ly/turn   median 2.1 ms   p99  7.2 ms

That is two to three orders of magnitude above the phase bots (microseconds).
It matters to knower: a Search seat runs every non-oracle rival's `decide` on
each rolled turn of each line, so against actuary its 150 ms `SEARCH_BUDGET_S`
trips sooner and the search stops shallower. actuary declares its cost
(`decide_ms`: 75th-percentile ms = `5.0 * (nodes / 40) ** 0.55`, fitted to a grid
of 18-120 systems, 3-18 ly/turn and 2-5 seats at load 0.05, where seats and ship
speed barely moved it), and knower's setup warning counts it; see "Cost per
decide" in [`knower.md`](knower.md). Offline runs (`bot_replay`, `tests.sim`
with guards lifted) are unaffected. The oracle's single call per turn is well
inside its 50 ms.

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

## The planned opening (Opening: Planned)

The seat's `aux` knob (`AUX_LABEL` *Opening*) has two stops. *Planned* (1, the
default, and anything above or unreadable) runs `opening(state, pid)` until the
seat first borders a rival, and the ledger from then on. *Greedy* (0) is the
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
  rival's, when a rival fleet heads for one of ours, or when the region is empty.

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

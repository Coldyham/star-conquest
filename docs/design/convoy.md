# convoy design notes

`models/convoy.py` plans the turn as routing over time. Every ship we will have
(garrisons now, hulls our systems will build, our fleets landing on our systems)
is a supply at a system and a turn. Every objective (a strike, a defence, a
guard) asks for N ships on one system by one turn. Objectives are committed
greedily against that supply, and the only orders issued are for ships that must
leave this turn to arrive on time. It was the third proposal in a list of new bot
families that would play differently from the roster (2026-10-05), after the
opening planner that became actuary's Opening knob ([`actuary.md`](actuary.md),
"The planned opening"). Roster-wide rules and the measurement checklist are in
[`bots.md`](bots.md). Index: [`../README.md`](../README.md).

## The plan

- **Supply.** `[ready turn, system, ships, expires]`. A garrison is ready at 0;
  each hull at the turn production finishes it; each of our fleets landing on a
  system of ours at its landing turn. A system the plan gives up (below) builds
  nothing from the turn it falls, and its ships must leave before then.
- **Reach.** A supply at s, ready at r, can serve an objective on T at t when
  r + (travel from s to T through our own systems) ≤ t. Routes are owned-only
  Dijkstra by travel turns; the last hop may leave our territory.
- **Defences first.** For each system with hostile fleets inbound, the garrison
  it needs at the first landing to hold at the worst roll (`edge_defending`).
  Its own ships are used first. A defence the supply cannot cover makes the
  system doomed, which shrinks the supply, so the pass repeats until no new
  system is given up.
- **Guards are reservations.** A frontier system keeps `GUARD` × the largest
  adjacent rival garrison × the defending edge. A strike may not spend a
  garrison below its guard against any rival neighbour except the one it
  strikes (see "The guard against the target" below).
- **Strikes.** For each system we do not hold and each turn up to the horizon
  (longest lane + `HORIZON_PAD`, as actuary), the fewest ships that take it at
  the worst roll against its projected timeline (production, fleets in flight,
  the engine's pile-up fold, `REINFORCE_WEIGHT` of a rival's adjacent ships),
  times `STRIKE_PAD`. Value: survivors minus ships sent, plus the target's income
  for `TAIL_TURNS` less the landing turn, plus a rival's garrison and income
  again when a rival holds it. The best value per ship is committed at its
  earliest feasible turn, and its survivors and production join the supply,
  so a plan can take one system and strike the next from it.
- **Launch at the last moment.** Of everything allocated, only ships whose slack
  is zero launch, one hop along their route. Ships from different distances
  land together, and a ship with slack waits at home, where it still counts as
  a guard. A frontier ship nobody wants holds; an interior one moves one hop
  towards the nearest frontier; a doomed garrison leaves for its strongest safe
  neighbour.

It draws nothing from `state.rng` and is not an oracle. Per decide, native
CPython, 18 nodes: median 0.3 ms in the opening and 0.6 ms contested, p99
1.1 ms (`tools/bot_distance.py`'s fingerprint). That is below actuary, so no
`decide_ms` is declared.

## Where convoy stands

Head to head, both seatings, seeds 201-260 (120 games a cell, timeouts left out),
win rate is convoy's. knower is at its default, Predict:

                        claudebot  thinker  knower  marshal  actuary
    12 nodes, 6 ly       100%       88%      66%     47%      18%
    18 nodes, 6 ly       100%       92%      47%     45%      19%
    40 nodes, 6 ly        99%       96%      48%     34%      24%
    18 nodes, 18 ly       97%       94%      34%     43%      19%
    24 nodes, 3 ly        97%       88%      65%     36%      26%

Clear of thinker in every cell, level with knower-Predict except on fast ships
(where the oracle sees one-turn strikes), below marshal and well below actuary.
Timeouts run 2-28 of 120, most against marshal and in the slow cell.

## How differently it plays

`tools/bot_distance.py`, 21 self-play games among actuary, marshal, thinker and
convoy, 18 nodes (905 contested and 254 opening positions):

- **Contested, it is not a new family.** Nearest neighbour claudebot at 0.21,
  thinker 0.22, marshal 0.26, and its move kinds agree with marshal's at kappa
  0.67. It launches 32% of its garrison (thinker 26%, marshal 41%, actuary
  48%), and 24% of what it launches goes at a rival (actuary 50%).
- **In the opening it is the most distinct bot.** Nearest neighbour thinker at
  0.25, where actuary, marshal and knower sit within 0.19 of each other. 56% of
  what it launches goes between its own systems (actuary 40%, marshal 24%): the
  forward flow, and ships routed through the interior towards a later strike.

Part of the contested similarity is the measure. A ship waiting so that it lands
on time is a hold in a single-turn snapshot, which is what a cautious phase bot
does too. But the plan also rarely does the thing it was built for. Of the
strikes whose final leg launched, by convoy's own allocation (seeds 101-130):

                        vs actuary            vs marshal
    neutral, one source   7.8/game  99%        7.8/game  97%    captured
    neutral, several      0.9/game  100%       0.8/game  100%
    rival, one source    22.6/game  83%       25.0/game  95%
    rival, several        2.0/game  94%        4.5/game  99%

The strikes it lands mostly take their target. Few of them converge from more
than one source, because the earliest feasible turn usually needs only one.

## Its opening in front of another bot (measured, not borrowed)

Since it is distinct only in the opening, convoy's opening was put in front of
other bots the way surveyor's was ([`actuary.md`](actuary.md), "In front of
marshal it costs"): convoy until the seat first borders a live rival or a rival
fleet heads for one of its systems, the host from then on. Paired against the
plain host, both seatings, seeds 101-160:

                                        12n   18n   40n   24n@3
    in front of marshal, vs marshal     32%   39%   45%   37%
    in front of actuary, vs Planned     49%   45%   50%   45%
    in front of actuary, vs Greedy      42%   49%   59%   50%

It costs in front of marshal in every cell. Against actuary's own opening
(Planned) it pools to 47% (n=453, z ~ -1.2). Against Greedy it gains only at 40
nodes, where Planned already gains. Distinct is not better. Its opening moves
more between its own systems but does not reach contact with more ships or
income (see "Where it loses to actuary": level with Planned at contact,
behind marshal on income). So it was not borrowed.

## Where it loses to actuary

At first contact the two are level (seeds 101-160, 18 nodes: 24.9 against 25.8
ships, 1.66 against 1.74 income a turn, contact at turn 29). From contact to the
end convoy loses 144 ships a game to actuary's 93. Against marshal it trades
evenly (160 against 161) but arrives at contact with less income (1.55 against
1.78). So the gap to actuary is in the contested middle, and to marshal partly in
the land-grab. The loss log of a typical game against actuary is frontier
systems of 0-7 ships hit by 10-25, each declared doomed and evacuated.

## What the measurements changed

All paired against the version before the change, both seatings, plus the
same variant against actuary and marshal. Cells are 18 and 40 nodes at 6 ly/turn,
seeds 101-160 (120 games) unless stated.

**The guard against the target.** The first version allocated guards before
strikes. A frontier system of 293 ships next to a rival's 201 kept 129 as a guard,
and the strike on that same rival needed 249 from the remaining 164, so it never
came. The game stalled for 500 turns. Letting a strike spend the guard kept
against its own target: against marshal 30% → 47% at 18 nodes, seeds 1-30, and
timeouts down from 14 to 5.

**Keeping that guard unless the lane is one turn (deleted).** The reasoning: on a
longer lane the strike is seen in flight, and the target can answer on the
emptied source, so the strike trades systems. Measured against waiving the guard
always, seeds 1-30: 33% at 18 nodes, 23% at 40, 37% at 18 ly/turn, 30% at 24
nodes 3 ly/turn, with timeouts up (27 against 5 in the slow cell). Against marshal
it read 35% at 18 nodes where waiving read 47%, and level with it elsewhere. A guard of `GUARD` × the
neighbour (0.67 × at the default edge) cannot hold against the neighbour's whole
garrison anyway, which needs 1.22 ×. So it only bites against a partial answer,
and it raises a strike's total ask to about 1.9 × the garrison. A guess, not
tested.

**`STRIKE_PAD` 1.25.** 1.0 → 1.25: 59% and 66% against 1.0. Against actuary
and marshal it moved from 10% / 40% to 18% / 40% at 18 nodes, and from 17% / 32%
to 20% / 36% at 40. 1.5 lost to 1.25 (48%, 43%), and 2.0 collapsed (13%, 5%).
Shipped at 1.25.

**Evacuating a doomed garrison is right.** Staying to fight: 38% against
evacuating, seeds 1-30, worse against marshal (36% against 47%) and actuary.

**Null, not adopted** (against the base):

    defence at the nominal roll       53%, 56%    pooled 54.8% (n=228), z ~1.5
    GUARD 0.3 / 0.8                   48%, 54% / 51%, 48%
    REINFORCE_WEIGHT 0                44%, 42%   (a little worse)
    HORIZON_PAD 6                     50%, 52%
    TAIL_TURNS 40                     49%, 50%

None of these moved the results against actuary or marshal outside the noise.
Nominal-roll defence is the only lean worth re-running, at n in the thousands.

**Deleted tactics, null:**

- *A frontier short of its guard draws spare ships from the rest*, nearest
  first: 47% and 50% against the base. The thin frontier systems in the loss log
  were not from ships parked in the wrong place.
- *Staging* (a ship with slack moves forward to the last owned system before its
  target and waits there): 50%, 52%.
- *Spending ships that exist before hulls to come*, so that more sources join a
  strike: 48%, 50%. Both together: 54%, 52%.

What did not fit (decided against before building): repricing a capture by
whether it can be held, since marshal's hold test on captures was worse the
harder it bit ([`marshal-flow.md`](marshal-flow.md), "What the board's human
wins say").

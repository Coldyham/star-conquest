# actuary's Field stop (once fulcrum)

actuary's *Style: Field*, the default stop, is its ledger with a different pull
on its ships. The pull (how much more a ship is worth standing in one place
than another) decides where a ledger bot's ships go, and it turned out to be
most of what makes actuary and marshal play alike. Greedy's front, where every
system touching anything not ours pulls the same, gives way to an influence
field (`_Ledger._field`): ships go where the balance of force is closest.

It was built on 2026-10-18 as a separate bot, `models/fulcrum.py`, to test
whether the two top non-oracle bots are fitted to each other and whether a bot
that plays differently can stand with them. Its ledger was actuary's to the
order, so it was folded in the same day: the field took the place of the
planned opening as stop 1, and Learning was rebuilt on it ("Folded into
actuary", at the end). Field plays exactly as fulcrum did (422 positions out of
roster games, every order the same), so the measurements below keep the name
fulcrum, and "actuary" in them is actuary as it was then, at Planned unless
they say otherwise. Roster-wide rules and the measurement checklist are in
[`bots.md`](bots.md), and actuary's own notes are in
[`actuary.md`](actuary.md). Index: [`../README.md`](../README.md).

Every measurement here was made with a scratch harness (not committed): duels
across 4 processes, both seatings per seed, `replay.reseed` each turn as
`tests.sim` does, wall-clock guards lifted 100x as `tools/sweep.py` lifts them,
600 turns. A timeout scores 0.5, plus or minus 0.05 for the side ahead
(`sweep.py`'s `TIMEOUT_LEAD`). "Paired" means each seed's two games scored
together against the same seed under the other arm. "pts%" is points per game.

## The middle game the roster plays

Read off actuary-marshal duels at 18 systems and 6 ly/turn, seeds 1-40, through
a wrapper on `combat.resolve_arrival`:

- **Half the strikes land on an empty system.** actuary lands on a rival's
  system about 50 times a game, and 61% of those landings find it empty: 25
  ships on average, 1.2 killed, 0.9 lost. marshal's figures are 54 a game, 49%
  empty, 1.6 killed and 1.8 lost. Stacks avoid each other, and systems change
  hands about every three turns.
- **A doomed garrison steps into its striker's source.** Of garrisons
  out-shipped by what is inbound, marshal evacuates 83% and actuary 59%.
  marshal sends 46% of its evacuees into the striker's own source and 26% into
  another rival system; actuary sends 31% and 18%. That is the swap in
  "Denying the swap" ([`marshal-flow.md`](marshal-flow.md)).
- **Big stacks, empty interiors.** A stack of 5 or more ships on a front
  averages 49 ships. 58% of the systems a side holds are interior, and they hold
  10% of its garrison ships. 18% of front stacks have no system of their own to
  retreat to.
- **Defence separates the winner from the loser.** Per 100 turns after turn 20,
  the winner captures about 36-39 rival systems and loses about 30, the loser
  the reverse. Ships lost attacking barely move with the result (actuary 50 as
  winner or loser, marshal 83-91). Ships lost defending do: actuary 48 winning
  against 87 losing, marshal 37 against 56. Per game, garrisons of 4+ that saw
  a dooming strike coming and stayed lose 28 ships for actuary and 24 for
  marshal, and relief that lands in a fight that is then lost another 12 and 17.
  Hulls built after the last decide add 12 that nothing can save.
- **The opening is level.** At turn 20 actuary and marshal hold 18 ships and 4
  systems each. actuary's lead comes between turns 40 and 60 (68 ships to 62).
  The side ahead on ships at turn 40 wins 75% of games.

## Where a ledger's moves come from

The first attempts went at the habits above directly. All were measured against
actuary and marshal at 18 systems (6 ly/turn) and 24 (3 ly/turn), seeds 1-50 or
1-60:

    a refill of our source the turn after a dooming strike, on actuary   45.5 vs actuary   55.2 vs marshal
    full relief of any system a visible fleet would take, on marshal     51.3 vs marshal   40.5 vs actuary (stock about 40)
    the same, on actuary                                                 51.0 vs actuary   58.8 vs marshal (stock about 60)
    a phase bot built round traps and full relief (two prototypes)       3.6-21.1 vs actuary and marshal, 37-44 vs thinker

Covering the swap costs more than the swap does, as marshal's `REFILL_SOURCE`
found ([`seat-play.md`](seat-play.md), "What was built from these games").
Neither top bot relieves in full, but they strike where full relief could not
land in time anyway: only 3-5% of their strikes of 5+ ships would have lost to
it. A new phase bot needs a land-grab and a flow as good as the roster's before
anything else counts.

So the next step was a stripped copy of actuary's ledger: Greedy, no opening
plan, no memory. It plays actuary's Greedy stop order for order (3,426 positions,
none different), so any change to it is an exact paired test. Changing what a
system or a ship is worth, 18 systems, seeds 1-60, 120 games against each (the
two rows marked ran at 24 nodes, 3 ly/turn, as well):

                                                vs actuary   vs marshal
    the copy, unchanged                           50.0         65.2
    a held system worth 10 turns of income        35.8         45.7
    ...worth 40 turns (24 ships)                  49.6         56.3
    a rival's ships counted 1.5x                  47.9         61.7
    our stacks valued as ships^1.1                33.0         44.7
    the defence priced against the rival's
      whole reach, as well as what is in flight   48.5-49.6    54.6-57.0  (marked)
    a doomed rival garrison assumed to flee into
      our nearest source, weight 0.5              46.8         59.4       (marked)
    FRONT_BONUS 0                                  5.6         15.4
    FRONT_BONUS 1.0                               43.7         64.6
    FRONT_DECAY 0.9                               46.6         59.2

actuary's values are hard to improve on, except in one place. Without its front
pull actuary wins 6 games in 100 against itself. The pull is how ships get to
the front, and that one choice is most of the bot. It is also the plainest part
of the ledger: a system touching a neutral pulls exactly as one facing a rival
stack, and every front pulls alike.

Pulls that tell fronts apart, at 18 systems, seeds 1-60:

                                                       vs actuary   vs marshal
    a front facing only neutrals pulls 0.5              55.7         73.7
    pull scaled by the income next door                 57.2         72.8
    pull scaled by the rival ships next door            57.8         72.0
      ...on fresh seeds 61-120, three cells              51.7         65.2
    ships at a front worth the bonus only up to
      0.8 of the rival stack facing it                  39.6         54.7

Steering the pull toward the enemy looked like a gain, but on fresh seeds it
shrinks to +2.3 points paired against the copy (z +1.44; the copy scores 62.4
against marshal on those seeds), and `tools/bot_distance.py` puts it 0.08 from
actuary on contested positions and 0.01 in the opening: a re-tune, not a
different player. Capping the pull to pin every rival system spreads the force
and does play differently (0.20 from actuary, 0.37 from marshal), but it loses.

## The field

`_Ledger._field` computes the pull once per decide, and the greedy then prices
every launch against it unchanged. Influence is ships over distance, kept at
`FIELD_DECAY` per median lane, out to `FIELD_REACH` median lanes:

    I_side(s) = sum over that side's garrisons g at w:   g * DECAY ** lanes(w, s)
              + sum over its fleets f landing at w:       f * DECAY ** (lanes(w, s) + f.turns_remaining / L)

L is the board's median lane in turns, so the field reads the same on 1-turn
lanes and on 6-turn ones (`test_the_field_reads_lanes_not_turns`). "Rival" is
every live rival, summed. A system's stake is what the ledger says it is worth,
weighted by how close the balance is there:

    stake(s) = (TAIL_TURNS * income(s) + a rival garrison on s) * exp(-x^2)
    x        = (I_ours(s) - I_rival(s)) / (CONTEST_WIDTH * max(1, I_ours(s) + I_rival(s)))

Our interior has no stake. A ship at our system w is pulled by the stake within
reach of it, and the strongest pull gets the whole bonus:

    pull(w)        = sum over s within reach of w: stake(s) * DECAY ** lanes(w, s)
    front_value(w) = 1 + FRONT_BONUS * pull(w) / max over our systems of pull

A system we do not hold keeps 1 + `FRONT_BONUS`, as in actuary, so survivors of
a capture count as ships on a front. Everything else is actuary's ledger
unchanged, and `test_with_actuarys_pull_it_plays_actuarys_greedy` holds it to
that: put actuary's `_front_values` back in place of `_field` and every order
matches Greedy. The constants are actuary's except the field's three.

What the formula does, read off positions:

- **A front nobody contests stops pulling.** Where our side swamps the rival
  next door, x is large and the stake vanishes. A system a lane behind a
  balance pulls harder than such a front (`test_a_lane_behind_a_balance_pulls_harder_than_a_quiet_front`).
- **A balance pulls from three lanes away.** The ships drawn to a contested
  point come from every system within reach of it, not only the front facing it.
- **Before first contact the middle of the map is the contest.** A neutral that
  neither side can yet reach has x near 0 and its full stake, so the pull draws
  ships to the systems that face it, not to whichever borders the nearest
  neutral. The neutrals it takes are no further out than actuary's (below);
  where its garrisons stand is most of why the opening plays differently.
- **It is relative.** Only the ratio to the strongest pull matters, so the
  bonus is always spent somewhere, as actuary's is.

### How it was tuned

The first field decayed per turn of travel (0.6 a turn, out to 8 turns). It was
level with actuary at 6 ly/turn and collapsed at 3 ly/turn. Paired against the
copy of actuary, seeds 1-120:

                                 vs actuary              vs marshal (copy)
    18 systems, 6 ly/turn         49.9                    69.6 (65.5)
    24 systems, 3 ly/turn          8.4                    21.7 (55.1)
    30 systems, 6 ly/turn         49.9                    64.7 (64.2)

At 3 ly/turn a lane is 5-8 turns, so a per-turn decay leaves a ship almost no
influence one lane away, and an 8-turn radius drops whole lanes from the field.
The pull goes flat, which is `FRONT_BONUS` 0 again. Counting in median lanes
fixed the cliff. The locality then mattered more than anything else. Paired
against the copy at 18 systems (6 ly) and 24 (3 ly), seeds 1-60:

    decay/lane  reach  width   vs actuary 18 / 24    vs marshal 18 / 24    pooled
    0.13        2      0.5       46.0 / 28.5           64.3 / 40.1          -10.4
    0.2         2.5    0.5       42.3 / 33.5           73.6 / 52.2           -4.7
    0.3         2.5    0.5       55.1 / 39.7           68.0 / 55.8           -0.4
    0.45        3      0.5       59.3 / 47.0           73.5 / 62.9           +5.6  z +2.52
    0.45        4      0.5       56.3 / 48.8           75.2 / 64.8           +6.2  z +2.88
    0.6         3      0.5       61.4 / 46.7           74.4 / 56.2           +4.6  z +2.09
    0.45        3      0.35      53.5 / 46.4           71.4 / 57.7           +2.2
    0.3         2.5    0.8       52.3 / 33.0           69.2 / 53.7           -3.1
    copy of actuary              50.0 / 50.0           65.2 / 55.2

A field that reaches a few lanes, keeping about half per lane, is the one that
pays. A reach of 4 was level with 3 at this sample, and the shorter was kept.
Three changes on top of 0.45 / 3 / 0.5 (same seeds):

    a neutral's garrison left out of its stake       60.7 / 49.4   72.7 / 62.2   +6.2  z +2.87   shipped
    FRONT_BONUS 0.75                                 49.7 / 45.8   69.8 / 49.4   -1.4
    a capture's income discounted by the balance
      (half of it scaled by a logistic of x)         59.3 / 48.5   79.4 / 60.9   +6.9  z +3.19
    half actuary's front, half the field             54.6 / 52.5   71.3 / 68.3   +6.6  z +3.04

The three that held are level with each other at this sample. The neutral's
garrison is left out because a neutral's ships are worth nothing to anyone, which
is how the ledger values them. The discounted capture adds a knob for no clear
gain. The half-and-half blend is as strong and the steadier of the two at 3
ly/turn, but half its pull is actuary's. Two more were worse: leave-one-out
(how contested a system would be without the ships already there, so that
arriving ships do not make their own post look safe) at -10.1, and a transit
cost on moves between our own systems, 0.05 or 0.15 a ship-turn, at -31 and -46.
The second is the forward flow stopping: the pull is not the problem.

## Where Field stands

Fresh seeds 1001-1200, both seatings, 400 games a cell, against the roster as it
plays by default (actuary at Planned, marshal as shipped). actuary against
marshal on the same seeds is alongside, for the comparison that matters:

                        fulcrum vs actuary   fulcrum vs marshal   actuary vs marshal
    18 nodes, 6 ly          58.1                 77.2                 64.7
    24 nodes, 12 ly         64.3                 80.4                 66.0
    24 nodes, 3 ly          45.1                 56.9                 53.3
    30 nodes, 6 ly          58.7                 77.1                 59.9
    40 nodes, 6 ly          50.7                 68.4                 62.3
    pooled (2,000 games)    55.4                 72.0                 61.2

pts%, a timeout counted as above. Against actuary the 95% interval is about 4.8
points a cell. fulcrum is ahead in three cells, level at 40 nodes and behind at
3 ly/turn. Against marshal, paired by seed with actuary's games on the same
seeds, it scores 10.8 points more (z +9.50), and more in every cell: z +4.92,
+5.76, +1.50, +6.91 and +2.33 in the order above.

**The slow regime is the weak one.** At 3 ly/turn fulcrum is behind actuary
(150 wins to 189, 61 timeouts in 400) and only a little ahead of it against
marshal. It is not the land-grab. At turn 100 it is level with actuary on
systems and ships (9.8 against 9.9, 154 against 151), in games that last about
275 turns. Where it pulls ahead elsewhere, it does so early: at 18 nodes it
holds 6.5 systems to actuary's 5.7 at turn 40, and 7.0 to marshal's 5.4.

**Against the rest of the roster**, seeds 1001-1100, 18 nodes at 6 ly/turn
and 24 at 3, 200 games a cell:

                    18 nodes, 6 ly   24 nodes, 3 ly
    knower (Predict)     87.7             86.1
    thinker              98.8             95.7
    claudebot            99.1             96.7
    heuristic            99.8             98.9

actuary scores 86.8 and 83.5 against knower-Predict in the same two cells
(seeds 1-30). **Against knower at Search**, seeds 1001-1050, 18 nodes, 100
games: 43.5% (43 wins to 56, 1 timeout, ±9.7). actuary reads 37% there
([`actuary.md`](actuary.md), "Where actuary stands"), so the oracle's search
still beats fulcrum, by less. `ai.LADDER_ORDER` puts fulcrum below knower and
above actuary for that reason. **Against council**, seeds 1001-1030, 18 nodes,
60 games: 6.7% (4 wins, ±6.3). [`council.md`](council.md), "The measurements",
has actuary at 1.0% in the same cell on its own seeds.

**The roster ladder** (`tests.sim --ladder --trials 30`, 18 nodes, default
settings) puts fulcrum first: 370 wins to actuary's 343 and marshal's 305, 61%
head to head against actuary, 75% against marshal and 93% against knower. Every
pair without fulcrum reproduced the previous run game for game. The grid is in
[`marshal.md`](marshal.md), "Where marshal stands".

**Against a bot from outside the roster.** `bots/severance` (Rust, built
offline with `cargo build --release`) plays the graph rather than the fight. 18
nodes, seeds 3001-3030, 60 games each: fulcrum 99.2% in 67 turns on average,
actuary 96.7% in 85, marshal 86.7% in 116.

**Across the combat sliders and on symmetric maps**, 18 nodes at 6 ly/turn,
seeds 2001-2060, 120 games a cell, with actuary against marshal on the same
seeds alongside:

                              fulcrum vs actuary   fulcrum vs marshal   actuary vs marshal
    defender advantage 1.5          56.2                 71.3                 64.5
    defender advantage 0.75         53.7                 65.0                 55.4
    combat jitter 0.0               67.5                 83.3                 71.8
    combat jitter 0.3               63.3                 33.5                 19.1
    symmetric map                   70.2                 73.0                 54.3

fulcrum is ahead of actuary in every cell (the 95% interval is about 8.5 points
a cell). Against marshal, paired by seed with actuary's games, it scores 12.2
points more (z +6.98), and more in every cell: z +1.88, +2.12, +3.45, +3.73 and
+4.59 in the order above. Wide jitter is the ledger's weak cell, as it is
actuary's ([`actuary.md`](actuary.md), "Where actuary stands"): marshal beats
both, actuary by 14 points more. Advantage 1.5 and the symmetric map are where
games stall: 33 and 41 of fulcrum's 120 games against marshal reach 600 turns,
against 47 and 58 of actuary's.

**In a free-for-all** (every seat rotation, seeds 2001-2100, 300 games a cell),
with actuary and marshal: fulcrum takes 40% of the wins at 18 nodes and 37% at
30, actuary 43% and 44%, marshal 15% and 17%. In the same games fulcrum wins
4.8 points a game fewer than actuary (z -1.31). With knower (Predict) as a
fourth seat, 24 nodes, seeds 2001-2050, 200 games: fulcrum 37%, actuary 33%,
marshal 20%, knower 8%, so 4.0 points a game more than actuary (z +0.68). In a
free-for-all, then, it is level with actuary at three seats and at four; only
in a duel is it clearly ahead. The field sums every rival as one. A field that weighs only the
strongest rival at each system, which plays every duel exactly as the shipped
one does (12 games of 12 identical), read +1.3 points at three seats, same
seeds (z +0.85), and was not kept.

## How differently it plays

`tools/bot_distance.py` at its defaults (21 two-seat games, 18 nodes, 6 ly/turn,
every 5 turns, the leaderboard profile: actuary at Learning, knower at Search),
on positions out of games among the seven older bots, so that fulcrum's own
play does not choose the positions it is measured on. 778 contested and 306
opening positions:

    nearest neighbour       contested          opening
      fulcrum            actuary  0.17      actuary  0.26
      actuary            fulcrum  0.17      marshal  0.17
      marshal            thinker  0.21      actuary  0.17
      knower             thinker  0.37      marshal  0.18

    distance from fulcrum: actuary 0.17 / 0.26, marshal 0.40 / 0.37,
    knower 0.45 / 0.43, thinker 0.46 / 0.52 (contested / opening)

    fingerprint      sent   own  neutral  rival  orders
      contested
        fulcrum       52%   28%     5%     68%     4.7
        actuary       50%   46%     5%     50%     4.7
        marshal       24%   46%     2%     52%     4.2
      opening
        fulcrum       71%   50%    50%      -      2.1
        actuary       47%   31%    69%      -      1.4
        marshal       49%   29%    71%      -      1.4

- **The opening is the most distinct of any strong bot.** actuary, marshal and
  knower open within 0.17-0.21 of each other: one land-grab, as the first
  reading found ([`bots.md`](bots.md), "How differently two bots play").
  fulcrum is 0.26 from its nearest and 0.37 from marshal. It launches half as
  much again of its garrison each turn, and half of what it launches goes
  between its own systems, forward toward the neutrals both sides can reach.
- **The middle game is actuary's relative.** At 0.17 it is half as far from
  actuary as marshal is (0.34), closer than marshal is to thinker (0.21), and
  its move kinds agree with actuary's at kappa 0.72. Where they differ, fulcrum
  strikes. Of what it launches on a contested position, 68% goes at a rival
  system, against actuary's 50%, and 28% moves between its own systems, against
  46%. A front facing a swamped or hopeless system no longer holds a stack, and
  the stack goes where a few more ships tip a balance.

**In its own games.** The same counts as "The middle game the roster plays",
through the same wrapper, seeds 3001-3040 at 18 nodes and 6 ly/turn, 80 games
each, fulcrum and actuary each against marshal:

                                             fulcrum   actuary
    first contact, turn                        23.8      26.9
    first three neutrals, lanes from home      1.79      1.75
    landings on a rival system, a game         40.8      48.3
      ...that find it empty                     64%       59%
    doomed garrisons evacuated                  66%       61%
      ...into a rival system                    51%       51%
    ships lost attacking, per 100 turns          32        39
    ships lost defending, per 100 turns          40        51
    marshal's losses attacking / defending     62 / 30   73 / 38

The dance is still there: its strikes find an empty system more often than
actuary's do, and its doomed garrisons step into a rival system as often. What
moves is contact, three turns sooner, and the losses: about a fifth fewer ships
lost defending, which is the count that separates a winner from a loser, and
fewer lost on both sides of the board per 100 turns. Against actuary the two
read alike (33 landings a game each, 58% of them empty, 30 and 33 ships lost
defending per 100 turns).

So fulcrum is not a new family the way knower or actuary was. It is a ledger
bot with a different idea of where force belongs, and on the evidence above
that idea is worth more than any constant in actuary's value system was.

## What it costs

The field is one bounded Dijkstra from each system, out to `FIELD_REACH` median
lanes, and two passes over what it reaches, once per decide. The ledger's
greedy dominates as it does in actuary. CPU time per decide, native CPython,
both bots timed on the same positions for the same seat (three-seat games of
fulcrum, actuary and marshal, seeds 5001-5003, every 5 turns up to turn 150,
an idle machine):

    systems            18    24 (3 ly)    40     80    120
    fulcrum p50 ms    1.8      2.4       3.1    6.7    8.0
    actuary p50 ms    1.6      2.0       3.2    9.2    7.9
    fulcrum p90 ms    6.5      6.7       8.8   16.4   18.2
    actuary p90 ms    5.4      5.9      11.3   19.4   23.8

They cost the same to within a fraction of a millisecond at the median, and
fulcrum's tail is the shorter on big boards. `decide_ms` reuses actuary's fit
(8.3 ms at 40 systems, exponent 0.77), so knower's setup warning counts a
fulcrum rival as it counts actuary.

## Built, measured, dropped

Everything dropped in "Where a ledger's moves come from", "How it was tuned"
and "Where fulcrum stands", in one place. Each was measured against both
actuary and marshal, mostly at 18 nodes (6 ly/turn) and 24 (3 ly/turn).

- **Traps.** A refill of our source the turn after a dooming strike, on actuary:
  45.5% against actuary. The same priced inside the ledger, a doomed rival
  garrison assumed to flee into our nearest source at weight 0.5: 46.8%.
  marshal's evacuees into a rival system that a refill could have met are 11%
  of them, about 30 ships a game; actuary's 20%, about 7.
- **Full relief.** Any system a visible fleet would take relieved from every
  neighbour that can land in time: null on marshal (51.3% against itself) and
  on actuary (51.0%). Only 3-5% of their strikes would lose to it.
- **Pricing the rival's reinforcement.** Our defence priced against what the
  rival could still add from next door: 48.5-49.6% against actuary.
- **Value systems** on actuary's ledger (`TAIL_TURNS` 10 or 40, a rival's
  ships at 1.5, stacks at ships^1.1): null or worse.
- **Pulls that only steer the front.** By rival ships, income next door, or
  neutral against rival front: a gain against marshal that shrinks to +2.3
  points paired on fresh seeds, and 0.08 from actuary in play. **Pinning** (a
  front's ships worth the bonus only up to 0.8 of the stack facing it): 39.6%
  against actuary.
- **The field per turn of travel** rather than per median lane: 8.4% against
  actuary at 3 ly/turn. **Leave-one-out** balance: -10.1 points. **A transit
  cost** on moves between our own systems: -31 and -46. **`FRONT_BONUS` 0.75**:
  -1.4. **Contest width** 0.35: +2.2 against +5.6 at the same decay and reach;
  0.8: -3.1 against -0.4 at its own.
- **Two phase bots** built round traps, full relief and worst-case floors (in a
  scratch harness only): 3.6-21.1% against actuary and marshal, with up to half
  their games against marshal and thinker timing out.
- **A field against the strongest rival** at each system rather than every
  rival summed, the same in a duel: +1.3 points in a three-seat free-for-all
  (z +0.85).

**Open, not rejected.** A capture's income discounted by the balance (+6.9 at
60 seeds) and half actuary's front with half the field (+6.6, the steadier at 3
ly/turn, 52.5% against actuary there) are level with the shipped field (+6.2)
at that sample; both were measured with a neutral's garrison still counted in
its stake. The slow regime is where either would be worth a fresh run.

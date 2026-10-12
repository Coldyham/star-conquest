# Hand-played games against marshal and actuary (2026-10-12)

Five games played seat by seat with `tools/seat_play.py`, every turn's reason in
the log's notes sidecar, then two readings over those logs and the board's
public replays. The point was to find where a person can catch the two bots in
active development out, and what that says about their skeletons. The play is
one player's, and five games is an anecdote. Anything below that a measurement
backs says so and gives the numbers. The rest is a lead, not a result.

Setups: default balance (6 ly/turn, jitter 0.1, advantage 1.0, no fog), random
maps, the human in seat 1, both bots at their default stop (actuary: Planned).

    seed  nodes  rivals              result                     turns
    101   13     marshal             won                         178
    202   13     actuary             lost                         81
    404   18     actuary             lost                         65
    505   18     marshal, actuary    lost (actuary won)          165
    606   13     marshal             lost                         61

## What decided the games

**Both bots evacuate into the system you struck from.** Every strike I made on a
garrison the bot judged doomed met a same-turn launch out of the target into my
emptied source, for marshal and for actuary alike. The swap is measured for
marshal ("Denying the swap" in `marshal-flow.md`: 21-23% of its strikes on a rival
meet one). By hand it was close to every strike that left its source thin. The
bot loses nothing, it takes the source, and the person has spent a turn and a
lane's travel to trade one system for another. In game 101 marshal's ships lost
from turn 60 to 130 were mostly mine, killed by evacuees landing in sources I had
emptied.

**The counter is to cover every exit before striking.** A doomed garrison only
dies when every system it could step into is held, at the moment it would land,
by more than it can take. In game 101 that was the only way marshal's stacks
died: the 13 on 1 (turn 141), the 14 on 4 in game 505 (turn 54, a pincer from
two sources that each kept 12), and the last stacks at the end of 101. Two
shapes worked:

- *Pincer with garrisons kept.* Strike from two sources landing together, each
  keeping more than the target's garrison over the attack edge. Neither source
  is a landing, so the garrison stands and dies.
- *Ambush the evacuee.* A garrison that leaves for one of my systems is a fleet
  with a known landing turn. Relief that lands on that turn joins the garrison
  (the pile-up pools a defender's arrivals), so the evacuee fights the whole of
  it. In game 101 marshal's 10 from 9 landed on my 1 together with my 10 from 7
  and died (turn 80).

A dead end makes the first shape free. A rival stack in a system whose only lane
is to mine cannot evacuate anywhere, and needs 1.22x my garrison to break out. In
game 505 marshal's last stack, 34 to 46 ships, sat in the dead end 12 behind my
10 for over 60 turns, and played no further part.

**actuary punishes a thinned garrison the turn it can.** Against actuary,
nothing I emptied survived: it struck 1 in game 202 the turn after I cut it from
16 to 13, using 7's 20, and in 404 and 505 it sent small exact coalitions (4+3,
6+3, 3+1) at every frontier garrison I thinned to raid with. Its own swaps were
followed by an immediate refill of the source (3->6 in game 404, the turn after
6 emptied), so my counter-swaps landed on a garrison already rebuilt. Against
marshal, thinning was safer: it struck thin garrisons too, but late and at its
fleeing-garrison price (below), so a stand often held.

**Opening tempo decided the losses to marshal.** In game 606 marshal chained its
home stack neutral to neutral and had 6 systems to my 4 by turn 26. I split the
opening into 7- and 5-ship captures, lost ships to a 10-on-8 roll, and never
caught up. In game 101 the same marshal opening met a single 12-ship chain of
mine and the economies stayed level.

**A free-for-all goes to whoever wins the swap war, not to the income lead.** In
game 505 I led at turn 88 (12 systems to actuary's 5, 3.9 to 1.8 ships a turn, 149
ships to 84) and lost. From turn 90 actuary moved its whole army every turn,
pulled it off any system I threatened and threw it at whatever I had just
thinned. Each exchange cost me more than it cost actuary (I lost 322 ships from
turn 84 on, actuary 198), until its 53-ship stack split four ways onto four thin
systems of mine at once (turn 126).

## Readings over the logs

### Strikes on a system emptied at launch: people refill, bots do not

A one-off script (`replay.reconstruct` with an `on_turn` snapshot; not committed) over the 172 current-rules public replays (the board's
`public_replays`) and the five games above: every landing by one bot on a system
another player held, with the target's garrison when the fleet launched (G_L),
its garrison on landing (production, own arrivals and its own launches that turn
counted), and whether the attacker held the system afterwards.

    public replays, strikes launched at a garrison of 0-2
    attacker  defender  strikes  captured  ships lost in failures
    marshal   person       21       9 (43%)          50
    marshal   bot          25      19 (76%)          20
    actuary   person       15       7 (47%)          55
    actuary   bot           6       4 (67%)           9

A bot that empties a system leaves it empty: its own flow goes to the front, and
the evacuee has already gone. A person who empties a system usually means to
refill it, or is baiting. Both bots price an emptied enemy system as nearly free,
and against people that is wrong more than half the time. Most failures were
small (F of 1-6 ships), so this is the "no lone trickles into a rival" item in
`marshal-flow.md` seen from the other side, and it applies to actuary as much as
to marshal. The sample is small, and only a model of the rival (the Learning
stop's kind of memory) can tell a person's empty from a bot's.

### marshal's price is for a garrison that runs

`_enemy_margin` carries no jitter premium because 86.7% of out-matched bot
garrisons leave before the blow ("Garrisons run away" in `marshal-pricing.md`).
A person who holds instead makes each strike at `defence + 1` a coin flip, and a
narrow loss by the square law leaves the defender most of its ships. In game 101
marshal sent 13 at my 1, priced against 11 plus the hull due on landing, so 13
against 12. I held, and 13 marshal ships died for 8 of mine (turn 47). Over the
public replays and these logs too few of marshal's strikes on a standing person
landed between 1.0x and 1.22x to read a rate (7, 4 captured), so this is a lead.
A per-rival "does this seat stand?" reading would let marshal price the jitter
half only against seats that hold. That is the same kind of memory Learning
keeps for actuary, and the same oracle question (`learner.md`).

### actuary crosses its own transfers

The greedy can commit A->B and then, its projection changed, B->A in the same
turn. Both stacks fly a lane's length and both garrisons sit thin meanwhile, when
one launch of the difference reaches the same end with both systems held. In 40
duels against marshal at 24 nodes, 273 crossings in 22,582 actuary launches, about
7 a game, averaging 3.4 ships a side over a 2.8-turn lane. Game 505 shows one at
turn 105, 5->14 x12 and 14->5 x15, with my stacks next to both.

Netting each pair after the greedy finishes was built and measured, and lost
(48.7%, seed pairs z -1.64): the crossings are not the waste they look. See
"Crossing transfers (measured, deleted)" in `actuary.md`.

### actuary waits for the worst roll against neutrals

actuary books a capture only if it wins at the worst roll (1.222x at the default
jitter), and a static neutral can neither flee nor be relieved. At 6 ly/turn and
a hull every 3-5 turns, the last 10% of a fleet is several turns of one system's
production. In game 404, from turn 9 to turn 19, actuary's only launches were single ships
flowing to 3 while it saved up the 10 that 6's 8-ship garrison needs at that
edge. At 1.10x the
attacker wins 86% of the time, at 1.15x 95% (Monte Carlo over the engine's
uniform swing). marshal measured the same question and lost (`NEUTRAL_MARGIN`
1.2: 47.1%, `marshal-flow.md`). actuary had not been measured on it. It
has now, and loses the same way: at 1.10x it reproduced worse (seed pairs
z -2.10), at 1.15x it is null. See "Neutrals at less than the worst roll
(measured, deleted)" in `actuary.md`. What a person gains by taking a neutral at
1.1x is not something a bot gains by doing it every time.

## What a person should do (the brief version)

- Open with one stack chaining neutral to neutral. Do not split the home fleet.
- Never strike from a system you cannot refill by the turn an evacuee would land.
  Pincer from two sources that both keep a garrison, or time relief onto the
  source.
- When a bot evacuates toward you, land on the evacuee's destination the turn it
  arrives.
- Against marshal, hold rather than flee when it out-ships you by less than 1.22x.
  It priced the strike for a garrison that leaves.
- Against actuary, do not thin a frontier garrison below 1/1.22 of everything
  next to it. It notices on the next turn.
- Bottle a stack in a dead end and leave it there.

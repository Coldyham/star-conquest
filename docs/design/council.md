# council design notes

`models/council.py` is knower's Search with three changes: a learned judge,
more borrowed candidates (actuary, and since 2026-10-20 evolved), and each
borrowed candidate playing out its own line. This file says what each change is
and what it bought, how council was measured, what it costs, and what was built
and dropped on the way. The search it builds on is in [`knower.md`](knower.md);
roster-wide rules are in [`bots.md`](bots.md). Index: [`../README.md`](../README.md).

Since 2026-10-19 council is no bot of its own but knower's fourth stop, *Oracle:
Council*, playing exactly as `models/council.py` did at On; its patches to a
private copy of knower are knower's `_COUNCIL` branches, and a seat stored as
`council` plays there (`ai.LEGACY`). Its Off became knower's Search. The
measurements below name it council. See [`bot-families.md`](bot-families.md).

## What council changes, and what each change bought

knower's Search compares candidate openings (its own plan, a timid posture, and
four borrowed bots' moves for its seat) by playing each a few turns forward and
scoring where it lands. council keeps all of that and changes three things, and
since 2026-10-20 borrows one more bot (the last row).

| Change | What it does | Gain against knower:2 |
| --- | --- | --- |
| **actuary as one more borrowed candidate** | actuary's move for our seat joins the openings | about +11 points |
| **A learned judge** | a rolled-out board is scored by a win probability (`_council_eval.value`) instead of knower's material difference | about +5 more |
| **Each borrowed candidate plays its own line** | a borrowed opening is played forward by the bot that proposed it, not by knower's blind planner | +27 more (paired, z 8.98) |
| **evolved as one more borrowed candidate** (2026-10-20) | evolved's move at Look-ahead Off joins the openings, after actuary's | +6.3 more, head to head against Council (z 3.49) |

Points are percentage points above the fair share (50% in a duel, 33% with three
players), pooled over cells and paired by seed.

The third change matters most. Played on by knower's planner, a borrowed
opening is judged as one move followed by someone else's strategy. Played on by
its own author, the search compares whole strategies against exactly predicted
rivals, and picks the one that wins from here.

**The judge** is a 29 → 16 → 1 network (tanh, logistic output) on 29
board-size-free features: production, ship and system shares and gaps against
the strongest rival, fleets in flight by arrival time, threat and exposure,
neutral production in reach, and the setup (seats, nodes, lane length, turn).
`DEFENDER_ADVANTAGE` and jitter are inputs too, but every training game used one
value of each, so their weights are zero and the judge cannot react to a tuned
fight. It was fitted on 3,000 games of roster self-play,
every second turn of every seat, labelled with whether that seat won. It ships
as constant tables in pure Python, a few hundred multiply-adds per board, and is
evaluated lazily: only the boards the search actually compares are scored.

**`aux`** is two named stops. 0 is knower at Search exactly (the control every
measurement is read against); 1, the default, is council.

## How it borrows knower

`knower.py` is loaded a second time as a private module (`sc_council_knower`),
and only that copy is patched: `_evaluate`, `_advance`, `_external_plan` (to
remember which bot proposed each opening) and `EXTERNAL_CANDIDATES`. A real
knower seat in the same game keeps its own module and globals. To every other
bot council is an oracle seat (`IS_ORACLE`, `is_oracle_seat`), modelled blind,
exactly as two knowers model each other. It keeps the oracle rules: it never
calls `ai.load_models`, reads rivals through knower's machinery, and draws
nothing from `state.rng` (`tests/test_council.py`).

## The measurements

All games at fixed work (guards lifted), so results don't depend on the
machine. Each map is played in every seating; win shares are per seed, and z is
taken over seeds. Cells are `mode:nodes:ly:players`. The seeds below were never
used while council was being developed.

**Against the roster, 50 seeds per cell:**

| Cell | knower:2 | actuary | marshal |
| --- | --- | --- | --- |
| random:7:6:2 | 87.5% | 98.5% | 99.0% |
| random:11:6:3 (fair 33%) | 78.7% | 94.7% | 98.0% |
| random:13:3:2 | 90.0% | 99.0% | 97.5% |
| random:18:6:2 | 89.0% | 99.0% | 97.5% |
| random:24:3:2 | 95.0% | 98.0% | 98.0% |
| random:18:18:2 | 83.0% | 98.0% | 98.5% |
| **Pooled, above fair** | **+40.0 points, z 32.8** | **+50.6** | **+50.9** |

**Positions from real games.** 288 positions from 173 public games, all wins for
the person, each played on by the bot in the person's seat: council won 94.8%,
knower:2 82.3%. council won 39 that knower lost, against 3 the other way.
Against the person's own result it was on a par: 144 faster, 6 level, 138
slower, the median within half a turn.

**The 18 posted maps no stored bot result had won:** council won 12, knower:2 8.

**Five players.** On random:31:6:5 (fair 20%) it wins 56% against knower:2.
Melees are its weakest setting; duels and three-player games are where it
dominates.

## Against the posted maps and the people who posted them

*2026-10-18*, all 152 posted maps. The stored bot column was of mixed vintage
(knower's rows from eight `engine_rev`s, none current), so council, knower:2
and actuary were replayed afresh on the main of that date, as the worker does
(guards ×100, no timeouts); the other bots' rows are the stored ones. A result
is ranked as the board ranks it: a win, then fewer turns, then fewer ships lost.

| | Stored column | Fresh replay |
| --- | --- | --- |
| council wins | 143 | 146 |
| council's result beats every other bot's | 97 (2 shared) | 112 |
| some bot beats it | 53 | 33 |
| … knower:2 | 32 (7 where council lost) | 22 (all faster; council never lost one) |
| … actuary | 27 | 10 |
| knower:2 wins | 114 | 110 |

**knower:2 is not a strictly weaker council, and its wins are not a pattern.**
Only 6 of the 32 maps knower took in the stored column are still knower's in
the fresh replay. The first turn the judge disagrees with knower's material
score, the two play different games with different fights and different
dice, so any one map can fall either way. Where both win, council is a median
13 turns faster (faster on 83 of 110). The tuned combat sliders on posted maps,
which the judge never saw (`DEFENDER_ADVANTAGE` 1.05, jitter 0.06 or 0.08),
do not explain knower's wins: it wins slightly fewer of those maps.

**council's stored losses** (9) are all three- and four-seat games on 13–20
systems at 7.5–8.5 ly, most against knower and marshal together.

**People beat it on most maps.** The best counted human score beats council's on
115 of 151 maps (108 against the fresh replay), and council's median win is 7
turns (16%) slower than the record. 106 posted games played at least 80% by
hand, read with `tools/human_habits.py`'s `score_log` beside council's stored
game on the same map (council's turns all counted as its own):

| | Person | council |
| --- | --- | --- |
| Turn reaching 50% of the board's income | 37 | **33.5** |
| 50% → 90% of the board's income (62 games reaching it) | **30** (mean 30.3) | 31.5 (mean 36.0) |
| 90% → the win | **4.5** (mean 5.6) | 6 (mean 8.0) |
| Ships lost (median) | **37** | 58.5 |
| Frontier systems lost per 100 frontier system-turns | **2.0** | 3.6 |
| Voluntary frontier empties lost within five turns | **6%** | 30% |
| … when relief could not land in time (`short`) | 13% | 41% |
| … with nothing covering them (`uncovered`) | 29% | 60% |
| A wave over the target's garrison (median) | **1.5×** | 1.33× |
| Waves smaller than the garrison | **20%** | 25% |

council's opening is as quick as a person's or quicker. It loses its time in the
middle game (349 turns more over the 62 games) and the close (143 more), and
most of it is stripped frontiers: people empty about as many frontier systems,
but lose a fifth as many of them, while council empties systems it cannot
relieve and then pays to take them back, which is where its extra ship losses
go. People also strike heavier and less often, and fewer of their waves fall
short.

One confound: rivals may not answer a person's seat as they answer a bot's
(council is modelled as an oracle seat; actuary learns the person's habits), so
part of the gap in punished empties may be the rivals rather than council.

**What to try.** Rank down rolled-out lines that leave a frontier system empty
with relief `short` or `uncovered`, in the judge's ranking rather than in a
stand-in (the traps under "Built, measured, dropped" point the same way), and
measure it against posted games, not the roster: relief bolted onto marshal
and actuary was null against bots (`docs/decided-against.md`), so self-play is
the wrong screen for a habit only people expose. The position suite and
`tests/council_traps.py` are the cheap first checks; the fresh replay above is
the before. A change aimed at the turns rather than the losses (striking sooner,
closing faster) can be screened on turns to win against the weak bots, where
council's win rate is saturated ([`win-turns.md`](win-turns.md)).
*2026-10-19:* tried, both ways, in "Stripped frontiers are trades, not
mistakes" below. Ranking the losses down cuts them as intended and plays much
worse, so the first half of this paragraph is closed.

## Stripped frontiers are trades, not mistakes (2026-10-19)

Tested on main at `827852a`, on the 153 posted maps among the public replays
on current rules (`public_replays`, read with the publishable key; one replay
per map, by challenge key and seed):
council in the posted seat against the posted bots, guards lifted, played to
the end. The empties and frontier losses are read with `tools/playstyle.py`'s
`empties`, `_outcomes` and `_frontier`, as in the table above. Arms are paired
map by map and ranked as the board ranks them.

**Council sees the losses coming.** On every twelfth public replay's map (18
games, 275 voluntary frontier empties), 54 empties were lost within five turns.
In 48 of them the line the search picked had already lost that system inside its
own rollout, within five plies. Only 6 were not foreseen. The rollouts play every
bot rival as the oracle reads it, so they see the punishment. The judge then
picks the line anyway, because what the opening bought is worth more than the
system.

**Offering the hole covered changed nothing** (`_cover`, built and removed).
After the pick, the same opening was offered again, keeping back just enough
at each system its line lost to beat the biggest wave seen inbound
(`combat.edge_defending`, floored at one more), or the whole garrison if that
was still lost. Transfers were cut first and strikes called off whole. The
bot that proposed it played it on, and the judge chose between the two. It
fired on a quarter of decides and won a quarter of those.

| Screen | Pairs | Beats the old council | z |
| --- | --- | --- | --- |
| Turns to win, `--quick`, against heuristic, rusherplus, thinker and marshal | 700 | 50.7% (turns −0.4%) | +0.6 |
| Against knower:2, `--quick`, seeds 7,000,000 + 25 | 175 | 55.4% (+2.4 pt won) | +1.7 |
| … confirmed on seeds 7,100,000 + 40 | 280 | 49.9% | −0.0 |
| The 153 posted maps | 153 | 48.0% (empties lost 27% → 26%) | −0.6 |

**Ranking the losses down plays much worse** (`LOSS_PENALTY`, built and
removed). Each line's judged win chance was reduced by a constant for every
system its opening drew from and a rival took within five plies, in the main
ranking and in `_cover`'s comparison.

| On the 153 posted maps | Old council | Penalty 0.03 | Penalty 0.10 |
| --- | --- | --- | --- |
| Frontier empties lost within five turns | 27% | 13% | 9% |
| … `short` / `uncovered` | 28% / 52% | 15% / 30% | 10% / 20% |
| Frontier systems lost per 100 frontier system-turns | 6.54 | 4.48 | 3.91 |
| Maps won | 148 | 145 | 137 |
| Beats the old council | | 37.6% (z −3.4) | 25.2% (z −7.8) |
| Turns, pairs both won (mean) | | +7.2 | +8.9 |
| Ships lost, pairs both won (mean) | | +28.1 | +24.6 |

At 0.10 council's empties and frontier losses come near a person's (6% and 2.0
in the table above), and it is slower, loses more ships and wins fewer maps.
Against these bots the systems council strips and loses are good trades, and
the judge prices them correctly. The stripped frontiers are a difference in
style from the people who beat it, not the reason they beat it, so the turns
people save have to come from somewhere else: their heavier, rarer waves, or
the confound above (rivals that answer a person differently). Anything that
changes the habit should be screened against the people's own games, not
bots. The traps in `tests/council_traps.py` were unchanged by `_cover`: it
holds sources, and the traps are lost targets.

## evolved as one more borrowed candidate (2026-10-19, shipped 2026-10-20)

evolved at Look-ahead Off, borrowed after actuary, its line played on by
evolved (`tools/candidate_bench.py --borrow evolved:0 --stop 3`), head to head
against Council on every seating: +5.2 points pooled (z +3.13) on a screen block
and +6.3 (z +3.49) on fresh seeds after `_flee` landed, every cell of the second
between +4 and +8. Its line is picked on 12.7% of contested decisions, fourth
after actuary, marshal and the default, and changes the move on 10.3%. It is the
first extra opening to measure well above the +1 that more postures, actuary at
Greedy and a hold opening each gave ("Built, measured, dropped"). It shipped in
`COUNCIL_CANDIDATES`, after actuary, where it was measured; Search does not
borrow it. The tables, and what shipping changed, are in [`knower.md`](knower.md),
"evolved as a borrowed candidate (2026-10-19)".

## actuary at Hunt in Field's place (2026-10-20, measured, not shipped)

Council borrows actuary at Field: its entry in `COUNCIL_CANDIDATES` is
`("actuary", None)`, and an aux of None is `config.AI_AUX`, Field. Hunt,
actuary's top stop, beats Field 66.9% head to head ([`hunter.md`](hunter.md),
"Where it stands"). Tried in Field's place, in its slot and with its salt
(`tools/candidate_bench.py --borrow actuary:3 --instead actuary --stop 3`; the
same swap at Field, `actuary:1`, plays Council exactly), head to head against
Council on seeds 7,200,000 + 50, after knower's `_flee`:

| Cell | Points above fair | Cost a decide, matched positions (median ms) |
| --- | --- | --- |
| random:7:6:2 | +4.0 (z 0.94) | 25 → 56 (×3.6) |
| random:11:6:3 | +8.0 (z 2.00) | 32 → 78 (×3.0) |
| random:13:3:2 | +4.0 (z 0.85) | 44 → 125 (×3.0) |
| random:18:6:2 | +5.0 (z 1.15) | 41 → 225 (×8.0) |
| random:24:3:2 | +6.0 (z 1.29) | 106 → 998 (×9.3) |
| random:18:18:2 | +14.0 (z 3.46) | 22 → 63 (×3.8) |
| **Pooled, 300 seeds** | **+6.8 (z 3.86)** | |

Every cell gains. Hunt's line is picked on 37.5% of contested decisions and
changes the move from the best other line's on 29.4%. The ratios are of the
totals (`--time`, one process, guards lifted).

**Not shipped, for its cost.** Its look-ahead runs again on every rolled turn of
its line, so a decide costs three to nine times as much, about a second at 24
systems on 3 ly/turn. The browser's 150 ms search guard would cut most of those
searches short, so a player would not meet the Council this bench measured.
evolved, added beside Field, buys about as much (+6.3, "evolved as one more
borrowed candidate") for a sixth more a decide, and has been borrowed since
2026-10-20; this run was against Council without it. Decided against
(2026-10-26) for its cost; the two together are not to be measured.

**Without its look-ahead** (`--with LOOKAHEAD=0`, set only around the borrowed
actuary's own calls; no stop of actuary's knob offers it), on the same seeds: +2.6
points (z +1.49), the cells −3.0, +3.3, +12.0, +5.0, +4.0, −6.0 in the order
above, at no measurable cost (×0.99 to ×1.06). Its line is still picked on
36.5% of contested decisions, as often as with the look-ahead, but the gain is
mostly gone. That matches plain Hunt against Field head to head, 50.8%
([`hunter.md`](hunter.md), "The field, by lane length"): the look-ahead is all
of Hunt's lead, and two near-equal candidates mostly trade which of two similar
lines wins. A lean, not adopted.

## Playing for the soonest win (`FAST_CLOSE`, 2026-11-11)

**What prompted it.** On the daily puzzle Council misses par by a median of 3
turns over the first 15 days (1.56 times par), and the blind bots by 8-18.
Against the best human line on each of 14 days both won, split at the 3:1 and
9:1 income leads ([`win-turns.md`](win-turns.md)), Council took 124 turns to 3:1
against the person's 113, but 70 from 3:1 to the win against 13. Once ahead it
stockpiles: on 2026-11-01 its ships climbed from 10 to 48 over 25 turns, where
the person won in 10 holding about 12. People spend ships to the end. Two causes
in the search:

- A line that wins scores 1.0 whatever ply it wins on, so a win on ply 2 and one
  on ply 8 tie, and the tie keeps the default.
- Once the judge rates every line near 1, the pick is decided by noise, which
  favours the line holding the most.

Tracing 2026-10-30, 11-01 and 11-03 showed a third: the judge read 0.99 for 18
turns while the rival kept one system, and no candidate won inside the horizon
(longest lane + 5, 8 plies there), because none pools ships for one strike on a
system three turns off; each turn's pick was a different bot's line, so the plays
added up to no plan.

**What it does** (`models/knower.py`, Council only):

- A won line ranks by the turn it wins on, soonest first.
- Once the real board shows a 3:1 income lead (`CLOSE_SHARE` 0.75), a line the
  judge rates at least `CLOSE_AT` (0.95) ranks by the rivals' remaining material,
  weighed as `_evaluate` weighs it, and never by our own ships.
- In that state, if no line won inside the horizon, every line rolls on a ply at a
  time until one wins or `CLOSE_DEEPER` (3) times the horizon is spent, under the
  same deadline as the rest of the search.
- In that state too, while `_evaluate` says we are ahead, a closer is offered: it
  picks the rival system it can take soonest on every roll (`combat.sure_count`
  against the garrison at landing), routes through our own systems only, and sends
  whole garrisons a hop whenever their distance equals the strike's; the rest wait.
  It plays its own line on in the rollouts.

**Measurements** (`tools/puzzle_bench.py` on the 15 stored puzzles and 100 dealt;
`tools/win_turns.py --quick`; the base is main at 1c6fd7e, run from a worktree):

| | Base | Judge-gated (first build) | 3:1-gated (shipped) |
| --- | --- | --- | --- |
| Puzzles: better / worse / same | | 59 / 24 / 32 | **59 / 7 / 49** |
| Puzzles: mean turns, z | 19.9 | -1.20, -3.2 | **-1.48, -6.5** |
| Weak bots: beats base, z | | 67.8%, +8.7 | **73.2%, +14.2** |
| Weak bots: turns | | -6.2% | -4.4% |
| Weak bots: before 3:1 / after | | +1.3 / -4.6 | **0.0** / -2.2 |
| marshal, actuary: beats base, z | | | **73.9%, +11.1** |
| marshal, actuary: wins, turns | | | +0.3 pt, -6.5% |

No puzzle or game won by the base was lost. The judge-gated build, which closed
whenever the judge read 0.95, played worse before 3:1 because on small boards the
judge reads that early (2026-10-30: from turn 1). Against marshal and actuary
ships lost fell in every cell. A game costs about the same (it is shorter); a turn
costs up to about 1.5 times as much on small boards with the guards lifted.

**What is left.** On puzzles the gap to par is still most of it (about 18.5 turns
against par near 8-10). The person's 10-turn line on 2026-10-30 took a rival
system on turn 4, held it while the rival threw fleets at it against the
defender's advantage, and took the drained home with 4 ships. Lines like that
(baiting a predictable rival, softening strikes, relays; [`par.md`](par.md),
"Beyond K launches") are not among Council's candidates.

## Baiting a covered frontier (2026-11-11, built, measured, deleted)

**The idea** (the author's): against most bots a person wins by baiting. Two
systems closer to each other than to any rival can bounce ships between them and
break the strikes a rival throws, and on a `core` map the centre reaches the ring
sooner than anything else, so everything can sit there and reinforce outward.
heuristic does it by accident, marshal and actuary lower their defend margins
when relief is in range, and marshal's wedge retreat was worse without an
oracle. Council's rollouts run each rival's own code, and knower's Phase 1 answers
a fleet in flight from any neighbour that lands in time, so only the first move
was missing.

**The arm.** A Council candidate: a system of ours next to a rival, with no
hostile fleet inbound and nothing the default plan sends it, is coverable if a
system of ours has a lane to it shorter than any rival's reach. The candidate is
the default plan with the coverable system's spare garrison moved to its cover:
the most tempting one emptied (`one`), every one (`all`), or half or a third of
the most tempting one's (`half`, `third`). Bait lines play on by the default plan.

**What it read** (Council with the fast close, both arms):

- *Offered and picked.* The 15 stored puzzles: a cover in 34 of 196 decides,
  offered in 21, never picked, in any mode. Six games each of symmetric core,
  wheel and hub and random 18 at 6 ly/turn against thinker, heuristic and
  rusherplus: offered in 3-21% of decides, picked in 0-2%.
- *Offering it.* 115 puzzles: 112 identical, 3 faster (-0.21 turns, z -1.4). 30
  seeds each of core 13 against thinker and heuristic, wheel 13 against thinker
  and random 11 against rusherplus: -0.17 to +0.23 turns, nothing significant.
- *Forcing it* (`half` played whenever offered and its judged value is within
  0.05 of the best line's): slower in every cell, +4.5 (z +3.7), +1.8 (z +2.4),
  +4.9 (z +3.2) and +1.6 (z +3.0) turns, no wins changed.

The rollouts were right to turn it down: an empty system draws only the strike
an empty system needs, and a thinned one loses tempo Council spends better
pressing. A person's bait pays against bots because it outlasts them over many
turns; Council already wins those games faster than the bait would. Deleted;
`tools/puzzle_bench.py --set` and `win_turns.py`'s layout cells
(`symmetric/core:13:6:2`) stay. The second attempt, "The bounce" below, is what
shipped.

## The bounce (`BOUNCE`, 2026-11-11, shipped)

**What the posted games show** (`tools/bait_census.py`, 435 public replays on
the current rules, nearly all the author's). A *thin* is at least half of a
garrison next to a rival sent to another system of the seat's own; it is *bit* if
a rival strike launched after it lands there within 8 turns.

| Seat | Thins per 100 turns | Bit | Held | ...with a bounce back |
| --- | --- | --- | --- | --- |
| The person | 10.7 | 580 | **343 (59%)** | **207** |
| actuary | 14.3 | 250 | 26 (10%) | 22 |
| knower | 13.2 | 145 | 13 (9%) | 10 |
| heuristic | 3.2 | 171 | 21 | 8 |
| marshal | 6.9 | 17 | 0 | 0 |

Bots thin as often as the person; what differs is what follows. Per 100 turns the
person thins 10.7 times, is bit 2.6, holds 1.5 and holds by a bounce 0.9. The
rival launches a median of one turn after the thin, the strike is 5 ships
(median), and in 306 of the 343 held baits the person's answer was launched after
the rival's launch. A bot's thin is ships flowing forward: they are spent by the
time the strike comes. The person's baits hold 74-81% of the time against thinker,
rusherplus and heuristic and 50-56% against marshal, actuary and knower.

**Council already answers.** Of the strikes on Council's systems in 192 games
against six bots, 107 could be held only with ships in reach; it held 97 and sent
help in 96. The reaction exists; the setup does not.

**What it does.** A Council candidate (`_bounce_plan`). For the three most
tempting of our systems next to a rival, with no hostile fleet inbound or
predicted and none the rival strikes anyway with the default plan played:

- the default plan, less whatever it sends that system, plus a share of its spare
  garrison (all, two thirds, a half, a third) sent to its nearest neighbour of
  ours;
- that turn is played forward and every rival's own `decide` is asked what it
  launches (`_strikes_on`). The bait stands if the strike is 3 ships or more and
  the garrison at landing, our fleets landing on it, and every ship of ours that
  can reach it once the strike is launched (the sent ships too, if they can get
  back) make `combat.hold_count`;
- of those, the one drawing the largest strike is offered. Its line keeps the bait
  home and sends in what holds the strike with half as much again to spare
  (`BOUNCE_PAD`), the farthest first, until it lands (`_bounce_on`).

Council plays it only where its rollout scores best.

**The strict build first.** Only the sent ships counted, they had to make the
round trip (twice the lane less one, against the strike's flight), and a system
the default plan fed was skipped. Against thinker, of 1,252 decides: 719 had a
frontier system, 195 the round-trip geometry, 70 were neither struck nor fed, 53
had spare ships, the rival bit 18, and 9 held. Over 800 games (five cells, thinker,
heuristic, rusherplus and evolved, 40 seeds) it was played in 27; those games ran
2.2 turns shorter (z -2.0). Forced, every verified bounce played (100): the rival
struck 99, 96 held, 519 strike ships predicted and 514 real, and the games ran
level (-0.4 turns, z -0.7).

**The shipped build**, same 800 games:

| | Council chooses | Forced |
| --- | --- | --- |
| Games with a bounce | 105 | 418 |
| Bounces played; struck; held | 114; 114; 104 | 667; 662; 565 |
| Strike ships predicted, real | 831, 831 | 3,914, 3,895 |
| Turns, those games | **-2.68 (z -4.5)**, 70 faster, 25 slower | +1.29 (z +4.4) |
| Ships lost, those games | -3.2 | 0.0 |

Every game without a bounce is the base's own. By rival: thinker -3.0 turns,
evolved -4.2, heuristic -1.4, rusherplus -1.6; symmetric core -2.3. The rollout
choosing among verified baits is what pays; forcing them loses.

**Against Council with the fast close** (`win_turns.py`, `puzzle_bench.py`):

| Bench | Beats, z | Turns, z | Wins |
| --- | --- | --- | --- |
| 115 puzzles | 9 better, 2 worse, 104 same | -0.43 a puzzle, -1.75 | none lost |
| heuristic, rusherplus, thinker (525 pairs) | 53.7%, +4.1 | -1.6%, -4.2 | none lost |
| marshal, actuary (350 pairs) | 52.8%, +2.4 | -2.1%, -3.4 | -0.2 pt (one), z -1.0 |
| Four symmetric layouts at 13, 13:3, 18:18, 24:12, four seats at 18:6, against thinker, rusherplus, evolved and marshal (1,080 pairs) | 53.0%, +4.8 | -0.9%, -4.0 | none lost |

No cell is significantly worse (the worst +1.2%, z +1.0), so it is not gated to
the weaker rivals. A turn costs the same (0.96 to 1.23 times, within noise).

**What is left.** Council plays a bounce 0.36 times per 100 turns against the
person's 1.5 held. Ten of the 114 were lost: the hold counts ships that could
reach, and a later turn's pick sometimes sends them elsewhere. The bite is checked
one turn ahead; 123 of the person's 343 held baits were bit later than that. It
needs the rival's `decide`, so it is Council's alone.

## What it costs

In the browser (CPython on WebAssembly, about 2× native), median per decide:

| Setup | council | knower:2 |
| --- | --- | --- |
| 7 nodes, duel | 42 ms | 31 ms |
| 18 nodes, duel | 62 ms | 28 ms |
| 31 nodes, duel | 94 ms | 38 ms |
| 31 nodes, 5 players | 107 ms (p90 141) | 70 ms |

Both fit knower's guards (150 ms of search, 50 ms of oracle). On slower devices
the guards trip and council degrades rather than freezes. Measured with the
guards on at a fraction of this machine's speed, against knower:2 under the same
handicap:

| Cell | No guards | 0.33 | 0.17 | 0.10 |
| --- | --- | --- | --- | --- |
| random:7:6:2 | 90.0% | 90.0% | 90.0% | 88.0% |
| random:11:6:3 (fair 33%) | 74.7% | 74.7% | 74.7% | 72.0% |
| random:18:6:2 | 86.0% | 86.0% | 86.0% | 96.0% |
| random:31:6:5 (fair 20%) | — | 56.0% | 58.0% | 42.0% |

**The leaderboard's bot column** (`tools/bot_replay.py`, guards lifted 100×).
On 24 of the 142 posted maps, sampled at random and replayed as the worker
does, council took a median 3.4 s of CPU a map against knower's 1.6 s (1.5×,
3.8× at worst) and 33 s on the slowest, a five-seat 31-node map at 3 ly. All
142 come to about 16 CPU minutes on a desktop, so the first fill takes one or
two of the worker's 40-minute runs. Council won 23 of the 24; knower won 19.

**A whole turn in the browser build** (headless Firefox on a desktop, 12 turns,
every bot seat included, guards on): 73 ms median on a 40-node duel, 163 ms on
the same at 2 ly (a setup the warning flags), 323 ms on 31 nodes with four
council seats of five.

These figures predate evolved joining the candidates (2026-10-20), which costs
about a seventh more ([`knower.md`](knower.md), "evolved as a borrowed candidate
(2026-10-19)").

**The setup warning** (`council.setup_warning`) is knower's, priced for
council: every council seat searches, an Off seat at knower's cost and an On
seat at `COST_VS_KNOWER` (2.5) times it, the top of the 1.4–2.5× range in the
table above, scaled by Council's lines over the seven that range was measured
with (`COST_COUNCIL_LINES`), so evolved's line is priced too. A cost model
fitted to council's own lines would replace the factor.

**The gentler-jitter test** (`tests/test_ai.py`) skips council
(`_ROLLS_OUT_FIGHTS`): its rollouts play fights at the real jitter, so its plan
follows the jitter by design, as knower's does at Search (the test only checks
knower at Predict). The judge's own jitter input has a zero weight, so a
gentler jitter never moves the score; it is also floored at the tuned 0.10
(`TUNED_JITTER`) in case a retrained judge learns to read it.

## Built, measured, dropped

Each was screened against knower:2 (and, late on, head-to-head against council)
on a screening seed set, and confirmed on a second set only after a positive
screen.

- **Synchronised strike openings** (every lane landing on one target the same
  turn): a gain on one seed set that did not replicate.
- **Staggered strikes, decoys, double strikes:** null or worse.
- **Melee guard, denial weighting, relative melee scoring:** null or worse in
  three-player games.
- **Predicting a knower rival by running its real Search:** null, at double the
  cost.
- **More openings** (all five knower postures, actuary at Greedy, a hold
  opening): each about +1 point, null.
- **Hybrids** (default and actuary crossed per system): −4.9 points.
- **Dice shopping** (strikes priced against the fight's real keyed dice): −8.7
  points.
- **Tuning** (NTBEA over 10 knobs, 800 games): the model was flat, and its pick
  screened −3.3 points.
- **Root crossover** (children bred from the best opening and the runners-up):
  +5.6 on the screen, +0.7 on the confirmation.
- **Successive halving** (the best three openings played on further before
  choosing): null; twice as far was worse with three players.
- **Retraining the judge** on council's own games, on its search values, on a
  deeper look-ahead, or with 13 more features (the board after fleets land,
  stack concentration, production at risk): none picked winning openings more
  often than the shipped judge on a ranking test built from positions played to
  the end. The shipped judge picks a losing opening in about a third of the
  positions where the choice matters, so there is headroom, but labels built
  from council's own view do not reach it.
- **A doomed garrison leaving whole** (`_flee`, knower's at Search since
  2026-10-19): null against the weak bots and marshal (50.8%, z +1.0; ships
  lost −0.39 a game) and 47.1% (z −1.2) on the posted maps, so Council does not
  run it. Its judge already picks openings knowing the garrison dies, and half
  as many of its ships die this way. See [`knower.md`](knower.md), "A doomed
  garrison leaves whole".
- **actuary as the stand-in for a person** in the rollouts, in place of
  knower's `_blind`, on the first rolled turn or on every one. Against a rival
  council was told is a person (20 seeds, both seatings, random:11:11:2 and
  random:13:6:2), every-turn actuary won 73/80 against marshal where `_blind`
  won 75/80. Its 78/80 against actuary itself (`_blind` 53/80) is an exact
  model, not a better guess. On the six traps from real games
  (`tests/council_traps.py`) it avoided one strike and shrank two, at about 3×
  the rollout cost. In two of the four it still lost, actuary sent the relief
  inside the rollouts and safer openings were offered, but the judge picked the
  strike anyway: the ranking, not the stand-in, is most of the gap. Worth
  revisiting only if posted human games show people relieve like actuary.
  *2026-10-17, merging main:* with knower's stand-ins and marshal's and
  actuary's relief changes, shipped council loses 4 of the six traps, not 6:
  it holds at 95210 t19 (was 9 sent) and takes 95208 t16 with 14 (was 8).
  `test_the_traps_rebuild_the_strikes_council_really_sent` pins the new
  strikes. Which change did it is not separated.

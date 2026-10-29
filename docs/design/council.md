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

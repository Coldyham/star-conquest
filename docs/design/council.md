# council design notes

`models/council.py` is knower's Search with three changes: a learned judge,
actuary as one more adviser, and each adviser playing out its own line. This file
says what each change is and what it bought, how council was measured, what it
costs, and what was built and dropped on the way. The search it builds on is in
[`knower.md`](knower.md); roster-wide rules are in [`bots.md`](bots.md). Index:
[`../README.md`](../README.md).

## What council changes, and what each change bought

knower's Search compares candidate openings (its own plan, a timid posture, and
four borrowed bots' moves for its seat) by playing each a few turns forward and
scoring where it lands. council keeps all of that and changes three things.

| Change | What it does | Gain against knower:2 |
| --- | --- | --- |
| **actuary as an adviser** | actuary's move for our seat joins the openings | about +11 points |
| **A learned judge** | a rolled-out board is scored by a win probability (`_council_eval.value`) instead of knower's material difference | about +5 more |
| **Each adviser plays its own line** | a borrowed opening is played forward by the bot that proposed it, not by knower's blind planner | +27 more (paired, z 8.98) |

Points are percentage points above the fair share (50% in a duel, 33% with three
players), pooled over cells and paired by seed.

The third change matters most. Played on by knower's planner, a borrowed
opening is judged as one move followed by someone else's strategy. Played on by
its own author, the search compares whole strategies against exactly predicted
rivals, and picks the one that wins from here.

**The judge** is a 29 → 16 → 1 network (tanh, logistic output) on 29
board-size-free features: production, ship and system shares and gaps against
the strongest rival, fleets in flight by arrival time, threat and exposure,
neutral production in reach, and the setup (seats, nodes, lane length, turn,
`DEFENDER_ADVANTAGE`, jitter). It was fitted on 3,000 games of roster self-play,
every second turn of every seat, labelled with whether that seat won. It ships
as constant tables in pure Python, a few hundred multiply-adds per board, and is
evaluated lazily: only the boards the search actually compares are scored.

**`aux`** is two named stops. 0 is knower at Search exactly (the control every
measurement is read against); 1, the default, is council.

## How it borrows knower

`knower.py` is loaded a second time as a private module (`sc_council_knower`),
and only that copy is patched: `_evaluate`, `_advance`, `_external_plan` (to
remember which adviser proposed each opening) and `EXTERNAL_CANDIDATES`. A real
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

**Known gap:** council declares no `setup_warning`. It searches on every setup
and costs about 1.5× knower's Search, so the big setups knower warns about would
deserve a warning here too, from a cost model fitted to council's own lines.

**The gentler-jitter test** (`tests/test_ai.py`) skips council
(`_ROLLS_OUT_FIGHTS`): its rollouts play fights at the real jitter, so its plan
follows the jitter by design, as knower's does at Search (the test only checks
knower at Predict). The judge's own jitter input is floored at the tuned 0.10
(`TUNED_JITTER`), so a gentler jitter never moves the score.

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

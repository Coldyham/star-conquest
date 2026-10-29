# The roster ladder

The one home for the measured standing of the whole roster. It is a
measurement, not a property of any one bot: knower predicts and borrows the
other bots, and every bot's margins read the shared combat pricing, so a change
to any roster bot, to a stop's default, or to `combat`/`engine` can move every
row. Each bot's own readings stay in its design file; this file holds only the
round-robin.

## Re-running it

    uv run python -m tests.sim --ladder --trials 30 \
        --ai knower actuary marshal evolved thinker claudebot heuristic rusherplus

- **The cell.** 18 nodes, default settings, every family at its default stop
  (`AiParams.aux` 1.0: knower at Predict, actuary at Field, evolved at
  Look-ahead On, thinker at Thinker). Add `--mode symmetric` for the symmetric
  cell; `tests.sim` has no ship-speed flag, so the slow cell needs a changed
  default. 28 pairs x 2 seatings x 30 seeds = 1,680 games.
- **Two stops of one family.** `--aux NAME=STOP` sets every seat of that name,
  so it cannot seat thinker at both stops. `claudebot` can: it is a legacy name
  (`ai.LEGACY`) that always plays thinker at its Claudebot stop, whatever its
  own `aux`. A grid wanting another family's second stop needs a legacy name
  or a change to `tests.sim`.
- **Who is missing.** Council, for its cost. evolved joined the command on
  2026-10-26, so every reading below predates it; the grid's totals depend on
  who is in it, so compare a pair's head-to-head across readings, never the
  win counts. Adding a bot is a change to this file's command.
- **How it runs.** On every core but two (`--workers`), each game appended as
  it finishes to `results/ladder/<rev>.jsonl` (`--record` names another file),
  where `rev` is `sim.play_rev`, a digest of the core modules a game runs
  through and every `models/` file. A rerun plays only the games that file
  lacks for this setup, so a killed run picks up where it stopped, a bot added
  to the roster plays just its new pairs, and any code change starts afresh.
  The bots' wall-clock guards are lifted 100x (`--budget-scale`), so no move
  depends on machine load. About 30 s a cell on this machine. `--external`
  plays in one process, into a record no later run resumes from.
- **When.** After shipping a change to any roster bot's decisions, to a stop's
  default, or to combat or engine pricing. Run the bot before the change
  beside it on the same seeds, and read that same-day pair, not one date
  against another: readings drift as the other bots move.
- **What it can resolve.** 60 games a pair less timeouts (timed-out games are
  excluded from the percentages): a direction and a regression check, never a
  few points. The change itself is measured by its paired bench
  ([`bots.md`](bots.md), "Measuring a bot").
- **Updating.** Replace "The current reading", move the reading it replaces to
  the top of "Earlier readings" with its date and what changed, and check that
  `LADDER_ORDER` still holds (below).

## `LADDER_ORDER` is not the grid

`starconquest/ai.py`'s `LADDER_ORDER` (mirrored in
`leaderboard/js/standings.mjs`; keep the two in sync) orders the menu and the
board's chips, one entry per family placed by its **top** stop: knower at
Council, actuary at Hunt, thinker at Thinker. The grid seats every bot at its
**default** stop, so actuary can top the grid while knower leads
`LADDER_ORDER`, and knower at Predict sits below evolved. Move an entry only
when a measurement at the top stops says so, and record that measurement here.

## The current reading (2026-10-26)

The first with evolved in the grid (at Look-ahead On), and no bot changed since
2026-10-20: every pair without evolved reads exactly as it did then, timeouts
included, in both cells, although the earlier readings ran in one process
with the guards live, so the guards never bound here. 1,680 games a cell; the
default cell is `results/ladder/45055465.jsonl` (gitignored).

    random, 18 nodes (the default cell)   52 timeouts
    actuary 380 (23%), marshal 351 (22%), evolved 304 (19%), knower 255 (16%),
    thinker 166 (10%), claudebot 83 (5%), heuristic 52 (3%), rusherplus 37 (2%)

    head-to-head (row's win rate vs column)
                knower  actuar  marsha  evolve  thinke  claude  heuris  rusher
      knower         —      8%     17%     32%     89%     95%    100%    100%
      actuary      92%       —     61%     87%    100%    100%    100%    100%
      marshal      83%     39%       —     72%     95%    100%    100%    100%
      evolved      68%     13%     28%       —     97%    100%    100%    100%
      thinker      11%      0%      5%      3%       —     89%     96%    100%
      claudebot     5%      0%      0%      0%     11%       —     66%     75%
      heuristic     0%      0%      0%      0%      4%     34%       —     61%
      rusherplus    0%      0%      0%      0%      0%     25%     39%       —

    symmetric, 18 nodes, 6 ly/turn        128 timeouts
    actuary 379 (24%), marshal 338 (22%), evolved 308 (20%), knower 233 (15%),
    thinker 128 (8%), claudebot 105 (7%), heuristic 39 (3%), rusherplus 22 (1%)

    head-to-head (row's win rate vs column)
                knower  actuar  marsha  evolve  thinke  claude  heuris  rusher
      knower         —     11%     22%     30%     94%    100%    100%    100%
      actuary      89%       —     67%     90%    100%    100%    100%    100%
      marshal      78%     33%       —     63%    100%    100%    100%    100%
      evolved      70%     10%     37%       —     98%    100%    100%     98%
      thinker       6%      0%      0%      2%       —     94%    100%    100%
      claudebot     0%      0%      0%      0%      6%       —     98%     98%
      heuristic     0%      0%      0%      0%      0%      2%       —     66%
      rusherplus    0%      0%      0%      2%      0%      2%     34%       —

evolved is third at the default stops in both cells: below actuary and marshal,
above knower at Predict (68-70% head to head) and far above thinker, as
`LADDER_ORDER` places it. Neither cell sees anything keyed on long lanes. The
slow cell was last read before actuary joined (`tests.sim` has no ship-speed
flag; actuary's head-to-heads there are in `actuary.md`, and marshal beats it
68%):

    random, 24 nodes, 3 ly/turn        96 timeouts, 900 games, before actuary
    marshal 270 (34%), knower 224 (28%), thinker 157 (20%),
    claudebot 84 (10%), heuristic 64 (8%), rusherplus 5 (1%)
    marshal vs knower 76%

## Earlier readings

Newest first. Each was the current reading until the one above it; read a
same-day pair, not the dates.

### 2026-10-20: marshal's CMA-ES ship

Re-run when marshal shipped nine constants tuned jointly by CMA-ES
([`marshal-cma.md`](marshal-cma.md)), with the bot before that ship beside it
on the same seeds. 1260 games, 52 timed out:

    actuary 328 (27%), marshal 308 (25%), knower 236 (20%), thinker 164 (14%),
    claudebot 83 (7%), heuristic 52 (4%), rusherplus 37 (3%)

    head-to-head (row's win rate vs column)
                knower  actuar  marsha  thinke  claude  heuris  rusher
      knower         —      8%     17%     89%     95%    100%    100%
      actuary      92%       —     61%    100%    100%    100%    100%
      marshal      83%     39%       —     95%    100%    100%    100%
      thinker      11%      0%      5%       —     89%     96%    100%
      claudebot     5%      0%      0%     11%       —     66%     75%
      heuristic     0%      0%      0%      4%     34%       —     61%
      rusherplus    0%      0%      0%      0%     25%     39%       —

The same ladder in two other cells, since this one cannot see anything keyed on
long lanes. 30 seeds each; the symmetric one re-run 2026-10-20 with the bot
before the ship beside it, the slow one not since actuary joined (its table is
under "The current reading"):

    symmetric, 18 nodes, 6 ly/turn     128 timeouts (before the ship: 121)
    actuary 325 (29%), marshal 300 (27%), knower 215 (19%), thinker 127 (11%),
    claudebot 105 (9%), heuristic 39 (3%), rusherplus 21 (2%)
    marshal vs actuary 33% (28%), vs knower 78% (76%), vs thinker 100% (98%)
    before the ship: actuary 330, marshal 300, knower 216

Among the six older bots the order is the same in all three, but marshal leads
knower by a much wider margin in both. That gap is not the gate: the symmetric cell has no 5-turn lane either.
Head to head it is 60 games a pair less timeouts, so read it as a direction.

### 2026-10-20: the bot before marshal's CMA-ES ship

The bot before the ship, same day and seeds (49 timeouts): actuary 337, marshal
305, knower 236, thinker 161, marshal beating actuary 27%, knower 83% and thinker
100%. So 39% against actuary from 27%, level against knower, and three games lost
to thinker it used to win; every pair without marshal reads game for game the
same, since knower at Predict borrows no marshal. The before-ship reading had
already moved from the 2026-10-18 table it replaces (actuary 336, marshal 285,
knower 242; marshal beating knower 70% and actuary 25%), so something else in
the roster moved between the two; read the same-day pair, not the dates.

### 2026-10-17 and 2026-10-16: marshal's pour floor and `FEED` re-tune

These readings, and the variants under the next heading, predate the
2026-10-20 ship; "the ship" in them is the 2026-10-17 one. The 2026-10-17 run, with actuary at Planned, read actuary 89% against knower,
65% against marshal and 98% against thinker, and in all: actuary 321 (27%),
marshal 290 (24%), knower 244 (20%), thinker 163, claudebot 84, heuristic 52,
rusherplus 37. It was taken when
marshal shipped a pour floor with `FEED` 3.0, `FEED_MAX_TURNS` 3 and
`RELIEF_AWARE` 0.65 ([`marshal-joint.md`](marshal-joint.md), "Screening against
the roster"); knower moved with it, since it borrows marshal. The bot before
that ship, run the same day on the same seeds, is an exact A/B: actuary 326,
marshal 282, knower 247, marshal beating knower 66% and actuary 24%.

Eight more wins, all against actuary and knower (marshal beats the rest 98-100%
either way): 35% against actuary from 24%, and 70% against knower from 66%. At 60
games a pair that is a direction; the paired measurement behind the ship is +6.8
points against actuary and +3.8 against knower over 2,400 cell-seeds each. The
previous reading (2026-10-16, when knower began running a Learning seat as
Planned; [`knower.md`](knower.md), "Seats it cannot run"), actuary 330, marshal
283, knower 246, predates `RELIEF_IN_FLIGHT` in marshal and actuary, merged the
same day. learner is no longer a file of its own but actuary's Learning stop, so
it has left the grid.

### Other stops (2026-10-17)

With `--aux actuary=2` (every actuary seat at Learning) the grid barely moves:
actuary 324, marshal 285, knower 242, actuary beating knower 93% and marshal 72%
(the bot before the ship: 328, 284, 245; 93% and 74%). Before knower ran the
stand-in, Learning beat knower 95% on the same seeds (`--ai knower actuary --aux
actuary=2`); now it reads 93% to Planned's 89%, so the oracle claim earns little
in this cell.

knower here is at its default, Predict. With `--aux knower=0` (Off) the same
ladder reads actuary 325, marshal 297, knower 209, thinker 170, actuary beating
knower-0 95% and marshal beating it 94% (the bot before the ship: 330, 297, 208;
95% and 96%). For all three Oracle settings against marshal and thinker over
more seeds, see [`knower.md`](knower.md), "Each Oracle setting against thinker
and marshal" (measured before the 2026-10-17 ship).

### Older: before `DENY_SWAP`, and after the retreat fixes

These are readings of the six older bots (before actuary joined). The previous
reading, before `DENY_SWAP`, was marshal 251 / knower 248 with the
head-to-head at 51% and 58 timeouts. The same caveat as the next paragraph
applies: 60 games a pair cannot resolve a few points, so this says nothing
regressed, not that the knower cell moved.

Re-run after the retreat fixes ([`marshal-pricing.md`](marshal-pricing.md), "Two doomed neighbours") and it did not move: 251/248 against
251/249, the knower head-to-head 51% against 50%, one more timeout. That is not
a null result about those fixes — this cell is 60 games a pair against opponents
marshal already beats 89-100% of the time, so it has no resolution at the
1-2 point scale their own paired sweep measures them at. It is here as a
regression check, and what it says is that nothing regressed.

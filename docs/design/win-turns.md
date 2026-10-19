# Turns to win (`tools/win_turns.py`)

How many turns the top bots take to win, against bots they nearly always beat.
It is the reading for a change aimed at the board's turns column, where a win
rate has nothing left to say. Roster-wide method is in [`bots.md`](bots.md),
"Measuring a bot: what earlier sweeps got wrong". Index:
[`../README.md`](../README.md).

## The question

A score is ranked by the win, then fewer turns, then fewer ships lost
([`core.md`](core.md), "Challenge links"). Against heuristic, rusherplus and
thinker, Council, Hunt and marshal win nearly every game (see "First readings"),
so a paired win-rate gate (`tools/hunt_bench.py`, `tools/evo_bench.py`) can't
tell a change from noise there. Turns are where people beat the bots. The best
human score beats council's on 115 of 151 posted maps, and council's median win
is 7 turns slower than the record. It loses that time in the middle game and the
close ([`council.md`](council.md), "Against the posted maps and the people who
posted them"). The question is whether a bot can win sooner by playing closer to
losing, giving up a little safety for speed. This bench measures that, against
opponents cheap enough to play in thousands. People don't simply take more risk,
though: they lose fewer ships than council and far fewer of the frontier systems
they empty.

## How it measures

- **Cells and line-ups.** Cells are `mode:nodes:ly:players`, by default
  `tools/evo_bench.py`'s six: where people play (7:6:2, 11:6:3) and the
  node × speed corners. An opponent line-up is one seat, which takes every other
  seat, or one seat per other seat joined by `+` (`heuristic+rusherplus` on a
  three-seat cell). A line-up plays only the cells it fits.
- **Every seed in every seat.** Each seed is played once with the bot in each
  seat, the line-up rotated with it (`seatings`), so start position cancels. The
  map is built through `settings.build_state` with `autoplay` on, so no seat is a
  person's and every oracle reads every seat exactly, as in the other benches.
- **Fixed work.** Wall-clock guards are lifted (`ai.set_budget_scale(inf)` in
  every worker), so a game is the same on any machine and a bot's code is the
  whole of its play.
- **One arm, per cell and line-up.**
  - *won* is the share of games won, and *unf* the count that reached
    `MAX_TURNS` (600) unfinished.
  - *p25, med, p75* are the turns to win at those percentiles, with a game not
    won counting as slower than any win. They read `—` once the percentile passes
    the share won. A mean over the wins alone would hide the losses and be swamped
    by the long tail. Counting a game not won as slow lets the median show a
    change that trades safety for speed, which is the trade being tested.
  - *to 3:1, 3:1-9:1, 9:1-win* are medians over the games won. The first is the
    turn the bot first held three quarters of the players' income (neutral
    systems left out; `tools/playstyle.py`'s `shares`), a 3:1 lead over every
    rival together. Then come the turns from 3:1 to 9:1, and from 9:1 to the win.
    A win holds every player's system, so every win passes both marks, and the
    split shows whether a change saves its turns before the lead, pressing it,
    or at the end.
    - *Why not the board's income.* council.md splits people and council at
      50% and 90% of the whole board's income, and a quick win ends with
      neutrals still holding part of the board. On a first run marked that way,
      Council reached 90% in 19 of 100 wins on 7:6:2 against heuristic and 8 of
      100 on 18:18:2, and the wins that missed it were the fast ones (a median 31
      turns against 47). A mark the fastest wins skip reads only the slow ones.
    - *Why 3:1.* A two-thirds mark falls in a duel's first few turns, as soon as
      one side takes a neutral the other hasn't.
  - *lost* is the median ships lost over the games won, the board's tie-break.
  - *low* is the median over the games won of the bot's lowest share of the
    players' income at any turn: how far behind it fell on the way to the win. A
    half in a duel (a third with three seats) means it was never behind. Against
    these opponents losses are too rare to show risk, so a change that rides
    closer to the line shows here first.
- **Two arms, paired** game by game: same map, same seat, same line-up. In one
  run every two bots are paired. With `--base` each bot is paired against its own
  rows in another ledger.
  - *beats* scores each pair 1, 0.5 or 0 as the board would rank the two
    results, so a loss outranks nothing and two games not won are level. It is
    the one number to gate on: it prices a lost game against a faster win the way
    the board does.
  - *Δwon* is the difference in games won.
  - *Δturns* is the geometric mean ratio of turns over the pairs both won, as a
    percentage (negative is faster). It isolates speed from risk, and a ratio
    pools cells whose games run 30 turns and 80 alike.
  - Under the table, the mean gap in turns over the pairs both won is split by
    phase (before 3:1, 3:1 to 9:1, 9:1 to the win), so the split adds up to the
    gap and shows where a change saved its turns.

  Each is averaged per seed and z is taken over seeds. A deterministic bot
  against itself reads exactly 50% and 0 (`tests/test_win_turns.py`). With 18
  groups at the defaults, about one |z| ≥ 2 is expected by chance, so read the
  pooled row first.
- **Seeds.** The default block starts at 7,000,000. It is the screen. Confirm a
  screened change on a fresh block (7,100,000 and up, one block per purpose, as
  in [`hunter.md`](hunter.md), "How it was measured"), since the best of several
  ideas on one seed set flatters the winner.

## Ledgers and revs

Rows append to `results/win_turns/<label>/<bot>.jsonl` (gitignored), one game a
line, and a rerun plays only the games missing. A rerun that only fills gaps
would mix two trees' games silently once the code changed under a ledger, so
every row carries `rev`, a digest of the code that decides a game (`play_rev`).
That code is the simulation's modules and every `models/` file, since a rival is
part of the game and knower borrows other bots as advisers. The digest also
covers `SHAPE`, the layout of a row, so a change to what a row records starts
new ledgers too. Every row also carries `git`, HEAD's short hash with `+` for
changes not committed, as a hint for a reader.

- **The label defaults to the rev**, so a changed tree starts a ledger of its
  own and the same code finds its games again.
- **A named ledger refuses rows from another tree.** Name a new label, or
  delete the ledger.
- **`--base` reads any ledger**, whatever tree played it. That is the point:
  play the base on the unchanged tree, then the change under a new label.

`play_rev` counts `replay.py`, because `replay.reseed` derives each turn's rng.
`tools/bot_replay.py`'s `engine_rev` leaves it out of `_OUTCOME_MODULES`, so a
change to `reseed` alone would not mark the bot column stale.

## First readings (2026-10-19)

Rev `af6119e0` (main at `81f561b`), the default cells and line-ups, seeds
7,000,000 + 50: 5,850 games and 3.2 CPU hours. Council's games took a median
2.27 s (7,768 CPU s in all), Hunt's 0.93 s (3,813 s) and marshal's 0.03 s (86 s),
about 12 minutes on 20 workers. Run twice, the bench read the same game for game.

**Median turns to win, and the share won** (a game not won counts as slower
than any win, so a median past the share won is —):

| Cell | Against | Council | Hunt | marshal |
| --- | --- | --- | --- | --- |
| 7:6:2 | heuristic | 34 · 100% | 48 · 98% | 75 · 86% |
| | rusherplus | 37 · 100% | 48.5 · 98% | 74.5 · 90% |
| | thinker | 44 · 100% | 75 · 96% | 129.5 · 70% |
| 11:6:3 | heuristic | 53 · 98.7% | 67 · 92.7% | 142.5 · 70.7% |
| | rusherplus | 53.5 · 100% | 66.5 · 94% | 84.5 · 90% |
| | thinker | 64 · 99.3% | 94 · 87.3% | — · 49.3% |
| 13:3:2 | heuristic | 83 · 100% | 95.5 · 100% | 128.5 · 86% |
| | rusherplus | 86 · 100% | 96 · 100% | 111 · 100% |
| | thinker | 106.5 · 99% | 141 · 93% | 245 · 75% |
| 18:6:2 | heuristic | 47 · 100% | 50 · 100% | 74 · 99% |
| | rusherplus | 52 · 100% | 54.5 · 100% | 75 · 100% |
| | thinker | 55 · 99% | 62.5 · 98% | 107 · 88% |
| 24:3:2 | heuristic | 100 · 100% | 110 · 100% | 129.5 · 96% |
| | rusherplus | 101 · 100% | 111 · 100% | 123 · 100% |
| | thinker | 123.5 · 100% | 150 · 99% | 205 · 86% |
| 18:18:2 | heuristic | 28 · 100% | 32 · 100% | 45 · 97% |
| | rusherplus | 29 · 100% | 32 · 100% | 44 · 98% |
| | thinker | 29 · 100% | 32 · 99% | 43.5 · 95% |

**Paired, pooled over the 18 groups**, with the mean gap over the pairs both won
split by phase (before 3:1 / 3:1 to 9:1 / 9:1 to the win):

| | beats | z | Δwon | Δturns | z | Where the turns go |
| --- | --- | --- | --- | --- | --- | --- |
| Council against Hunt | 76.9% | +28.0 | +2.3 pt | −18.7% | −25.8 | −13.1 / −2.0 / −2.1 = −17.2 |
| Council against marshal | 96.1% | +102.3 | +12.2 pt | −40.7% | −42.4 | −31.5 / −10.8 / −6.0 = −48.4 |
| Hunt against marshal | 84.2% | +41.5 | +9.9 pt | −28.5% | −30.0 | −20.4 / −8.9 / −4.5 = −33.8 |

What they say:

- **Council is saturated.** It won 1,945 of 1,950 games (2 unfinished, 3
  lost), at least 98.7% in every group, so whatever is left to measure in it here
  is turns. It is the fastest of the three in every group:
  it beats Hunt in all 18, from 63% (18:6:2 against heuristic) to 89% (11:6:3
  against thinker).
- **Hunt is nearly saturated**, except with three seats and against thinker
  (87.3% on 11:6:3 against thinker, 92.7% against two heuristics, 93% on 13:3:2
  against thinker).
- **marshal is not saturated, and what it fails at is finishing.** It wins
  49-88% against thinker and 70.7% against two heuristics. Of its 269 games not
  won, 189 were still unfinished at turn 600: 44 of 150 on 11:6:3 against
  thinker, 31 of 150 against two heuristics, and 26 of 100 on 7:6:2 against
  thinker. At 0.03 s a game, a marshal variant runs the whole default schedule in
  under a minute.
- **The turns, and the gap between the bots, come before the lead.** Every bot
  spends about two thirds of a win before its 3:1 lead (Council 67%, Hunt 69%,
  marshal 66%), a fifth from 3:1 to 9:1 and a tenth after, and most of the gap
  lies there too: 76% of Council's 17 turns over Hunt and 65% of its 48 over
  marshal. Council's lead over Hunt depends on the board: 22-25 turns a game on
  7:6:2, 11:6:3 and 13:3:2 and 19 on 24:3:2, but 4-5 on 18:6:2 and 18:18:2. The
  span before 3:1 takes in the land grab and the fight for the lead, so it is not
  council.md's opening.
- **The fastest bot doesn't ride closer to the line.** *low* is within five
  points across the three bots in every group, and Council's is the highest or
  within three points of it. Against heuristic no bot is ever behind in a duel
  (50%). Against rusherplus and thinker every bot falls behind at some point
  (36-48% in duels).
- **thinker is the hardest of the three opponents for every bot.**

### Against marshal

Most posted maps carry marshal, so Council and Hunt were also run against it
(`--vs marshal`), on the same cells and seeds. That was 1,300 games and 10,500
CPU s, Council a median 4.68 s a game and Hunt 4.16 s.

| Cell | Council | Hunt |
| --- | --- | --- |
| 7:6:2 | 61.5 · 99% | 161 · 70% |
| 11:6:3 | 83 · 98% | 292 · 57.3% |
| 13:3:2 | 137 · 97% | 309 · 66% |
| 18:6:2 | 68.5 · 99% | 110 · 83% |
| 24:3:2 | 157 · 98% | 246.5 · 73% |
| 18:18:2 | 27 · 100% | 36 · 81% |

- **Council is saturated against marshal too** (97-100%), so marshal is a fourth
  line-up for Council's turns, and the one nearest the posted maps. Its median
  win runs 25-80% longer than against the weak bots, except on 18:18:2, where it
  beats marshal as fast as it beats heuristic.
- **Hunt is not** (57-83%, 55 of its 650 games unfinished), so against marshal
  Hunt is still a win-rate question.
- **Paired**, Council beats Hunt 90.0% (z +32.8). On the 459 games both won Hunt
  is 62 turns slower: 33.2 before 3:1, 12.5 from 3:1 to 9:1 and 16.6 after 9:1.
  About a quarter of the gap is Hunt failing to finish marshal off.

## Reading it

- **These are bots, not the posted maps.** In the board's snapshot of
  2026-10-14 (`results/par-2026-10-14/board_maps.json`, local), 25 of 140 maps
  face marshal alone, and 31 face only heuristic, rusherplus, thinker or
  claudebot. `--vs marshal` measures the first kind (see "Against marshal"). A
  change that reads faster here still has to be checked on the posted maps,
  where council.md's fresh replay is the before.
- **Read *beats* first.** *Δturns* covers only the pairs both won, so a change
  that wins more of the hard maps, which run long, can read slower on it while
  beating more often.
- **One |z| of 2 or more in 18 groups is chance.** Read the pooled row, and
  confirm on a fresh seed block.
- **A rerun on the same tree is the same run.** The games are deterministic at
  fixed work, so z measures the spread over maps, and only more seeds tighten it.
- **The phase split is in raw turns, pooled**, so the long cells (13:3:2,
  24:3:2) weigh most in it.
- **What it costs.** On this machine (24 cores, 20 workers), Council's full
  default schedule is 7,800 CPU s, about 7 minutes, and `--quick` (3 cells, 25
  seeds) is 1,070 CPU s, about a minute. Hunt's are 3,800 and 680 CPU s, and
  marshal's 86 and 20.

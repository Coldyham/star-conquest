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

## marshal: sparing the target and feeding on every board (2026-10-19)

The first reading left marshal as the one unsaturated bot, with 189 of 1,950
games unfinished at turn 600. Two changes to `models/marshal.py` take that to
53, win 6.2 points more games and win 15.8% sooner on the ones both arms won.
They also gain against stock marshal and knower and stay level against actuary.
Neither costs anything per turn.

### What the stalemates were

Each of the 189 games was replayed to turn 300, and marshal's cheapest strike on
a rival was split into its price terms and what its sources could spend:

    blocked by                                   stalls
    the guard alone (enough ships next door,       99
      less the guard, is short of the price)
    the guard and the relief terms together        30
    the relief terms alone                          4
    short of ships even with neither               50
    nothing (a strike was affordable)               6

The guard is `FRONTIER_GUARD` (0.55) of the largest rival stack next door, and
that stack is usually the target itself. A source holds 0.55 of a stack back from
the strike on that same stack, and two sources bordering one stack each hold 0.55
of all of it. The deadlock doesn't depend on scale. Ships bank in proportion to
income, so the ratio of the two stacks settles near the income ratio. That has to
pass about 1.55 (a 61% income share, one source) before the guard leaves enough
to strike, and waiting never gets there. In one 18:6:2 game against thinker,
marshal held 2.1 times thinker's ships for 300 turns, its two fronts guarding
522 ships against one 474-ship stack. That is the mechanism
[`marshal-joint.md`](marshal-joint.md), "The decision order is what couples
them", describes for marshal against itself.

The 50 stalls short of ships even without guard or relief are mostly marshal
behind. 47 of the 63 left after sparing the target are three-seat games where
marshal holds 10-40% of the income and neither rival finishes it.

### The two changes

- **`SPARE_TARGET` (Phase 3).** A priced strike on a rival may spend the part of
  a source's guard that only the target called for. The guard against every
  other adjacent stack stays, and a source under attack (Phase 1's
  `threatened_set`) keeps all of it. `spendable` adds back the guard less
  `FRONTIER_GUARD` of the largest *other* rival stack (`_max_adjacent_enemy`'s
  `skip`). The pour floor is untouched. Against the guard set to 0, which ends
  even more stalls (189 to 27) but loses 8.2 points to actuary (z -5.1), this
  keeps actuary level. The guard that matters against actuary is the one against
  a stack you are not striking.
- **`FEED_MAX_TURNS` 0 (Phase 4).** `FEED` now runs on every board. Rear
  surplus goes to fronts short of a rival stack rather than to the nearest front,
  which on a slow board is often one facing a neutral. "Feeding on every board"
  read level with the 3-turn cap against the roster in 2026-10
  ([`marshal-joint.md`](marshal-joint.md), "Levels around the corner"), so the cap
  had been a coin-flip choice. Against the weak bots it is not.

### Measurements

Each arm is a copy of `models/marshal.py` with constants patched in each worker,
played on this bench's schedule and paired against stock marshal game by game.
The roster arm plays the same cells against stock marshal, knower (Predict) and
actuary (Field) in every seat. Points score a win 1, a timeout 0.5 and a loss 0,
averaged per seed with z over seeds.

Screen (seeds 7,000,000 + 50), against stock:

    arm                         beats      z    Δwon   unf     Δturns   roster: marshal / knower / actuary
    SPARE_TARGET                66.7%  +16.7  +6.1pt    63    -10.9%   +9.8 / +2.6 / +0.5
    FRONTIER_GUARD 0            66.8%  +15.4  +4.5pt    27    -12.9%   +1.5 / -2.2 / -8.2 (z -5.1)
    LEAD_GUARD 1.5 (see below)  65.0%  +14.2  +3.7pt   108    -11.0%   worse against knower, actuary
    both changes                71.1%  +21.4  +6.2pt    53    -15.8%   +11.2 / +4.1 / +0.8

On top of `SPARE_TARGET`, `FEED_MAX_TURNS` 0 alone: 57.7% (z +8.1), -5.3% turns,
roster +1.4 / +1.5 / +0.3.

Confirmation, fresh seeds 7,100,000 + 50, the same comparison:

    weak bots   beats 72.3% (z +22.2), Δwon +5.8pt (z +8.0), Δturns -15.9% (z -18.1),
                unfinished 180 -> 52; per phase -13.0 / -4.7 / -1.1 turns
    roster      marshal +8.6 (z +5.7): won 318 vs 228, lost 251 vs 262, unfinished 81 vs 160
                knower  +5.9 (z +3.5): won 423 vs 361, lost 188 vs 196, unfinished 39 vs 93
                actuary  0.0 (z 0.0):  won 187 vs 175, lost 447 vs 429, unfinished 16 vs 46

In the roster confirmation the opponents' marshal (and knower's marshal adviser)
was patched back to stock. With the new marshal as the opponent instead, the
same pairs read +8.4 / +5.9 / 0.0.

The bench on the new tree (rev `d589c682`, `--base af6119e0`) reproduces the
screen game for game. Median turns to win · share won · unfinished:

| Cell | Against | before | after | Council |
| --- | --- | --- | --- | --- |
| 7:6:2 | heuristic | 75 · 86% · 14 | 52 · 94% · 2 | 34 · 100% |
| | rusherplus | 74.5 · 90% · 2 | 56 · 94% · 0 | 37 · 100% |
| | thinker | 129.5 · 70% · 26 | 76 · 89% · 7 | 44 · 100% |
| 11:6:3 | heuristic | 142.5 · 71% · 31 | 87.5 · 81% · 13 | 53 · 99% |
| | rusherplus | 84.5 · 90% · 5 | 74 · 90% · 1 | 53.5 · 100% |
| | thinker | — · 49% · 44 | 226.5 · 60% · 17 | 64 · 99% |
| 13:3:2 | heuristic | 128.5 · 86% · 14 | 107.5 · 96% · 3 | 83 · 100% |
| | rusherplus | 111 · 100% · 0 | 106 · 100% · 0 | 86 · 100% |
| | thinker | 245 · 75% · 24 | 168 · 91% · 8 | 106.5 · 99% |
| 18:6:2 | heuristic | 74 · 99% · 1 | 65 · 100% · 0 | 47 · 100% |
| | rusherplus | 75 · 100% · 0 | 69 · 100% · 0 | 52 · 100% |
| | thinker | 107 · 88% · 7 | 89 · 97% · 1 | 55 · 99% |
| 24:3:2 | heuristic | 129.5 · 96% · 4 | 122 · 100% · 0 | 100 · 100% |
| | rusherplus | 123 · 100% · 0 | 117.5 · 100% · 0 | 101 · 100% |
| | thinker | 205 · 86% · 12 | 166.5 · 100% · 0 | 123.5 · 100% |
| 18:18:2 | heuristic | 45 · 97% · 3 | 43 · 99% · 1 | 28 · 100% |
| | rusherplus | 44 · 98% · 0 | 43 · 100% · 0 | 29 · 100% |
| | thinker | 43.5 · 95% · 2 | 40 · 97% · 0 | 29 · 100% |

Cost: 0.27 ms a turn before and 0.28 after (all seats), and the default schedule
fell from 85 to 61 CPU s because games are shorter. Council's lead over marshal
fell from 96.1% to 90.6% on beats, and from 48 to 31 turns a game on the pairs
both won (18.5 before 3:1, 6.7 from 3:1 to 9:1, 5.8 after).

### What else was tried (all deleted)

Each was screened on the same seeds against both weak bots and roster, on top of
the two changes unless stated:

- **A guard scaled down by a lead in ships** (`LEAD_GUARD`, gone at 1.5-3 times
  the rival's ships), instead of sparing the target: 63-65% on beats, but worse
  against knower and actuary, which a lead in ships doesn't stop.
- **No guard or pour floor past an income share** (0.75, 0.9): slightly slower
  (+0.8% turns at 0.75). Even far ahead, the guard is what holds new captures.
- **Releasing part of the guard against the target** (half, three quarters):
  worse on beats (38%, 43%) and against marshal and knower; +3.4 against actuary
  at half, mostly by turning its losses into timeouts.
- **The pour floor sparing the target too**: no gain over the strike alone, and
  worse against marshal and knower.
- **Relief from a rival system next to one of ours at least its size**, counted
  at 0.5 or 0: null on the weak bots, -5.1 against actuary at 0.
- **A front facing only neutrals passing its leftover to a short rival front**:
  56% on beats but fewer games won (-0.7pt), and -2.6 against marshal.
- **No neutral targets past 75% or 90% of the income**: beats moved (55-57%) but
  turns didn't. The gain was the ships-lost tie-break, and it can strand marshal
  when the rival's last system is behind a neutral.
- **Constants on top of `SPARE_TARGET`**: the neutral price at the worst-roll
  edge (`NEUTRAL_MARGIN` 1.0 with pad 0.001 or 0.03), `RELIEF_AWARE` 0.35,
  `RELIEF_IN_FLIGHT` 0.25, `FRONTIER_GUARD` 0.45, `POUR_GUARD` 0.55, `FEED` off,
  `HOLD_FOLLOW` 0.5. Null or worse everywhere. The neutral cushion is worth its
  turns even against weak bots, as in [`marshal-flow.md`](marshal-flow.md).

### What is left

- **The land grab** is a few turns behind Council's (4-7 at 18:18:2, about 2 at
  18:6:2). Phase 4 pulls toward the richest neutral, not the one nearest to
  affordable, and fronts facing only neutrals keep their leftover. So two fronts
  can mass separately when pooling would take a cheap neutral turns sooner. A
  relay rule (a neutral-only front hands its leftover to a neighbour it would
  complete a capture for) is the obvious next arm. It is not built.
- **After the lead**, Council and marshal hold their ships in the same places
  (about a fifth on rival fronts, a third or more in flight between their own
  systems, 18:6:2 and 13:3:2 against thinker), yet marshal takes twice the turns
  from 3:1 to the win. The difference is which strikes are made, and Council's
  edge there is knowing what the rival will do. A rule bot doesn't get that.
- **The three-seat stalls** where marshal is the weakest seat.
- **The second round** (below) tried the relay and 29 other arms. None ships.
- **Not re-run:** the full ladder in [`marshal.md`](marshal.md), "Where marshal
  stands", the advantage and jitter sliders, free-for-alls past three seats, and
  the posted maps.

## marshal: a second round, nothing shipped (2026-10-19)

On the tree after `SPARE_TARGET` (stock marshal at `827852a`), 30 arms were
screened on the default schedule against stock, game by game, at 23 s a run on 4
cores. Each was a constant patched in every worker. One reached a fresh block,
and none ships. The arms and the diagnostics behind them are below, so the next
round can start from them.

### Where marshal still loses

- **Three seats.** 72 of the 97 games stock marshal loses, and 31 of its 52
  unfinished, are on 11:6:3. In the 43 it loses against two thinkers it is behind
  by turn 5 (31% of the income against the leading thinker's 41%) and falls
  steadily, while the leader eats both other seats. In the games it wins it
  leads by turn 10.
- **Council is ahead from turn 8** in the same seats (11:6:3 against thinker, 20
  seeds): 2.28 systems against 2.12 at turn 8 and 4.70 against 3.63 at turn 30,
  with more ships at every mark. Over the first 30 turns Council takes 1.73
  rival systems a game with no fight against marshal's 0.85, half of them
  emptied that same turn by the owner's own launch. It also attacks rivals more
  (1.05 fights to 0.62) and sends more ships per fight (7.1 to 3.9). In one
  game traced turn by turn (seed 7,000,019, seat 1), marshal raced a thinker to a
  neutral and landed beside its fleet. Council struck the system that fleet had
  just left. A rule bot sees the fleet on the lane, but not the launch on the
  turn it is made.
- **Against heuristic, rival fights fail.** On 7:6:2 over 40 turns marshal wins
  66% of the rival fights it lands (1.38 of 2.10 a game) and Council 84% (2.23 of
  2.67). Council sends 1.29 times the garrison it meets and marshal 1.15.
  Heuristic stands where thinker flees, so the missing jitter cushion
  (`_enemy_margin`) costs marshal against heuristic and pays against thinker. See
  the pad arms below.
- **The two-seat stalls are mostly level.** Of the 21 unfinished duels, 19 never
  reach a 3:1 lead. One traced (7:6:2 against thinker, seed 7,000,012) sits at
  257 ships against 333 across a single 8-turn lane at turn 300, and neither
  side can cross.

### The arms

Pooled over the 18 groups, seeds 7,000,000 + 50, against stock:

    arm                                         beats      z    Δwon   Δturns
    relay: a neutral-only front sends what a
      neighbour lacks for an unstruck neutral   50.5%   +0.6  -0.2pt   -0.1%
    ...sending its whole leftover               52.6%   +3.0  -0.1pt   -0.8%
    ...to rival targets too                     52.7%   +3.0  -0.1pt   -1.0%
    ...even when its own target is sooner       52.1%   +2.5  -0.3pt   -0.5%
    FEED 20 (rear surplus to rival fronts only) 53.0%   +3.0  +0.5pt   -0.3%
    FEED 5                                      51.6%   +1.8  +0.1pt   -0.1%
    FEED 2 / 1.5 / 1                            47.0 / 43.1 / 39.8%, +1.4 / +3.9 / +6.6% turns
    relay (whole leftover) + FEED 20            54.1%   +3.9  +0.3pt   -1.3%
    Phase 4 routed by travel turns, not hops    49.3%   -0.8  -0.2pt   +0.1%
    rival attack margin x1.1                    50.5%   +0.5  -0.7pt   +0.7%
    ...x1.2                                     45.9%   -3.8  -1.8pt   +4.1%
    ...x1.1-1.3 on 1-turn strikes only   49.9 / 49.7 / 49.1%                null to worse
    ...x1.2 on a target with no friendly
      neighbour to flee to                      49.8%   -0.2  +0.2pt   +1.3%
    no relief pricing on a target of 0 / <3     49.4 / 50.4%                    null
    no relief pricing at all                    48.1%   -1.8  -3.8pt   +2.1%
    a neutral priced through a rival bloc
      landing with us (any field / 3+ seats)    50.5 / 50.0%                    null
    targets by richness per ship (^1, ^0.5)     50.4 / 50.3%                    null
    NEUTRAL_MARGIN 1.2 / 1.4                    48.2 / 49.9%                    null to worse
    FRONTIER_GUARD 0.45 / POUR_GUARD 0.7        50.2 / 51.7%, the pour -0.4pt won, +0.6% turns
    WEDGE_MIN_PLAYERS 3, BEYOND_DECAY 0 / 0.7,
      REMNANT_MAX_PLAYERS 3, OVERWHELM 1.5,
      RISK_PARITY 0.9                           49.9-50.9%                      null
    FAST_GUARD_WEIGHT 1                         48.5%   -3.3  -0.3pt   +0.1%

- **The rival margin splits by opponent.** At x1.1 it gains 58-68% on beats
  against heuristic on four boards and loses 38-44% against thinker on four.
  That is "Garrisons run away" in [`marshal-pricing.md`](marshal-pricing.md)
  seen from the other side: heuristic doesn't run. Telling the two apart needs a
  model of the rival, which marshal's no-state contract rules out. Padding only
  the strikes a garrison cannot see coming (a 1-turn lane), or a garrison with
  nowhere of its own to flee to, gains nothing.
- **More feeding is better, up to a plateau.** Below 3, `FEED` loses steeply
  (39.8% at 1): a rival front that isn't short gives the rear's surplus to a
  neutral front, and that is slower. Above 3 it creeps up (53.0% at 20).

**Fresh seeds** (7,200,000 + 50; stock replayed there as `stock72`):

    FEED 20                    55.0% (z +5.1)  -0.3pt  -1.5% turns
    relay (whole leftover)     50.5% (z +0.6)  -0.4pt  -0.3%
    relay + FEED 20            52.4% (z +2.2)  -0.3pt  -1.1%

The relay doesn't survive the fresh block. `FEED` 20 does, so it was played
against the roster on the same block: the same cells against stock marshal
(a copy of `models/marshal.py` at `827852a`), knower at Predict and actuary at
Field, 1,950 games a side, points a win 1, a timeout 0.5 and a loss 0.

    against stock marshal   -1.1 (z -1.1)   won 288 vs 298
    against knower          +1.0 (z +1.0)   won 428 vs 424
    against actuary         -1.5 (z -1.5)   won 183 vs 194

Level within noise, but leaning worse on two of three, for 1.5% of turns
against the weak bots. Not shipped.

### What this leaves

- **Constants are at their optimum here.** Every constant moved either way reads
  null or worse, including the four already re-tuned in the first round.
- **The gaps that are left need knowledge of the rival.** Council's lead before
  3:1 comes from strikes timed to a rival's launch and from not racing where a
  rival is going, and the x1.1 margin shows that the right price differs by
  opponent. A bot that learns its rivals (actuary at Learning or Hunt) can reach
  those. A rule bot with no memory can't.
- **Still not built:** a rule that counter-punches a rival system the turn after
  its garrison leaves. The fleet is on the lane and visible, so it is
  non-oracle. It goes through the target sort, though, and the sort has measured
  null every time it was changed.
- **`FEED` 20 is the one candidate standing**: worth a re-test if the roster
  moves.

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

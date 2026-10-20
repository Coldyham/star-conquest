# knower design notes

`models/knower.py` is the oracle bot. It predicts every rival by running that
rival's real `decide`, then searches over candidate openings. This file covers
why the oracle is possible, what it buys, the depth search and its horizon
(Oracle: Off / Predict / Search), the borrowed candidates, the cost of a
decide, which drives the slow-setup warning, and what Search does with a
garrison a borrowed opening leaves to die. Roster-wide rules (margins,
measurement method, leaderboard replays at `replay_aux`) are in
[`bots.md`](bots.md). marshal's results against knower are in
[`marshal.md`](marshal.md), "Where marshal stands". Index:
[`../README.md`](../README.md).

## `models/knower.py` and simultaneous resolution

Because turns resolve simultaneously — `_collect_orders` hands every seat the
same unmutated state and applies nothing until all have decided — an
opponent's orders can't depend on yours. That means a bot can clone the
board, call each rival's own registered `decide`, and know their moves before
the engine asks for them: there's no fixed point to solve, one forward pass
of their real code *is* the answer. knower folds those predictions into a
"post-launch board" (predicted orders applied via `engine.apply_order` but
not advanced) and runs thinker's phases against it.

The rng is no obstacle either. Every bot seat decides from the same start of
the turn's `state.rng` (`engine.decide_seat`), so each rival is predicted from
its own copy of it and comes out bit-exact, whichever side of knower it sits.
Until 2026-10-08 seats drew in series: the seats after knower were exact, but the
seats before it had already drawn from a position it could not recover, so they
were predicted from a private rng (right on 99.6% of turns, labelled `"likely"`).
That is gone, and with it the "chain" a raising seat used to break. Since the
same change, combat dice are keyed per fight from the board (`engine._Dice`),
so a Search rollout on a clone meets the dice the real turn will roll. That is
deliberate (`core.md`, "Two streams"): it sharpens knower's choices and moves
nothing for any other seat.

Forked from `models/thinker.py` at commit f94ff20; the four phases and the
helpers below `_richness` are thinker's, changed only where the oracle changes
them.

## What the oracle buys, and where

The largest gain is **a turn of warning thinker structurally cannot have.**
thinker only sees fleets already on a lane, so a strike along an L-turn lane
reaches it with L-1 turns to spare, and a 1-turn strike is never visible at all,
because `end_turn` launches, advances and resolves it in one call. How much that
costs depends on the ship-speed slider:

- At the default 6 ly/turn no lane is shorter than 2 turns, so nothing is wholly
  invisible, but a blow landing next turn still cannot be answered: the
  reinforcement filter `travel_turns(sid, n) <= t_bind` has no lane short enough
  to find. Over 12 thinker-vs-thinker games at 24 nodes, 43.7% of the threats
  thinker detects sit at that horizon and *none* of them are reinforceable.
- At 18 ly/turn, 72.5% of lanes are 1 turn; at 30, all of them are. There thinker
  cannot see most attacks until they have landed.

Measured at depth 1 against thinker, ladder, both seatings, 24-node random maps
(2026-08, when knower was new):

    default 6 ly/turn, 200 games   73% (119-45)
    18 ly/turn, 100 games          91% (88-9)     <- mostly 1-turn lanes

The gap between those rows is the thesis of the bot: the faster ships are, the
more of the game thinker cannot see, and the oracle scales with it. The full
roster ladder of the day read knower 143 > thinker 117 > claudebot 72 >
heuristic 33 > rusherplus 10. That predates both marshal and the depth search,
so treat it as history rather than a current ranking.

Depth 0 is thinker-*strength*, not thinker: `RESERVE_FLOOR` is 0 against
thinker's 1, `_richness` peeks a hop further (`BEYOND_DECAY`), and tie-breaks
are deterministic where thinker's draw from `state.rng`. It measures stronger
than thinker (73-77% on the current code; an earlier, unrecorded run read
80%-20%), so the two are not interchangeable. All three settings against both
neighbours are under the next heading.

## Each Oracle setting against thinker and marshal

knower at Off / Predict / Search (`aux` 0/1/2) head to head with thinker and
marshal, two seats, both seatings per seed, everything else default
(2026-10, at `cf3408b`). Each knower row is one `--aux` ladder, which also
plays thinker against marshal; the three cells, at `--aux knower=0`, `1` and `2`:

    uv run python -m tests.sim --ladder --ai knower thinker marshal --aux knower=2 --trials 100
    uv run python -m tests.sim --ladder --ai knower thinker marshal --aux knower=2 --trials 50 --mode symmetric
    uv run python -m tests.sim --ladder --ai knower thinker marshal --aux knower=2 --trials 50 --nodes 24 --max-turns 1500  # with config.SHIP_LY_PER_TURN = 3

The ladder's per-pair grid shows rates only; the counts below came from a
driver that stamped seats the same way and logged each game. Seeds 1-30 of the
first cell reproduce the roster ladder's knower cells (Predict) in
[`marshal.md`](marshal.md) to the game, 51-4 and 24-30. Win rate is knower's,
over finished games; ± is a 95% interval:

    random, 18 nodes, 6 ly/turn, seeds 1-100 (200 games a cell)
                  vs thinker              vs marshal            s/game
      Off         73% ±7 (125-47)  28 TO  14% ±5 (25-158)  17 TO   0.07
      Predict     85% ±5 (156-28)  16 TO  38% ±7 (72-119)   9 TO   0.15
      Search      99% ±2 (196-3)    1 TO  81% ±5 (162-37)   1 TO   3.5

    symmetric (hub), 18 nodes, 6 ly/turn, seeds 1-50 (100 games a cell)
      Off        100% (26-0)       74 TO   3% (1-34)       65 TO   0.13
      Predict    100% (59-0)       41 TO  30% ±15 (11-26)  63 TO   0.31
      Search     100% (100-0)       0 TO  88% ±7 (74-10)   16 TO   4.0

    random, 24 nodes, 3 ly/turn, --max-turns 1500, seeds 1-50 (100 a cell)
      Off         77% ±9 (61-18)   21 TO   6% ±5 (5-76)    19 TO   0.27
      Predict     84% ±8 (67-13)   20 TO  22% ±9 (19-69)   12 TO   0.68
      Search     100% (100-0)       0 TO  62% ±10 (61-38)   1 TO  14

What it says:

- **Each setting is a full tier.** On the random maps Off beats thinker about
  three games in four and loses to marshal about six in seven. Predict loses
  to marshal in every cell. Search is the only setting that beats marshal, and
  it does so in all three cells.
- **The roster ladder's 93% for knower over thinker was a lucky 30 seeds.**
  Over 100 seeds Predict reads 85%, and its 44% against marshal reads 38%. The
  ladder's knower is Predict (default `AiParams`), so the ladder ranks the
  default setting, not the bot at its best; `bot_replay` already runs it at
  Search (`replay_aux`).
- **Search ends games.** Off and Predict time out a lot against thinker on
  the symmetric map (74 and 41 of 100). Search times out once against thinker
  in all 400 of its games across the three cells. On the symmetric map the
  timeout counts are half the result, since the win rates there rest on a few
  dozen finished games.
- **Cost.** Search costs 13-23x Predict per game (whole-game seconds, four
  games in parallel on a 4-core box, so read the ratio, not the absolute).
  [Cost per decide](#cost-per-decide-and-where-the-search-guard-trips) is the
  per-turn measurement.

## Built, measured, removed

- **Pricing three-way pile-ups.** The oracle knows who else lands on a node this
  turn, so knower can fold the pile-up with the jitter pinned against it and
  demand enough mass to survive it. 71% vs 72% head-to-head over 200 games, and
  33 vs 35 wins in a 3-player free-for-all: a wash, if anything worse. It makes
  knower skip attacks it cannot overpay for, and this game rewards the leaner
  strike (the same finding that set thinker's margins).
- **Striking perishable targets first.** A vacated garrison refills, so ordering
  targets by how much of theirs is leaving looks obviously right. It changes
  nothing: 118-45 vs 119-45 over 200 games, 35 vs 36 in the free-for-all.
  Pricing the target correctly is what wins; the order it happens in does not.

Don't re-add either without a measurement.

## The depth search: branch the root, play the rest on

Every candidate opening is rolled out on the root board, then each line is played
on by the blind default plan for the rest of the depth (`_advance`), and the lines
are compared where they end. One rollout per candidate per ply, so cost is linear
in the candidate count. Until 2026-09 the search branched every line across every
candidate at *every* ply and cut back per opening (`_prune`); that version, and why
it went, is below.

**Why deeper branching did nothing.** The per-ply cut ranked children on a one-ply
`_evaluate`, and `_material` counts ships in transit at full value, so a launch that
will fail scores like one that will land until the turn it arrives. Children of one
node therefore mostly tied, and ties go to the default. Instrumented at depth 12
against thinker on 24-node maps, one game per cell, the default child won 97-100%
of the nodes below the root. Rerunning every search on the same positions with the
deeper plies replaced by the default alone changed the root pick in:

    1 ly/turn   0 of 250 decisions (0%)
    3 ly/turn   18 of 203 (8.9%)
    6 ly/turn   8 of 104 (7.7%), 4 seats 10 of 82 (12.2%)
    18 ly/turn  12 of 40 (30%)      <- 1-2 turn lanes land inside one ply

while that branching was ~75% of the search's cost. Picking *which* plies to branch
cannot fix a myopic cut, and the cheap signals do not find the plies that mattered
anyway: "a system changed hands last turn" fired at 11-44% of nodes but caught only
33-69% of those where a non-default child won (1% of the score gap at 1 ly), and
"a fleet arrives" / "an enemy launched" fired at 73-98%.

**Head-to-head, depth 12, guards lifted, 2 seats, 24-node random maps, both
seatings of seeds 1-20 (1 ly: seeds 1-10, 1-20 for E vs A, at a 1200-turn cap — 600
for B, C and D against A — with a capped game scored to whoever leads on
`_evaluate`).** A is the old search; B branches the root only with
the old two borrowed candidates; E is B plus marshal and claudebot, i.e. what ships:

    E vs A     18 ly 72.5% (n=40)   6 ly 53.8% (n=80)   1 ly 60.0% (n=40)
               pooled 60%, z ~ 2.6
    B vs A     18 ly 52.5%          6 ly 50.0%          1 ly 55.6%   (n=40/40/20)

    vs marshal, paired        A      B      E
               18 ly         62%    65%    57%    E-A  -5.0 +/- 9.4
                6 ly         50%    45%    75%    E-A +25.0 +/- 6.0 (n=80)
                1 ly         15%    20%    45%    E-A +30.0 +/- 10.5 (n=20)

So root-only branching alone (B) is even with A at a quarter of the cost, and the
headroom it frees, spent on more *openings*, is what beat A. Spending it on more
*turns* instead did not: rolling every line on with the default until the fleets it
launched had landed (all lines to the same turn, capped at 30 more) lost to A
head-to-head whether stacked on B (40%, 40%, 47% at 18/6/1 ly) or on A itself
(52.5%, 40%, 40%), although it did well against marshal. The rollout models a
non-oracle rival with its real `decide` but an oracle or a human with `_blind`, so a
longer rollout is faithful against the former and compounds a fiction against the
latter — the same reason depth past 5 was noise (below).

**Most of E's marshal result is marshal.** F, E without marshal as a candidate,
scores 45% against marshal at 6 ly — F-E -32.5 +/- 9.0, F-A -7.5 +/- 8.3 — and 30%
at 1 ly (F-E -15.0 +/- 8.2). Borrowing a predictable rival's own planner is worth a
lot *against that rival*: its plan is priced by a rollout that simulates the rival
exactly. Against A (an oracle, so not borrowable) the added candidates are worth
roughly the 8 points between B and E. That is why the ladder moves only at the top:

    6 ly/turn, 40 games a pair (80 for A/E/marshal), row's score vs column
                A     E   marshal claudebot thinker rusherplus heuristic  mean
    E          54%    -     75%     100%     100%     100%      100%      88%
    A           -    46%    50%     100%     100%     100%      100%      83%
    marshal    50%   25%     -      100%      98%     100%      100%      79%
    thinker     0%    0%     2%      90%       -      100%       95%      48%
    claudebot   0%    0%     0%       -       10%      92%       85%      31%
    heuristic   0%    0%     0%      15%       5%      85%        -       18%
    rusherplus  0%    0%     0%       8%       0%       -        15%       4%

Every other seat is already 100% for both, so the ranking below knower is unchanged.
At 40-80 games a cell, treat anything within ~10 points as unresolved.

## Borrowed candidates (`EXTERNAL_CANDIDATES`)

Four now: rusherplus, heuristic, marshal, claudebot (see the search above for what
the last two bought). How often each wins E's search, over every decision of the
games above (the land-grab included, so the default share is inflated — see below):

    6 ly    default 54%  rusherplus 12%  marshal 11%  timid 9%  heuristic 8%  claudebot 5%
    18 ly   default 80%  timid 6%  marshal 5%  rusherplus 5%  heuristic 3%  claudebot 2%
    1 ly    default 85%  rusherplus 6%  heuristic 4%  marshal 3%  claudebot 1%  timid 1%

Neither marshal nor claudebot reads a clock, and `_external_plan` gives each a
private clone and rng (claudebot tie-breaks through `state.rng`), so both keep the
search iteration-bounded. Everything below was measured on the old
branch-every-ply search with the first two candidates only.

The single largest measured win in the search. At depth 12 against thinker:

    with rusherplus + heuristic     97.5%  (390-10, n=400)   0 timeouts
    postures only                   89.7%  (96-11,  n=107)  13 timeouts

Without them extra depth stops paying entirely: postures-only measures 92.0% at
depth 8 and 89.7% at depth 12, no better than depth 1. Rolling "knower, more or
less aggressive" forward a dozen turns only compounds a fiction; depth converts
into wins because there is something structurally different in the tree to
find.

How often each candidate wins the search, over 1128 **contested** decisions (we
hold a system adjacent to a live rival):

    default 54.8%   rusherplus 16.9%   heuristic 14.9%   timid 13.4%

Measure this on contested positions only. During the land-grab nothing is in
contact, every candidate rolls out to the same material and the tie-break
returns the default by construction; the same 1128 decisions read 85% default
with the opening phase folded in. Nearly a third of real decisions are moves
knower's own four phases cannot express.

That is also why `SEARCH_WIDTH` is 2. Over 1355 contested decisions with the
full posture list in play:

    default 53.6%   rusherplus 17.3%   timid 14.2%   heuristic 13.1%
    all-in   1.3%   push-for-depth 0.5%

The two aggressive postures stopped paying once a real rusher was a candidate,
which expresses "commit everything" far better than a margin tweak to knower's
own phases. Dropping them paid for the whole depth increase and more. They are
still listed in `POSTURE_VARIANTS`, outside the width.

actuary was measured as a fifth candidate (10.4% of contested picks, its move
distinct from the default 91% of the time) but is not in the list; see
[`actuary.md`](actuary.md), "As one of knower's borrowed candidates". What it is
worth in games, beside evolved's, is in "evolved as a borrowed candidate (2026-10-19)".

## evolved as a borrowed candidate (2026-10-19)

"Borrowed candidates" credits a candidate with the moves it proposes that
knower's four phases cannot express, not with its strength: rusherplus ranks
last on the ladder and wins more contested searches than any other borrowed
bot. evolved tests that claim from the other side. It ranks third of six, and it
plays unlike the roster ([`evolved.md`](evolved.md), "Where it stands": its
nearest bot is 0.35 away in duels, where every roster bot but rusherplus has one
within 0.12-0.23).

**How it was measured** (`tools/candidate_bench.py`). evolved at Look-ahead Off,
`("evolved", 0.0)`, appended after every other borrowed opening (after actuary
at Council) for one seat, which played knower at the same stop on every seating
of each map. A seat whose candidate proposes nothing plays bit-identically to
knower at both stops (`tests/test_candidate_bench.py`), so the null is exactly
the fair share and every point off it is the candidate's. At Council the
candidate's line is played on by evolved, as every borrowed candidate's is.
Look-ahead Off because the plain policy answers in well under a millisecond,
while the look-ahead's 50 ms wall-clock guard would make knower's plan depend on the
clock, and at Council every rolled turn of evolved's line would pay 8-34 ms for
it. Guards lifted, the six `evo_bench` cells, 50 seeds a cell. The screen's block
lies inside the seeds evolved was trained and selected on (2,000,000 to
5,400,000; [`evolved.md`](evolved.md), "Measuring it"), and the other two lie
outside it. Main moved knower's Search ("A doomed garrison leaves whole") and
marshal while this ran, so the last block was played on the tree after them
(`play_rev` a5f404b5).

Points above the fair share, z over seeds:

| Cell | Search, screen 4,400,000 | Search, 7,100,000 | Search after `_flee`, 7,200,000 | Council, screen 4,400,000 | Council after `_flee`, 7,200,000 |
| --- | --- | --- | --- | --- | --- |
| random:7:6:2 | +14.0 (3.46) | +5.0 (1.15) | 0.0 | +4.0 (0.85) | +5.0 (1.15) |
| random:11:6:3 | 0.0 | −2.0 (−0.57) | +4.0 (1.52) | +3.3 (1.00) | +6.0 (1.84) |
| random:13:3:2 | +5.0 (1.09) | +6.0 (1.43) | +1.0 (0.24) | +11.0 (2.85) | +8.0 (1.66) |
| random:18:6:2 | +13.0 (3.07) | +12.0 (3.06) | +5.0 (1.09) | +1.0 (0.23) | +8.0 (1.53) |
| random:24:3:2 | +8.0 (2.06) | +10.0 (2.33) | +10.0 (3.50) | +9.0 (2.27) | +7.0 (1.48) |
| random:18:18:2 | +17.0 (3.84) | +11.0 (2.85) | +14.0 (3.69) | +3.0 (0.72) | +4.0 (0.94) |
| **Pooled, 300 seeds** | **+9.5 (5.61)** | **+7.0 (4.23)** | **+5.7 (3.68)** | **+5.2 (3.13)** | **+6.3 (3.49)** |

One cell in 30 reads below zero, by two points. Search's gain is smaller after
`_flee` (+7.0 on the block before it, +5.7 after), but on different maps, so the
tree and the maps are not separated. Council's held.

**How often it is picked**, on contested decisions in the block after `_flee`:

    Search    default 54.7%  rusherplus 15.2%  marshal 8.9%  heuristic 7.2%
              evolved 6.4%   timid 3.9%        thinker:0 3.8%
    Council   actuary 30.1%  marshal 23.6%  default 15.7%  evolved 12.7%
              rusherplus 7.9%  thinker:0 4.1%  heuristic 3.5%  timid 2.4%

evolved's opening differs from the default's on 77% of them, and from every
earlier line's on 62% (Search) and 57% (Council). At Search a new opening of
evolved's scored level with the pick on another 17% (38% in the opening) and
lost on list order, since the last candidate loses every tie: its share is a
floor. At Council its line is picked more often (12.7%) than it changes the move
(10.3%). The rest are the opening the search would have played without it,
which evolved's play-on scored higher.

**What it adds.** Over 140 contested decisions where Search picked evolved's line
and the move changed (three duel cells, the screen's tree), evolved's opening
sent 58% of the garrison against 39% for the opening it displaced: 5.6 ships to
our own systems against 2.8, and 2.1 at rivals against 2.8. Its move kinds
there are its usual mix (a reinforce in 90%, a strike in 51%, pool and stagger
in 1% between them). So what knower takes from it is its flow of ships to the
front, not its rarer moves.

**Against the roster** (Search, the same block after `_flee`; knower with
evolved and without, each against marshal and actuary on the same seeds and
seatings, paired by seed):

| Cell | Against marshal | Against actuary |
| --- | --- | --- |
| random:7:6:2 | 83.0% → 90.0% (+7.0) | 65.5% → 63.5% (−2.0) |
| random:11:6:3 | 80.7% → 77.3% (−3.3) | 54.7% → 52.7% (−2.0) |
| random:13:3:2 | 80.5% → 84.5% (+4.0) | 60.5% → 67.0% (+6.5) |
| random:18:6:2 | 80.0% → 93.0% (+13.0) | 60.0% → 64.0% (+4.0) |
| random:24:3:2 | 73.5% → 82.5% (+9.0) | 60.0% → 64.0% (+4.0) |
| random:18:18:2 | 97.5% → 97.0% (−0.5) | 79.0% → 85.0% (+6.0) |
| **Pooled, 300 seeds** | **+4.9 (z +3.50)** | **+2.8 (z +1.51)** |

The gain holds against marshal and leans the same way against actuary, both
below the head-to-head +5.7. The three-seat cell leans worse against both (−3.3,
−2.0), where evolved is weakest on its own ([`evolved.md`](evolved.md), "Why
multi-seat is short").

**actuary in its place** (Search, the same block after `_flee`): +13.4 points
pooled (z +8.69), every cell positive (+6.0, +16.7, +8.0, +17.0, +21.0, +12.0 in
the order above), picked on 10.7% of contested decisions, its opening new on
68% of them. The strongest bot is worth more than twice the distinct one at
Search, and most of all in three seats (+16.7 against evolved's +4.0). evolved
is not a substitute for actuary but an addition: at Council, which borrows
actuary already, it still adds +6.3, the first extra opening there to measure
well above the +1 that more postures, actuary at Greedy and a hold opening each
gave ([`council.md`](council.md), "Built, measured, dropped"). Council borrows
actuary at Field; Hunt in Field's place gains about as much (+6.8), at three to
nine times a decide ([`council.md`](council.md), "actuary at Hunt in
Field's place").

**What it costs** (`--time`: CPU ms of a decide on the same positions with the
candidate and without, every tenth turn of knower-only games on the same block,
one process, guards lifted, load average about 1). Medians, and the ratio of the
totals:

| Cell | Search, + evolved | Search, + actuary | Council, + evolved |
| --- | --- | --- | --- |
| random:7:6:2 | 15.0 → 17.3 (×1.17) | 14.1 → 17.6 (×1.24) | 23.7 → 29.7 (×1.16) |
| random:11:6:3 | 19.3 → 22.6 (×1.18) | 19.3 → 25.0 (×1.23) | 30.8 → 35.7 (×1.17) |
| random:13:3:2 | 28.9 → 34.3 (×1.18) | 27.6 → 35.0 (×1.26) | 69.8 → 80.0 (×1.13) |
| random:18:6:2 | 18.8 → 23.3 (×1.22) | 17.7 → 23.7 (×1.30) | 32.1 → 37.7 (×1.19) |
| random:24:3:2 | 47.5 → 55.1 (×1.19) | 45.0 → 59.9 (×1.32) | 109.9 → 120.9 (×1.11) |
| random:18:18:2 | 11.1 → 13.4 (×1.18) | 11.6 → 14.8 (×1.34) | 21.0 → 24.8 (×1.16) |

evolved costs what one more line costs, a sixth at Search (seven lines for six)
and a seventh at Council: its plain policy adds next to nothing. actuary's
ledger adds about a tenth on top of its line.

**Neither is in the list yet**: the call is the author's. evolved buys +5.7
points head to head at Search (+4.9 against marshal, +2.8 against actuary) and
+6.3 at Council, for about a sixth more a decide. actuary buys +13.4 at Search
for about a quarter more. Shipping either goes past `EXTERNAL_CANDIDATES` (or
`COUNCIL_CANDIDATES`). `_lines()` counts the candidates, but only the rival bots'
share of `estimated_decide_ms` reads it: `ply_ms` and `COST_VS_KNOWER` were
fitted with the lines of the day, so the setup warning would price a decide
about a line short. Every knower result in the bot column goes out of date, and
the strikes `tests/council_traps.py` pins can move.

## How far to look: the longest lane, plus a cushion

The Oracle knob was a search depth, 0-12, until 2026-09-30. The root-only search's
depth curve showed that no fixed depth is right: a depth pays only once the
horizon covers the lanes, and then stops paying. Against marshal, depth 12 guards
lifted, 24-node 2-seat random maps, both seatings, knower's score (a game capped at
600 turns — 1200 at 1 ly — scored to whoever leads on `_evaluate`):

    depth              1     2     3     5     8    12    20
    6 ly  (n=80)      36%   38%   40%   66%   74%   75%   65%
    18 ly (n=40)      72%   85%   80%   78%   72%   65%   80%
    1 ly  (n=20)      20%    -    15%   20%    -    45%   55%

    6 ly head-to-head vs depth 12:  1 19%, 2 29%, 3 34%, 5 55%, 8 47.5%, 20 51%

Lanes on that map run 2-5 turns at 6 ly/turn, 1-2 at 18 and 10-26 at 1. At 6 ly
nothing pays until depth 5 and 8-12 is a plateau; at 18 ly depth is flat to
harmful (against the pre-`FEED` marshal, depth 12 read 25 points below depth 1,
z = -3.2; against the current one the gap is 7.5 and within noise); at 1 ly
nothing pays until the horizon approaches the lanes, and 20 was still rising.
The mechanism is the one the root-only search was built on: ships in transit
count at full value, so a horizon short of a lane cannot tell a good launch from a
bad one, while one far past it spends its extra turns compounding the blind
rollout's model of our own play.

So Search sets its horizon from the map: the longest lane in *current* travel
turns plus `LANE_CUSHION` (`_horizon`, re-read every decide, since
`SHIP_SPEED_GROWTH_PCT` shortens lanes as the game goes on). Against a fixed 12 on
the same seeds, paired, knower's score against marshal (6 ly: seeds 1-80, n=160,
except +0 at n=80):

    cushion       +0          +2          +3          +4          +5
    18 ly     +15.0 ± 6.7 +12.5 ± 8.2 +15.0 ± 6.7 +15.0 ± 7.6  +7.5 ± 7.5
     6 ly      -7.5 ± 5.6  -5.0 ± 3.5  -2.5 ± 3.5  -5.0 ± 3.5  +2.5 ± 3.8
     1 ly     +15.0 ±13.1 +40.0 ±11.2 +30.0 ±12.8 +35.0 ±13.1 +25.0 ± 9.9
    pooled     +2.9 ± 4.1  +1.0 ± 3.1  +2.9 ± 3.0  +0.6 ± 3.1  +5.7 ± 3.2

Every cushion beats a fixed 12 where the lanes are short or long and matches it
at the default speed, where +5 is about the old horizon anyway (lanes of 4-6
turns). Between +2 and +5 the differences are noise — not even monotonic — so the
cushion is +5, the best pooled and the only one at or above 12 at 6 ly. A shared
gain at 1 ly needs the guard lifted (below); inside `SEARCH_BUDGET_S` a slow-ship
search is clipped long before its horizon.

That left three settings that behave differently — Off, Predict (the one-turn
oracle) and Search — so the slider is those three named stops (`AUX_NAMES`). Any
stored value above 2 is read as Search, which is what every depth past 1 meant, so
no setup or link changed its digest.

**The previous depth curve** (branch-every-ply search). Against thinker, both
seatings, 24-node maps (n = decided games):

    depth  1    90.4%   n=114     6 timeouts
    depth  5    96.6%   n=119     1
    depth  8    96.0%   n=400     0
    depth 12    97.5%   n=400     0     <- the top of the slider
    depth 16    95.8%   n=118     2

Depth 1 -> 5 is real and large. **Everything past 5 is inside the noise**: 12
over 8 is +1.5 pt with SE 1.25 (z = 1.20), and head-to-head knower@12 vs
knower@8 finished 63-57 (52.5%, SE 4.6), even. Depth 12 came out ahead in every
measurement taken and behind in none, which is why the slider goes there, but it
is a mild preference and not a proven gain. Don't re-tune this on a hundred
games; the differences are smaller than that.

Depth also plays faster and less passively: from depth 1 to 12 against thinker,
timeouts fall 6 -> 0 and games shorten 112 -> 100 turns.

How much depth is worth depends on whether the opponent is an oracle. Against
knower@1, depth 12 wins 66.1% (78-40); against knower@8 only 52.5%.
`_rollout_decide` plays a non-oracle seat with its real `decide`, so a rollout
against thinker is a faithful simulation for a dozen turns; it plays an oracle
seat with `_blind`, a poor model of a deep knower, so a mirror compounds a
fiction. Useful depth tracks how well the rollout can model the opposition.
Against claudebot there is little headroom: 96.2% at depth 1, 98.8% (79-1) at
12.

## Cost per decide, and where the search guard trips

Two measurements, chained, because they were taken on different machines.

**The old search, 2026-09-29, when maps grew to 120 systems.** Every seat knower,
one game per cell driven at depth 1, each live seat's decide timed every 20 turns
at depth 1 and depth 12 with both guards lifted (`BUDGET_SCALE` inf), so these are
the search's real, untruncated costs. Thread CPU ms on an i5-6300U (2 cores, 4
threads), native CPython, load average under 1.8 throughout:

    systems seats ly/turn   ply p75   depth-12 median / max   depth-1 max
       24     2      6        12         122 /   142            2
       24     6      1        38         305 /   674           10
       24     6      6        15         159 /   225            3
       40     2      6        14         129 /   182            1
       40     6      1        53         428 /   727           10
       40     6      6        23         203 /   288            4
       40     6     18        14         145 /   188            2
       80     2      1        66         250 /  1239            7
       80     6      1       130         674 /  1607           21
       80     6      6        55         544 /   667            7
       80     6     18        26         271 /   324            5
      120     2      6        89         774 /  1177            5
      120     6      1       159         710 /  3540           28
      120     6      6        99         885 /  1340           14
      120     6     18        38         396 /   651            4

(A ply is `(depth-12 - depth-1) / 11` for one decide; the p75 is over every
decide in the game. The full 24-cell grid, 2/6 seats x 1/6/18 ly x
24/40/80/120 systems, was the fit's input: `17.5 ms x (systems/40)^1.0 x
(seats/2)^0.42 x (ly/6)^-0.41`, within +/-25% on most cells.)

**The root-only search, 2026-09-30, as a ratio to the old one.** The same 24 cells,
every seat knower at depth 1, seed 1, sampled every 25 turns to turn 250, each
live seat timed with the old search and the new one *on the same position*, both
unguarded: a ply is `(depth-12 - depth-2) / 10`, the root is depth 2. A different,
faster container, so only ratios are carried over, never its milliseconds:

    new ply / old ply, p75 per cell    0.34 - 0.51, median 0.37
    fit                                0.414 x (systems/40)^+0.03 x (seats/2)^-0.14 x (ly/6)^+0.01
    new root / new ply                 median 1.09 (0.97 - 1.24)
    old root / old ply                 median 0.24 (the old model's 1/4)

The last line is the check on the method: it recovers the old model's root share
to within a point. Folding the fit into the i5 figures gives `ply_ms` =
`7.2 ms x (systems/40)^1.0 x (seats/2)^0.28 x (ly/6)^-0.41` (map size and ship
speed barely move the ratio, so their exponents stand; more seats lowers it, since
a rollout's rival decides are the part that did not shrink), and
`COST_ROOT_PLIES` = 1.1: the root rolls each opening out once, like a ply, and also
builds every candidate's plan.

What it shows:

- **Depth 1 is free at any size.** 28 ms was the worst decide in the old grid, so
  `ORACLE_BUDGET_S` (50 ms) never fires and a depth-1 seat never needs a warning.
- **A ply is ~0.4x what it was, so the guard trips far later.** At the default
  6 ly/turn a Search horizon (4-6 lanes plus 5) fits `SEARCH_BUDGET_S` (150 ms)
  on most maps; slow ships are the tail twice over, since their lanes are longer
  *and* each ply costs more.
- **Cost is linear in map size, and slow ships are the tail.** At 1 ly/turn
  fleets stay in flight for many turns, so every rollout has more to simulate.

With the guard on, a clipped decide stops after the ply that crosses 150 ms.
`_search_run` models that (the root is `COST_ROOT_PLIES` of a ply; the guard is
checked before each ply after it), pinned against the real `_search` by a
fake-clock test. The guarded runs that validated the old model's reach against
real ones have not been repeated for the new one.

**The menu warns only where it matters** (`setup_warning`): when the guard
would stop a Search seat short of the setup's longest lane (read off the same lane
survey the Advanced tab reports), or a turn's Search thinking summed over its seats
passes `WARN_TURN_MS` (1 s). Losing some of the cushion costs little; stopping short
of the lanes means the search cannot price the launches it makes. One Search seat,
longest lane / plies reached, W where it warns:

    nodes   2p/1ly  2p/3ly  2p/6ly  2p/18ly    6p/1ly  6p/3ly  6p/6ly  6p/18ly
      12    33/34   11/16    6/11    2/7      W 33/25  11/16    6/11    2/7
      24  W 26/17    9/14    5/10    2/7      W 26/13   9/14    5/10    2/7
      40  W 19/10    7/12    4/9     2/7      W 19/8    7/12    4/9     2/7
      80  W 20/5     7/8     4/9     2/7      W 20/4  W 7/6     4/8     2/7
     120  W 21/4   W 7/6     4/7     2/7      W 21/3  W 7/4     4/6     2/7

So it is a slow-ship warning now: almost every map at 1 ly/turn, the largest at
3, never at the default speed or faster. Off and Predict never warn.

**Rival bots that think are counted too.** `ply_ms` was fitted against
opponents that answer in microseconds, so it holds nothing for one that takes
milliseconds. A Search seat runs every rival bot's `decide` on each rolled turn
of each line (`_rollout_decide`), and the forecast runs it once more, so a rival
costing `r` ms adds `_lines() * r` to every ply and `r` to the root (`_rivals`,
read off `ai.decide_ms`, which a model declares). People, oracles and other
knower seats are modelled with `_blind`, already inside `ply_ms`, and are not
counted. With two actuary seats on 40 systems (6 ly/turn) the warning now
fires and says the search reaches about 2 of its 4-turn lanes; with marshal in
the same seats it stays silent. Checked against a timed game (guards lifted,
40 systems, 3 seats, horizon 8, load 0.3): knower's decide p75 512 ms against
the model's ~560 ms, and actuary's calls inside its rollouts p75 4.4 ms against
its declared 5.0.

Every figure it quotes is turn one's. With `SHIP_SPEED_GROWTH_PCT` on that is the
slowest the game gets — lanes shorten and plies cheapen as ships speed up, and
`_horizon` follows them every decide — so a clipped warning then adds the turn
from which the guarded search first reaches past the longest lane
(`_sees_lanes_from`, which re-runs the same `_search_run` at each turn's speed off
the setup's own knobs, since `config` still holds the last game's). At 24 systems
and 1 ly/turn that is about turn 31 at 1%/turn and turn 16 at 2%/turn.

**The browser is not modelled.** Every figure here is native CPython; the
pygbag build is slower by an unmeasured factor, so there the guard clips
harder and a turn stalls longer than the warning says (its text says as much).
Measure that before tightening the thresholds on the web build.

**Don't measure this on a loaded machine.** A first pass of the old grid ran
three jobs at once beside other work, at load average 16 on 4 threads. Thread CPU
time excludes being descheduled but not sharing a core, its cache and its clock
with a busy neighbour, and every cell read ~1.9x high (the same seed's 120/6/1 p75
ply: 299 ms loaded, 159 ms quiet). The warning fitted to it fired on 40-system
maps. Run one job at a time and record `os.getloadavg()` beside the numbers. The
ratio grid ran three at a time, which is safe for a ratio only because both
searches were timed back to back on the same position under the same load.

## Seats it cannot run: stand-ins and people

knower cannot run two kinds of seat: one that predicts (it would recurse) and a
person's (there is no code). Until 2026-10-16 both were modelled with `_blind`,
knower's own plan with the oracle off, and neither was trusted. That is right for
another knower, since Off is the nearest version of it that does not predict. It is
wrong for actuary at Learning, the one other oracle seat on the roster: `_blind`
guessed at an actuary with a thinker-strength plan.

**The stand-in.** A predicting module may export `stand_in_params(player)`, the
params of its nearest version that does not predict (actuary's: Field for a
Learning seat, Planned before 2026-10-18; knower's: Off). `_stand_in` runs that module's own `decide` at
those params on the probe, swapping the seat's params in and back out
(`_run`), in the one-turn forecast and in Search rollouts alike
(`_rollout_decide`). Params that still answer `is_oracle_seat`, `None` or a
raise fall back to `_blind`. knower's own seats keep `_blind`, which is the same
thing for them. A stand-in is trusted (`TRUST_STAND_INS`), a `_blind` fallback
never. `_rivals` counts an oracle seat at its bot's `decide_ms` when the module
has the hook; it already did for actuary, which has no module-level flag, so the
setup warning priced the Learning seat correctly before knower ran it.

**What it changed**, knower against actuary-Learning, fresh seeds 10001-10200
(Search 10001-10100), both seatings, 6 ly/turn, paired by game. Learning's win
share:

                         blind      stand-in,    stand-in,
                                    untrusted    trusted
    Predict  18          96.0%      95.8%        90.5%
             40          93.2%      92.0%        82.0%
    Search   18          78.5%      52.5%        46.0%
             40          79.5%      55.0%        47.0%

- **Predict gains only from trust.** Untrusted, pooled +23 / -17 (z +0.95):
  the stand-in can only add threat, and knower already kept thinker's guard
  against the seat. Trusted against untrusted: +74 / -13 (z +6.54). The ~20
  points [`learner.md`](learner.md) puts on the flag ("As a bot") were knower
  believing an exact read of Planned (it won 20% / 32% against an unflagged
  Planned there), and a trusted stand-in gives back a third to a half of that.
- **Search gains from the rollouts.** Untrusted already moves Search +107 / -6
  (z +9.50): rolled turns now play the seat as actuary rather than thinker, so
  each line is priced against something like the real opponent. Trusted, against
  an untrusted run repeated at the same load: +67 / -29 (z +3.88). The two
  untrusted runs, identical code, differ by +34 / -43 (z -1.03), which is the
  wall-clock noise in a Search pairing.
- **The free-for-all** (knower, actuary-Learning, marshal, seeds 11001-11100,
  every rotation, 300 games): Predict knower 9% -> 11%, actuary 70% -> 66%,
  marshal 20% -> 23%, timeouts 9 -> 6. Search (34 seeds, 102 games): knower
  22% -> 53%, actuary 65% -> 36%, marshal 14% -> 11%, timeouts 0 -> 2. The
  after arm ran partly beside other work, which slows Search, so the gain is not
  flattered by load.

Trusting a stand-in believes that a Learning seat which vacates a system under
Planned vacates it for real. It is wrong exactly where the memory moves a launch,
which on these numbers is rare enough to pay. A module could name params far from
how it plays and be believed. A module can also leave the hook out and keep the
flag's protection. That is the same choice a module had before, now visible as one
function.

**People stay on `_blind`.** Two alternatives, both measured and both rejected:

- *A stronger bot.* marshal and actuary at Planned both beat knower at Off, but
  as forecasts of a person they are worse than its blind plan
  ([`learner.md`](learner.md), "Predicting people"): Brier +0.021 and +0.025
  over 176 posted games, both clear of zero.
- *Assume they hold.* The blind guess has a Brier of 0.180 against 0.107 for "no
  launches", and in play its only job is the phantom launches it adds to the board
  knower defends. `_garrison` already prices a person's systems as if they
  hold, and `_pessimistic_owners` keeps the standing guard. Dropping the guess
  (built as `GUESS_HUMAN`, removed) was tested against bots, with the bot's seat
  flagged a person to knower only, at Predict, seeds 12001-12200, both seatings:
  against marshal 14.2% -> 14.2% (18 systems), 15.0% -> 12.0% (40); against
  actuary at Planned 4.5% -> 2.5%, 5.8% -> 2.2%. Pooled +35 / -69, z -3.33. A bot
  is nearer knower's blind plan than a person is, so this test favours the guess
  and does not settle it for people. It is the only test of play there is, and it
  says the phantoms catch more than the guard they waste.

## A doomed garrison leaves whole (2026-10-19)

Reported from play: knower at Search sometimes leaves a few ships on a system a
wave is about to overrun, and they die for nothing. Search plays whichever
opening wins its rollouts, and a borrowed opening keeps its author's habits.
heuristic never evacuates: it launches only the surplus over
`AI_RESERVE_FLOOR`/`AI_RESERVE_FRACTION` and keeps the reserve home whatever is
coming. The other borrowed bots hold a garrison in some positions where
knower's own Phase 2 would leave. The square law makes that a pure loss: a
garrison far smaller than the wave kills almost nothing, and the rounding can
make it nothing (3 against 20 leaves round(√391) = 20).

**What it costs.** 72 games per stop (7:6:2, 11:6:3 and 18:6:2, against
heuristic, rusherplus, thinker and marshal, seeds 8,000,000 + 6), counting every
fight that took one of knower's garrisons, by the attackers it killed over its
size at the fight:

| A game | Search | Council |
| --- | --- | --- |
| Ships lost | 42.3 | 29.5 |
| … in fights that killed under half the garrison | 2.40 | 0.90 |
| … of those, ships there when knower decided, which could have left | 0.65 | 0.31 |
| … the same, in fights that killed under the whole garrison | 0.90 | 0.74 |

Most ships that die in a lopsided fight could not have left that turn. They were
built there that turn (production comes before combat), or they are our own
fleets landing as the system falls. The habit reported is the rest, about 1.5%
of Search's losses.

**The rule** (`_flee`, `FLEE_BELOW` 0.5), run on the opening Search chose:

- It looks at every system of ours where hostile ships land this turn. Only
  certain arrivals count: fleets already flying, and the launches of seats the
  oracle reads exactly. A person's predicted strike never counts (defence only:
  a guess may raise a guard, never empty a system).
- The garrison at the fight is what the opening leaves, plus this turn's
  production, plus our fleets landing there.
- It acts only if the wave beats the garrison even at the garrison's best roll
  (`combat.preview_fight`'s worst corner for the attacker). Several attackers
  are folded strongest first, as a pile-up resolves.
- It acts only if, at the nominal roll, the garrison kills fewer than
  `FLEE_BELOW` of its own number. Kills are judged at the nominal roll and the
  loss at the best one. Judged at the best roll, a 3-ship remnant facing 7
  "kills 2" and stays, though it really kills 1.
- Then what the opening left there leaves. It joins the biggest fleet the
  opening already sends from that system, or else takes `_evacuate`'s route: a
  capture in reach (the attacker's emptied home included), else the friend next
  door with the biggest garrison. A cornered garrison stays. Our own launches
  that would land there this turn stay home.

With it, the ships that could have left and died anyway fell from 47 to 0 over
Search's 72 games, and from 22 to 1 over Council's.

**Where it pays.** Not mostly in ships. Over 24 Search games against marshal
and thinker it fired 45 times. 34 were retreats (50 ships). In 11, a garrison of
about 7 that a borrowed opening had left to die stepped into the attacker's
home, emptied to launch the wave, and 8 of those took it. None joined a
departing fleet: against these bots the habit is mostly a whole garrison a
borrowed opening holds, not a reserve kept back from an evacuation.

**Measured** with `tools/win_turns.py --quick` against heuristic, rusherplus,
thinker and marshal, paired against the tree before it:

| Stop | Seeds | Pairs | Beats | z | Turns | Ships lost a game |
| --- | --- | --- | --- | --- | --- | --- |
| Search | 7,000,000 + 25 | 700 | 53.5% | +3.4 | −0.6% | −1.23 (z −1.8) |
| Search | 7,100,000 + 40 | 1,120 | 51.2% | +1.5 | +0.1% | −0.35 (z −0.7) |
| Council | 7,000,000 + 25 | 700 | 50.8% | +1.0 | +0.3% | −0.39 (z −0.9) |

On the 153 posted maps, Council with the rule beat Council without it on 24,
was level on 96 and worse on 33 (47.1%, z −1.2). On maps with a knower rival at
Search the rival changed too. Council against knower:2 is confounded the same
way, since both seats are this module: 50.6%, −2.9 points won.

Search's fresh block is smaller than its screen: the marshal cells that carried
the screen (61%, 56%, 58%) came back at 51%, 50% and 49%. Both blocks lean the
same way, and together they come to about 52% over 1,820 pairs. **Shipped on
Search.** **Not on Council**, for three reasons. Its judge, ranking lines
played on by the bots that proposed them, already picks openings knowing the
garrison dies
([`council.md`](council.md), "Stripped frontiers are trades, not mistakes").
Half as many of its ships die this way. And it measured null against bots and
leaned worse on the posted maps.

**Open.**

- `FLEE_BELOW` 1.0, leaving fights that would trade below even. It would reach
  about 0.25 more ships a game on Search. Untested.
- Our own fleets landing into a falling system, the larger share of the dead.
  The rule keeps home only this turn's launches that land with the wave. Relief
  sent on an earlier turn cannot be recalled, so the check belongs where it is
  launched. knower's Phase 1 makes that check for its own plan, and a borrowed
  opening does not. Unmeasured.

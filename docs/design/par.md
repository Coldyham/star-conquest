# Par search (`tools/par_search.py`)

This tool estimates the best score possible on one setup: it plays the human seat
as a person who rewinds freely and knows exactly what every bot will do. It is a
**local check**, run by hand to see how much room a score or a map has. Nothing
reads its output, and it will not go on the board (see "Not on the board").

## The question

Could a workflow find the true best score (turns to win, ties on fewest ships
lost) on a leaderboard map? Proving the optimum is out of reach except on tiny
maps: each owned system can send any count to any neighbour, so the number of
possible order sets per turn explodes by mid-game, and a 13-node game runs 30+
turns deep. A bounded search is cheap, though, and gives two numbers per map: a
**floor** nobody can beat and the **best line found**.

## Why it is less random than it looks

- **Knowing the bots' orders is free.** Turns resolve simultaneously, so a
  bot's turn-t orders depend only on the board at the start of turn t, not on
  the human's orders that turn. On a copy of the board, each rival's `decide`
  gives its orders exactly. knower relies on the same fact.
- **There are two sources of chance, and they no longer touch:**
  - **combat dice**: two `uniform(-j, j)` per pairwise fight
    (`combat.resolve_fight`), each fight rolled from a stream keyed by seed,
    dice salt, turn and place (`engine._Dice`), never from `state.rng`;
  - **bot tie-breaks**: heuristic (`ai._frontier_order`), thinker (target sort,
    `_evacuate`), claudebot (target sort) and rusherplus all draw. knower,
    marshal and actuary draw nothing. The flow-to-front helpers themselves are
    deterministic. Each turn's `state.rng` is derived from `(seed, turn)`
    (`replay.reseed`), and every bot seat decides from that same start
    (`engine.decide_seat`).

  So a rival's orders follow from the board alone, and a fight's dice depend
  only on which fight it is. An order changes only the fights it takes part in.
- **A rewind is not a re-roll.** A rewound turn ended with the same orders lands
  where the straight game did, so a rewinding player has nothing to fish with
  but the orders of the fight itself.

  **Until 2026-10-08 the game was looser.** Until 2026-10-07 a rewind left the
  rng where map generation left it (`reconstruct` deals recorded dice and draws
  nothing), so each turn offered two streams, "carry on" and "reset". Until
  2026-10-08 bots and combat drew in series from that one stream, so a throwaway
  fight elsewhere used up rolls, and fishing for a roll reshuffled what thinker
  or claudebot did. Reasons in `docs/design/core.md`, "Persistence, replay &
  history" and "Two streams". The exhaustive search below models the old rule
  on purpose. The readings are from 2026-10-14, under the new one.

## The floor

Win means every rival is gone, with no systems and no fleets (`engine._check_win`).
Neutrals don't count. So the win comes no sooner than the latest of:

- the shortest travel from the searcher's systems and fleets to each
  rival-held system;
- each rival fleet's arrival.

Lanes are timed at the horizon turn, so ship-speed growth can only make the real
trip longer. **It is admissible except in one case:** two rivals annihilating
each other at a system, which leaves it neutral without the searcher arriving.
`tests/test_par_search.py` checks the floor against real all-bot wins.

There is no ship-count bound. Under the square law a big enough stack loses
almost nothing (losses ≈ B²/2A), so "ships needed" barely prunes anything.

## Dice modes

- **`lucky`**: every fight the searcher is in resolves at its best roll (own
  side `+j`, other side `-j`). The draws carry no side information, so this wraps
  `combat.resolve_fight` inside the tool's own process. Every fight (arrivals,
  pile-up folds, lane clashes) calls it by name, so one wrapper covers them all.
  The rolls dealt are recorded, so the line replays through `reconstruct`.
  Under the derived rng no player can reach these rolls, since a rewind no
  longer re-rolls, so lucky is an optimist's yardstick rather than a model of
  any play. **It is still not a ceiling.** Fights between two rivals roll what
  they roll on honest dice, since each fight has its own stream. But the best
  roll in each fight is not always the best for the game: a different survivor
  count changes what every bot does next, and the beam then follows other
  lines. Measured, honest dice found a sooner win than lucky in 3 of 124 paired
  runs (see "Readings").
- **`honest`**: every simulated turn is seeded as the game seeds it
  (`replay.reseed` inside `par_search.step`), and every fight rolls its keyed
  dice inside `end_turn`, so the dice and tie-breaks are the ones a player would
  meet with the same orders. Rewinding offers nothing more, so this is the
  **achievable** number. A fight falls differently only when the orders in it
  change, and the search's candidates are its only fishing. Until
  2026-10-07 honest mode carried the rng and also tried each turn from the
  stream a rewind left (`--resets`, now removed), which modelled the old
  re-roll rule.

## The search

It is a beam search over turns:
- **Candidates for the human's orders**: each roster bot's orders for the
  seat, decided on a private copy with an rng of its own; the same orders sent
  all-in; holding; and the first bot's orders with one strike dropped.
- **Scoring**: each child is rolled out to the end once per `--policy` bot in
  the seat (marshal and actuary by default) and ranked by the best of them
  (`Search.score`). Before 2026-10-14 there was one rollout, with marshal.
- **Pruning and merging**: a child whose floor can't beat the best (turns,
  then lost) is pruned, and transpositions are merged on the board. That merge
  is exact, since each turn's rng follows from the turn.
- **Incumbents**: every winning rollout is a complete line and a new incumbent.
  The starting incumbent comes from each candidate bot playing the seat alone,
  under the same dice.

The best line is written as a `GameLog`, replayed through `reconstruct` and
checked before it is reported.

**Cost.** One step is a cheap copy plus an `end_turn`. On 8 nodes against
heuristic, a turn costs about 0.05 ms. Rival decide cost dominates as maps and
rosters grow. Rivals on knower Search take seconds per decide, so leave them out.

## Readings (2026-10-14)

Retaken under the derived rng and keyed dice. Two things moved since the first
readings on 2026-10-07, so neither change alone explains a shift: the rules,
since honest mode can no longer fish through a rewind, and the roster
(actuary's `SELF_RELIEF` and gated opening, marshal's `REMNANT`, both shipped
2026-10-12/13).

Every run used beam width 6 with a 1200 s budget, and all 248 best lines
replay through `reconstruct` to the turn and losses reported. The runs went 16
at a time on 24 cores, so wall times run longer than the serial ones of
2026-10-07, and the budget, which is wall clock, bites sooner. Cells are
turn/ships lost. "Bot alone" is the best single roster bot playing the seat
under the same dice, with the seat still flagged human, so it is not the
board's bot column. The scripts and every run's output are in the gitignored
`results/par-2026-10-14/`.

**Against recorded human wins** (local `games/`, `--log`). The last three were
played on keyed dice, the same dice the honest search meets:

| Systems, players, rivals | Floor | Human | Honest | Lucky | Time (honest) | 2026-10-07 honest, lucky |
| --- | --- | --- | --- | --- | --- | --- |
| 9, 2, heuristic | 6 | 32/28 | 14/8 | 11/1 | 1 s | 13/5, 11/2 |
| 9, 2, marshal | 6 | 24/11 | 18/6 | 11/3 | 2 s | 14/7, 16/2 |
| 11, 3, heuristic ×2 | 19 | 67/52 | 49/22 | 39/7 | 13 s | 42/26, 42/9 |
| 15, 4, marshal knower thinker | 12 | 68/45 | 42/27 | 38/10 | 1034 s | 47/44, 44/17 |
| 18, 3, heuristic ×2 | 21 | **42/20** | 46/33 | 42/10 | 13 s | 51/30, 40/5 |
| 25, 4, marshal knower thinker | 15 | 64/82 | 49/56 | 41/20 | 1208 s (budget) | 44/35, 37/12 |
| 21, 3, marshal actuary | 13 | 52/73 | 43/38 | 34/13 | 135 s | |
| 11, 2, learner | 11 | 50/21 | 29/14 | 16/3 | 3 s | |
| 18, 3, claudebot actuary | 26 | 87/97 | no win (73/50) | 68/20 | 712 s | |

The bracketed 73/50 is the same search with actuary rollouts (see "The rollout
bot" below).

**Grid** (random, 3 players, seeds 1 and 2; honest | lucky, best bot alone in brackets):

| Nodes | vs marshal + actuary | vs thinker + heuristic |
| --- | --- | --- |
| 8 | 44 (75), 51 (94) \| 43 (52), 44 (54) | 61 (173), 55 (80) \| 65 (141), 35 (48) |
| 13 | 68 (93), 87 (none) \| 47 (59), 73 (86) | 59 (121), 70 (75) \| 48 (56), 56 (74) |
| 18 | 87 (none), 71 (none) \| 55 (none), 64 (81) | 50 (65), 73 (86) \| 43 (45), 51 (52) |

What they say:

- **The search still beats the recorded human win on honest dice in 7 of 9
  games**, and in 8 of 9 with actuary rollouts. On the three games played on
  keyed dice it wins 9, 21 and 14 turns sooner, on the very dice the person
  met. The exception is still the 18-system game against two heuristics (42
  against 46). Lucky reaches 42 there and loses half the ships.
- **Honest moved later, by a median 5 turns on the grid** (8 of 12 cells
  later, 3 sooner, 1 the same), which is what losing rewind re-rolls should
  cost. Lucky moved a median 2.5 turns later too, so part of that is the roster
  and the rivals' new tie-break streams, not the rewinds.
- **It beats the best bot alone in every grid run.** On 2026-10-07 it tied
  once.
- **Lucky beat honest in 20 of 21 pairs** (15 of 18 on 2026-10-07). Honest
  won sooner once, on the 8-node grid against thinker and heuristic, seed 1 (61
  against 65). Across all 124 pairs, board included, honest was sooner 3 times.
- **Cost is fine for everything but knower.** Grid runs took 6-243 s. The one
  budget hit among the recorded games has knower as a rival.

## The live board (2026-10-14)

Every board map with fewer than 20 systems was searched: 103 of the 140 maps,
counting systems by building each map. 93 have a win in the bot column, 84 a
counted human score, and 7 a human win played wholly on keyed dice (every turn
`keyed`, read from `public_replays`).

| Against | Lucky | Honest |
| --- | --- | --- |
| Best human score (84 maps) | beats 77, worse 7; median 10.5 turns sooner | beats 47, ties 1, worse 33, no win 3; median 1 turn sooner |
| Human wins on keyed dice (7 maps) | beats 7; median 25 turns sooner | beats 6, worse 1; median 18 turns sooner |
| Bot column's best win (93 maps) | beats 93 | beats 82, ties 1, worse 9, no win 1 |

On 2026-10-07 the human rows read 76 of 84 (lucky) and 47 of 84 with a median
of 1 turn (honest), and the bot column 80 of 80 and 75 of 80.

- **Against the board's bests, honest play is where it was: about as good as
  the board's best player.** Most of those bests were set when a rewind
  re-rolled, so they had fishing that the honest search never had.
- **Against people on the same dice, the search is well ahead.** On the 7
  keyed-dice wins it is a median 18 turns sooner on honest dice. The one
  person ahead of it, on `7a81e11e6a977b74` (7 systems against marshal, 14/8
  against 16/9), falls to actuary rollouts, which find 13/8. All 7 were
  played since 2026-10-08, so they are a small sample of fresh scores, not the
  board's best play.
- **Seven maps have a line on the floor**, as on 2026-10-07: `06a74fc834bdf656`
  (lucky 16/4), `23c791a5c2780eef`, `71f7fab85ca23341`, `732decc8f9619f60`,
  `c3c0f862c6f497f7`, `e33cf0993ff6b95c` (lucky 15/2; the board's 15/9 is on
  the floor too) and `b9119a724511200d`, the only one where honest dice reach
  the floor (10/9; the board's best is 11/2).
- **People still beat the lucky search on 7 maps** (8 on 2026-10-07). Four have
  knower as a rival, and two of those hit the budget:

  | Map | Systems, rivals | Floor | Board best | Lucky | Honest |
  | --- | --- | --- | --- | --- | --- |
  | `83653810811196b0` | 5, heuristic | 12 | 18/7 | 21/3 | 30/13 |
  | `50f1e509421c1cd3` | 13, marshal knower | 14 | 27/11 | 29/3 | 39/21 |
  | `7f7fabfca0969ba4` | 13, marshal knower | 18 | 38/25 | 42/10 | 50/26 |
  | `a0e7c9f268079977` | 14, marshal knower thinker | 19 | 64/37 | 76/17 (budget) | no win |
  | `e11525f88c9f825d` | 14, marshal knower thinker | 19 | 34/15 | 41/10 | 41/23 |
  | `e8fb9306833d62be` | 14, marshal knower thinker | 15 | 85/100 | 128/64 (budget) | no win |
  | `665714b9291851c6` | 18, thinker ×2 | 20 | 51/33 | 61/7 | 126/112 |

- **Knower is the cost.** Maps without knower took at most 234 s a mode. All 18
  budget hits have knower as a rival (9 on 2026-10-07, run serially).
- **The floor stays loose:** best found is a median 2.1x the floor on lucky dice
  and 2.75x on honest, as before.
- **The old stalemate map is easy now.** On `4222d9e81bd86a17` (5 systems, every
  lane one turn) the default marshal rollouts found no win on 2026-10-07; they
  now find 13/11 on honest dice (lucky 13/5, floor 7). The board's best is
  22/23.
- **`06a74fc834bdf656` settles at 17.** Honest dice find 17/6, as the
  exhaustive search predicted, and the board's best is 17/4.

## The rollout bot

The honest search's worst readings are rollout failures, not beam failures.
Rerun with other rollout bots (`--policy`, honest dice, width 6), on the
maps where the default marshal rollouts did worst:

| Map or game | Systems, rivals | marshal | actuary | rusherplus | Human |
| --- | --- | --- | --- | --- | --- |
| game of 2026-10-09 | 18, claudebot actuary | no win | **73/50** | no win | 87/97 |
| `71f7fab85ca23341` | 7, marshal | no win | **19/3** | no win | 13/7 |
| `7a81e11e6a977b74` | 7, marshal | 16/9 | **13/8** | 44/20 | 14/8 |
| `665714b9291851c6` | 18, thinker ×2 | 126/112 | **60/34** | 133/118 | 51/33 |
| `979382e8f69c0f0d` | 9, claudebot | 112/67 | 103/43 | **87/49** | 91/60 |
| `f0eec8559752bb90` | 18, marshal actuary | 222/223 | **47/34** | 222/223 | 82/83 |

Actuary is the best rollout bot on 5 of 6 and rusherplus on the other. On
`f0eec8559752bb90` the marshal search found nothing better than a bot playing
alone, and actuary rollouts cut that from 222 to 47 turns. These maps were
picked because marshal did badly on them, so this does not show actuary is the
better default everywhere. It does show that rolling out with more than one bot
and keeping the best would fix the worst readings.

**So the default is now both** (`ROLLOUT_POLICIES`, marshal and actuary). Each
child is rolled out with each and ranked by the best. Rerun on honest dice
against the marshal-only results above (the 12 grid setups, the 9 recorded
games, the 5 non-knower maps in the table, and 10 other non-knower board maps
drawn at random), it was better on 28 of 36, the same on 1 and worse on 7. The
median was 6.5 turns sooner, and it took a median 2.4x the time. All 7 losses
were small (1-9 turns, or the same turn with more ships lost), from the beam
following other children. The search now beats 8 of the 9 recorded human wins
on honest dice, all but the 18-system game against two heuristics (48/29
against 42/20), and `7a81e11e6a977b74` falls to 12/7. In 10 of the 28 gains the
winning line came from a marshal rollout: actuary's ranking had steered the beam
somewhere better.

## Exhaustive on a tiny map (`06a74fc834bdf656`, 2026-10-07)

> The first half of this section models the old rewind rule on purpose. The
> last paragraph is the same search under `replay.reseed`.

The map has 5 systems and 2 players against heuristic. We hold 3, the rival
holds 2, and 2's only lane leads to 1, so the fastest route is 3 → 1 → 2, 8
turns each. That is the floor of 16. With two players no rival can annihilate
another, so the floor is a strict bound here. The board's best was 17/4, made
by waiting one turn before attacking. The beam found 16/4 on lucky dice and
17/4 on honest.

**What makes it exhaustible.** Every roll the game deals there is one call to
`random()` (heuristic's tie-break `uniform` and combat's), and a rewind leaves
the rng at R0, where map generation left it. So the rng at the start of any
turn is R0 plus some count of draws. The search ran over (board, draw count)
for turns 0-15. At every turn it tried "carry on" and "rewind here", crossed
with the human's orders:
- every turn-0 send from 3 to 1 of 9-12 ships;
- relaunching all, or all but one, from 1 to 2;
- fishing sends from 3: one ship or every ship to the neutral at 4, or one ship
  to 1.

It pruned on the floor and kept the fewest losses per (board, draw count). It
took 2 minutes and expanded about 2 million turns. It checked that no other rng
method was ever called.

**Result: 16 is reachable on real dice, and 16/10 is the best.**
- 10,464 lines win on turn 16. Their losses are 10 (7,900 lines), 11 (1,888),
  12 (564) and 13 (112).
- **None of them win without a rewind.** 10,012 need one rewind, and 452 need two.
  The simplest: send all 12 on turn 0, rewind to turn 7 so the fights at 1 roll
  from R0, then relaunch the survivors at 2. It replays through `reconstruct`
  to 16/10.
- The same evening a person posted 16/10 on the board, matching the search
  exactly.

So the lucky beam's 16/4 rested on rolls no rewind reaches, and the honest
beam's 17 was a real limit of play without fishing. **The order set is a
model, not every legal order.** It covers every send that can still win by 16
and the obvious fishing sends, not every split of every garrison. The script was
a one-off and is not committed. It would generalise to any map small enough
that floor-pruning leaves a few thousand states a turn.

**Under the derived rng, 16 is gone.** The same search with each turn seeded as
`replay.reseed` seeds it (no carry or rewind modes, since they now coincide)
finds no line that wins on turn 16; every one has passed the floor by turn 10.
Sending 12 on turn 0 now captures 1 with 5 left, where the carried stream
gave 6. The best left is probably 17, as the board's 17/4 scores have it. The
16/10 was set under the old re-roll rule. That run predates keyed dice
(2026-10-08) and has not been repeated by exhaustion. Under keyed dice the
beam finds 17/6 on honest dice and 16/4 on lucky (2026-10-14).

## Not on the board (decided 2026-10-07)

Publishing a par or a floor per map was considered and decided against. The
tool stays as something to run locally against a map or a score
(`--game-key`, `--log`). The bot column remains the only computed number the
board shows.

## Next, if it goes further

- **Search quality.** Rolling out with more than one bot is built ("The
  rollout bot"). Next: wider beams, and candidates that aren't some bot's idea
  (the maps where people still win sooner suggest these exist). Throwaway
  orders no longer re-roll anything, so they are not worth searching.
- **Cost.** Anything with knower as a rival wants its own time budget.

## A gap this exposes in `verify_scores`

`tools/verify_scores.py` checks that a log replays consistently. It does not
check that the dice came from the seed, or that the bots' orders are what those
bots would play. Rechecked on 2026-10-07, after `replay.reseed`, on a 12-node
3-player map against heuristics: **both halves are still open.** The dice half
was closed on 2026-10-08 for keyed turns (the last paragraph of this section).

- **Dice.** A lucky line, which carries hand-picked rolls in 19 of its 20
  fighting turns, verifies.
- **Bot orders.** An honest line with 2 rival orders deleted from its last 10
  turns still wins on the same turn (36 lost instead of 34) and verifies.

**What `replay.reseed` makes possible, and what it doesn't.** Every live turn
now starts from `Random(f"{seed}:turn:{turn}")`. The bots draw first, then
combat draws its dice back to back. So a genuine turn's dice are an unbroken
run of `random()` values from that stream, starting after however many 32-bit
words the bots used. A prototype that scans the first 4096 words for that run
found it on 27 of 27 fighting turns of an honest line, 1 of 20 of the lucky
line, and 0 of 6 of a board log from before the change. It has three limits:

- **Where the run starts isn't known.** The bots' draws come first, and the
  verifier would have to re-run the bots to count them, which the board rules
  out ("bots are free to move"). Accepting any start leaves a cheater a few
  thousand starting points to fish among per turn.
- **It says nothing about bot orders.** The doctored line above passes it,
  27 of 27, because its recorded dice are unchanged.
- **It only applies to games played after `replay.reseed`.** A log does not
  say which rule it was played under, so the verifier would need a marker.

Rolling combat from a stream of its own removes the first limit, and was built
on 2026-10-08 (`docs/design/core.md`, "Two streams"). Each fight's dice are
derived inside `end_turn` from the board alone, and every turn since carries a
`keyed` marker, so a keyed turn's dice can be rolled again from its recorded
orders and compared, with nothing to fish among. `pbp.verify_turn` does this for
a shared match's live turn, and `tools/verify_scores.py` does it for every keyed
turn behind a posted score (`_DiceAudit`, a `mismatch` on the first that
differs). On a 12-node 3-player map, seed 5, a lucky line (20 fighting turns)
was caught at turn 7, and an honest line passed all 87 of its turns. The second
limit holds for a log with every `keyed` marker stripped, which reads as one
from before the change. The bot-orders half stays open by design. Also recorded
in `docs/design/leaderboard.md`, "Checked scores".

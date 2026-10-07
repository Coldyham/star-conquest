# Par search (`tools/par_search.py`)

This tool estimates the best score possible on one setup: it plays the human seat
as a person who rewinds freely and knows exactly what every bot will do. It is a
**measurement spike**, not a feature. Nothing reads its output yet, and nothing
goes on the board until the numbers say it is worth doing.

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
- **There are two sources of chance, and both draw from `state.rng`:**
  - **combat dice**: two `uniform(-j, j)` per pairwise fight (`combat.resolve_fight`);
  - **bot tie-breaks**: heuristic (`ai._frontier_order`), thinker (target sort,
    `_evacuate`), claudebot (target sort) and rusherplus all draw. knower,
    marshal and actuary draw nothing. The flow-to-front helpers themselves are
    deterministic.

  The two are coupled. Rivals decide before combat, so this turn's fights set
  where next turn's tie-breaks start.
- **A rewind resets the rng.** `main.apply_rewind` goes through
  `replay.reconstruct`, which deals recorded dice and asks no bot to decide, so
  it draws nothing from `state.rng`. After any rewind the stream sits exactly
  where map generation left it, whatever turn was rewound to. A rewinding player
  therefore has two streams per turn, "carry on" and "reset", plus whatever an
  extra order shifts: a throwaway fight that resolves first uses up rolls.
  Against thinker or claudebot, fishing for a roll also reshuffles what the
  rival does.

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
  **It is not a ceiling, even against marshal and actuary.** Fights between two
  rivals still draw from the stream, and in lucky mode the searcher's own fights
  stop consuming it, so those rival fights fall differently. The best roll in
  each fight is also not always the best for the game, since a different
  survivor count changes what every bot does next. Measured, honest dice found a
  sooner win than lucky in 2 of 18 paired runs (see "Readings").
- **`honest`**: dice and tie-breaks come from the copied board's own rng, and
  every turn is also tried from the reset stream (`--resets`). This is the
  **achievable** number. Throwaway re-rolls are not modelled yet.

## The search

It is a beam search over turns:
- **Candidates for the human's orders**: each roster bot's orders for the
  seat, decided on a private copy with an rng of its own; the same orders sent
  all-in; holding; and the first bot's orders with one strike dropped.
- **Scoring**: each child is ranked by a rollout to the end with `--policy`
  (marshal) in the seat.
- **Pruning and merging**: a child whose floor can't beat the best (turns,
  then lost) is pruned, and transpositions are merged on the board, ignoring
  the rng.
- **Incumbents**: every winning rollout is a complete line and a new incumbent.
  The starting incumbent comes from each candidate bot playing the seat alone,
  under the same dice.

The best line is written as a `GameLog`, replayed through `reconstruct` and
checked before it is reported.

**Cost.** One step is a cheap copy plus an `end_turn`. On 8 nodes against
heuristic, a turn costs about 0.05 ms. Rival decide cost dominates as maps and
rosters grow. Rivals on knower Search take seconds per decide, so leave them out.

## Readings (2026-10-07)

Every run used beam width 6 with a 1200 s budget, and none needed the budget.
All 38 best lines (these 36 and the 2026-10-06 pair) replay through
`reconstruct` to the turn and losses reported. Cells are turn/ships lost; "bot
alone" is the best single roster bot playing the seat under the same dice, with
the seat still flagged human, so it is not the board's bot column.

**Against recorded human wins** (local `games/`, `--log`):

| Systems, players, rivals | Floor | Human | Honest | Lucky | Time (honest) |
| --- | --- | --- | --- | --- | --- |
| 9, 2, heuristic | 6 | 32/28 | 13/5 | 11/2 | 1 s |
| 9, 2, marshal | 6 | 24/11 | 14/7 | 16/2 | 1 s |
| 11, 3, heuristic ×2 | 19 | 67/52 | 42/26 | 42/9 | 4 s |
| 15, 4, marshal knower thinker | 12 | 68/45 | 47/44 | 44/17 | 645 s |
| 18, 3, heuristic ×2 | 21 | **42/20** | 51/30 | 40/5 | 6 s |
| 25, 4, marshal knower thinker | 15 | 64/82 | 44/35 | 37/12 | 559 s |

**Grid** (random, 3 players, seeds 1 and 2; honest | lucky, best bot alone in brackets):

| Nodes | vs marshal + actuary | vs thinker + heuristic |
| --- | --- | --- |
| 8 | 40 (92), 51 (90) \| 48 (73), 42 (101) | 103 (none), 52 (73) \| 41 (63), 32 (60) |
| 13 | 61 (101), 81 (none) \| 48 (48), 60 (174) | 53 (78), 64 (146) \| 47 (57), 55 (81) |
| 18 | 73 (none), 78 (277) \| 48 (96), 57 (115) | 57 (58), 67 (167) \| 33 (47), 53 (62) |

What they say:

- **The search beat the recorded human win in 5 of 6 games on honest dice**,
  by 10 to 25 turns, and with fewer ships lost every time. The exception is the
  18-system game against two heuristics (42 against 51). Lucky dice reach 40
  there, so the person may have fished well or found a plan no candidate
  proposes. A real score therefore usually has a lot of room in it, and the
  board could show a par.
- **It nearly always beats the best bot alone, often by a lot** (8 nodes
  against marshal + actuary: 40 against 92). It tied once (13 nodes, seed 1,
  lucky: 48 against marshal's 48). The beam turns a roster of mediocre seat
  players into a strong one, because it is choosing among their ideas turn by
  turn with the bots' replies known.
- **Lucky beat honest in 15 of 18 pairs, tied once on turns and lost twice**.
  This is why lucky is not a ceiling (see "Dice modes").
- **The floor is loose.** The best line found lands at 1.5 to 4.7 times the
  floor, and the floor pruned 0 to 14% of children. It is honest as a "nobody
  can beat this" number and does little as a search bound.
- **Cost is fine for everything but knower.** Grid runs took 3-161 s, and
  marshal + actuary cost about 3x thinker + heuristic. The two games with
  knower as a rival took 4-11 minutes, which is still affordable for a
  scheduled job on the board's maps, which mostly have 12-33 nodes.
- Aside: on the 8-node 2-player map, marshal and thinker never win against
  heuristic in the plain harness either (`sim.play_settings`), so this is not
  something the search caused. It could be worth a look on its own.

## The live board (2026-10-07)

Every board map with fewer than 20 systems was searched: 86 of the 119 maps,
counting systems by building each map rather than reading `nodes`. 80 have a
win in the bot column. Each map ran on both dice modes (`--game-key`, beam
width 6, 1200 s budget). All 172 lines found replay through `reconstruct` to
the turn and losses reported.

**Human scores:** 67 maps have a counted score, but 84 have a score that
replays. `verify_scores` had wrongly marked 40 scores `mismatch` and so dropped
them from `counted_scores`, because a log that leaves its seed to the log
read as another map. That is fixed on branch `verify-seedless-log`. Two more
already verify on today's main but have not been re-checked. The table counts
all 84.

| Against | Lucky | Honest |
| --- | --- | --- |
| Best human score (84 maps) | beats 76, worse 8; median 10 turns sooner | beats 47, ties 4, worse 32, no win 1; median 1 turn sooner |
| Bot column's best win (80 maps) | beats 80 | beats 75 |

- **On honest dice the search plays about as well as the board's best player**
  (median 1 turn sooner). On lucky dice it is about 10 turns sooner. A board best
  is the best of many attempts, and probably rewind-fished, so lucky is the fairer
  comparison and the likelier shape of a published par.
- **Seven maps have a line that reaches the floor, so nobody can win them
  sooner.** On one of these (`e33cf0993ff6b95c`, 7 systems, against heuristic),
  the board's best is already on the floor at turn 15. The search ties it on
  turns and loses 4 ships to the person's 9.
- **People still beat the lucky search on 8 maps**, by 1-8 turns, usually
  losing more ships to do it. Five of the eight have knower as a rival, and two
  of those ran into the time budget. The rest point to plans no candidate
  proposes:

  | Map | Systems, rivals | Floor | Board best | Lucky | Honest |
  | --- | --- | --- | --- | --- | --- |
  | `83653810811196b0` | 5, heuristic | 12 | 18/7 | 21/3 | 21/8 |
  | `24be4a35810b94e8` | 7, marshal thinker | 17 | 51/31 | 52/7 | 53/15 |
  | `50f1e509421c1cd3` | 13, marshal knower | 14 | 27/11 | 29/5 | 35/15 |
  | `7f7fabfca0969ba4` | 13, marshal knower | 18 | 38/25 | 46/12 | 41/24 |
  | `91e180ce1b50757f` | 13, marshal knower | 16 | 42/23 | 47/13 | 68/50 |
  | `a0e7c9f268079977` | 14, marshal knower thinker | 19 | 64/37 | 67/28 | 209/144 |
  | `e11525f88c9f825d` | 14, marshal knower thinker | 19 | 34/15 | 35/9 | 40/17 |
  | `698b9659d4c657a1` | 19, marshal actuary | 10 | 41/24 | 42/17 | 42/29 |

- **The rollout bot matters more than the beam.** `4222d9e81bd86a17` is 5
  systems at 19.5 ly/turn, so every lane takes one turn. Every bot in the
  column stalemates to turn 600, and so does the honest search with its default
  marshal rollouts, because marshal is what stalemates. People have won it on
  turn 25 (lost 17, against the marshal of 2026-09-17, which today's marshal
  first departs from on turn 15) and turn 35 (lost 13). Rerun with other
  rollout bots (honest dice):

  | Rollout bot | Width 6 | Width 16 |
  | --- | --- | --- |
  | marshal | no win | 28/19 |
  | actuary | **16/10** | **16/10** |
  | rusherplus | 47/24 | 22/11 |
  | thinker, heuristic | no win | no win |

  So an honest 16 exists, at the floor plus 9. The lucky search also found 16.
- **Knower is the cost.** Maps without knower took at most 64 s a mode, most
  of them a few seconds. Maps with knower took 1-20 minutes, and 9 runs hit the 1200 s budget. Every budget hit had knower as a
  rival, apart from the beam bug below.
- **The floor stays loose:** best found is a median 2.1x the floor on lucky dice
  and 2.7x on honest.
- **A bug the board found:** the beam ignored `--max-turns`, so a map nobody
  could win kept the honest search going to turn 35,827 before the budget
  stopped it. Fixed, with `test_the_beam_stops_at_max_turns`.

## Next, if it goes further

- **A par on the board** would be the best honest line per map, computed
  offline like the bot column, with its log stored for a Watch link. The
  floor alone is free, but too loose to be interesting.
- **Search quality.** The first lever is rolling out with more than one bot
  and keeping the best, since the default marshal rollout hides whole maps from
  the search. After that: wider beams, throwaway re-rolls in honest mode, and
  candidates that aren't some bot's idea (the maps where people still win
  sooner suggest these exist).
- **Cost.** Anything with knower as a rival wants its own time budget.

## A gap this exposes in `verify_scores`

`tools/verify_scores.py` checks that a log replays consistently. It does not
check that the dice came from the seed, or that the bots' orders are what those
bots would play. A lucky line from this tool, or any hand-made log, therefore
verifies. This is recorded in `docs/design/leaderboard.md`, "Checked scores",
and is not fixed here.

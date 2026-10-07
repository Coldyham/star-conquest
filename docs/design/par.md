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

  **That was the game until 2026-10-07.** `replay.reseed` (PR #104, reasons
  in `docs/design/core.md`, "Persistence, replay & history") now seeds each live
  turn from `(seed, turn)`.
  A rewound turn ended with the same orders rolls the same dice, and only
  changing that turn's orders shifts them. Every reading below was taken under
  the old rule.

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
  any play. **It is not a ceiling, even against marshal and actuary.** Fights
  between two rivals still draw from the turn's stream, and in lucky mode the
  searcher's own fights stop consuming it, so those rival fights fall
  differently. The best roll in
  each fight is also not always the best for the game, since a different
  survivor count changes what every bot does next. Measured, honest dice found a
  sooner win than lucky in 2 of 18 paired runs (see "Readings").
- **`honest`**: every simulated turn is seeded as the game seeds it
  (`replay.reseed` inside `par_search.step`), so the dice and tie-breaks are
  the ones a player would meet with the same orders. Rewinding offers nothing
  more, so this is the **achievable** number. Only a different order set rolls
  differently, and the search's candidates are its only fishing. Until
  2026-10-07 honest mode carried the rng and also tried each turn from the
  stream a rewind left (`--resets`, now removed), which modelled the old
  re-roll rule.

## The search

It is a beam search over turns:
- **Candidates for the human's orders**: each roster bot's orders for the
  seat, decided on a private copy with an rng of its own; the same orders sent
  all-in; holding; and the first bot's orders with one strike dropped.
- **Scoring**: each child is ranked by a rollout to the end with `--policy`
  (marshal) in the seat.
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

## Readings (2026-10-07)

> **Possibly stale.** This section was measured before `replay.reseed`, with
> honest mode carrying the rng and trying rewinds. Honest numbers would now
> differ (no rewind re-rolls), and lucky numbers can move too, since rival
> tie-breaks and rival fights roll from different streams. Not recomputed.

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
  proposes. A real score therefore usually has a lot of room in it.
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

> **Possibly stale.** This section was measured before `replay.reseed`, with
> honest mode carrying the rng and trying rewinds. Honest numbers would now
> differ (no rewind re-rolls), and lucky numbers can move too, since rival
> tie-breaks and rival fights roll from different streams. Not recomputed.

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
  yardstick when checking one.
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
16/10 was set under the old re-roll rule.

## Not on the board (decided 2026-10-07)

Publishing a par or a floor per map was considered and decided against. The
tool stays as something to run locally against a map or a score
(`--game-key`, `--log`). The bot column remains the only computed number the
board shows.

## Next, if it goes further

- **Retake the readings** under the derived rng. Honest mode now seeds turns
  as the game does, and the tables above predate that.
- **Search quality.** The first lever is rolling out with more than one bot
  and keeping the best, since the default marshal rollout hides whole maps from
  the search. After that: wider beams, throwaway re-rolls in honest mode, and
  candidates that aren't some bot's idea (the maps where people still win
  sooner suggest these exist).
- **Cost.** Anything with knower as a rival wants its own time budget.

## A gap this exposes in `verify_scores`

`tools/verify_scores.py` checks that a log replays consistently. It does not
check that the dice came from the seed, or that the bots' orders are what those
bots would play. Rechecked on 2026-10-07, after `replay.reseed`, on a 12-node
3-player map against heuristics: **both halves are still open.**

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

Rolling combat from a stream of its own, seeded from `(seed, turn)` and handed
to `end_turn` only by the shell, would remove the first limit: the dice would
then follow exactly from the fights in order, with nothing to fish among. That
needs `main.resolve_turn` and the verifier to agree on it, and a marker in the
log, and is not built. The bot-orders half stays open by design. Also recorded
in `docs/design/leaderboard.md`, "Checked scores".

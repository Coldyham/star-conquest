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
  Against marshal and actuary, which draw nothing, this is fully deterministic
  and an **upper bound** on what rewind-fishing can reach. Against bots that
  draw, the rivals' tie-breaks and their fights with each other still come from
  the stream, so the result is one sample, not a bound. Don't call it a ceiling.
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

## First readings (2026-10-06, two runs only)

Both runs used lucky dice and beam width 4-6:

| Setup | Floor | Best found | Bots alone (same dice) | Time |
| --- | --- | --- | --- | --- |
| random, 8 nodes, 2 players, seed 3, vs heuristic | 17 | 28 (lost 3) | actuary 30/4; marshal and thinker stalemate at 400 | <1 s |
| random, 13 nodes, 3 players, seed 1, vs marshal + actuary | 24 | 48 (lost 8) | marshal 48/11, actuary 69/17 | 12 s |

What they suggest, nothing more yet:

- Small maps against cheap rivals are fast. The search finished its beam well
  inside budget on both.
- On 13 nodes the search matched marshal's turn and only shaved losses. The
  candidate set probably can't express a faster plan: every candidate is some
  bot's idea. Wider beams, more mutations, or a planner of its own are the next
  things to try.
- The floor sits about half the best found on both maps. It is useful as a
  floor, too loose to prune much (46 of 1902 children on 13 nodes).
- Aside: marshal and thinker stalemate on the 8-node 2-player map against
  heuristic in the plain harness too (`sim.play_settings`), so this is not a
  search artefact. It could be worth a look on its own.

## Not yet run

- The 18-node cell, the honest mode, the thinker + heuristic cell, and
  comparisons against real human wins (`--log`).
- Wider beams and longer budgets.

## A gap this exposes in `verify_scores`

`tools/verify_scores.py` checks that a log replays consistently. It does not
check that the dice came from the seed, or that the bots' orders are what those
bots would play. A lucky line from this tool, or any hand-made log, therefore
verifies. This is recorded in `docs/design/leaderboard.md`, "Checked scores",
and is not fixed here.

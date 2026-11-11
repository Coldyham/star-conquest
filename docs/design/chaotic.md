# chaotic: a different bot every turn

`models/chaotic.py`. Proposed 2026-11-11 as "a bot option which is really
random": each turn the seat hands its whole decision to one entry of
`chaotic.POOL`, picked afresh, so there is no single style to read off its
opening, yet every turn's moves are a real bot's.

## How it plays

- **The pool** is every rule bot stop that neither predicts nor remembers:
  actuary Greedy, Field and Hunt; marshal; evolved Off and On; thinker
  Claudebot and Thinker; and the weak baselines heuristic and rusherplus, in
  on purpose (2026-11-11): a wider spread of moves is harder to read, and it
  cuts actuary's share of turns from 3 in 8 to 3 in 10. Left out: knower (an
  oracle) and actuary Learning (it remembers between turns, and would see only
  the turns it was dealt).
- **The point is to be unreadable, not strong.** marshal is the one pool bot
  that staggers a pincer: it launches the far wave and re-derives the near one
  next turn, which fires only if marshal is dealt again (1 turn in 10). So most
  of chaotic's pincers are a lone far wave, an unplanned feint against a player
  who knows marshal. Holding the bot while its own strike is in flight would
  finish them, at the cost of some predictability; not built.
- **The pick is derived, never drawn**: `random.Random(f"{seed}:chaotic:{turn}:{pid}")`
  off `state.seed` and `state.turn`, the construction `botio.decide_seed` and
  the random seat use. Nothing comes from `state.rng`, so a chaotic seat moves
  no other seat's dice and a seed replays it exactly. The entry's own
  tie-breaks still draw from `state.rng`, as they would on their own seat.
- **The entry plays as itself at its stop**: for the call the seat is shown
  under the entry's name and `aux`, then put back (as `ai.strategy_fn` does a
  legacy name). chaotic has no knob; the seat's own `aux` is never read.
- **`POOL` is positional**: editing it changes what every chaotic seat plays
  from then on. A stored game never moves (a replay asks no seat to decide).

## Why this is not the dispatcher the random seat refused

`core.md`, "A seat left to the seed", rejects a `models/` dispatcher for the
random seat because it cannot answer knower's `is_oracle_seat(player)`: the
bot it forwards to depends on a seed a `Player` does not carry. chaotic's
answer is the same for every seat and turn: nothing in the pool predicts or
remembers, so `False` is always true and knower runs `chaotic.decide`
directly. Its clone keeps `seed` and `turn`, so it picks exactly what the real
seat picks (`tests/test_chaotic.py`). `decide_ms` is the pool's mean, so
knower's slow-setup warning prices a chaotic seat.

## Where it stands

Not in `ai.LADDER_ORDER` (it sorts after the ladder in the dropdown) and not
in any `settings.RANDOM_POOLS`. Default boards, both seatings, 40 seeds,
against marshal / thinker: 21% / 91% with the eight-entry pool, 12% / 81% once
heuristic and rusherplus joined (6/6 against heuristic either way,
`tools/check_bot.py`). Still between marshal and thinker.

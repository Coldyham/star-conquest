# chaotic: a different bot every turn

`models/chaotic.py`. Proposed 2026-11-11 as "a bot option which is really
random": each turn the seat hands its whole decision to one entry of
`chaotic.POOL`, picked afresh, so there is no single style to read off its
opening, yet every turn's moves are a real bot's and so reasonable.

## How it plays

- **The pool** is every rule bot stop that neither predicts nor remembers:
  actuary Greedy, Field and Hunt; marshal; evolved Off and On; thinker
  Claudebot and Thinker. Left out: knower (an oracle), actuary Learning (it
  remembers between turns, and would see only the turns it was dealt), and the
  weak baselines heuristic and rusherplus (their turns would not all be
  reasonable).
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
in any `settings.RANDOM_POOLS`. First reading, 12-node default boards, both
seatings, 40 seeds: 21% against marshal, 91% against thinker, 6/6 against
heuristic (`tools/check_bot.py`). Somewhere between marshal and thinker, as
an average of its pool would suggest.

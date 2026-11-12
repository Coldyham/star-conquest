"""Chaotic: a different bot every turn.

Each turn this seat hands its whole decision to one entry of `POOL`, picked
afresh, so its play has no single style to learn: an opening read as marshal's
is followed by a turn of actuary's and one of thinker's. Every entry is a
rule bot that plays sensibly on its own, so each turn's moves are reasonable
even though the plan behind them changes.

**The pick is derived, never drawn**: ``random.Random(f"{seed}:chaotic:{turn}:
{pid}")``, the construction `botio.decide_seed` and the random seat use, off
``state.seed`` and ``state.turn``. Nothing is taken from ``state.rng``, so a
chaotic seat moves no other seat's dice, and a seed replays it exactly.

**Why this is not the dispatcher the random seat refused**
(`docs/design/core.md`, "A seat left to the seed"). That dispatcher could not
answer knower's ``is_oracle_seat(player)``, because the bot it would forward
to depended on a seed a `Player` does not carry. Here the answer is the same
for every seat and every turn: the pool holds no oracle and no bot that
remembers between turns (knower, actuary's Learning stop), so ``False`` is
always true, and knower may run this module directly. It then picks exactly
what the real seat picks, since the clone it decides on keeps the seed and
the turn.

The entry plays the seat as itself at its stop: for the length of the call the
seat is shown under the entry's name with its ``aux``, and put back after, as
`ai.strategy_fn` does for a legacy name. The seat's own ``aux`` is never read.
"""

from __future__ import annotations

import random
from dataclasses import replace

from starconquest import ai, config

# Fixed, in order: the pick indexes it by position, so editing it changes what
# every chaotic seat plays from then on (a stored game never moves: a replay
# asks no seat to decide). `name:stop`, as in `settings.RANDOM_POOLS`. Left out:
# knower (an oracle), actuary:2 (Learning remembers between turns, and would
# see only the turns it was dealt). The weak baselines heuristic and
# rusherplus are in on purpose: a wider spread of moves is harder to read.
POOL: tuple[str, ...] = (
    "actuary:0", "actuary:1", "actuary:3",
    "marshal",
    "evolved:0", "evolved:1",
    "thinker:0", "thinker:1",
    "heuristic", "rusherplus",
)

IS_ORACLE = False


def pick(seed: int, turn: int, pid: int) -> tuple[str, float | None]:
    """``(strategy, aux)`` this seat plays on ``turn``; aux None keeps the
    entry's default."""
    entry = POOL[random.Random(f"{seed}:chaotic:{turn}:{pid}").randrange(len(POOL))]
    name, _, stop = entry.partition(":")
    return name, float(stop) if stop else None


def decide(state, pid):
    name, aux = pick(state.seed, state.turn, pid)
    fn = ai.STRATEGIES.get(name)          # read here: models/ load in name order
    if fn is None or fn is decide:
        return ai.compute_orders(state, pid)
    player = state.players[pid]
    held = player.ai_strategy, player.ai_params
    player.ai_strategy = name
    player.ai_params = replace(held[1], aux=config.AI_AUX if aux is None else aux)
    try:
        return fn(state, pid)
    finally:
        player.ai_strategy, player.ai_params = held


def is_oracle_seat(player) -> bool:
    """Never: nothing in `POOL` predicts or remembers, so an oracle may run us."""
    return False


class _AtStop:
    """``settings`` with one seat's params shown at a pool entry's stop."""

    def __init__(self, settings, seat, aux):
        self._settings, self._seat, self._aux = settings, seat, aux

    def __getattr__(self, attr):
        return getattr(self._settings, attr)

    def seat_params(self, seat):
        params = self._settings.seat_params(seat)
        if seat != self._seat:
            return params
        return replace(params, aux=config.AI_AUX if self._aux is None else self._aux)


def decide_ms(settings, seat) -> float:
    """Typical CPU ms of one decide (`ai.decide_ms`): the mean over `POOL`,
    since every entry is equally likely on any turn."""
    total = 0.0
    for entry in POOL:
        name, _, stop = entry.partition(":")
        total += ai.decide_ms(name, _AtStop(settings, seat, float(stop) if stop else None), seat)
    return total / len(POOL)

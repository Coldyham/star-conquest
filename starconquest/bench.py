"""How long one seat's bot takes to decide, measured in a real game.

One function, run the same way natively, in Pyodide under Node and in the
browser's worker (`tools/spike_bench.py`), so the three runtimes are compared
on identical work: the same setup, seed and turns, with every other seat
deciding as usual and only the measured seat's ``decide`` calls timed.
"""

from __future__ import annotations

import time

from starconquest import ai, engine, replay
from starconquest.model import AiParams
from starconquest.settings import Settings, build_state


def decide_times(bot: str, aux: float, nodes: int, seed: int, turns: int,
                 rivals: tuple[str, ...] = ("marshal", "heuristic")) -> dict:
    """Play ``turns`` turns of ``bot`` (at stop ``aux``) against ``rivals`` on a
    random map of ``nodes`` systems, timing seat 1's decisions in ms. Answers the
    times and the final board's digest, which must match across runtimes when
    every bot's wall-clock guard is lifted (`ai.set_budget_scale`)."""
    settings = Settings(players=1 + len(rivals), nodes=nodes, seed=seed, autoplay=True)
    for i, name in enumerate((bot, *rivals)):
        settings.ai_strategy[i] = name
        settings.ai[i] = AiParams(aux=float(aux) if i == 0 else 0.0)
    state = build_state(settings, seed)
    times: list[float] = []

    def timed(s, pid):
        if pid != 1:
            return ai.decide(s, pid)
        t0 = time.perf_counter()
        orders = ai.decide(s, pid)
        times.append((time.perf_counter() - t0) * 1000)
        return orders

    for _ in range(turns):
        if state.winner is not None:
            break
        replay.reseed(state)
        engine.end_turn(state, decide=timed)
    return {"ms": [round(t, 3) for t in times], "turns": state.turn, "digest": replay.digest_hex(state)}

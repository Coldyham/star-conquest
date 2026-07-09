"""The AI strategy registry/dispatcher and per-seat params.

Pure/headless (no pygame). Guards the custom-AI seam: `ai.decide` routes each
seat to its named strategy, and per-seat `AiParams` actually change behaviour.
"""

from __future__ import annotations

from starconquest import ai, mapgen
from starconquest.model import AiParams, Order


def test_decide_routes_to_named_strategy():
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    sentinel = [Order(2, 0, 1, 1)]
    ai.register("dummy_test", lambda st, pid: list(sentinel))
    try:
        state.players[2].ai_strategy = "dummy_test"
        assert ai.decide(state, 2) == sentinel        # routed to the custom fn
        assert ai.decide(state, 3) == ai.compute_orders(state, 3)  # default heuristic
    finally:
        ai.STRATEGIES.pop("dummy_test", None)


def test_unknown_strategy_falls_back_to_heuristic():
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    state.players[2].ai_strategy = "nope"
    assert ai.decide(state, 2) == ai.compute_orders(state, 2)   # no crash, falls back


def test_per_seat_params_change_behaviour():
    state = mapgen.generate_random(3, num_nodes=18, num_players=3)
    # A hoarder that reserves nearly everything issues no orders...
    state.players[2].ai_params = AiParams(reserve_fraction=0.99, reserve_floor=999)
    hoarder = ai.compute_orders(state, 2)
    # ...while an all-in seat commits its surplus.
    state.players[2].ai_params = AiParams(reserve_fraction=0.0, reserve_floor=0)
    aggressive = ai.compute_orders(state, 2)
    assert hoarder == []
    assert len(aggressive) > len(hoarder)

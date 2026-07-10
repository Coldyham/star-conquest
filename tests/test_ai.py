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


# --------------------------------------------------------------------------- #
# Drop-in model loading
# --------------------------------------------------------------------------- #
def test_load_models_registers_and_routes(tmp_path):
    (tmp_path / "sentinel_ai.py").write_text(
        "from starconquest.model import Order\n"
        "def decide(state, pid):\n"
        "    return [Order(pid, 0, 1, 1)]\n"
    )
    try:
        loaded = ai.load_models(tmp_path)
        assert loaded == ["sentinel_ai"]
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        state.players[2].ai_strategy = "sentinel_ai"
        assert ai.decide(state, 2) == [Order(2, 0, 1, 1)]
    finally:
        ai.STRATEGIES.pop("sentinel_ai", None)


def test_load_models_skips_broken_and_underscore(tmp_path):
    (tmp_path / "broken.py").write_text("this is not python !!!\n")
    (tmp_path / "nodecide.py").write_text("x = 1\n")
    (tmp_path / "_private.py").write_text("def decide(s, p): return []\n")
    try:
        loaded = ai.load_models(tmp_path)
        assert loaded == []                       # nothing valid registered, no crash
        for name in ("broken", "nodecide", "_private"):
            assert name not in ai.STRATEGIES
    finally:
        for name in ("broken", "nodecide", "_private"):
            ai.STRATEGIES.pop(name, None)


def test_load_models_missing_dir_is_empty(tmp_path):
    assert ai.load_models(tmp_path / "does_not_exist") == []


def test_available_strategies_lists_heuristic_first():
    ai.register("zzz_test", lambda st, pid: [])
    try:
        names = ai.available_strategies()
        assert names[0] == "heuristic"
        assert "zzz_test" in names
        assert names[1:] == sorted(names[1:])     # the rest are sorted
    finally:
        ai.STRATEGIES.pop("zzz_test", None)

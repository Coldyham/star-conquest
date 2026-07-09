"""Settings -> GameState wiring: seed resolution, sizing, and determinism.

Pure/headless (settings.py imports no pygame), so this needs no display.
"""

from __future__ import annotations

from starconquest import config
from starconquest.settings import Settings, build_state, resolve_seed


def test_resolve_seed_uses_explicit():
    assert resolve_seed(Settings(seed=42)) == 42


def test_resolve_seed_random_when_none():
    v = resolve_seed(Settings(seed=None))
    assert isinstance(v, int) and 0 <= v < 1_000_000


def test_from_args_maps_fields():
    class Args:
        mode, players, nodes, seed, autoplay = "symmetric", 4, 22, 9, True

    s = Settings.from_args(Args())
    assert (s.mode, s.players, s.nodes, s.seed, s.autoplay) == ("symmetric", 4, 22, 9, True)


def test_build_state_respects_settings():
    s = Settings(mode="random", players=4, nodes=20, seed=7)
    state = build_state(s, resolve_seed(s))
    assert state.mode == "random"
    assert set(state.players) == {0, 1, 2, 3, 4}   # neutral + four seats
    assert len(state.systems) == 20


def test_build_state_deterministic_for_same_seed():
    s = Settings(mode="random", players=3, nodes=18)
    a = build_state(s, 123)
    b = build_state(s, 123)
    assert {i: sys.pos for i, sys in a.systems.items()} == {i: sys.pos for i, sys in b.systems.items()}
    assert a.lanes.keys() == b.lanes.keys()


def test_build_state_symmetric_mode():
    state = build_state(Settings(mode="symmetric", players=3, nodes=19), 5)
    assert state.mode == "symmetric"
    assert len([s for s in state.systems.values() if s.owner_id != 0]) == 3


def test_min_nodes_tracks_players():
    assert Settings(players=config.MIN_PLAYERS).min_nodes() == config.MIN_PLAYERS + 3

"""Settings -> GameState wiring: seed resolution, sizing, and determinism.

Pure/headless (settings.py imports no pygame), so this needs no display.
"""

from __future__ import annotations

import contextlib

from starconquest import config
from starconquest.model import AiParams
from starconquest.settings import _GLOBAL_KNOBS, Settings, build_state, resolve_seed


@contextlib.contextmanager
def _preserve_config():
    """Snapshot & restore the menu-managed config knobs (build_state mutates them)."""
    saved = {const: getattr(config, const) for _, const in _GLOBAL_KNOBS}
    try:
        yield
    finally:
        for const, val in saved.items():
            setattr(config, const, val)


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


def test_build_state_applies_global_knobs():
    with _preserve_config():
        s = Settings(players=3, nodes=18, home_start_ships=30, combat_jitter=0.33,
                     node_jitter=0.1)
        build_state(s, 5)
        assert config.HOME_START_SHIPS == 30
        assert config.COMBAT_JITTER == 0.33
        assert config.NODE_JITTER == 0.1


def test_build_state_assigns_per_seat_ai_params():
    with _preserve_config():
        s = Settings(players=3, nodes=18)
        s.ai[1] = AiParams(reserve_fraction=0.5, attack_margin=2.4)  # seat 2
        state = build_state(s, 5)
        assert state.players[2].ai_params.reserve_fraction == 0.5
        assert state.players[2].ai_params.attack_margin == 2.4
        # it's a copy — editing settings afterwards doesn't reach into the game
        s.ai[1].reserve_fraction = 0.9
        assert state.players[2].ai_params.reserve_fraction == 0.5


def test_node_jitter_changes_the_map():
    with _preserve_config():
        grid = build_state(Settings(players=3, nodes=18, node_jitter=0.0), 7)
        jittered = build_state(Settings(players=3, nodes=18, node_jitter=1.0), 7)
        pos_a = {i: s.pos for i, s in grid.systems.items()}
        pos_b = {i: s.pos for i, s in jittered.systems.items()}
        assert pos_a != pos_b

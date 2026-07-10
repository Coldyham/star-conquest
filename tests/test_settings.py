"""Settings -> GameState wiring: seed resolution, sizing, and determinism.

Pure/headless (settings.py imports no pygame), so this needs no display.
"""

from __future__ import annotations

import contextlib

import pytest

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


# --------------------------------------------------------------------------- #
# save / load
# --------------------------------------------------------------------------- #
def _customised() -> Settings:
    s = Settings(mode="symmetric", players=5, nodes=30, seed=42, autoplay=True,
                 home_start_ships=25, combat_jitter=0.3, ship_ly_per_turn=9.5)
    s.ai[0] = AiParams(reserve_fraction=0.5, reserve_floor=4, attack_margin=2.6)
    s.ai[3] = AiParams(expand_margin=2.1, reinforce_margin=5)
    s.ai_strategy[1] = "rusher"
    s.ai_strategy[2] = "turtle"
    return s


def test_to_from_dict_round_trip_defaults():
    s = Settings.defaults()
    assert Settings.from_dict(s.to_dict()) == s


def test_to_from_dict_round_trip_customised():
    s = _customised()
    assert Settings.from_dict(s.to_dict()) == s


def test_save_load_round_trip(tmp_path):
    s = _customised()
    path = tmp_path / "cfg.json"
    s.save(path)
    assert path.exists()
    assert Settings.load(path) == s


def test_from_dict_ignores_unknown_and_fills_missing():
    loaded = Settings.from_dict({"players": 4, "junk": "ignored"})
    assert loaded.players == 4
    assert loaded.nodes == Settings().nodes            # missing -> default
    assert not hasattr(loaded, "junk")


def test_from_dict_clamps_structural_fields():
    loaded = Settings.from_dict({"players": 99, "nodes": 2, "mode": "bogus"})
    assert loaded.players == config.MAX_PLAYERS
    assert loaded.nodes == loaded.min_nodes()          # floored by player count
    assert loaded.mode == "random"


def test_from_dict_normalises_ai_list_length():
    assert len(Settings.from_dict({"ai": []}).ai) == config.MAX_PLAYERS
    over = [{"attack_margin": 2.0} for _ in range(config.MAX_PLAYERS + 3)]
    assert len(Settings.from_dict({"ai": over}).ai) == config.MAX_PLAYERS


def test_from_dict_normalises_ai_strategy_list():
    # coerced to str, padded to MAX_PLAYERS, truncated if too long
    loaded = Settings.from_dict({"ai_strategy": [1, "rusher"]})
    assert loaded.ai_strategy[:3] == ["1", "rusher", "heuristic"]
    assert len(loaded.ai_strategy) == config.MAX_PLAYERS
    over = ["x"] * (config.MAX_PLAYERS + 3)
    assert len(Settings.from_dict({"ai_strategy": over}).ai_strategy) == config.MAX_PLAYERS
    # a non-list is ignored -> all default
    assert Settings.from_dict({"ai_strategy": "nope"}).ai_strategy == ["heuristic"] * config.MAX_PLAYERS


def test_from_dict_coerces_scalar_types():
    loaded = Settings.from_dict({"players": "4", "combat_jitter": 1})
    assert loaded.players == 4
    assert loaded.combat_jitter == 1.0 and isinstance(loaded.combat_jitter, float)


def test_load_raises_on_garbage(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json {{{")
    with pytest.raises(ValueError):          # JSONDecodeError subclasses ValueError
        Settings.load(path)


def test_copy_from_mutates_in_place_without_aliasing():
    target = Settings()
    source = _customised()
    target.copy_from(source)
    assert target == source
    # ai is deep-copied: mutating the source afterwards must not touch target
    source.ai[0].attack_margin = 9.9
    assert target.ai[0].attack_margin == _customised().ai[0].attack_margin
    # ai_strategy is copied too (not aliased)
    source.ai_strategy[1] = "changed"
    assert target.ai_strategy[1] == "rusher"


def test_build_state_stamps_per_seat_strategy():
    with _preserve_config():
        s = Settings(players=3, nodes=18)
        s.ai_strategy[1] = "rusher"                    # seat 2
        state = build_state(s, 5)
        assert state.players[2].ai_strategy == "rusher"
        assert state.players[3].ai_strategy == "heuristic"

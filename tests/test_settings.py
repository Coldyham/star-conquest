"""Settings -> GameState wiring: seed resolution, sizing, and determinism.

Pure/headless (settings.py imports no pygame), so this needs no display.
"""

from __future__ import annotations

import contextlib

import pytest

from starconquest import config
from starconquest.model import AiParams
from starconquest.settings import (_GLOBAL_KNOBS, _LEGACY_KEY_DROPS, Challenge,
                                   Settings, _hash_setup, build_state,
                                   random_seed, resolve_seed)


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


def test_to_from_token_round_trip_defaults():
    s = Settings.defaults()
    assert Settings.from_token(s.to_token()) == s


def test_to_from_token_round_trip_customised():
    s = _customised()
    assert Settings.from_token(s.to_token()) == s


def test_token_is_url_fragment_safe():
    token = _customised().to_token()
    assert not (set(token) & set("+/="))


def test_from_token_raises_on_garbage():
    with pytest.raises(ValueError):
        Settings.from_token("not a valid token !!!")


# --- token pruning ----------------------------------------------------------- #
# `to_token` omits anything the reader would infer, so the property that matters
# is that a round trip is still *exact* — a dropped field must never change a game.
def test_pruned_token_omits_defaults_but_keeps_identity():
    pruned = Settings.defaults().token_dict()
    assert set(pruned) == {"mode", "players", "nodes", "seed"}


def test_pruning_shrinks_the_default_token_a_lot():
    assert len(Settings.defaults().to_token()) < 200   # was ~1470 unpruned


def test_pruned_token_keeps_params_of_a_non_heuristic_seat():
    """`ai_params` is documented as readable by drop-in bots, so a custom seat's
    params are load-bearing and must survive even though its strategy isn't the
    built-in heuristic."""
    s = Settings(players=3)
    s.ai_strategy[1] = "thinker"
    s.ai[1] = AiParams(attack_margin=1.9)
    assert Settings.from_token(s.to_token()).ai[1].attack_margin == 1.9


def test_pruned_token_trims_unused_seats():
    s = Settings(players=2)
    s.ai_strategy[4] = "thinker"        # seat 5 isn't in a 2-player game
    assert "ai_strategy" not in s.token_dict()


def test_legacy_uncompressed_token_still_loads():
    """Links shared before the token was deflated must keep working."""
    import base64
    import json

    s = _customised()
    raw = json.dumps(s.to_dict(), separators=(",", ":")).encode("utf-8")
    legacy = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    assert Settings.from_token(legacy) == s


# --- challenges -------------------------------------------------------------- #
def _challenged() -> Settings:
    s = _customised()
    s.challenge = Challenge(turns=137, lost=412, hand=119, by="Name")
    s.challenge.key = s.challenge_key()
    return s


def test_challenge_round_trips_through_dict_and_token():
    s = _challenged()
    assert Settings.from_dict(s.to_dict()) == s
    assert Settings.from_token(s.to_token()) == s


def test_challenge_absent_or_scoreless_reads_as_none():
    assert Settings.from_dict({}).challenge is None
    assert Settings.from_dict({"challenge": "nonsense"}).challenge is None
    assert Settings.from_dict({"challenge": {"turns": 0}}).challenge is None


def test_copy_from_carries_the_challenge():
    target = Settings.defaults()
    target.copy_from(_challenged())
    assert target.challenge == _challenged().challenge
    target.challenge.turns = 1          # must be a copy, not the same object
    assert _challenged().challenge.turns == 137


def test_challenge_key_ignores_the_attached_score_and_autoplay():
    plain = Settings(seed=7)
    scored = Settings(seed=7)
    scored.challenge = Challenge(turns=99)
    scored.autoplay = True
    assert plain.challenge_key() == scored.challenge_key()


def test_challenge_key_changes_with_the_setup():
    assert Settings(seed=7).challenge_key() != Settings(seed=8).challenge_key()
    a, b = Settings(players=3), Settings(players=3)
    b.ai_strategy[1] = "thinker"
    assert a.challenge_key() != b.challenge_key()


def test_challenge_matches_until_the_config_is_edited():
    s = _challenged()
    assert s.challenge.matches(s)
    s.nodes += 1
    assert not s.challenge.matches(s)


def test_without_challenge_strips_the_score_only():
    s = _challenged()
    plain = s.without_challenge()
    assert plain.challenge is None
    assert s.challenge is not None                    # the original is untouched
    assert plain.challenge_key() == s.challenge_key()  # same setup, no target
    assert Settings.from_token(plain.to_token()).challenge is None


def test_challenge_without_a_key_is_taken_on_trust():
    s = _customised()
    s.challenge = Challenge(turns=10)      # hand-written: no key stamped
    assert s.challenge.matches(s)


def test_challenge_key_is_stable():
    """Pins the digest of the default setup.

    A field joining `Settings` moves this, which is the point: restore it by
    appending the new field to `settings._LEGACY_KEY_DROPS` (cumulatively — the
    entry below it lacked its fields too) and updating the literal here, so links
    already in circulation keep resolving to the setup they describe.
    """
    assert Settings().challenge_key() == "a61a1888857255e8"


def test_challenge_keys_lead_with_the_canonical_one():
    s = _customised()
    keys = s.challenge_keys()
    assert keys[0] == s.challenge_key()
    assert len(set(keys)) == len(keys) == 1 + len(_LEGACY_KEY_DROPS)


def test_a_legacy_key_is_dropped_once_its_own_field_is_moved():
    """The dropped field is the one a legacy digest cannot see, so a setup that
    moved it is not one that version could have stamped."""
    field = _LEGACY_KEY_DROPS[0][0]
    s = _customised()
    assert len(s.challenge_keys()) == 1 + len(_LEGACY_KEY_DROPS)

    setattr(s, field, getattr(Settings(), field) + 0.2)
    assert s.challenge_keys() == (s.challenge_key(),)


def test_a_key_stamped_before_a_field_existed_still_matches():
    s = _customised()
    older = s.to_dict()
    for skip in ("challenge", "autoplay", *_LEGACY_KEY_DROPS[0]):
        older.pop(skip, None)
    s.challenge = Challenge(turns=10, key=_hash_setup(older))

    assert s.challenge.key != s.challenge_key()   # a different digest, same setup
    assert s.challenge.matches(s)
    s.nodes += 1                                  # and still detects a real edit
    assert not s.challenge.matches(s)


def test_an_old_link_still_detects_an_edit_to_the_field_it_predates():
    field = _LEGACY_KEY_DROPS[0][0]
    s = _customised()
    older = s.to_dict()
    for skip in ("challenge", "autoplay", *_LEGACY_KEY_DROPS[0]):
        older.pop(skip, None)
    s.challenge = Challenge(turns=10, key=_hash_setup(older))
    assert s.challenge.matches(s)

    setattr(s, field, getattr(Settings(), field) + 0.2)
    assert not s.challenge.matches(s)


def test_a_pre_defender_advantage_challenge_link_still_matches():
    """The concrete case `_LEGACY_KEY_DROPS` exists for: a real link shared before
    the defender-advantage slider landed, against the same setup shared after."""
    token = ("eNpNjkEOgyAQRe_y12ysVCxXaZoGZRQiQgO4MMa7d0y66O7N_Mz7c2BNlqCRTbRphcAn"
             "mJ1ygW4FImdMTS9QiCx0rx7qzpPx71KzqTTv0E842rIv1Y98X52PC-U_egmMzoRAceam"
             "A3XL8bI23BBSqaztBBw_8FsOLAULFrqgJTVI2faSpsmq4Ybz_AKt1Tg9")
    s = Settings.from_token(token)
    assert s.challenge is not None and s.challenge.matches(s)
    assert s.challenge.key != s.challenge_key()


def test_build_state_ignores_the_challenge():
    with _preserve_config():
        plain = build_state(Settings(seed=5, nodes=14, players=2), 5)
        challenged = Settings(seed=5, nodes=14, players=2)
        challenged.challenge = Challenge(turns=1, lost=1)
        scored = build_state(challenged, 5)
    assert plain.systems.keys() == scored.systems.keys()
    assert all(plain.systems[i].owner_id == scored.systems[i].owner_id
               for i in plain.systems)


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


def test_from_dict_clamps_defender_advantage_to_a_winnable_range():
    """Alone among the balance knobs this one is clamped on load: above the
    ceiling the map cannot be conquered at all, so a hand-edited or pre-cap file
    must not open into an unwinnable game."""
    assert Settings.from_dict({"defender_advantage": 5.0}).defender_advantage == config.DEFENDER_ADVANTAGE_MAX
    assert Settings.from_dict({"defender_advantage": -3.0}).defender_advantage == 0.0
    assert Settings.from_dict({"defender_advantage": 1.25}).defender_advantage == 1.25  # in range, untouched


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


def test_random_seed_is_in_range_and_not_a_fixed_sequence():
    """Rolled seeds must differ *within* a run even if the clock is coarse (the
    browser blunts it), which is what a repeated roll on the web build hit."""
    seeds = [random_seed() for _ in range(50)]
    assert all(0 <= s < config.SEED_MAX for s in seeds)
    assert len(set(seeds)) > 45           # collisions vanishingly unlikely


def test_random_seed_ignores_the_global_random_state():
    """The web build can boot `random` from the same state every page load, so a
    fresh seed must not come off it — reseeding must not reproduce the roll."""
    import random as _random

    _random.seed(1234)
    first = [random_seed() for _ in range(5)]
    _random.seed(1234)
    assert [random_seed() for _ in range(5)] != first

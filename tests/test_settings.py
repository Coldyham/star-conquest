"""Settings -> GameState wiring: seed resolution, sizing, and determinism.

Pure/headless (settings.py imports no pygame), so this needs no display.
"""

from __future__ import annotations

import contextlib

import pytest

from starconquest import config
from starconquest.custommap import CustomMap, MapNode
from starconquest.model import AiParams
from starconquest.settings import (_GLOBAL_KNOBS, _LEGACY_KEY_DROPS, Challenge,
                                   RANDOM_STRATEGY, Settings, _hash_setup,
                                   build_state, random_seed, resolve_seed,
                                   resolve_strategy)


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
def _tiny_map() -> CustomMap:
    """The smallest playable hand map: two seats, one lane, well clear of the
    separation and graze rules."""
    return CustomMap(
        nodes=[MapNode(200, 200, 3, 10, 1), MapNode(600, 600, 4, 8, 2)],
        lanes=[(0, 1)],
    ).normalised()


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


def test_challenge_carries_the_id_of_its_replay():
    """`Challenge.log` names the uploaded game log behind the score, and has to
    survive the link — it is what tools/verify_scores.py keys on."""
    s = _challenged()
    s.challenge.log = "00112233445566ff"
    assert Settings.from_token(s.to_token()).challenge.log == "00112233445566ff"
    assert Settings.from_dict(s.to_dict()).challenge.log == "00112233445566ff"


def test_challenge_log_does_not_move_the_setup_digest():
    """The reason the id rides on Challenge rather than Settings: `challenge_keys`
    drops the whole field before hashing, so adding to it invalidates no link that
    was ever shared (unlike a new Settings field, which needs a
    `_LEGACY_KEY_DROPS` entry). `test_challenge_key_is_stable` pins the other half.
    """
    plain, logged = Settings(seed=7), Settings(seed=7)
    logged.challenge = Challenge(turns=10, log="abcdef0123456789")
    assert plain.challenge_keys() == logged.challenge_keys()


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


def test_challenge_key_ignores_seats_beyond_players():
    """A leftover seat past `players` (e.g. from a since-shrunk player count)
    never reaches the token (`token_dict` truncates `ai`/`ai_strategy` to
    `players`), so it must not move the key either — otherwise a shared link
    fails to match itself the moment it's decoded back (the seat's real value
    is gone; the recipient always pads with fresh defaults)."""
    dirty = Settings(players=3)
    dirty.ai[3] = AiParams(reserve_fraction=0.9)
    dirty.ai_strategy[4] = "rusher"
    clean = Settings(players=3)
    assert dirty.challenge_key() == clean.challenge_key()

    s = Settings(players=3)
    s.ai[3] = AiParams(reserve_fraction=0.9)
    s.challenge = Challenge(turns=10, key=s.challenge_key())
    reopened = Settings.from_token(s.to_token())
    assert reopened.challenge.matches(reopened)


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
    assert Settings().challenge_key() == "38c8b7ba470f6f4c"


def test_challenge_keys_lead_with_the_canonical_one():
    s = _customised()
    keys = s.challenge_keys()
    assert keys[0] == s.challenge_key()
    assert len(set(keys)) == len(keys) == 1 + len(_LEGACY_KEY_DROPS)


def _moved(field: str):
    """A value for ``field`` that differs from its default.

    Both tests below are written against *whichever* `_LEGACY_KEY_DROPS` entry is
    newest, so they cannot assume the field is a number — `custom_map` is a
    dataclass whose default is None, and `default + 0.2` raises on it.
    """
    if field == "custom_map":
        return _tiny_map()
    return getattr(Settings(), field) + 0.2


def test_a_legacy_key_is_dropped_once_its_own_field_is_moved():
    """The dropped field is the one a legacy digest cannot see, so a setup that
    moved it is not one that version could have stamped."""
    field = _LEGACY_KEY_DROPS[0][0]
    s = _customised()
    assert len(s.challenge_keys()) == 1 + len(_LEGACY_KEY_DROPS)

    setattr(s, field, _moved(field))
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

    setattr(s, field, _moved(field))
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


def test_an_int_aux_keeps_its_type_but_the_other_knobs_do_not():
    """`aux` is the one field whose int/float form is preserved on the way in —
    the menu stores an int for an `AUX_INT` strategy, and `challenge_key` hashes
    the JSON, where `12` and `12.0` are different setups."""
    loaded = Settings.from_dict({"ai": [{"aux": 12, "reserve_fraction": 0, "expand_margin": 2}]})
    assert isinstance(loaded.ai[0].aux, int)
    assert isinstance(loaded.ai[0].reserve_fraction, float)
    assert isinstance(loaded.ai[0].expand_margin, float)
    assert isinstance(Settings.from_dict({"ai": [{"aux": 1.5}]}).ai[0].aux, float)


def test_challenge_key_survives_a_round_trip_of_an_int_aux():
    """A digest stamped over a live `Settings` has to be one its own link
    recomputes, or the recipient's menu reads the setup as edited on open."""
    s = _customised()
    s.ai[4] = AiParams(aux=12)             # what the aux slider stores at AUX_INT
    s.ai_strategy[4] = "knower"
    assert Settings.from_dict(s.to_dict()).challenge_key() == s.challenge_key()
    assert Settings.from_token(s.to_token()).challenge_key() == s.challenge_key()


def test_a_challenge_link_with_an_int_aux_still_matches():
    """5 players, 33 nodes, seed 749187, a knower at search depth 12 — a real link
    whose key was stamped over the int the slider left in `aux`."""
    token = ("eNrlU1tuwyAQvMt-W5GdOC9fpaoQgcXQ2IAWnIci371LZEXpASpV6t_uLDsz2MMDxqAROiDpdRihgjjIO1KCbluB5xlXm00F"
             "CVFDt2-PzWFfAd4ySYG6R2FIquyCh65e7ficdVEMdxGRRJ6I4c1qu8ApMonoKVyzFVHlsrLmoQ0jikhBTwvTugIVxpPM4svl"
             "jFQO1ocKNBr0mpmlvkifZc_Om1XNFCb0IrneMmezdCpM3LF16aD7eABhQrr8NFzUX_gQAj218Rb5a4hRUu98USgkOUt1fsOe"
             "m86bQApfMC_L6fY0NVd_RrL-D5f8Ncn1_FkyJBJHPmN_5zCBxYlcyk7xg6EpWaQ4TImb9wEzJSsHrs4-XDnFzKMYGNCX4D6g"
             "vI9UFDjaQ0gluy3_K8uOF_TEalAIsBRNfTRGmUaZVrfrXQ3z_A36DT7h")
    s = Settings.from_token(token)
    assert s.challenge is not None and s.challenge.matches(s)
    s.nodes += 1
    assert not s.challenge.matches(s)


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


@contextlib.contextmanager
def _roster(*names):
    """Register throwaway strategies, so a pool test doesn't ride on models/."""
    from starconquest import ai
    saved = dict(ai.STRATEGIES)
    try:
        ai.STRATEGIES.clear()
        for name in names:
            ai.register(name, lambda state, pid: [])
        yield
    finally:
        ai.STRATEGIES.clear()
        ai.STRATEGIES.update(saved)


def test_a_random_seat_resolves_to_a_real_bot():
    """The whole point: the placeholder never reaches a live game, so every
    oracle, `botio`'s seat reveal and the bot column see the bot that is really
    deciding rather than a dispatcher they cannot see through."""
    with _preserve_config(), _roster("alpha", "beta"):
        s = Settings(players=3, nodes=18)
        s.ai_strategy[1] = RANDOM_STRATEGY
        state = build_state(s, 5)
        assert state.players[2].ai_strategy in ("alpha", "beta")
        assert s.ai_strategy[1] == RANDOM_STRATEGY, "the setup keeps the placeholder"


def test_a_random_seat_is_the_same_bot_every_time_that_seed_is_played():
    """A seed reproduces the opponents as surely as it reproduces the map — which
    is what lets a challenge link be raced fairly, and a match be resumed."""
    with _roster("alpha", "beta", "gamma", "delta"):
        picks = [resolve_strategy(RANDOM_STRATEGY, 7, 2) for _ in range(20)]
        assert len(set(picks)) == 1


def test_random_seats_are_drawn_independently_of_each_other():
    with _roster("alpha", "beta", "gamma", "delta"):
        # Over enough seeds, two seats must disagree at least sometimes; a shared
        # draw would make them identical on every one.
        pairs = [(resolve_strategy(RANDOM_STRATEGY, n, 2),
                  resolve_strategy(RANDOM_STRATEGY, n, 3)) for n in range(40)]
        assert any(a != b for a, b in pairs)


def test_resolving_a_random_seat_never_touches_the_engine_dice():
    """Derived, never drawn (`botio.decide_seed`'s rule). Leaving a seat to chance
    must not shift `state.rng`, or the same seed would fight the same map
    differently depending on how many seats were left to it."""
    with _preserve_config(), _roster("alpha", "beta", "gamma"):
        fixed = Settings(players=3, nodes=18)
        fixed.ai_strategy[1] = "alpha"
        chance = Settings(players=3, nodes=18)
        chance.ai_strategy[1] = RANDOM_STRATEGY
        rolls = []
        for cfg in (fixed, chance):
            state = build_state(cfg, 11)
            rolls.append([state.rng.random() for _ in range(8)])
        assert rolls[0] == rolls[1]


def test_a_named_strategy_passes_through_resolution_untouched():
    with _roster("alpha", "beta"):
        assert resolve_strategy("beta", 3, 2) == "beta"
        assert resolve_strategy("not_registered", 3, 2) == "not_registered"


def test_a_random_seat_falls_back_when_nothing_is_registered():
    """Same degradation `ai.decide` already applies to an unrecognised name,
    rather than an exception out of the one funnel to a GameState."""
    with _roster():
        assert resolve_strategy(RANDOM_STRATEGY, 3, 2) == "heuristic"


def test_the_pool_never_offers_the_placeholder_itself():
    """`random` names no decision function, so picking it would loop."""
    with _roster("alpha", RANDOM_STRATEGY):
        assert all(resolve_strategy(RANDOM_STRATEGY, n, 2) == "alpha"
                   for n in range(20))


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


# --------------------------------------------------------------------------- #
# Hand-authored maps riding along in the setup
# --------------------------------------------------------------------------- #
def _with_map() -> Settings:
    s = Settings()
    s.custom_map = _tiny_map()
    s.players, s.nodes = s.custom_map.seats(), len(s.custom_map.nodes)
    return s


def test_a_custom_map_survives_to_dict_to_token_and_save(tmp_path):
    """Three round trips, not one: `to_dict` is no longer a bare `asdict`, and a
    forgotten override there half-breaks the save file silently while the token
    keeps working."""
    s = _with_map()
    assert Settings.from_dict(s.to_dict()).custom_map == s.custom_map
    assert Settings.from_token(s.to_token()).custom_map == s.custom_map
    path = tmp_path / "hand.json"
    s.save(path)
    assert Settings.load(path).custom_map == s.custom_map


def test_to_dict_writes_the_compact_wire_form_not_the_dataclass():
    """A bare `asdict` would write a keyed object per node, roughly trebling every
    shared link and giving the setup digest a second shape nothing else reads."""
    data = _with_map().to_dict()
    assert set(data["custom_map"]) == {"v", "n", "l"}
    assert data["custom_map"]["n"][0] == [200, 200, 3, 10, 1]


def test_from_dict_reconciles_nodes_and_players_to_the_recipe():
    """They must agree with the seats actually placed, or `token_dict`'s seat
    truncation and `challenge_keys`' seat blanking disagree with the sender's."""
    data = _with_map().to_dict()
    data["players"], data["nodes"] = 6, 30      # a token that disagrees with its map
    out = Settings.from_dict(data)
    assert out.players == 2 and out.nodes == 2


def test_a_hand_map_may_sit_below_the_generated_node_floor():
    """The reconciliation is deliberately after the clamps and overrides them: a
    two-system map is a legitimate thing to draw, and `min_nodes()` only governs
    what the *generator* is asked for."""
    s = _with_map()
    assert s.nodes < s.min_nodes()
    assert Settings.from_dict(s.to_dict()).nodes == 2


def test_a_broken_recipe_lands_at_none_and_the_setup_still_builds():
    """Rejection is never silent even though nothing is raised: `challenge_key()`
    no longer matches the key a sender stamped, so the menu's existing "this setup
    has been edited" banner fires with nothing added."""
    data = _with_map().to_dict()
    data["custom_map"]["l"] = []               # two systems, no lane: disconnected
    out = Settings.from_dict(data)
    assert out.custom_map is None
    assert build_state(out, 3) is not None


def test_a_custom_setup_offers_exactly_one_challenge_key():
    """`custom_map` appears in every `_LEGACY_KEY_DROPS` entry, so no older
    version could have described such a map — correctly, since none could."""
    assert len(_with_map().challenge_keys()) == 1


def test_copy_from_deep_copies_the_recipe():
    s = _with_map()
    other = Settings()
    other.copy_from(s)
    other.custom_map.nodes[0].x = 999
    assert s.custom_map.nodes[0].x == 200


def test_build_state_takes_the_custom_branch():
    state = build_state(_with_map(), 11)
    assert state.mode == "custom"
    assert len(state.systems) == 2


def test_a_setup_without_a_map_prunes_the_key_from_its_token():
    """So a generated map's shared link is byte-identical either side of this
    change, and the leaderboard needs no KEY_ALIASES entry for it."""
    assert "custom_map" not in Settings().token_dict()


def test_no_core_module_imports_pygame():
    """The hard split the whole layout rests on — it is what lets `tests/sim` and
    most of the suite run with no display. There is no violation today, and this
    change adds both a new core module (`custommap`) and a new shell one
    (`mapmaker`), so pin it rather than rely on nobody noticing.

    Parsed rather than grepped: every one of these modules says "no pygame" in its
    own docstring, so a substring search passes nothing and fails everything.
    """
    import ast
    import pathlib

    core = ("model", "geometry", "mapgen", "combat", "engine", "ai", "botio",
            "settings", "fog", "replay", "turnfilm", "custommap")
    pkg = pathlib.Path(__file__).resolve().parent.parent / "starconquest"
    for name in core:
        tree = ast.parse((pkg / f"{name}.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(n.split(".")[0] == "pygame" for n in names), \
                f"{name}.py is core and must not import pygame"

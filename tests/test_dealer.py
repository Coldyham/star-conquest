"""Dealing gauntlet stages from a spec (`starconquest/dealer.py`,
`docs/design/gauntlet.md`, "Generated gauntlets")."""

from __future__ import annotations

import random
from dataclasses import fields

import pytest

from starconquest import ai, config, dealer, gauntlet, mapgen, settings
from starconquest.settings import Settings, build_state

SPEC = {"stages": 6,
        "rules": {"mode": {"fix": "symmetric"}, "systems": {"range": [13, 29]},
                  "ship_speed_growth_pct": {"fix": 0}, "home_production": {"fix": 2},
                  "fog": {"fix": False}, "players": {"range": [2, 4]}},
        "opponents": {"not": ["knower"]}}


@pytest.fixture(scope="module", autouse=True)
def _roster():
    ai.load_models()


def _deals(spec: dealer.Spec, n: int = 30, seed: int = 7) -> list[Settings]:
    rng = random.Random(seed)
    return [dealer.deal(spec, rng, i / max(1, n - 1)) for i in range(n)]


def test_every_dealt_setup_keeps_the_spec():
    spec = dealer.Spec.from_dict(SPEC)
    assert dealer.problems(spec) == []
    for s in _deals(spec):
        assert s.mode == "symmetric" and 13 <= s.system_count() <= 29
        assert s.ship_speed_growth_pct == 0 and s.home_production == 2
        assert s.fog_sight == s.fog_scout == config.FOG_MAX_HOPS
        assert 2 <= s.players <= 4
        bots = [s.seat_strategy(seat) for seat in range(2, s.players + 1)]
        assert "knower" not in bots and "random" not in bots
        assert isinstance(s.seed, int)


def test_dealing_is_a_pure_function_of_the_spec_and_the_rng():
    spec = dealer.Spec.from_dict(SPEC)
    a = [s.challenge_key() for s in _deals(spec, 10, seed=3)]
    b = [s.challenge_key() for s in _deals(spec, 10, seed=3)]
    assert a == b and len(set(a)) == 10


def test_opponents_follow_depth_weakest_first():
    spec = dealer.Spec.from_dict({"opponents": ["heuristic", "marshal", "rusherplus", "thinker"],
                                  "rules": {"players": {"fix": 4}}})
    assert dealer.ladder(spec.opponents) == ["rusherplus", "heuristic", "thinker", "marshal"]
    rng = random.Random(1)
    order = dealer.ladder(spec.opponents)

    def mean_rank(depth):
        ranks = [order.index(s.seat_strategy(seat)) for s in (dealer.deal(spec, rng, depth) for _ in range(20))
                 for seat in range(2, 5)]
        return sum(ranks) / len(ranks)

    assert mean_rank(0.0) < 1 < mean_rank(0.5) < 2.5 < mean_rank(1.0)


def test_an_opponent_stop_is_dealt_onto_the_seat():
    spec = dealer.Spec.from_dict({"opponents": ["actuary:3"], "rules": {"players": {"fix": 2}}})
    s = dealer.deal(spec, random.Random(2))
    assert s.seat_strategy(2) == "actuary" and s.ai[1].aux == 3.0


def test_unruled_knobs_roll_or_stay_at_their_defaults():
    rolled = _deals(dealer.Spec.from_dict({"roll": True}), 12)
    assert len({s.home_production for s in rolled}) > 1
    plain = _deals(dealer.Spec.from_dict({"roll": False, "rules": {"players": {"fix": 3}}}), 6)
    base = Settings.defaults()
    for s in plain:
        assert s.home_production == base.home_production and s.mode == base.mode
        assert s.nodes == base.nodes and s.combat_jitter == base.combat_jitter


def test_ranges_in_and_not_rules_hold():
    spec = dealer.Spec.from_dict({"rules": {
        "home_start_ships": {"range": [5, 9]}, "garrison_base": {"in": [0, 3]},
        "home_production": {"not": [1, 2, 3, 4, 5, 6, 7]}, "mode": {"not": ["symmetric"]},
        "defender_advantage": {"fix": 1.2}}})
    for s in _deals(spec, 15):
        assert 5 <= s.home_start_ships <= 9 and s.garrison_base in (0, 3)
        assert s.home_production == 8 and s.mode == "random" and s.defender_advantage == 1.2


def test_fog_on_always_leaves_a_range_short_of_unlimited():
    spec = dealer.Spec.from_dict({"rules": {"fog": {"fix": True}}, "roll": True})
    for s in _deals(spec, 10):
        assert min(s.fog_sight, s.fog_scout) < config.FOG_MAX_HOPS


def test_a_symmetric_stage_draws_every_layout_without_roll():
    spec = dealer.Spec.from_dict(SPEC)
    assert not spec.roll
    assert {s.layout for s in _deals(spec, 40)} == set(mapgen.SYMMETRIC_LAYOUTS)
    ruled = dealer.Spec.from_dict({**SPEC, "rules": {**SPEC["rules"], "layout": {"fix": "ring"}}})
    assert {s.layout for s in _deals(ruled, 10)} == {"ring"}


def test_fog_on_with_nothing_else_said_is_the_menu_checkbox_preset():
    spec = dealer.Spec.from_dict({"rules": {"fog": {"fix": True}}})
    for s in _deals(spec, 5):
        assert (s.fog_sight, s.fog_scout) == (config.FOG_ON_SIGHT, config.FOG_ON_SCOUT)


@pytest.mark.parametrize("junk", [None, 3, "x", [], {"stages": "9", "rules": 4, "opponents": 5, "roll": "no"},
                                  {"rules": {"mode": {"fix": "spiral"}, "players": {"range": [2]},
                                             "nodes_please": {"fix": 3}, "home_production": {"fix": True},
                                             "fog": {"range": [0, 1]}, "layout": {"in": "hub"},
                                             "garrison_k": {"fix": 1, "range": [1, 2]}}},
                                  {"opponents": {"in": ["a:b"]}}])
def test_from_dict_never_raises_and_drops_what_it_cannot_read(junk):
    spec = dealer.Spec.from_dict(junk)
    assert spec.rules == {}
    assert 1 <= spec.stages <= dealer.MAX_STAGES


def test_ranges_are_clamped_to_their_sliders_and_round_trip():
    spec = dealer.Spec.from_dict({"stages": 500, "rules": {"home_production": {"range": [9, -4]},
                                                          "players": {"fix": 40}}})
    assert spec.stages == dealer.MAX_STAGES
    assert spec.rules["home_production"] == dealer.Rule("range", (1, 8))
    assert spec.rules["players"] == dealer.Rule("fix", config.MAX_PLAYERS)
    assert dealer.Spec.from_dict(spec.to_dict()) == spec


def test_problems_names_a_spec_that_can_never_deal():
    assert dealer.problems(dealer.Spec.from_dict(
        {"rules": {"players": {"fix": 6}, "systems": {"range": [5, 8]}}})) == ["6 players need at least 9 systems"]
    assert dealer.problems(dealer.Spec.from_dict({"rules": {"mode": {"in": []}}})) == ["mode has no value left to deal"]
    assert dealer.problems(dealer.Spec.from_dict({"opponents": {"in": []}})) == ["no opponents are allowed"]
    with pytest.raises(ValueError):
        dealer.deal(dealer.Spec.from_dict({"rules": {"mode": {"in": []}}}), random.Random(1))


def test_a_dealt_stage_is_an_ordinary_setup_the_gauntlet_reads():
    """No Settings field is added: a dealt stage builds, keeps its key through
    the gauntlet file, and pins its seed."""
    names = {f.name for f in fields(Settings)}
    spec = dealer.Spec.from_dict(SPEC)
    s = dealer.deal(spec, random.Random(5), 0.5)
    stage = dealer.stage_dict(s)
    assert set(stage) <= names and "challenge" not in stage and "autoplay" not in stage
    g = gauntlet.Gauntlet.from_dict({"stages": [{"id": 0, "settings": stage}]})
    assert g is not None
    assert gauntlet.stage_key(g.stages[0]) == s.challenge_key()
    build_state(gauntlet.stage_settings(g.stages[0]), s.seed)
    assert dealer.describe(s).startswith("Symmetric · ")


LANES = {"stages": 1, "rules": {"systems": {"range": [10, 30]}, "ship_speed_growth_pct": {"fix": 0},
                                "ship_ly_per_turn": {"range": [6, 12]}, "lane_turns": {"range": [2, 5]}}}


def test_lane_turns_fits_the_speed_to_the_dealt_map():
    """The median lane of the map actually dealt lands in range, at a speed the
    speed rule allows (`docs/design/daily.md`, "Ship speed follows the map")."""
    spec = dealer.Spec.from_dict(LANES)
    assert spec.rules["lane_turns"] == dealer.Rule("range", (2, 5))
    for s in _deals(spec, 12):
        median = settings.lane_turns(settings.lane_lengths(s), s.ship_ly_per_turn)[1]
        assert 2 <= median <= 5 and 6 <= s.ship_ly_per_turn <= 12
        assert s.ship_ly_per_turn * 2 == int(s.ship_ly_per_turn * 2)


def test_lane_turns_is_drawn_after_everything_else():
    """It only changes the speed: the setup is otherwise the one the spec
    without it deals from the same rng."""
    without = dict(LANES, rules={k: v for k, v in LANES["rules"].items() if k != "lane_turns"})
    a = dealer.deal(dealer.Spec.from_dict(LANES), random.Random(11))
    b = dealer.deal(dealer.Spec.from_dict(without), random.Random(11))
    b.ship_ly_per_turn = a.ship_ly_per_turn
    assert a.challenge_key() == b.challenge_key()


def test_a_lane_turns_no_speed_can_reach_never_deals():
    spec = dealer.Spec.from_dict({"rules": {"ship_ly_per_turn": {"fix": 30}, "lane_turns": {"fix": 20}}})
    with pytest.raises(ValueError):
        dealer.deal(spec, random.Random(1))

"""Tests for random map generation: connectivity, counts, and planarity sanity."""

from __future__ import annotations

import itertools
import math
import random

from starconquest import config, mapgen, starnames
from starconquest.geometry import point_segment_dist, segments_intersect

SEEDS = list(range(60))


def test_node_and_player_counts():
    for seed in SEEDS:
        state = mapgen.generate_random(seed, num_nodes=18, num_players=3)
        assert len(state.systems) == 18
        # neutral + 3 real players
        assert len(state.players) == 4
        homes = [s for s in state.systems.values() if s.owner_id != 0]
        assert len(homes) == 3
        owners = {s.owner_id for s in homes}
        assert owners == {1, 2, 3}


def test_connected_across_seeds():
    for seed in SEEDS:
        for n in (8, 14, 24, 32):
            state = mapgen.generate_random(seed, num_nodes=n, num_players=3)
            assert mapgen.is_connected(state), f"disconnected: seed={seed} n={n}"


def test_travel_turns_positive_and_symmetric():
    for seed in SEEDS[:20]:
        state = mapgen.generate_random(seed, num_nodes=20, num_players=4)
        for lane in state.lanes.values():
            assert lane.travel_turns >= 1
            assert lane.a < lane.b
        # adjacency is symmetric with matching travel times
        for a, nbrs in state.adjacency.items():
            for b, t in nbrs.items():
                assert state.adjacency[b][a] == t


def test_homeworlds_configured():
    state = mapgen.generate_random(1, num_nodes=20, num_players=3)
    for s in state.systems.values():
        if s.owner_id != 0:
            assert s.production == config.HOME_PRODUCTION
            assert s.ships == config.HOME_START_SHIPS
        else:
            assert s.ships >= config.GARRISON_BASE
            assert s.production in config.PRODUCTION_WEIGHTS


def test_planar_sanity_low_crossings():
    """MST is planar and extras reject crossings, so non-incident edges shouldn't cross."""
    for seed in SEEDS[:20]:
        state = mapgen.generate_random(seed, num_nodes=22, num_players=3)
        edges = [(lane.a, lane.b) for lane in state.lanes.values()]
        crossings = 0
        for (a, b), (c, d) in itertools.combinations(edges, 2):
            if len({a, b, c, d}) < 4:
                continue  # share a node -> incident, not a crossing
            pa, pb = state.systems[a].pos, state.systems[b].pos
            pc, pd = state.systems[c].pos, state.systems[d].pos
            if segments_intersect(pa, pb, pc, pd):
                crossings += 1
        assert crossings == 0, f"seed {seed} had {crossings} edge crossings"


def test_no_lane_grazes_an_unrelated_node():
    """A lane shouldn't pass so close to a third system that it renders as if
    running underneath it (e.g. a direct edge nearly collinear with a two-hop
    path through that system)."""
    clearance = config.LANE_NODE_CLEARANCE_FRAC * config.WORLD_SIZE
    for seed in SEEDS[:20]:
        state = mapgen.generate_random(seed, num_nodes=22, num_players=3)
        for lane in state.lanes.values():
            pa, pb = state.systems[lane.a].pos, state.systems[lane.b].pos
            for sid, sys in state.systems.items():
                if sid in (lane.a, lane.b):
                    continue
                d = point_segment_dist(sys.pos, pa, pb)
                assert d >= clearance, (
                    f"seed {seed}: lane {lane.a}-{lane.b} passes {d:.1f} from node {sid}"
                )


def test_deterministic_from_seed():
    a = mapgen.generate_random(42, num_nodes=20, num_players=3)
    b = mapgen.generate_random(42, num_nodes=20, num_players=3)
    assert [s.pos for s in a.systems.values()] == [s.pos for s in b.systems.values()]
    assert set(a.lanes.keys()) == set(b.lanes.keys())
    assert [s.ships for s in a.systems.values()] == [s.ships for s in b.systems.values()]


def test_every_system_gets_a_distinct_star_name():
    for mode in ("random", "symmetric"):
        for seed in SEEDS[:10]:
            state = mapgen.generate(seed, mode=mode, num_nodes=24, num_players=3)
            names = [s.name for s in state.systems.values()]
            assert all(names), f"unnamed system: seed={seed} mode={mode}"
            assert len(set(names)) == len(names), f"duplicate name: seed={seed} mode={mode}"
            assert set(names) <= set(starnames.NAMES)


def test_names_are_deterministic_from_seed():
    a = mapgen.generate_random(42, num_nodes=20, num_players=3)
    b = mapgen.generate_random(42, num_nodes=20, num_players=3)
    assert [s.name for s in a.systems.values()] == [s.name for s in b.systems.values()]


def test_naming_is_the_last_roll_of_generation(monkeypatch):
    """Names are drawn after everything that shapes the map, so a seed lays out the
    same board with or without them — which is what keeps existing seeds, saved
    setups and recorded games playing exactly as they did."""
    named = mapgen.generate_random(7, num_nodes=22, num_players=4)
    monkeypatch.setattr(mapgen, "_name_systems", lambda state: None)
    bare = mapgen.generate_random(7, num_nodes=22, num_players=4)
    assert [s.pos for s in named.systems.values()] == [s.pos for s in bare.systems.values()]
    assert [s.ships for s in named.systems.values()] == [s.ships for s in bare.systems.values()]
    assert [s.production for s in named.systems.values()] == [s.production for s in bare.systems.values()]
    assert set(named.lanes) == set(bare.lanes)
    assert not any(s.name for s in bare.systems.values())


def test_pick_names_numbers_the_surplus_when_asked_for_more_than_exist():
    rng = random.Random(0)
    n = len(starnames.NAMES) + 5
    names = starnames.pick(rng, n)
    assert len(names) == n
    assert len(set(names)) == n


# --------------------------------------------------------------------------- #
# Custom maps — the second source of a board, built from a concrete recipe
# --------------------------------------------------------------------------- #
def _recipe(seed: int = 5, mode: str = "random", nodes: int = 18, players: int = 3):
    from starconquest import custommap
    return custommap.from_state(mapgen.generate(seed, mode, nodes, players))


def test_generate_custom_honours_every_explicit_value():
    """Nothing in a recipe is a sentinel resolved at build time, which is what
    lets the seed stop shaping a custom map at all."""
    design = _recipe()
    state = mapgen.generate_custom(123, design)
    for i, node in enumerate(design.nodes):
        system = state.systems[i]
        assert system.pos == (float(node.x), float(node.y))
        assert system.production == node.production
        assert system.ships == node.ships
        assert system.owner_id == node.owner


def test_generate_custom_stamps_the_custom_mode():
    """On the *state*, never on `settings.MODES` — the leaderboard's schema
    constrains that column to random/symmetric, so a token carrying "custom"
    would be refused on submit."""
    from starconquest.settings import MODES
    assert mapgen.generate_custom(1, _recipe()).mode == "custom"
    assert "custom" not in MODES


def test_generate_custom_satisfies_the_same_invariants_as_a_generated_map():
    state = mapgen.generate_custom(9, _recipe())
    assert mapgen.is_connected(state)
    for key, lane in state.lanes.items():
        assert len(key) == 2                      # canonical, order-independent
        assert lane.travel_turns >= 1
        a, b = sorted(key)
        assert state.adjacency[a][b] == state.adjacency[b][a]
    names = [s.name for s in state.systems.values()]
    assert len(set(names)) == len(names)
    assert set(names) <= set(starnames.NAMES)


def test_a_custom_map_is_deterministic_from_its_seed():
    design = _recipe()
    a = mapgen.generate_custom(77, design)
    b = mapgen.generate_custom(77, design)
    assert [s.name for s in a.systems.values()] == [s.name for s in b.systems.values()]


def test_naming_is_the_last_roll_of_a_custom_build(monkeypatch):
    """Same property `generate_random` has: names are the final draw, so nothing
    that shapes the board depends on them."""
    design = _recipe()
    named = mapgen.generate_custom(7, design)
    monkeypatch.setattr(mapgen, "_name_systems", lambda state: None)
    bare = mapgen.generate_custom(7, design)
    assert [s.pos for s in named.systems.values()] == [s.pos for s in bare.systems.values()]
    assert [s.ships for s in named.systems.values()] == [s.ships for s in bare.systems.values()]
    assert set(named.lanes) == set(bare.lanes)
    assert not any(s.name for s in bare.systems.values())


def test_a_generated_map_never_violates_the_hand_placement_rules():
    """The regression guard on `CUSTOM_MIN_NODE_SEP_FRAC`: raise it past the
    tightest pair mapgen actually produces (measured at 53.6 world units) and
    loading a generated map into the creator lights up with violations."""
    sep = config.CUSTOM_MIN_NODE_SEP_FRAC * config.WORLD_SIZE
    for seed in range(12):
        for nodes in (12, 18, 24, 40):
            for mode in ("random", "symmetric"):
                for players in (2, 4, 6):
                    design = _recipe(seed, mode, nodes, players)
                    assert design.problems() == [], \
                        f"{mode}/{nodes}n/{players}p seed {seed}: {design.problems()}"
                    for i, a in enumerate(design.nodes):
                        for b in design.nodes[i + 1:]:
                            gap = math.hypot(a.x - b.x, a.y - b.y)
                            assert gap >= sep, f"{mode}/{nodes}n seed {seed}: gap {gap:.1f}"

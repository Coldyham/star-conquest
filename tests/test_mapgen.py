"""Tests for random map generation: connectivity, counts, and planarity sanity."""

from __future__ import annotations

import itertools

from starconquest import config, mapgen
from starconquest.geometry import segments_intersect


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


def test_deterministic_from_seed():
    a = mapgen.generate_random(42, num_nodes=20, num_players=3)
    b = mapgen.generate_random(42, num_nodes=20, num_players=3)
    assert [s.pos for s in a.systems.values()] == [s.pos for s in b.systems.values()]
    assert set(a.lanes.keys()) == set(b.lanes.keys())
    assert [s.ships for s in a.systems.values()] == [s.ships for s in b.systems.values()]

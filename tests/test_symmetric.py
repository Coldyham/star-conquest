"""Symmetric map generation: structural fairness (identical sectors per player).

We assert *structural* symmetry rather than AI-vs-AI win parity: two identical
AIs on a perfect mirror map deadlock (that's expected), so outcome parity is not
a meaningful check here. Equal, rotationally identical sectors are.
"""

from __future__ import annotations

from collections import Counter

from starconquest import config, mapgen


def _sector_of(nid: int, per_player: int, num_players: int) -> int | None:
    if nid >= per_player * num_players:
        return None  # the shared central node
    return nid // per_player


def _degree(state) -> dict[int, int]:
    return {sid: len(state.adjacency.get(sid, {})) for sid in state.systems}


def test_connected_all_configs():
    for players in (2, 3, 4, 5):
        for seed in range(15):
            s = mapgen.generate_symmetric(seed, num_nodes=20, num_players=players)
            assert mapgen.is_connected(s)


def test_sectors_are_identical():
    for players in (2, 3, 4):
        for seed in range(15):
            s = mapgen.generate_symmetric(seed, num_nodes=20, num_players=players)
            per = (len(s.systems) - 1) // players
            deg = _degree(s)

            prod_by_sector: dict[int, Counter] = {}
            deg_by_sector: dict[int, Counter] = {}
            count_by_sector: Counter = Counter()
            for sid, sys in s.systems.items():
                sec = _sector_of(sid, per, players)
                if sec is None:
                    continue
                prod_by_sector.setdefault(sec, Counter())[sys.production] += 1
                deg_by_sector.setdefault(sec, Counter())[deg[sid]] += 1
                count_by_sector[sec] += 1

            # every sector: same node count, same production multiset, same degrees
            assert len(set(count_by_sector.values())) == 1
            base_prod = prod_by_sector[0]
            base_deg = deg_by_sector[0]
            for sec in range(players):
                assert prod_by_sector[sec] == base_prod
                assert deg_by_sector[sec] == base_deg


def test_one_home_per_player_and_central_node():
    s = mapgen.generate_symmetric(1, num_nodes=18, num_players=3)
    homes = [sys for sys in s.systems.values() if sys.owner_id != 0]
    assert len(homes) == 3
    assert {h.owner_id for h in homes} == {1, 2, 3}
    for h in homes:
        assert h.production == config.HOME_PRODUCTION
        assert h.ships == config.HOME_START_SHIPS
    # a single shared central node exists and is neutral
    center = s.systems[len(s.systems) - 1]
    assert center.owner_id == 0
    assert len(s.adjacency[center.id]) == 3  # one seam per sector


def test_travel_turns_positive_and_deterministic():
    a = mapgen.generate_symmetric(9, num_nodes=18, num_players=3)
    b = mapgen.generate_symmetric(9, num_nodes=18, num_players=3)
    for lane in a.lanes.values():
        assert lane.travel_turns >= 1
    assert [s.pos for s in a.systems.values()] == [s.pos for s in b.systems.values()]
    assert set(a.lanes.keys()) == set(b.lanes.keys())

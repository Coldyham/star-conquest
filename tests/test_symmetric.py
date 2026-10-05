"""Symmetric map generation: structural fairness (identical sectors per player).

We assert *structural* symmetry rather than AI-vs-AI win parity: two identical
AIs on a perfect mirror map deadlock (that's expected), so outcome parity is not
a meaningful check here. Equal, rotationally identical sectors are.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter

from starconquest import config, mapgen
from starconquest.geometry import point_segment_dist, segments_intersect


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


# --------------------------------------------------------------------------- #
# Layouts: how the sectors are joined
# --------------------------------------------------------------------------- #
def _shared(layout: str, players: int) -> int:
    """How many systems a layout adds beyond the sectors."""
    return {"hub": 1, "wheel": 1, "core": players}.get(layout, 0)


def _rotation(state, layout: str, players: int) -> dict[int, int]:
    """Where each id lands when the board turns one sector: sector k to k+1,
    the hub onto itself, core k onto core k+1."""
    shared = _shared(layout, players)
    per = (len(state.systems) - shared) // players
    first = per * players
    out = {}
    for sid in state.systems:
        if sid < first:
            out[sid] = (sid + per) % first
        elif layout == "core":
            out[sid] = first + (sid - first + 1) % players
        else:
            out[sid] = sid
    return out


def _fingerprint() -> str:
    rows = []
    for seed, nodes, players in ((3, 18, 2), (11, 24, 3), (7, 40, 6), (5, 60, 4)):
        s = mapgen.generate_symmetric(seed, nodes, players)
        rows.append([[sid, round(v.pos[0], 6), round(v.pos[1], 6), v.production, v.ships, v.owner_id, v.name]
                     for sid, v in s.systems.items()])
        rows.append(sorted([l.a, l.b, l.length_ly, l.travel_turns] for l in s.lanes.values()))
    return hashlib.blake2s(json.dumps(rows).encode(), digest_size=8).hexdigest()


def test_the_hub_layout_is_the_board_it_always_was():
    """Pinned against the generator as it stood before layouts existed. A stored
    symmetric game replays by regenerating its map from the seed, and
    `RULES_VERSION` did not move when the other layouts joined — so if this
    fails, either restore the hub's draws or bump `RULES_VERSION`."""
    assert _fingerprint() == "75cad1b15cde46ad"


def test_an_unknown_layout_is_the_hub():
    a = mapgen.generate_symmetric(4, 20, 3, "spiral")
    b = mapgen.generate_symmetric(4, 20, 3)
    assert set(a.lanes) == set(b.lanes)
    assert [s.pos for s in a.systems.values()] == [s.pos for s in b.systems.values()]


def test_every_layout_is_connected_uncrossed_and_grazes_nothing():
    clearance = config.LANE_NODE_CLEARANCE_FRAC * config.WORLD_SIZE
    for layout in mapgen.SYMMETRIC_LAYOUTS:
        for players in (2, 3, 4, 6):
            for nodes in (8, 20, 40):
                for seed in range(6):
                    s = mapgen.generate_symmetric(seed, nodes, players, layout)
                    where = f"{layout} {players}p {nodes}n seed {seed}"
                    assert mapgen.is_connected(s), where
                    edges = [(lane.a, lane.b) for lane in s.lanes.values()]
                    pos = {sid: sys.pos for sid, sys in s.systems.items()}
                    for (a, b), (c, d) in itertools.combinations(edges, 2):
                        if len({a, b, c, d}) == 4:
                            assert not segments_intersect(pos[a], pos[b], pos[c], pos[d]), where
                    for a, b in edges:
                        for sid, p in pos.items():
                            if sid not in (a, b):
                                assert point_segment_dist(p, pos[a], pos[b]) >= clearance, where


def test_every_layout_turns_onto_itself():
    """The fairness claim, whatever joins the sectors: one sector's turn maps
    every lane onto a lane of the same length and every system onto one with the
    same production, garrison and (rotated) owner."""
    for layout in mapgen.SYMMETRIC_LAYOUTS:
        for players in (2, 3, 4, 5):
            for seed in range(6):
                s = mapgen.generate_symmetric(seed, 22, players, layout)
                turn = _rotation(s, layout, players)
                for lane in s.lanes.values():
                    other = s.lanes.get(frozenset((turn[lane.a], turn[lane.b])))
                    assert other is not None, f"{layout} {players}p seed {seed}"
                    assert other.travel_turns == lane.travel_turns
                for sid, sys in s.systems.items():
                    image = s.systems[turn[sid]]
                    assert (image.production, image.ships) == (sys.production, sys.ships)
                    if sys.owner_id:
                        assert image.owner_id == sys.owner_id % players + 1


def test_what_joins_the_sectors_is_what_the_layout_says():
    players = 4
    for seed in range(6):
        boards = {layout: mapgen.generate_symmetric(seed, 24, players, layout)
                  for layout in mapgen.SYMMETRIC_LAYOUTS}
        for layout, s in boards.items():
            shared = _shared(layout, players)
            per = (len(s.systems) - shared) // players
            first = per * players
            sector = {sid: sid // per for sid in s.systems if sid < first}
            across = [lane for lane in s.lanes.values()
                      if lane.a in sector and lane.b in sector and sector[lane.a] != sector[lane.b]]
            homes = [sid for sid, sys in s.systems.items() if sys.owner_id]
            assert not any(h in s.adjacency[g] for h in homes for g in homes)
            if layout == "hub":
                assert not across and len(s.adjacency[first]) == players
            elif layout == "ring":
                assert shared == 0 and len(across) == players
            elif layout == "wheel":
                assert len(across) == players and len(s.adjacency[first]) == players
            else:  # core: each core system meets two sectors and its two neighbours
                assert not across
                for core in range(first, first + players):
                    nbrs = s.adjacency[core]
                    assert len({sector[n] for n in nbrs if n in sector}) == 2
                    assert sum(1 for n in nbrs if n >= first) == 2
                    assert all(n not in homes for n in nbrs)

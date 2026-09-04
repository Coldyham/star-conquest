"""Tests for fog-of-war visibility — pure core, no pygame.

Visibility is hop-distance BFS from a player's systems, so a hand-built line /
star graph pins down every boundary the renderer relies on.
"""

from __future__ import annotations

from starconquest import config, fog
from starconquest.model import Fleet, GameState, Player, System


def _line(n: int, owned: set[int], pid: int = 1) -> GameState:
    """Line graph ``0-1-...-(n-1)``; systems in ``owned`` belong to ``pid``."""
    s = GameState.new(0)
    for sid in range(n):
        s.systems[sid] = System(id=sid, pos=(sid * 10.0, 0.0),
                                 owner_id=pid if sid in owned else 0)
    s.players[0] = Player(0, "Neutral", (0, 0, 0), is_neutral=True)
    s.players[pid] = Player(pid, "P", (0, 0, 0), is_human=True)
    for a in range(n - 1):
        s.add_lane(a, a + 1, 1.0, 1)
    s.rebuild_topology()
    return s


def test_owned_systems_always_visible():
    # sight 0: only your own systems, nothing else
    s = _line(4, owned={0})
    visible, scouted = fog.observe(s, 1, sight=0, scout=0)
    assert visible == {0}
    assert scouted == set()


def test_sight_radius_counts_hops():
    s = _line(6, owned={0})
    visible, scouted = fog.observe(s, 1, sight=2, scout=2)
    assert visible == {0, 1, 2}      # within 2 hops
    assert scouted == set()          # scout == sight -> no silhouette ring


def test_scout_ring_is_beyond_sight():
    s = _line(6, owned={0})
    visible, scouted = fog.observe(s, 1, sight=1, scout=3)
    assert visible == {0, 1}         # 0 + 1 hop
    assert scouted == {2, 3}         # hops 2..3 as silhouettes
    # everything past the scout radius is neither seen nor scouted (hidden)
    assert (visible | scouted).isdisjoint({4, 5})


def test_scout_below_sight_yields_no_ring():
    s = _line(6, owned={0})
    visible, scouted = fog.observe(s, 1, sight=3, scout=1)
    assert visible == {0, 1, 2, 3}
    assert scouted == set()


def test_multi_source_from_both_ends():
    s = _line(6, owned={0, 5})
    visible, scouted = fog.observe(s, 1, sight=1, scout=1)
    assert visible == {0, 1, 4, 5}   # each owned end lights its neighbour
    assert scouted == set()


def test_unlimited_sight_reveals_whole_graph():
    s = _line(6, owned={0})
    visible, scouted = fog.observe(s, 1, sight=config.FOG_MAX_HOPS, scout=config.FOG_MAX_HOPS)
    assert visible == set(range(6))  # fog off: everything reachable is full-detail
    assert scouted == set()


def test_unlimited_scout_greys_the_rest():
    s = _line(6, owned={0})
    visible, scouted = fog.observe(s, 1, sight=0, scout=config.FOG_MAX_HOPS)
    assert visible == {0}
    assert scouted == set(range(1, 6))   # whole rest of the map as silhouettes


def test_owning_nothing_sees_nothing():
    s = _line(4, owned=set())        # pid 1 owns no systems
    assert fog.observe(s, 1, sight=5, scout=5) == (set(), set())


def test_disconnected_systems_stay_hidden():
    s = _line(3, owned={0})
    s.systems[9] = System(id=9, pos=(999.0, 999.0), owner_id=0)  # island, no lane
    s.rebuild_topology()
    visible, scouted = fog.observe(s, 1, sight=config.FOG_MAX_HOPS, scout=config.FOG_MAX_HOPS)
    assert 9 not in visible and 9 not in scouted   # unreachable -> never revealed


def test_monotonic_in_range():
    s = _line(8, owned={0})
    prev: set[int] = set()
    for r in range(0, 6):
        visible, scouted = fog.observe(s, 1, sight=r, scout=r)
        seen = visible | scouted
        assert prev <= seen          # a wider range never un-reveals a system
        prev = seen


def test_deterministic():
    s = _line(6, owned={0, 3})
    a = fog.observe(s, 1, sight=1, scout=2)
    b = fog.observe(s, 1, sight=1, scout=2)
    assert a == b


def test_player_totals_counts_ships_in_transit():
    s = GameState.new(0)
    s.systems[0] = System(id=0, pos=(0.0, 0.0), owner_id=1, ships=5, production=3)
    s.systems[1] = System(id=1, pos=(10.0, 0.0), owner_id=1, ships=3, production=0)
    s.players[1] = Player(1, "P", (0, 0, 0), is_human=True)
    s.fleets.append(Fleet(owner_id=1, source_id=0, dest_id=1, ships=4,
                          turns_total=2, turns_remaining=1))
    systems, ships, prod = fog.player_totals(s, 1)
    assert systems == 2
    assert ships == 12               # 5 + 3 garrison + 4 in transit
    assert prod == 1.0 / 3           # only the production>0 system contributes

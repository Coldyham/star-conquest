"""Fog of war — pure visibility queries over the map graph.

No pygame and no state mutation: given a viewpoint player, work out which systems
they can see, measured in lane hops from the territory they hold. The shell
(``main.py``) folds the result into the human's transient view state each turn and
``render.py`` draws from it; the engine and AI never consult any of this (the AI
always plays with full information). Living in the pure core keeps it testable
headlessly, like the rest of the simulation.
"""

from __future__ import annotations

from collections import deque

from . import config
from .model import GameState


def observe(state: GameState, pid: int, sight: int, scout: int) -> tuple[set[int], set[int]]:
    """``(visible, scouted)`` system ids from ``pid``'s viewpoint.

    Distance is lane hops from the nearest ``pid``-owned system (an owned system
    is 0 hops from itself, hence always visible):

      * ``visible`` — within ``sight`` hops (full detail);
      * ``scouted`` — beyond ``sight`` but within ``scout`` hops (grey silhouette),
        empty when ``scout <= sight``.

    A range of ``config.FOG_MAX_HOPS`` or more means *unlimited* — the whole
    reachable graph, so fog is effectively off for that tier. A player owning no
    systems sees nothing.
    """
    owned = [sid for sid, s in state.systems.items() if s.owner_id == pid]
    visible: set[int] = set()
    scouted: set[int] = set()
    if not owned:
        return visible, scouted

    inf = float("inf")
    sight_cap = inf if sight >= config.FOG_MAX_HOPS else sight
    scout_cap = inf if scout >= config.FOG_MAX_HOPS else scout
    reach = max(sight_cap, scout_cap)

    # Multi-source BFS: every owned system starts at depth 0, expansion stops at
    # `reach` (never, when a range is unlimited — the queue drains the graph).
    depth: dict[int, float] = {sid: 0 for sid in owned}
    queue: deque[int] = deque(owned)
    while queue:
        cur = queue.popleft()
        d = depth[cur]
        (visible if d <= sight_cap else scouted).add(cur)
        if d >= reach:
            continue
        for nbr in state.systems[cur].neighbors:
            if nbr not in depth:
                depth[nbr] = d + 1
                queue.append(nbr)
    return visible, scouted


def player_totals(state: GameState, pid: int) -> tuple[int, int, float]:
    """``(systems owned, ships incl. in-transit, production in ships/turn)`` for ``pid``.

    Mirrors the scoreboard maths in ``render`` so the shell can snapshot a rival's
    last-known strength for the fogged scoreboard without importing the pygame layer.
    """
    systems = ships = 0
    prod = 0.0
    for s in state.systems.values():
        if s.owner_id == pid:
            systems += 1
            ships += s.ships
            if s.production > 0:
                prod += 1.0 / s.production
    ships += sum(f.ships for f in state.fleets if f.owner_id == pid)
    return systems, ships, prod

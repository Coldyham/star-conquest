"""Fog of war — pure visibility queries over the map graph.

No pygame and no state mutation: given a viewpoint player, work out which systems
they can see, measured in lane hops from the territory they hold. The shell
(``main.py``) folds the result into the human's transient view state each turn and
``render.py`` draws from it. Living in the pure core keeps it testable headlessly,
like the rest of the simulation.

By default the AI plays with full information, but a game (or a headless sim) can
choose to fog the bots too: ``fog_aware`` wraps a decider so each seat decides
from ``fogged_state`` — the board masked to what that seat can actually see. That
seam stays outside the engine and never imports the AI, so the engine's AI
inversion is preserved; the bots themselves are simply handed a restricted view.
"""

from __future__ import annotations

from collections import deque
from typing import Callable

from . import config
from .model import GameState, Order, System

# A masked system's ``owner_id``: you can see the node exists, but not who holds
# it or how many ships sit there. A fog-aware bot can test ``owner_id`` against
# this; it is deliberately not a real player id (``state.players`` has no -1).
UNKNOWN_OWNER = -1

# A decision function — the shape the engine injects as ``decide`` (see engine).
DecideFn = Callable[[GameState, int], list[Order]]


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


def fogged_state(state: GameState, pid: int, sight: int, scout: int) -> GameState:
    """Return ``state`` as player ``pid`` perceives it under fog of war.

    Gives a bot exactly the information the UI shows a human at these ranges, in
    the same three tiers (see ``render._fog_state``):

      * **In sight** (<= ``sight`` hops from owned): full detail — the real
        ``owner_id`` and ``ships``.
      * **Scouted** (<= ``scout`` hops): a silhouette — you know the star is there
        and how rich it is (``pos`` and ``production``, which the map draws as node
        size), but ``owner_id`` reads ``UNKNOWN_OWNER`` and ``ships`` reads 0 — the
        renderer's grey "?": "we know where it is, not who holds it or how strong".
      * **Beyond scout**: omitted entirely, exactly as an unexplored system is
        undrawn. The perceived systems form a self-consistent subgraph — neighbour
        lists, ``adjacency`` and ``lanes`` are all pruned to it — so a bot can treat
        what it sees as the whole board without tripping over ids it can't see.

    Enemy fleets are shown only where you have full sight of an endpoint; your own
    systems and fleets are always visible. ``rng`` and ``players`` are shared with
    ``state`` by reference (a decider only reads them), so the rng draw order — and
    hence replay — is unchanged. This models a single turn's *perception*, not
    memory: a stateless bot sees only what is perceptible now, not systems glimpsed
    on earlier turns (the UI's cumulative ``seen``). When fog is off (both ranges
    >= ``FOG_MAX_HOPS``) the real ``state`` is returned untouched, costing nothing.
    """
    if sight >= config.FOG_MAX_HOPS and scout >= config.FOG_MAX_HOPS:
        return state  # fog off: full information, no copy

    visible, scouted = observe(state, pid, sight, scout)
    perceived = visible | scouted

    systems: dict[int, System] = {}
    for sid in sorted(perceived):                   # sorted -> deterministic build
        s = state.systems[sid]
        nbrs = [n for n in s.neighbors if n in perceived]
        if sid in visible:
            systems[sid] = System(id=s.id, pos=s.pos, owner_id=s.owner_id, ships=s.ships,
                                   production=s.production, prod_progress=s.prod_progress,
                                   neighbors=nbrs)
        else:                                       # scouted: silhouette only
            systems[sid] = System(id=s.id, pos=s.pos, owner_id=UNKNOWN_OWNER, ships=0,
                                   production=s.production, prod_progress=0, neighbors=nbrs)

    adjacency = {sid: {n: t for n, t in state.adjacency.get(sid, {}).items() if n in perceived}
                 for sid in sorted(perceived)}
    lanes = {k: ln for k, ln in state.lanes.items()
             if ln.a in perceived and ln.b in perceived}
    fleets = [f for f in state.fleets
              if f.owner_id == pid or f.source_id in visible or f.dest_id in visible]

    return GameState(
        systems=systems, lanes=lanes, adjacency=adjacency, fleets=fleets,
        players=state.players, turn=state.turn, seed=state.seed, mode=state.mode,
        winner=state.winner, rng=state.rng,
    )


def fog_aware(decide: DecideFn) -> DecideFn:
    """Wrap a decider so each seat decides from its own fogged view of the board.

    Reads ``config.FOG_SIGHT``/``FOG_SCOUT`` live at call time (like every other
    config knob), so it follows the game's fog setting and is transparent when fog
    is off. Generic in the decider and free of any AI import, so the engine's AI
    inversion still holds — the wrapped bot just receives a restricted ``state``.
    """
    def fogged(state: GameState, pid: int) -> list[Order]:
        return decide(fogged_state(state, pid, config.FOG_SIGHT, config.FOG_SCOUT), pid)
    return fogged

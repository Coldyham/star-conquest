"""A cheap, stateless heuristic AI: ``compute_orders(state, pid) -> [Order]``.

Recomputed from scratch every turn, O(nodes x degree). Each owned system issues
at most one order per turn, so the AI never over-commits a garrison. Behaviour:

  * A system under threat holds (keeps its ships home).
  * A frontier system with surplus reinforces a threatened neighbour, else takes
    the best-scoring adjacent neutral or weak enemy it can afford.
  * A rear system streams its surplus one hop toward the nearest frontier.

All tie-breaks go through ``state.rng`` so multiple AIs don't play identically.
"""

from __future__ import annotations

import math
from collections import deque

from . import config
from .model import GameState, Order


def compute_orders(state: GameState, pid: int) -> list[Order]:
    owned = {s.id for s in state.systems.values() if s.owner_id == pid}
    if not owned:
        return []

    max_prod = max(config.PRODUCTION_WEIGHTS)
    frontier = {sid for sid in owned if _is_frontier(state, sid, pid)}
    parent = _flow_to_frontier(state, owned, frontier)  # rear -> next hop toward front

    orders: list[Order] = []
    for sid in owned:
        sys = state.systems[sid]
        surplus = _surplus(sys.ships)
        if surplus <= 0:
            continue

        # Hold if this system is itself under pressure.
        if _threat(state, sid, pid) >= sys.ships:
            continue

        if sid in frontier:
            order = _frontier_order(state, pid, sid, surplus, max_prod)
            if order is not None:
                orders.append(order)
        else:
            nxt = parent.get(sid)
            if nxt is not None:
                orders.append(Order(pid, sid, nxt, surplus))
    return orders


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def _frontier_order(state: GameState, pid: int, sid: int, surplus: int, max_prod: int):
    sys = state.systems[sid]
    jitter = lambda: state.rng.uniform(0.0, 0.01)  # noqa: E731 — tiny tie-break noise

    best = None  # (priority, score, target, ships)
    for nbr in sys.neighbors:
        n = state.systems[nbr]
        travel = state.travel_turns(sid, nbr) or 1

        if n.owner_id == pid:
            # reinforce a threatened friendly frontier neighbour
            if _is_frontier(state, nbr, pid):
                t = _threat(state, nbr, pid)
                if t >= n.ships:
                    best = _better(best, (2, t + jitter(), nbr, surplus))
            continue

        if n.owner_id == 0:  # neutral -> expand
            if surplus >= math.ceil(n.ships * config.AI_EXPAND_MARGIN):
                score = _desirability(n, max_prod) / (travel * max(1, n.ships) ** 0.5)
                cand = (1, score + jitter(), nbr, surplus)  # commit fully: concentration wins
                best = _better(best, cand)
        else:  # enemy -> attack
            if surplus >= math.ceil(n.ships * config.AI_ATTACK_MARGIN):
                score = _desirability(n, max_prod) / (travel * max(1, n.ships))
                cand = (1, score + jitter(), nbr, surplus)  # mass the whole surplus
                best = _better(best, cand)

    if best is None:
        return None
    _, _, target, ships = best
    return Order(pid, sid, target, ships)


def _better(best, cand):
    """Keep the candidate with higher priority, then higher score."""
    if best is None:
        return cand
    if (cand[0], cand[1]) > (best[0], best[1]):
        return cand
    return best


def _desirability(system, max_prod: int) -> float:
    """Richer systems (lower turns-per-ship) are worth more."""
    return float(max_prod - system.production + 1)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _surplus(ships: int) -> int:
    reserve = max(config.AI_RESERVE_FLOOR, math.ceil(config.AI_RESERVE_FRACTION * ships))
    return ships - reserve


def _is_frontier(state: GameState, sid: int, pid: int) -> bool:
    return any(state.systems[n].owner_id != pid for n in state.systems[sid].neighbors)


def _threat(state: GameState, sid: int, pid: int) -> int:
    """Largest adjacent enemy garrison plus enemy ships already inbound."""
    adj = 0
    for n in state.systems[sid].neighbors:
        other = state.systems[n]
        if other.owner_id != pid and other.owner_id != 0:
            adj = max(adj, other.ships)
    incoming = sum(f.ships for f in state.fleets if f.dest_id == sid and f.owner_id != pid)
    return adj + incoming


def _flow_to_frontier(state: GameState, owned: set[int], frontier: set[int]) -> dict[int, int]:
    """Multi-source BFS over owned territory; returns rear-node -> next-hop-toward-front."""
    parent: dict[int, int] = {}
    seen = set(frontier)
    queue = deque(frontier)
    while queue:
        cur = queue.popleft()
        for nbr in state.systems[cur].neighbors:
            if nbr in owned and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur  # move from nbr toward cur (closer to the front)
                queue.append(nbr)
    return parent

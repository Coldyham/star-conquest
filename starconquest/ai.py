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

import importlib.util
import math
import sys
from collections import deque
from pathlib import Path
from typing import Callable

from . import config
from .model import AiParams, GameState, Order

# User-supplied strategies live in a gitignored dir beside the repo (mirrors
# menu._SAVE_DIR), so a drop-in `.py` becomes a selectable AI without touching src.
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

# A seat's decision function: same shape the engine injects as `decide`.
DecideFn = Callable[[GameState, int], list[Order]]


def compute_orders(state: GameState, pid: int) -> list[Order]:
    owned = {s.id for s in state.systems.values() if s.owner_id == pid}
    if not owned:
        return []

    params = state.players[pid].ai_params
    max_prod = max(config.PRODUCTION_WEIGHTS)
    frontier = {sid for sid in owned if _is_frontier(state, sid, pid)}
    parent = _flow_to_frontier(state, owned, frontier)  # rear -> next hop toward front

    orders: list[Order] = []
    for sid in owned:
        sys = state.systems[sid]
        surplus = _surplus(sys.ships, params)
        if surplus <= 0:
            continue

        # Hold if this system is itself under pressure.
        if _threat(state, sid, pid) >= sys.ships:
            continue

        if sid in frontier:
            order = _frontier_order(state, pid, sid, surplus, max_prod, params)
            if order is not None:
                orders.append(order)
        else:
            nxt = parent.get(sid)
            if nxt is not None:
                orders.append(Order(pid, sid, nxt, surplus))
    return orders


# --------------------------------------------------------------------------- #
# Strategy registry — the seam for user-written AIs. A strategy is any
# DecideFn; each seat names one via ``Player.ai_strategy`` and `decide` routes
# to it. The engine still just calls `decide(state, pid)`, unaware of any of it.
# --------------------------------------------------------------------------- #
STRATEGIES: dict[str, DecideFn] = {}


def register(name: str, fn: DecideFn) -> None:
    """Make a decision function selectable per seat under ``name``."""
    STRATEGIES[name] = fn


def decide(state: GameState, pid: int) -> list[Order]:
    """Dispatch a seat to its chosen strategy (falling back to the heuristic)."""
    strategy = STRATEGIES.get(state.players[pid].ai_strategy, compute_orders)
    return strategy(state, pid)


def available_strategies() -> list[str]:
    """Names for the menu dropdown: the built-in heuristic first, then the rest."""
    return ["heuristic"] + sorted(n for n in STRATEGIES if n != "heuristic")


def load_models(directory: Path = MODELS_DIR) -> list[str]:
    """Import every ``*.py`` in ``directory`` and register its ``decide`` function.

    Each file becomes a strategy named after the file stem. A file that fails to
    import or lacks a callable ``decide`` is skipped (never crashes discovery), so
    a broken model simply won't appear. Idempotent: re-running re-imports and
    overwrites, picking up edits and newly-added files. Returns the sorted names
    that registered successfully. Trusted local code — importing it is the point.
    """
    loaded: list[str] = []
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"sc_model_{path.stem}", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module          # so dataclasses/typing resolve
            spec.loader.exec_module(module)
            fn = getattr(module, "decide", None)
            if callable(fn):
                register(path.stem, fn)
                loaded.append(path.stem)
        except Exception:                            # noqa: BLE001 — one bad model mustn't break the rest
            continue
    return sorted(loaded)


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def _frontier_order(state: GameState, pid: int, sid: int, surplus: int, max_prod: int,
                    params: AiParams):
    sys = state.systems[sid]
    jitter = lambda: state.rng.uniform(0.0, 0.01)  # noqa: E731 — tiny tie-break noise
    self_deficit = _threat(state, sid, pid) - sys.ships  # how far short of our own threat we are

    best = None  # (priority, score, target, ships)
    for nbr in sorted(sys.neighbors):
        n = state.systems[nbr]
        travel = state.travel_turns(sid, nbr) or 1

        if n.owner_id == pid:
            # Reinforce a frontier neighbour only if it is meaningfully more
            # exposed than we are (deficit = threat - ships). Requiring a margin
            # makes reinforcement one-directional, so two adjacent frontier
            # systems no longer send ships to each other every turn.
            if _is_frontier(state, nbr, pid):
                nbr_deficit = _threat(state, nbr, pid) - n.ships
                if nbr_deficit > 0 and nbr_deficit - self_deficit >= params.reinforce_margin:
                    best = _better(best, (2, nbr_deficit + jitter(), nbr, surplus))
            continue

        if n.owner_id == 0:  # neutral -> expand
            if surplus >= math.ceil(n.ships * params.expand_margin):
                score = _desirability(n, max_prod) / (travel * max(1, n.ships) ** 0.5)
                cand = (1, score + jitter(), nbr, surplus)  # commit fully: concentration wins
                best = _better(best, cand)
        else:  # enemy -> attack
            if surplus >= math.ceil(n.ships * params.attack_margin):
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
def _surplus(ships: int, params: AiParams) -> int:
    reserve = max(params.reserve_floor, math.ceil(params.reserve_fraction * ships))
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
    """Multi-source BFS over owned territory; returns rear-node -> next-hop-toward-front.

    Seeds and neighbours are visited in sorted order so the flow is deterministic:
    while the frontier is stable, a rear system keeps the same next-hop every turn
    instead of flip-flopping, which is what made rear ships oscillate.
    """
    parent: dict[int, int] = {}
    seen = set(frontier)
    queue = deque(sorted(frontier))
    while queue:
        cur = queue.popleft()
        for nbr in sorted(state.systems[cur].neighbors):
            if nbr in owned and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur  # move from nbr toward cur (closer to the front)
                queue.append(nbr)
    return parent


register("heuristic", compute_orders)

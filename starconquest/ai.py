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
from pathlib import Path
from typing import Callable

from . import config, model
from .model import AiParams, GameState, Order
from .paths import data_dir

# User-supplied strategies live in a gitignored dir under the writable data dir
# (mirrors menu._SAVE_DIR), so a drop-in `.py` becomes a selectable AI without
# touching src. On Android this is the app-private dir, a real filesystem, so a
# strategy dropped in there is still importable by path.
MODELS_DIR = data_dir() / "models"

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


# The one bot-defined knob, `AiParams.aux`: a strategy declares what it means by
# exporting ``AUX_LABEL`` (optionally ``AUX_RANGE = (lo, hi, step)`` and
# ``AUX_INT``); the menu shows the slider under that label, and hides it for any
# strategy that declares nothing.
AuxSpec = tuple[str, float, float, float, bool]  # label, lo, hi, step, is_int
AUX_RANGE_DEFAULT = (0.0, 8.0, 1.0)


def aux_spec(name: str) -> AuxSpec | None:
    """What ``ai_params.aux`` means for strategy ``name``, or None if it ignores it.

    Tolerant like the rest of the drop-in contract: a missing, blank or malformed
    declaration falls back rather than raising, so a bad model can only cost itself
    the slider.
    """
    fn = STRATEGIES.get(name)
    module = sys.modules.get(getattr(fn, "__module__", "") or "")
    label = getattr(module, "AUX_LABEL", None)
    if not isinstance(label, str) or not label.strip():
        return None
    try:
        lo, hi, step = (float(v) for v in getattr(module, "AUX_RANGE", None))
    except (TypeError, ValueError):
        lo, hi, step = AUX_RANGE_DEFAULT
    if hi <= lo or step <= 0:
        lo, hi, step = AUX_RANGE_DEFAULT
    return label.strip(), lo, hi, step, bool(getattr(module, "AUX_INT", False))


def set_budget_scale(scale: float) -> list[str]:
    """Widen (or restore) every registered strategy's wall-clock guards.

    Some bots carry a catastrophe guard on how long one ``decide`` may take —
    ``models/knower.py`` has two — sized for the browser build, where the
    alternative to giving up is freezing the tab. An offline batch caller has no
    such constraint, and tripping a guard is the one thing that makes those bots'
    output depend on the wall clock, so lifting it out of the way there makes a
    result *more* reproducible rather than less. ``tools/bot_replay.py`` is why
    this exists.

    Opt-in and declarative, like ``aux_spec`` above: a module that declares a
    module-level ``BUDGET_SCALE`` float is multiplying its own budgets by it at
    call time, and is saying so. Anything that doesn't is left alone. Returns the
    names actually set, so a caller can report what it changed.

    Never call this from inside a model, and never mid-game: it is process-wide,
    so it must be set once at startup, before any ``decide`` runs.
    """
    touched: list[str] = []
    for name, fn in sorted(STRATEGIES.items()):
        module = sys.modules.get(getattr(fn, "__module__", "") or "")
        if module is not None and isinstance(getattr(module, "BUDGET_SCALE", None), (int, float)):
            module.BUDGET_SCALE = float(scale)
            touched.append(name)
    return touched


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
            sys.modules[spec.name] = module  # so dataclasses/typing resolve
            spec.loader.exec_module(module)
            fn = getattr(module, "decide", None)
            if callable(fn):
                register(path.stem, fn)
                loaded.append(path.stem)
        except Exception:  # noqa: BLE001 — one bad model mustn't break the rest
            continue
    return sorted(loaded)


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def _frontier_order(state: GameState, pid: int, sid: int, surplus: int, max_prod: int, params: AiParams):
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

        # What we actually have to out-fight, not what is parked there: a dug-in
        # defender fights at `config.DEFENDER_ADVANTAGE` times its ship count
        # (combat._apply_advantage). Without this the margins below understate
        # every target, and at a high setting the AI simply stops expanding —
        # it keeps waiting for a surplus that is already more than enough.
        garrison = n.ships * config.DEFENDER_ADVANTAGE

        if n.owner_id == 0:  # neutral -> expand
            if surplus >= math.ceil(garrison * params.expand_margin):
                score = _desirability(n, max_prod) / (travel * max(1, n.ships) ** 0.5)
                cand = (1, score + jitter(), nbr, surplus)  # commit fully: concentration wins
                best = _better(best, cand)
        else:  # enemy -> attack
            if surplus >= math.ceil(garrison * params.attack_margin):
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

    The frontier is the seed set, so a rear system flows toward whichever front is
    fewest hops away. See ``model.flow_field`` for the determinism this relies on.
    """
    return model.flow_field(state, owned, frontier)


register("heuristic", compute_orders)

"""council — knower's Search, advised by other bots and judged by a learned evaluator.

Every turn knower's Search compares candidate openings by playing each a few
turns forward and scoring where it lands. council changes three things:

* **A learned judge.** A rolled-out board is scored by `_council_eval.value`, a
  small network fitted offline on roster self-play that estimates the seat's
  chance of winning, in place of knower's hand-weighted material difference
  (which counts a fleet in transit at full value).
* **actuary as one more adviser.** actuary's move for our seat is offered as an
  opening, beside knower's own plans and the bots knower already borrows.
* **Each adviser plays its own line.** A borrowed opening is played forward by
  the bot that proposed it, not by knower's blind planner, so the search
  compares whole strategies rather than first moves.

knower.py is loaded a second time as a private module and only that copy is
patched; a real knower seat keeps its own module and globals. To every other bot
council is an oracle seat, modelled blind, as two knowers model each other.

``aux``: 0 is knower's Search exactly (the control); 1, the default, is council.

Rules kept, as for any oracle (CLAUDE.md, "The AI"): never calls
`ai.load_models`, reads rivals lazily through knower's own machinery, draws
nothing from `state.rng`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

IS_ORACLE = True

AUX_LABEL = "Council"
AUX_RANGE = (0, 1, 1)
AUX_INT = True
AUX_NAMES = ("Off (knower)", "On")

# Multiplies the private knower's wall-clock guards, as knower's own does. Pushed
# into the private copy on every call, since `ai.set_budget_scale` only reaches
# registered modules.
BUDGET_SCALE = 1.0

_HERE = Path(__file__).resolve().parent
_SEARCH_DEPTH = 2              # knower's "Search" stop
_ADVISERS = ("actuary",)       # on top of knower's own `EXTERNAL_CANDIDATES`
_SALT_POLICY = 51              # an adviser's line i plays on from salt _SALT_POLICY + i


def _load(stem: str, name: str):
    module = sys.modules.get(name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_ev = _load("_council_eval", "sc_council_eval")
_kn = _load("knower", "sc_council_knower")
_material_evaluate = _kn._evaluate
_knower_advance = _kn._advance
_knower_external_plan = _kn._external_plan
_KNOWER_EXTERNALS = _kn.EXTERNAL_CANDIDATES

# The private copy always searches.
_kn._seat_depth = lambda player: _SEARCH_DEPTH


class _Lazy:
    """A score computed only when compared.

    The search scores every line after every ply but only ever compares the
    scores the lines end on, so evaluating eagerly would spend the network on
    boards whose score is thrown away. The board is a rollout's own clone and is
    never stepped again, so holding it is safe.
    """

    __slots__ = ("board", "pid", "_v")

    def __init__(self, board, pid) -> None:
        self.board, self.pid, self._v = board, pid, None

    def get(self) -> float:
        if self._v is None:
            self._v = _ev.value(self.board, self.pid)
        return self._v

    def __gt__(self, other) -> bool:
        return self.get() > (other.get() if isinstance(other, _Lazy) else other)

    def __lt__(self, other) -> bool:
        return self.get() < (other.get() if isinstance(other, _Lazy) else other)


def _learned_evaluate(state, pid):
    if state.winner is not None:
        return 1.0 if state.winner == pid else 0.0
    return _Lazy(state, pid)


_SOURCE: dict[int, str] = {}       # id(opening) -> the adviser that proposed it


def _tagged_external_plan(state, pid, name, salt):
    plan = _knower_external_plan(state, pid, name, salt)
    if plan is not None:
        _SOURCE[id(plan)] = name
    return plan


_kn._external_plan = _tagged_external_plan


def _adviser_plan(state, pid, name, salt):
    """``name``'s orders for our seat on ``state``, or None if it can't give any."""
    from starconquest import ai
    fn = ai.STRATEGIES.get(name)
    if fn is None or _kn._is_oracle(fn):
        return None
    probe = _kn._clone(state, _kn._priv(state, pid, salt))
    probe.players[pid].ai_params.aux = 1.0
    try:
        orders = fn(probe, pid) or []
        return [o for o in orders if getattr(o, "owner_id", None) == pid]
    except Exception:                     # noqa: BLE001 — a broken bot is their bug
        return None


def _policy_advance(lines, pid, humans):
    """knower's `_advance`, except a borrowed opening is played on by its adviser."""
    out = []
    for i, line in enumerate(lines):
        if line.board.winner is not None:
            out.append(line)
            continue
        plan = None
        src = _SOURCE.get(id(line.root))
        if src is not None:
            plan = _adviser_plan(line.board, pid, src, _SALT_POLICY + i)
        if plan is None:
            plan = _kn._plan(line.board, pid, None)
        board = _kn._rollout(line.board, pid, plan, humans,
                             _kn._priv(line.board, pid, _kn._SALT_ROLLOUT))
        out.append(_kn._Line(line.root, board, _kn._evaluate(board, pid)))
    return out


def is_oracle_seat(player) -> bool:
    return True


def _on(player) -> bool:
    """Stop 1 unless the seat asks for 0; clamped on read, never rewritten."""
    try:
        return round(float(player.ai_params.aux)) >= 1
    except (AttributeError, TypeError, ValueError):
        return True


def decide(state, pid):
    on = _on(state.players[pid])
    _kn.EXTERNAL_CANDIDATES = _KNOWER_EXTERNALS + (_ADVISERS if on else ())
    _kn.BUDGET_SCALE = BUDGET_SCALE
    _kn._evaluate = _learned_evaluate if on else _material_evaluate
    _kn._advance = _policy_advance if on else _knower_advance
    _SOURCE.clear()
    return _kn.decide(state, pid)

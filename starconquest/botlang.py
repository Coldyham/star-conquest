"""A tiny rule language for bots, and an interpreter for it.

Pure core (no pygame), sibling to ``ai.py``. A ``Program`` is a flat, ordered
list of ``WHEN ... THEN ...`` rules over a *curated* vocabulary — the data model
behind an eventual in-app, drag-free rule builder, so a player who doesn't write
Python can still shape an AI beyond the AI tab's five sliders.

The bet this module tests is that a flat rule list is enough, and the encouraging
sign is that ``ai.compute_orders`` — the built-in ``heuristic`` — is already
structurally a three-rule program: hold when threatened, else reinforce or strike
from the frontier, else stream surplus toward the front. ``STARTERS`` re-expresses
it (``blockheuristic``) so ``tests/sim --ladder`` can measure how close it lands.

Evaluation mirrors ``compute_orders`` exactly. For each owned system, in sorted
id order, walk the rules and let the first *usable* one fire, producing **at most
one Order from that system** — the same cap the heuristic and ``engine.apply_order``
already assume, so a rule bot can never over-commit a garrison.

"Usable" is the one subtlety: a rule whose conditions hold but which has nothing
to spend or no legal target **falls through** to the next rule, so ``frontier ->
attack`` degrades naturally into a later ``send to the front``. ``hold`` is the
exception — it is a real action and stops evaluation. That fall-through is what
lets a useful bot be four rows long instead of fifteen.

Everything a rule means resolves to a helper that already exists: ``ai._threat``,
``ai._is_frontier``, ``ai._surplus``, ``ai._desirability`` and ``model.flow_field``
(the unweighted hop default the AI uses, not route mode's ``by_turns=True``).
They are imported under their private names deliberately — ``export`` vendors
their *source* into a generated model file, so the emitted Python is literally
the code this interpreter ran and the two cannot drift.

Tie-breaks draw from ``state.rng`` like ``ai._frontier_order``, ``thinker`` and
``rusherplus`` do, so a seed still reproduces a match. (The ``IS_ORACLE``
prohibition on drawing applies to bots that *predict* rivals; a rule bot doesn't.)

No ``AUX_LABEL`` is declared here on purpose: ``ai.aux_spec`` reads it off the
strategy function's module, and every block bot shares this one, so declaring it
would give them all the same meaningless slider. A program's knobs live in its
rules.
"""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import ai, config, model
from .ai import _desirability, _flow_to_frontier, _is_frontier, _surplus, _threat
from .model import AiParams, GameState, Order

# --------------------------------------------------------------------------- #
# The program
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Cond:
    """One test against a system we own. ``value`` is the condition's single knob,
    unused by the ones that take none."""

    kind: str
    value: float = 0.0


@dataclass(frozen=True)
class Rule:
    """``WHEN <every cond> THEN <action> <amount>``.

    Two numbers, each with a meaning the *table entry* defines rather than the
    field name — the same trick ``AiParams.aux`` plays. ``margin`` is a force
    ratio for the attack actions and a ships-of-exposure gap for ``reinforce_exposed``;
    ``amount_value`` is only read by the amounts that declare a knob.
    """

    when: tuple[Cond, ...] = ()
    action: str = "hold"
    margin: float = 1.5
    amount: str = "surplus"
    amount_value: float = 0.0


@dataclass(frozen=True)
class Program:
    name: str
    rules: tuple[Rule, ...] = ()


# --------------------------------------------------------------------------- #
# Per-turn derived context, computed once per decide()
# --------------------------------------------------------------------------- #


@dataclass
class _Ctx:
    """What every condition and action needs, derived once per turn per seat."""

    state: GameState
    pid: int
    params: AiParams
    owned: set[int]
    frontier: set[int]
    parent: dict[int, int]   # rear system -> next hop toward the nearest front
    max_prod: int
    rival_best: int          # systems held by the strongest rival


def _context(state: GameState, pid: int) -> _Ctx:
    owned = {s.id for s in state.systems.values() if s.owner_id == pid}
    frontier = {sid for sid in owned if _is_frontier(state, sid, pid)}
    counts: dict[int, int] = {}
    for s in state.systems.values():
        if s.owner_id != pid and s.owner_id != 0:
            counts[s.owner_id] = counts.get(s.owner_id, 0) + 1
    return _Ctx(
        state=state,
        pid=pid,
        params=state.players[pid].ai_params,
        owned=owned,
        frontier=frontier,
        parent=_flow_to_frontier(state, owned, frontier),
        max_prod=max(config.PRODUCTION_WEIGHTS),
        rival_best=max(counts.values(), default=0),
    )


def _needed(ctx: _Ctx, target: int, margin: float) -> int:
    """Ships required to take ``target`` at ``margin``, against the *effective*
    garrison — a dug-in defender fights at ``config.DEFENDER_ADVANTAGE`` times its
    count (``combat._apply_advantage``), which is what ``ai._frontier_order``
    measures its own margins against. Read live, never cached."""
    ships = ctx.state.systems[target].ships
    return max(1, math.ceil(ships * config.DEFENDER_ADVANTAGE * margin))


# --------------------------------------------------------------------------- #
# Conditions
# --------------------------------------------------------------------------- #


def _c_always(ctx: _Ctx, sid: int, v: float) -> bool:
    return True


def _c_frontier(ctx: _Ctx, sid: int, v: float) -> bool:
    return sid in ctx.frontier


def _c_interior(ctx: _Ctx, sid: int, v: float) -> bool:
    return sid not in ctx.frontier


def _c_threatened(ctx: _Ctx, sid: int, v: float) -> bool:
    return _threat(ctx.state, sid, ctx.pid) >= ctx.state.systems[sid].ships


def _c_safe(ctx: _Ctx, sid: int, v: float) -> bool:
    return _threat(ctx.state, sid, ctx.pid) < ctx.state.systems[sid].ships


def _c_ships_at_least(ctx: _Ctx, sid: int, v: float) -> bool:
    return ctx.state.systems[sid].ships >= v


def _c_production_richer(ctx: _Ctx, sid: int, v: float) -> bool:
    return ctx.state.systems[sid].production <= v


def _c_turn_at_least(ctx: _Ctx, sid: int, v: float) -> bool:
    return ctx.state.turn >= v


def _c_behind_leader(ctx: _Ctx, sid: int, v: float) -> bool:
    return len(ctx.owned) < ctx.rival_best


@dataclass(frozen=True)
class CondSpec:
    """A condition's label, its test, its source form, and the knob (if any) the
    editor should offer. ``src`` is an expression over ``state``/``pid``/``sid``/
    ``sys_``/``ctx``, with ``{v}`` for the knob."""

    label: str
    test: Callable[[_Ctx, int, float], bool]
    src: str
    knob: Optional[str] = None
    lo: float = 0.0
    hi: float = 0.0
    step: float = 1.0
    is_int: bool = True
    default: float = 0.0


CONDITIONS: dict[str, CondSpec] = {
    "always": CondSpec("always", _c_always, "True"),
    "frontier": CondSpec("on the frontier", _c_frontier, "sid in ctx.frontier"),
    "interior": CondSpec("in the rear", _c_interior, "sid not in ctx.frontier"),
    "threatened": CondSpec(
        "under threat", _c_threatened, "_threat(state, sid, pid) >= sys_.ships"),
    "safe": CondSpec(
        "not under threat", _c_safe, "_threat(state, sid, pid) < sys_.ships"),
    "ships_at_least": CondSpec(
        "garrison is at least", _c_ships_at_least, "sys_.ships >= {v}",
        knob="Ships", lo=0, hi=40, step=1, is_int=True, default=5),
    "production_richer": CondSpec(
        "production is at least as rich as", _c_production_richer,
        "sys_.production <= {v}",
        knob="Turns per ship", lo=2, hi=5, step=1, is_int=True, default=3),
    "turn_at_least": CondSpec(
        "the turn is at least", _c_turn_at_least, "state.turn >= {v}",
        knob="Turn", lo=0, hi=200, step=5, is_int=True, default=20),
    "behind_leader": CondSpec(
        "we are behind the leader", _c_behind_leader,
        "len(ctx.owned) < ctx.rival_best"),
}


# --------------------------------------------------------------------------- #
# Actions — each picks one target, or None to fall through to the next rule
# --------------------------------------------------------------------------- #


def _any_target(n) -> bool:
    return True


def _neutral_only(n) -> bool:
    return n.owner_id == 0


def _score_best(ctx: _Ctx, sid: int, n) -> float:
    """``ai._frontier_order``'s own scoring: richness per turn of travel, damped by
    how big the garrison is. Neutrals damp by sqrt (they never grow or retaliate),
    enemies linearly."""
    travel = ctx.state.travel_turns(sid, n.id) or 1
    damp = max(1, n.ships) ** (0.5 if n.owner_id == 0 else 1)
    return _desirability(n, ctx.max_prod) / (travel * damp)


def _score_weakest(ctx: _Ctx, sid: int, n) -> float:
    return -float(n.ships)


def _score_richest(ctx: _Ctx, sid: int, n) -> float:
    return _desirability(n, ctx.max_prod)


def _best_target(ctx: _Ctx, sid: int, pool: int, margin: float, allow, score) -> Optional[int]:
    """The highest-scoring non-owned neighbour we can actually afford at ``margin``.

    Neighbours are walked in sorted id order and scores carry a tiny ``state.rng``
    nudge, exactly as ``ai._frontier_order`` does, so ties break reproducibly
    rather than by dict order.
    """
    state, pid = ctx.state, ctx.pid
    best: Optional[tuple[float, int]] = None
    for nbr in sorted(state.systems[sid].neighbors):
        n = state.systems[nbr]
        if n.owner_id == pid or not allow(n):
            continue
        if pool < _needed(ctx, nbr, margin):
            continue
        value = score(ctx, sid, n) + state.rng.uniform(0.0, 0.01)
        if best is None or value > best[0]:
            best = (value, nbr)
    return None if best is None else best[1]


def _attack_best(ctx: _Ctx, sid: int, pool: int, margin: float) -> Optional[int]:
    return _best_target(ctx, sid, pool, margin, _any_target, _score_best)


def _attack_weakest(ctx: _Ctx, sid: int, pool: int, margin: float) -> Optional[int]:
    return _best_target(ctx, sid, pool, margin, _any_target, _score_weakest)


def _attack_richest(ctx: _Ctx, sid: int, pool: int, margin: float) -> Optional[int]:
    return _best_target(ctx, sid, pool, margin, _any_target, _score_richest)


def _expand_neutral(ctx: _Ctx, sid: int, pool: int, margin: float) -> Optional[int]:
    return _best_target(ctx, sid, pool, margin, _neutral_only, _score_best)


def _reinforce_exposed(ctx: _Ctx, sid: int, pool: int, margin: float) -> Optional[int]:
    """The frontier neighbour meaningfully more exposed than we are.

    Requiring a gap (``ai.AI_REINFORCE_MARGIN``'s job) makes reinforcement
    one-directional, so two adjacent frontier systems don't trade ships every turn.
    """
    state, pid = ctx.state, ctx.pid
    self_deficit = _threat(state, sid, pid) - state.systems[sid].ships
    best: Optional[tuple[float, int]] = None
    for nbr in sorted(state.systems[sid].neighbors):
        n = state.systems[nbr]
        if n.owner_id != pid or not _is_frontier(state, nbr, pid):
            continue
        deficit = _threat(state, nbr, pid) - n.ships
        if deficit <= 0 or deficit - self_deficit < margin:
            continue
        value = deficit + state.rng.uniform(0.0, 0.01)
        if best is None or value > best[0]:
            best = (value, nbr)
    return None if best is None else best[1]


def _send_to_front(ctx: _Ctx, sid: int, pool: int, margin: float) -> Optional[int]:
    return ctx.parent.get(sid)


@dataclass(frozen=True)
class ActionSpec:
    """``pick`` is None only for ``hold``, which is handled before any target is
    sought. ``margin_label`` both names the knob and marks the actions whose
    margin is a force ratio, which is what ``enough`` needs to know."""

    label: str
    pick: Optional[Callable[[_Ctx, int, int, float], Optional[int]]]
    fn_name: str = ""
    margin_label: Optional[str] = None
    lo: float = 1.0
    hi: float = 3.0
    step: float = 0.1
    is_int: bool = False
    default: float = 1.5


ACTIONS: dict[str, ActionSpec] = {
    "hold": ActionSpec("hold", None),
    "attack_best": ActionSpec(
        "attack the best target", _attack_best, "_attack_best",
        margin_label="Force margin", default=1.4),
    "attack_weakest": ActionSpec(
        "attack the weakest neighbour", _attack_weakest, "_attack_weakest",
        margin_label="Force margin", default=1.2),
    "attack_richest": ActionSpec(
        "attack the richest neighbour", _attack_richest, "_attack_richest",
        margin_label="Force margin", default=1.5),
    "expand_neutral": ActionSpec(
        "take a neutral system", _expand_neutral, "_expand_neutral",
        margin_label="Force margin", default=1.3),
    "reinforce_exposed": ActionSpec(
        "reinforce the most exposed neighbour", _reinforce_exposed, "_reinforce_exposed",
        margin_label="Exposure gap", lo=0, hi=10, step=1, is_int=True, default=2),
    "send_to_front": ActionSpec(
        "send toward the nearest front", _send_to_front, "_send_to_front"),
}


# --------------------------------------------------------------------------- #
# Amounts — how much of the garrison this rule is willing to spend
# --------------------------------------------------------------------------- #


def _p_surplus(ctx: _Ctx, ships: int, v: float) -> int:
    return _surplus(ships, ctx.params)


def _p_all(ctx: _Ctx, ships: int, v: float) -> int:
    return ships


def _p_all_but(ctx: _Ctx, ships: int, v: float) -> int:
    return ships - int(v)


def _p_half(ctx: _Ctx, ships: int, v: float) -> int:
    return ships // 2


@dataclass(frozen=True)
class AmountSpec:
    label: str
    pool: Callable[[_Ctx, int, float], int]
    src: str
    knob: Optional[str] = None
    lo: float = 0.0
    hi: float = 0.0
    step: float = 1.0
    is_int: bool = True
    default: float = 0.0


AMOUNTS: dict[str, AmountSpec] = {
    # The one deliberate point of contact with the AI tab: `surplus` reads the
    # seat's reserve sliders, so those two knobs keep working for a block bot.
    "surplus": AmountSpec(
        "its surplus", _p_surplus, "_surplus(sys_.ships, ctx.params)"),
    "all": AmountSpec("everything", _p_all, "sys_.ships"),
    "all_but": AmountSpec(
        "all but a few", _p_all_but, "sys_.ships - {v}",
        knob="Keep", lo=0, hi=20, step=1, is_int=True, default=1),
    "half": AmountSpec("half its garrison", _p_half, "sys_.ships // 2"),
    # Target-dependent, so its pool is resolved in _pool and trimmed in _ships.
    "enough": AmountSpec("just enough to win", _p_all, "sys_.ships"),
}


# --------------------------------------------------------------------------- #
# The interpreter
# --------------------------------------------------------------------------- #


def _matches(ctx: _Ctx, sid: int, rule: Rule) -> bool:
    for cond in rule.when:
        spec = CONDITIONS.get(cond.kind)
        if spec is None or not spec.test(ctx, sid, cond.value):
            return False
    return True


def _pool(ctx: _Ctx, sid: int, rule: Rule) -> int:
    """How many ships this rule may spend, before a target is known."""
    ships = ctx.state.systems[sid].ships
    action = ACTIONS[rule.action]
    if rule.amount == "enough":
        # Spend up to everything for a margin action (we trim to what's needed);
        # for one with nothing to be "enough" for, fall back to the surplus.
        return ships if action.margin_label else _surplus(ships, ctx.params)
    return AMOUNTS[rule.amount].pool(ctx, ships, rule.amount_value)


def _ships(ctx: _Ctx, rule: Rule, pool: int, target: int) -> int:
    if rule.amount != "enough" or not ACTIONS[rule.action].margin_label:
        return pool
    return max(1, min(pool, _needed(ctx, target, rule.margin)))


def _decide_system(program: Program, ctx: _Ctx, sid: int) -> Optional[Order]:
    for rule in program.rules:
        if rule.action not in ACTIONS or rule.amount not in AMOUNTS:
            continue                       # tolerant, like the rest of the contract
        if not _matches(ctx, sid, rule):
            continue
        if rule.action == "hold":
            return None                    # a real action: it stops evaluation
        pool = _pool(ctx, sid, rule)
        if pool <= 0:
            continue                       # nothing to spend -> the next rule tries
        pick = ACTIONS[rule.action].pick
        target = pick(ctx, sid, pool, rule.margin) if pick is not None else None
        if target is None:
            continue                       # no legal target -> the next rule tries
        return Order(ctx.pid, sid, target, _ships(ctx, rule, pool, target))
    return None


def run(program: Program, state: GameState, pid: int) -> list[Order]:
    """Play one turn of ``program`` for seat ``pid``. The shape ``ai.DecideFn`` wants."""
    ctx = _context(state, pid)
    if not ctx.owned:
        return []
    orders: list[Order] = []
    for sid in sorted(ctx.owned):
        order = _decide_system(program, ctx, sid)
        if order is not None:
            orders.append(order)
    return orders


def strategy(program: Program) -> ai.DecideFn:
    """Wrap a program as a registrable ``decide(state, pid) -> list[Order]``."""

    def _decide(state: GameState, pid: int) -> list[Order]:
        return run(program, state, pid)

    _decide.__name__ = f"decide_{program.name}"
    return _decide


# --------------------------------------------------------------------------- #
# Readable descriptions (export comments today, rule rows in an editor later)
# --------------------------------------------------------------------------- #


def _fmt(value: float, is_int: bool) -> str:
    return str(int(round(value))) if is_int else f"{value:g}"


def describe_cond(cond: Cond) -> str:
    spec = CONDITIONS.get(cond.kind)
    if spec is None:
        return f"<unknown condition {cond.kind}>"
    if spec.knob is None:
        return spec.label
    return f"{spec.label} {_fmt(cond.value, spec.is_int)}"


def describe(rule: Rule) -> str:
    """``"when on the frontier -> attack the best target (force margin 1.4), sending its surplus"``."""
    action = ACTIONS.get(rule.action)
    amount = AMOUNTS.get(rule.amount)
    when = " and ".join(describe_cond(c) for c in rule.when) or "always"
    if action is None:
        return f"when {when} -> <unknown action {rule.action}>"
    text = f"when {when} -> {action.label}"
    if action.margin_label:
        text += f" ({action.margin_label.lower()} {_fmt(rule.margin, action.is_int)})"
    if action.pick is None or amount is None:
        return text
    label = amount.label
    if amount.knob:
        label = f"{label} (keeping {_fmt(rule.amount_value, amount.is_int)})"
    return f"{text}, sending {label}"


# --------------------------------------------------------------------------- #
# Serialization — the bridge an editor and a share link will need. Tolerant in
# the house style of `settings._ai_from_dict` / `replay.GameLog.from_dict`:
# unknown keys ignored, missing defaulted, junk dropped rather than raised.
# --------------------------------------------------------------------------- #


def to_dict(program: Program) -> dict:
    return {
        "name": program.name,
        "rules": [
            {
                "when": [{"kind": c.kind, "value": c.value} for c in r.when],
                "action": r.action,
                "margin": r.margin,
                "amount": r.amount,
                "amount_value": r.amount_value,
            }
            for r in program.rules
        ],
    }


def _cond_from_dict(data) -> Optional[Cond]:
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    if kind not in CONDITIONS:
        return None
    try:
        return Cond(kind, float(data.get("value", 0.0)))
    except (TypeError, ValueError):
        return Cond(kind, 0.0)


def _rule_from_dict(data) -> Optional[Rule]:
    if not isinstance(data, dict):
        return None
    action = data.get("action", "hold")
    amount = data.get("amount", "surplus")
    if action not in ACTIONS or amount not in AMOUNTS:
        return None
    conds = tuple(c for c in (_cond_from_dict(d) for d in data.get("when", []) or ()) if c)

    def _num(key: str, fallback: float) -> float:
        try:
            return float(data.get(key, fallback))
        except (TypeError, ValueError):
            return fallback

    return Rule(conds, action, _num("margin", ACTIONS[action].default),
                amount, _num("amount_value", AMOUNTS[amount].default))


def from_dict(data) -> Program:
    if not isinstance(data, dict):
        return Program("untitled")
    name = data.get("name")
    rules = tuple(r for r in (_rule_from_dict(d) for d in data.get("rules", []) or ()) if r)
    return Program(str(name) if isinstance(name, str) and name.strip() else "untitled", rules)


# --------------------------------------------------------------------------- #
# Starter programs
# --------------------------------------------------------------------------- #
# Templates to copy in an editor, worked examples of the vocabulary, and the
# test corpus that decides whether this language is worth an editor at all.

_BLOCKRUSH = Program("blockrush", (
    Rule((), "attack_weakest", 1.2, "all_but", 1),
    Rule((), "send_to_front", 0.0, "all_but", 1),
))

# Consolidate first, strike late: take neutrals cheaply with only the ships the
# job needs, and don't touch an enemy until a system has really massed. The
# `ships_at_least` gate is what makes it patient rather than merely timid — an
# earlier draft with no enemy rule at all could never take an enemy system, and
# so could never win a game, which is the trap this vocabulary makes easiest to
# fall into and easiest to see.
_BLOCKTURTLE = Program("blockturtle", (
    Rule((Cond("threatened"),), "hold"),
    Rule((Cond("frontier"),), "reinforce_exposed", 1, "surplus"),
    Rule((Cond("frontier"),), "expand_neutral", 1.5, "enough"),
    Rule((Cond("frontier"), Cond("ships_at_least", 12)), "attack_best", 1.8, "surplus"),
    Rule((), "send_to_front", 0.0, "surplus"),
))

# `ai.compute_orders` re-expressed, and the reason this spike exists. Not
# bit-identical: the heuristic resolves reinforce-vs-attack-vs-expand in one
# scoring pass, while rule order spreads that priority over three rules, so the
# rng streams diverge. Comparable strength is the goal, and it gets there —
# ~48% head-to-head over 64 finished games.
#
# Separate expand and attack rules matter: the heuristic gates neutrals at
# `AI_EXPAND_MARGIN` and enemies at the stricter `AI_ATTACK_MARGIN`, and folding
# both into one `attack_best` at a split-the-difference 1.4 cost ~13 points.
# The margins are read from `config` rather than written out, so this program
# tracks the heuristic if those defaults are ever retuned.
_BLOCKHEURISTIC = Program("blockheuristic", (
    Rule((Cond("threatened"),), "hold"),
    Rule((Cond("frontier"),), "reinforce_exposed", config.AI_REINFORCE_MARGIN, "surplus"),
    Rule((Cond("frontier"),), "expand_neutral", config.AI_EXPAND_MARGIN, "surplus"),
    Rule((Cond("frontier"),), "attack_best", config.AI_ATTACK_MARGIN, "surplus"),
    Rule((), "send_to_front", 0.0, "surplus"),
))

STARTERS: dict[str, Program] = {
    p.name: p for p in (_BLOCKRUSH, _BLOCKTURTLE, _BLOCKHEURISTIC)
}


def register_starters() -> list[str]:
    """Make every starter selectable per seat, like a drop-in ``models/`` file.

    Unlike ``ai.load_models`` this needs no re-running — starters live in this
    module, not on disk, so there are no edits to pick up. Registration is
    process-wide, so calling it once at boot is enough for the menu's Strategy
    dropdown to list them.
    """
    for name, program in STARTERS.items():
        ai.register(name, strategy(program))
    return sorted(STARTERS)


# --------------------------------------------------------------------------- #
# Export to a standalone models/*.py
# --------------------------------------------------------------------------- #
# The graduation path from rules to real code. The generated file is a normal
# drop-in bot: it imports nothing private, vendors its helpers the way
# `thinker.py` and `knower.py` do, and never calls back into this module.
#
# Those helpers are emitted with `inspect.getsource`, so what runs in the file is
# *literally* the source this interpreter ran — they cannot drift. The per-rule
# code above them is generated from the same `src` templates that sit beside each
# table entry's evaluator, and `tests/test_botlang.py` pins the pair by running
# both implementations over several seeds and demanding identical orders.

_EXPORT_HELPERS = (
    _surplus, _is_frontier, _threat, _desirability, _flow_to_frontier,
    _Ctx, _context, _needed,
    _any_target, _neutral_only, _score_best, _score_weakest, _score_richest,
    _best_target, _attack_best, _attack_weakest, _attack_richest,
    _expand_neutral, _reinforce_exposed, _send_to_front,
)

_EXPORT_HEADER = '''"""{name} — generated from a visual rule program by starconquest.botlang.

{rules}

A normal drop-in model: `decide(state, pid) -> list[Order]`. Edit it freely — it
is plain Python from here, and nothing links it back to the program it came from.

Rules are tried in order for each system you own, and the first one that can act
fires, issuing at most one order from that system. A rule whose conditions hold
but which has nothing to spend or no legal target falls through to the next;
`hold` is the exception and stops there.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional

from starconquest import config, model
from starconquest.model import AiParams, GameState, Order


# --- helpers, vendored verbatim from starconquest.ai / starconquest.botlang --- #
'''


def _cond_src(cond: Cond) -> str:
    spec = CONDITIONS[cond.kind]
    return spec.src.format(v=_fmt(cond.value, spec.is_int))


def _pool_src(rule: Rule) -> str:
    action = ACTIONS[rule.action]
    if rule.amount == "enough":
        return "sys_.ships" if action.margin_label else "_surplus(sys_.ships, ctx.params)"
    spec = AMOUNTS[rule.amount]
    return spec.src.format(v=_fmt(rule.amount_value, spec.is_int))


def _rule_src(rule: Rule, index: int) -> list[str]:
    """One rule as source lines, indented to sit inside the per-system loop."""
    action = ACTIONS[rule.action]
    tests = [_cond_src(c) for c in rule.when if CONDITIONS[c.kind].src != "True"]
    pad = " " * (12 if tests else 8)
    lines = [f"        # Rule {index}: {describe(rule)}"]
    if tests:
        lines.append(f"        if {' and '.join(tests)}:")
    if rule.action == "hold":
        lines.append(f"{pad}continue")
        return lines
    margin = _fmt(rule.margin, action.is_int)
    ships = "pool"
    if rule.amount == "enough" and action.margin_label:
        ships = f"max(1, min(pool, _needed(ctx, target, {margin})))"
    lines += [
        f"{pad}pool = {_pool_src(rule)}",
        f"{pad}target = {action.fn_name}(ctx, sid, pool, {margin}) if pool > 0 else None",
        f"{pad}if target is not None:",
        f"{pad}    orders.append(Order(pid, sid, target, {ships}))",
        f"{pad}    continue",
    ]
    return lines


def export(program: Program) -> str:
    """``program`` as the source of a standalone, editable ``models/*.py`` bot."""
    usable = [r for r in program.rules if r.action in ACTIONS and r.amount in AMOUNTS]
    summary = "\n".join(f"  {i}. {describe(r)}" for i, r in enumerate(usable, 1)) or "  (no rules)"
    parts = [_EXPORT_HEADER.format(name=program.name, rules=summary)]
    parts += [inspect.getsource(obj).rstrip() + "\n" for obj in _EXPORT_HELPERS]
    body = [
        "",
        "# --- the program ----------------------------------------------------------- #",
        "def decide(state, pid):",
        "    ctx = _context(state, pid)",
        "    if not ctx.owned:",
        "        return []",
        "    orders = []",
        "    for sid in sorted(ctx.owned):",
        "        sys_ = state.systems[sid]",
    ]
    for i, rule in enumerate(usable, 1):
        body.append("")
        body += _rule_src(rule, i)
    body += ["", "    return orders", ""]
    return "\n".join(parts) + "\n".join(body)

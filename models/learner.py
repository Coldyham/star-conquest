"""learner — a model of each rival, read off the board and kept from turn to turn.

Every rival decides on the board we saw last turn, since turns resolve
simultaneously, and what it decided is on the board we see now: its fresh
fleets, and the ships a one-turn lane carried off unseen. learner keeps the last
board, so each turn it can pair what a rival saw with what it did, and fold that
into a running model of the rival:

  * **A strike curve.** For each of a rival's systems and each neighbour it does
    not hold, the ratio of its garrison to the neighbour's effective garrison on
    arrival, and whether it struck. Counted per ratio bin, split by neutral or
    player target and by whether the rival's system had hostile ships inbound,
    then fitted monotone (`strike_curve`).
  * **How many ships a strike sends.** Whether it sends all its garrison, and if
    not, what it sends against the target's effective garrison (`strike_ships`).
  * **What a frontier system keeps home**, against its largest adjacent enemy.
  * **Whether a doomed system leaves.**

`predict` turns a rival's model into the launches it should make at our systems
this turn, each with a probability and an expected size.

**As a bot it is actuary, played on a board with those launches on it.** Each
predicted launch at one of our systems goes onto a private copy of the board as
a fleet of its expected size (chance times ships), with its source left
standing, so a prediction can only add a threat, and actuary's ledger plans
against that copy. The seat's ``ai_params.aux`` (``AUX_LABEL``: *Trust*) is *Raise*
(that, the default) or *Off* (nothing added: the seat plays exactly as actuary).

**Memory is keyed by the game's path.** Each board is a node in a memo tree,
found by content: a node's parent is a stored board one turn earlier that this
board provably follows from (`_follows`), and its models are the parent's plus
what that turn showed. The same board always finds the same node, a branch gets
its own, and a board with no known parent starts from the prior (a cold start).

It reads other seats from the board, never by running their code, but it
advertises ``IS_ORACLE`` so an oracle (knower) models it rather than running a
``decide`` whose answer depends on what it remembers. docs/design/learner.md has
why memory is keyed this way and the measurements.

Contract: ``decide(state, pid) -> list[Order]``. Draws nothing from ``state.rng``
or the ``random`` module, and never mutates the state.
"""

from __future__ import annotations

import copy
import hashlib
import random
import sys
from collections import Counter, OrderedDict, defaultdict
from typing import NamedTuple

from starconquest import ai, config
from starconquest.model import Fleet

# --- tunables -------------------------------------------------------------- #
NODE_CAP = 512              # boards remembered, least recently used dropped first
RATIO_BINS = 31             # a ratio in tenths; the last bin is 3.0 and up
GUARD_BINS = 21             # kept / largest adjacent enemy in tenths; the last is 2.0 and up
ALL_IN = 0.9                # a strike sending this share of its garrison is all-in
PRIOR_WEIGHT = 4.0          # observations the prior is worth, per bin

OFF, RAISE = 0, 1           # the Trust knob's stops; anything above or unreadable is Raise
READ_MS = 1.0               # what the memo and `predict` add to actuary's decide, ms

NEUTRAL, PLAYER = 0, 1      # target kinds
_ROWS = 4                   # (kind, pressed) pairs, row = kind * 2 + pressed

# --- the prior -------------------------------------------------------------- #
# Pooled over the roster in self-play, all four cells, seeds 1001-1040
# (`tools/learner_check.py --fit-prior`). A rival nobody has watched yet is
# assumed to play like the average of the roster.
PRIOR_STRIKE = (
    # neutral, unpressed
    (0.004, 0.007, 0.009, 0.012, 0.018, 0.018, 0.020, 0.020, 0.020, 0.023, 0.023,
     0.024, 0.038, 0.192, 0.207, 0.290, 0.290, 0.290, 0.324, 0.333, 0.333, 0.333,
     0.333, 0.333, 0.333, 0.333, 0.333, 0.333, 0.333, 0.333, 0.333),
    # neutral, pressed
    (0.008, 0.008, 0.017, 0.017, 0.017, 0.017, 0.017, 0.017, 0.023, 0.023, 0.023,
     0.023, 0.036, 0.091, 0.091, 0.091, 0.167, 0.167, 0.250, 0.379, 0.379, 0.379,
     0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379),
    # player, unpressed
    (0.034, 0.034, 0.034, 0.035, 0.035, 0.040, 0.040, 0.040, 0.040, 0.040, 0.061,
     0.061, 0.100, 0.128, 0.128, 0.155, 0.212, 0.224, 0.254, 0.254, 0.278, 0.278,
     0.325, 0.329, 0.329, 0.329, 0.329, 0.329, 0.329, 0.329, 0.329),
    # player, pressed
    (0.057, 0.077, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.131,
     0.131, 0.168, 0.177, 0.218, 0.286, 0.286, 0.297, 0.297, 0.336, 0.375, 0.375,
     0.375, 0.375, 0.375, 0.375, 0.375, 0.426, 0.426, 0.426, 0.445),
)
PRIOR_ALLIN = 0.464
PRIOR_SIZE = (0.032, 0.050, 0.058, 0.043, 0.031, 0.053, 0.034, 0.023, 0.024, 0.005, 0.076,
              0.023, 0.059, 0.092, 0.061, 0.084, 0.039, 0.014, 0.013, 0.004, 0.073, 0.002,
              0.005, 0.005, 0.001, 0.007, 0.004, 0.002, 0.001, 0.000, 0.081)
PRIOR_GUARD = (0.517, 0.018, 0.019, 0.043, 0.036, 0.077, 0.068, 0.027, 0.017, 0.005, 0.059,
               0.008, 0.009, 0.006, 0.003, 0.008, 0.004, 0.002, 0.002, 0.000, 0.071)
PRIOR_EVAC = 0.531


class Threat(NamedTuple):
    """A launch `predict` expects from a rival this turn."""

    rival: int
    source: int
    target: int
    turns: int
    ships: float        # ships sent if it strikes
    p: float            # chance it strikes this target from this source


class Model:
    """What one rival has been seen to do: raw counts, the prior added on read."""

    __slots__ = ("strikes", "passes", "allin", "size", "guard", "evac", "turns")
    COUNTS = __slots__[:-1]

    def __init__(self, strikes, passes, allin, size, guard, evac, turns):
        self.strikes = strikes      # per (kind, pressed, target ratio bin): struck
        self.passes = passes        # ...and not struck
        self.allin = allin          # (strikes sending all-in, strikes sized to a target)
        self.size = size            # per ratio bin: a sized strike's ships / target
        self.guard = guard          # per bin: kept home / largest adjacent enemy
        self.evac = evac            # (doomed and left, doomed and stayed)
        self.turns = turns

    def plus(self, other: Model) -> Model:
        return Model(*(_add(getattr(self, name), getattr(other, name))
                       for name in self.COUNTS), self.turns + other.turns)


EMPTY = Model((0.0,) * (_ROWS * RATIO_BINS), (0.0,) * (_ROWS * RATIO_BINS), (0.0, 0.0),
              (0.0,) * RATIO_BINS, (0.0,) * GUARD_BINS, (0.0, 0.0), 0)


def _add(a: tuple, b: tuple) -> tuple:
    return tuple(x + y for x, y in zip(a, b))


# --------------------------------------------------------------------------- #
# Boards and the memo tree
# --------------------------------------------------------------------------- #
class _Snap:
    """A board as the turn began: everything a rival could have read off it."""

    __slots__ = ("turn", "owner", "ships", "progress", "fleets", "key")

    def __init__(self, state):
        sids = sorted(state.systems)
        systems = state.systems
        self.turn = state.turn
        self.owner = {sid: systems[sid].owner_id for sid in sids}
        self.ships = {sid: systems[sid].ships for sid in sids}
        self.progress = {sid: systems[sid].prod_progress for sid in sids}
        self.fleets = tuple(sorted((f.owner_id, f.source_id, f.dest_id, f.ships,
                                    f.turns_total, f.turns_remaining)
                                   for f in state.fleets))
        self.key = (self.turn,
                    tuple((sid, self.owner[sid], self.ships[sid], self.progress[sid])
                          for sid in sids),
                    self.fleets)


class _Node:
    __slots__ = ("snap", "parent", "models")

    def __init__(self, snap, parent, models):
        self.snap = snap
        self.parent = parent
        self.models = models


_NODES: OrderedDict = OrderedDict()     # (game key, board key) -> _Node
_AT: dict = {}                          # (game key, turn) -> [board key, ...]


def reset() -> None:
    """Forget every board."""
    _NODES.clear()
    _AT.clear()


def _game_key(state) -> bytes:
    """The map and the rules a board is played under. Boards on different maps,
    or under different combat or speed settings, never share a path."""
    topology = (tuple(sorted((sid, tuple(sorted(nbrs.items())))
                             for sid, nbrs in state.adjacency.items())),
                tuple(sorted((sid, s.production) for sid, s in state.systems.items())),
                config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE, config.NEUTRAL_PRODUCES,
                config.IN_LANE_BATTLES, config.SHIP_SPEED_GROWTH_PCT)
    return hashlib.blake2b(repr(topology).encode(), digest_size=16).digest()


def _node_for(state) -> _Node:
    """This board's node, made (and its parent found) if it is new."""
    game = _game_key(state)
    snap = _Snap(state)
    key = (game, snap.key)
    node = _NODES.get(key)
    if node is not None:
        _NODES.move_to_end(key)
        return node
    parent = None
    for board in sorted(_AT.get((game, snap.turn - 1), ())):
        candidate = _NODES[(game, board)]
        if _follows(candidate.snap, snap, state):
            parent = candidate
            break
    if parent is None:
        node = _Node(snap, None, {})
    else:
        node = _Node(snap, (game, parent.snap.key),
                     _fold(parent.models, observe(parent.snap, snap, state)))
    _NODES[key] = node
    _AT.setdefault((game, snap.turn), []).append(snap.key)
    while len(_NODES) > NODE_CAP:
        (old_game, old_board), old = _NODES.popitem(last=False)
        boards = _AT[(old_game, old.snap.turn)]
        boards.remove(old_board)
        if not boards:
            del _AT[(old_game, old.snap.turn)]
    return node


def models_for(state) -> dict[int, Model]:
    """Every player's model as of this board (a player never seen is absent:
    read it as `EMPTY`, which is the prior alone)."""
    return _node_for(state).models


def _fold(models: dict[int, Model], seen: dict[int, Model]) -> dict[int, Model]:
    out = dict(models)
    for pid, obs in seen.items():
        out[pid] = out.get(pid, EMPTY).plus(obs)
    return out


def _one_turn_lane(state, sid: int) -> bool:
    return any(state.travel_turns(sid, n) == 1 for n in state.systems[sid].neighbors)


def _hulls(state, snap: _Snap, sid: int, turns: int) -> int:
    """Ships `sid` builds in the next `turns` turns if nobody takes it, as
    `engine._production` builds them."""
    production = state.systems[sid].production
    if production <= 0 or (snap.owner[sid] == 0 and not config.NEUTRAL_PRODUCES):
        return 0
    return (snap.progress[sid] + turns) // production


def _follows(prev: _Snap, cur: _Snap, state) -> bool:
    """Whether `cur` is the board one `end_turn` makes from `prev`: every fleet
    still flying moved one step, every fresh fleet left a system its owner held
    with the ships to send, and a system no fleet could have reached unseen kept
    its owner and changed by exactly its production less its launches."""
    if cur.turn != prev.turn + 1:
        return False
    lane_battles = config.IN_LANE_BATTLES
    expected: Counter = Counter()
    landing: set[int] = set()
    for owner, src, dst, ships, total, left in prev.fleets:
        if left > 1:
            expected[(owner, src, dst, total, left - 1) if lane_battles
                     else (owner, src, dst, ships, total, left - 1)] += 1
        else:
            landing.add(dst)
    seen: Counter = Counter()
    launched: Counter = Counter()
    for owner, src, dst, ships, total, left in cur.fleets:
        if left == total - 1:
            if prev.owner.get(src) != owner:
                return False
            launched[src] += ships
        else:
            seen[(owner, src, dst, total, left) if lane_battles
                 else (owner, src, dst, ships, total, left)] += 1
    if lane_battles:
        if any(count > expected[k] for k, count in seen.items()):
            return False
    elif seen != expected:
        return False
    if any(ships > prev.ships[src] for src, ships in launched.items()):
        return False
    for sid in prev.owner:
        if sid in landing or _one_turn_lane(state, sid):
            continue
        if cur.owner.get(sid) != prev.owner[sid]:
            return False
        if cur.ships[sid] != prev.ships[sid] + _hulls(state, prev, sid, 1) - launched[sid]:
            return False
    return True




# --------------------------------------------------------------------------- #
# One turn's evidence
# --------------------------------------------------------------------------- #
class _Option(NamedTuple):
    target: int
    kind: int
    turns: int
    eff: float
    ratio: float


def _ratio_bin(ratio: float) -> int:
    return min(RATIO_BINS - 1, max(0, int(ratio * 10)))


def _share_bin(share: float, bins: int) -> int:
    return min(bins - 1, max(0, int(share * 10)))


def _effective(state, snap: _Snap, sid: int, turns: int) -> float:
    """What a strike landing on `sid` in `turns` turns has to beat: the garrison,
    the hulls it builds by then and its holder's fleets landing by then, with the
    defender's advantage. Never below half a ship, so a ratio stays finite."""
    owner = snap.owner[sid]
    ships = snap.ships[sid] + _hulls(state, snap, sid, turns)
    for f_owner, _src, dst, f_ships, _total, left in snap.fleets:
        if dst == sid and f_owner == owner and left <= turns:
            ships += f_ships
    return max(ships * config.DEFENDER_ADVANTAGE, 0.5)


def _hostile_inbound(snap: _Snap) -> dict[int, int]:
    """Ships flying at each system from anyone but its holder."""
    out: dict[int, int] = defaultdict(int)
    for owner, _src, dst, ships, _total, _left in snap.fleets:
        if owner != snap.owner[dst]:
            out[dst] += ships
    return out


def _largest_enemy(snap: _Snap, state, sid: int, q: int) -> int:
    return max((snap.ships[n] for n in state.systems[sid].neighbors
                if snap.owner[n] not in (0, q)), default=0)


def _options(snap: _Snap, state, sid: int) -> list[_Option]:
    """Every neighbour of `sid` its holder does not hold, priced as a target."""
    q = snap.owner[sid]
    garrison = snap.ships[sid]
    out = []
    for n in sorted(state.systems[sid].neighbors):
        holder = snap.owner[n]
        if holder == q:
            continue
        turns = state.travel_turns(sid, n) or 1
        eff = _effective(state, snap, n, turns)
        out.append(_Option(n, NEUTRAL if holder == 0 else PLAYER, turns, eff, garrison / eff))
    return out


def observe(prev: _Snap, cur: _Snap, state) -> dict[int, Model]:
    """What each player did on the turn from `prev` to `cur`, as counts. `state`
    is read for the map alone."""
    sent: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for _owner, src, dst, ships, total, left in cur.fleets:
        if left == total - 1:
            sent[src][dst] += ships
    landing = {dst for _o, _s, dst, _n, _t, left in prev.fleets if left <= 1}
    hostile = _hostile_inbound(prev)

    counts: dict[int, dict] = {}
    for sid in sorted(prev.owner):
        q = prev.owner[sid]
        garrison = prev.ships[sid]
        if q == 0 or garrison <= 0:
            continue
        out = dict(sent.get(sid, {}))
        unknown = 0
        one_turn = sorted(n for n in state.systems[sid].neighbors
                          if state.travel_turns(sid, n) == 1)
        if one_turn and sid not in landing and cur.owner.get(sid) == q:
            unseen = (garrison + _hulls(state, prev, sid, 1) - sum(out.values())
                      - cur.ships[sid])
            if unseen > 0:
                if len(one_turn) == 1:
                    out[one_turn[0]] = out.get(one_turn[0], 0) + unseen
                else:
                    unknown = unseen
        launched = sum(out.values()) + unknown

        c = counts.setdefault(q, {name: list(getattr(EMPTY, name)) for name in Model.COUNTS})
        pressed = 1 if hostile.get(sid, 0) > 0 else 0
        for option in _options(prev, state, sid):
            cell = (option.kind * 2 + pressed) * RATIO_BINS + _ratio_bin(option.ratio)
            ships = out.get(option.target, 0)
            if ships <= 0:
                c["passes"][cell] += 1
                continue
            c["strikes"][cell] += 1
            if ships >= ALL_IN * garrison:
                c["allin"][0] += 1
            else:
                c["allin"][1] += 1
                c["size"][_ratio_bin(ships / option.eff)] += 1
        threat = _largest_enemy(prev, state, sid, q)
        if threat > 0 and launched > 0:
            c["guard"][_share_bin((garrison - launched) / threat, GUARD_BINS)] += 1
        if hostile.get(sid, 0) > garrison * config.DEFENDER_ADVANTAGE:
            c["evac"][0 if 2 * launched >= garrison else 1] += 1

    return {q: Model(*(tuple(c[name]) for name in Model.COUNTS), 1)
            for q, c in counts.items()}


# --------------------------------------------------------------------------- #
# Reading a model
# --------------------------------------------------------------------------- #
def _monotone(values: list[float], weights: list[float]) -> list[float]:
    """The non-decreasing fit closest to `values` (pool adjacent violators)."""
    blocks: list[list[float]] = []
    for v, w in zip(values, weights):
        blocks.append([v, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks[-1]
            blocks[-1] = [(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, c1 + c2]
    out: list[float] = []
    for v, _w, c in blocks:
        out += [v] * int(c)
    return out


def strike_curve(model: Model, kind: int, pressed: int) -> list[float]:
    """Chance one target is struck, per bin of its own ratio, never falling as
    the ratio rises."""
    row = kind * 2 + pressed
    base = row * RATIO_BINS
    prior = PRIOR_STRIKE[row]
    rates, weights = [], []
    for b in range(RATIO_BINS):
        hit = model.strikes[base + b] + PRIOR_WEIGHT * prior[b]
        total = model.strikes[base + b] + model.passes[base + b] + PRIOR_WEIGHT
        rates.append(hit / total)
        weights.append(total)
    return _monotone(rates, weights)


def allin_rate(model: Model) -> float:
    """The share of strikes that send all the garrison."""
    allin, sized = model.allin
    return (allin + PRIOR_WEIGHT * PRIOR_ALLIN) / (allin + sized + PRIOR_WEIGHT)


def size_ratio(model: Model) -> float:
    """What a strike that is not all-in sends, against its target, on average."""
    weights = [model.size[b] + PRIOR_WEIGHT * PRIOR_SIZE[b] for b in range(RATIO_BINS)]
    return sum(w * (b + 0.5) / 10 for b, w in enumerate(weights)) / sum(weights)


def strike_ships(model: Model, garrison: int, eff: float) -> float:
    """The ships a strike from `garrison` at a target of effective `eff` sends,
    on average."""
    allin = allin_rate(model)
    return allin * garrison + (1.0 - allin) * min(garrison, size_ratio(model) * eff)


def guard_share(model: Model) -> float:
    """What a frontier system that launched kept home, against its largest
    adjacent enemy garrison, at the median."""
    weights = [model.guard[b] + PRIOR_WEIGHT * PRIOR_GUARD[b] for b in range(GUARD_BINS)]
    half, run = sum(weights) / 2, 0.0
    for b, w in enumerate(weights):
        run += w
        if run >= half:
            return (b + 0.5) / 10
    return (GUARD_BINS - 0.5) / 10


def evac_rate(model: Model) -> float:
    left, stayed = model.evac
    return (left + PRIOR_WEIGHT * PRIOR_EVAC) / (left + stayed + PRIOR_WEIGHT)


def _chances(model: Model, options: list[_Option], pressed: int, curves: dict) -> list[float]:
    """Each option's chance of a strike from one source, scaled to sum to at most
    one, since a source mostly strikes once."""
    chances = []
    for option in options:
        key = (id(model), option.kind, pressed)
        if key not in curves:
            curves[key] = strike_curve(model, option.kind, pressed)
        chances.append(curves[key][_ratio_bin(option.ratio)])
    total = sum(chances)
    return [p / total for p in chances] if total > 1.0 else chances


def predict(state, pid: int, models: dict[int, Model] | None = None) -> list[Threat]:
    """The launches each rival should make at `pid`'s systems this turn.

    A rival's chances across one source's targets are scaled to sum to at most
    one, since a source mostly strikes once. With `models` given (``{}`` for the
    prior alone) nothing is remembered."""
    if models is None:
        node = _node_for(state)
        snap, models = node.snap, node.models
    else:
        snap = _Snap(state)
    mine = {sid for sid, owner in snap.owner.items() if owner == pid}
    hostile = _hostile_inbound(snap)
    curves: dict = {}
    out: list[Threat] = []
    for sid in sorted(snap.owner):
        q = snap.owner[sid]
        garrison = snap.ships[sid]
        if q in (0, pid) or garrison <= 0:
            continue
        if not any(n in mine for n in state.systems[sid].neighbors):
            continue
        model = models.get(q, EMPTY)
        pressed = 1 if hostile.get(sid, 0) > 0 else 0
        options = _options(snap, state, sid)
        for option, p in zip(options, _chances(model, options, pressed, curves)):
            if option.target in mine:
                out.append(Threat(q, sid, option.target, option.turns,
                                  strike_ships(model, garrison, option.eff), p))
    return out


# --------------------------------------------------------------------------- #
# The bot
# --------------------------------------------------------------------------- #
IS_ORACLE = True

# What the AI tab's generic aux slider is called when this bot holds the seat (read
# by `ai.aux_spec`/`ai.aux_names`; see models/README.md).
AUX_LABEL = "Trust"
AUX_RANGE = (OFF, RAISE, 1)
AUX_INT = True
AUX_NAMES = ("Off", "Raise")


def is_oracle_seat(player) -> bool:
    """Every seat: what a seat decides depends on the boards it has seen, so an
    oracle has to model it rather than run it."""
    return True


def _trust_of(player) -> int:
    try:
        return OFF if float(player.ai_params.aux) < 0.5 else RAISE
    except Exception:                     # noqa: BLE001
        return RAISE


def _board(state, pid: int, trust: int) -> object:
    """A private copy of the board with each rival's expected launches at our
    systems flying on it (Raise), their sources left standing."""
    board = copy.copy(state)
    board.systems = {sid: copy.copy(s) for sid, s in state.systems.items()}
    for s in board.systems.values():
        s.neighbors = list(s.neighbors)
    board.fleets = [copy.copy(f) for f in state.fleets]
    board.players = {q: copy.copy(p) for q, p in state.players.items()}
    for p in board.players.values():
        p.ai_params = copy.copy(p.ai_params)
    board.adjacency = {sid: dict(nbrs) for sid, nbrs in state.adjacency.items()}
    board.rng = random.Random(0)
    if trust == OFF:
        return board
    for threat in predict(state, pid):
        ships = round(threat.p * threat.ships)
        if ships > 0:
            board.fleets.append(Fleet(threat.rival, threat.source, threat.target, ships,
                                      threat.turns, threat.turns))
    return board


def decide(state, pid):
    if not any(s.owner_id == pid for s in state.systems.values()):
        return []
    actuary = sys.modules.get("sc_model_actuary")
    if actuary is None:
        return ai.compute_orders(state, pid)
    _node_for(state)
    board = _board(state, pid, _trust_of(state.players[pid]))
    board.players[pid].ai_params.aux = float(actuary.PLANNED)
    return actuary.decide(board, pid)


def decide_ms(settings, seat) -> float:
    """Typical CPU ms of one decide (`ai.decide_ms`): actuary's, plus reading."""
    actuary = sys.modules.get("sc_model_actuary")
    base = actuary.decide_ms(settings, seat) if actuary is not None else 0.0
    return base + READ_MS

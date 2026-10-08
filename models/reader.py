"""reader — a model of each rival, read off the board and kept from turn to turn.

Every rival decides on the board we saw last turn, since turns resolve
simultaneously, and what it decided is on the board we see now: its fresh
fleets, and the ships a one-turn lane carried off unseen. reader keeps the last
board, so each turn it can pair what a rival saw with what it did, and fold that
into a running model of the rival:

  * **A strike curve.** For each of a rival's systems and each neighbour it does
    not hold, the ratio of its garrison to the neighbour's effective garrison on
    arrival, and whether it struck. Counted per ratio bin, split by neutral or
    player target and by whether the rival's system had hostile ships inbound,
    then fitted monotone (`strike_curve`).
  * **How much a strike sends**, as a share of the source's garrison.
  * **What a frontier system keeps home**, against its largest adjacent enemy.
  * **Whether a doomed system leaves.**

`predict` turns a rival's model into the launches it should make at our systems
this turn, each with a probability.

**Memory is keyed by the game's path.** Each board is a node in a memo tree,
found by content: a node's parent is a stored board one turn earlier that this
board provably follows from (`_follows`), and its models are the parent's plus
what that turn showed. The same board always finds the same node, a branch gets
its own, and a board with no known parent starts from the prior (a cold start).

There is no ``decide`` yet, so ``ai.load_models`` imports this file and registers
nothing. docs/design/reader.md has why memory is keyed this way, the
measurements, and the gate a bot waits on.

Draws nothing from ``state.rng`` or the ``random`` module, and never mutates the
state.
"""

from __future__ import annotations

import hashlib
from collections import Counter, OrderedDict, defaultdict
from typing import NamedTuple

from starconquest import config

# --- tunables -------------------------------------------------------------- #
NODE_CAP = 512              # boards remembered, least recently used dropped first
RATIO_BINS = 31             # garrison-to-target ratio in tenths; the last is 3.0 and up
SEND_BINS = 11              # ships sent / garrison in tenths; the last is everything
GUARD_BINS = 21             # kept / largest adjacent enemy in tenths; the last is 2.0 and up
PRIOR_WEIGHT = 4.0          # observations the prior is worth, per bin

NEUTRAL, PLAYER = 0, 1      # target kinds
_ROWS = 4                   # (kind, pressed) pairs, row = kind * 2 + pressed

# --- the prior -------------------------------------------------------------- #
# Pooled over the roster in self-play, all four cells, seeds 1001-1040
# (`tools/reader_check.py --fit-prior`). A rival nobody has watched yet is
# assumed to play like the average of the roster.
PRIOR_STRIKE = (
    # neutral, unpressed
    (0.007, 0.007, 0.011, 0.011, 0.018, 0.018, 0.018, 0.019, 0.019, 0.019, 0.019,
     0.020, 0.038, 0.199, 0.208, 0.284, 0.284, 0.284, 0.284, 0.324, 0.379, 0.379,
     0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379, 0.379),
    # neutral, pressed
    (0.013, 0.014, 0.015, 0.015, 0.015, 0.015, 0.015, 0.015, 0.015, 0.030, 0.030,
     0.030, 0.044, 0.097, 0.135, 0.184, 0.184, 0.184, 0.184, 0.250, 0.325, 0.325,
     0.325, 0.325, 0.325, 0.325, 0.325, 0.325, 0.325, 0.325, 0.325),
    # player, unpressed
    (0.037, 0.037, 0.037, 0.037, 0.037, 0.038, 0.038, 0.038, 0.038, 0.038, 0.053,
     0.053, 0.102, 0.111, 0.124, 0.161, 0.202, 0.245, 0.283, 0.283, 0.317, 0.371,
     0.371, 0.371, 0.371, 0.371, 0.371, 0.371, 0.371, 0.371, 0.371),
    # player, pressed
    (0.055, 0.080, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.081, 0.133,
     0.133, 0.140, 0.189, 0.206, 0.279, 0.311, 0.311, 0.311, 0.379, 0.379, 0.379,
     0.379, 0.379, 0.379, 0.379, 0.382, 0.382, 0.382, 0.382, 0.425),
)
PRIOR_SEND = (0.055, 0.048, 0.048, 0.042, 0.038, 0.067, 0.066, 0.082, 0.078, 0.052,
              0.424)
PRIOR_GUARD = (0.526, 0.019, 0.020, 0.042, 0.030, 0.075, 0.060, 0.024, 0.017, 0.005,
               0.060, 0.008, 0.012, 0.006, 0.003, 0.008, 0.004, 0.002, 0.002, 0.000,
               0.080)
PRIOR_EVAC = 0.527


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

    __slots__ = ("strikes", "passes", "send", "guard", "evac", "turns")

    def __init__(self, strikes, passes, send, guard, evac, turns):
        self.strikes = strikes
        self.passes = passes
        self.send = send
        self.guard = guard
        self.evac = evac
        self.turns = turns

    def plus(self, other: Model) -> Model:
        return Model(_add(self.strikes, other.strikes), _add(self.passes, other.passes),
                     _add(self.send, other.send), _add(self.guard, other.guard),
                     _add(self.evac, other.evac), self.turns + other.turns)


EMPTY = Model((0.0,) * (_ROWS * RATIO_BINS), (0.0,) * (_ROWS * RATIO_BINS),
              (0.0,) * SEND_BINS, (0.0,) * GUARD_BINS, (0.0, 0.0), 0)


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

        c = counts.setdefault(q, {"strikes": [0.0] * (_ROWS * RATIO_BINS),
                                  "passes": [0.0] * (_ROWS * RATIO_BINS),
                                  "send": [0.0] * SEND_BINS,
                                  "guard": [0.0] * GUARD_BINS,
                                  "evac": [0.0, 0.0]})
        pressed = 1 if hostile.get(sid, 0) > 0 else 0
        for n in sorted(state.systems[sid].neighbors):
            holder = prev.owner[n]
            if holder == q:
                continue
            kind = NEUTRAL if holder == 0 else PLAYER
            turns = state.travel_turns(sid, n) or 1
            cell = (kind * 2 + pressed) * RATIO_BINS + _ratio_bin(
                garrison / _effective(state, prev, n, turns))
            if out.get(n, 0) > 0:
                c["strikes"][cell] += 1
                c["send"][_share_bin(out[n] / garrison, SEND_BINS)] += 1
            else:
                c["passes"][cell] += 1
        threat = _largest_enemy(prev, state, sid, q)
        if threat > 0 and launched > 0:
            c["guard"][_share_bin((garrison - launched) / threat, GUARD_BINS)] += 1
        if hostile.get(sid, 0) > garrison * config.DEFENDER_ADVANTAGE:
            c["evac"][0 if 2 * launched >= garrison else 1] += 1

    return {q: Model(tuple(c["strikes"]), tuple(c["passes"]), tuple(c["send"]),
                     tuple(c["guard"]), tuple(c["evac"]), 1)
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
    """Chance of a strike in each ratio bin, never falling as the ratio rises."""
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


def _centre(b: int, bins: int) -> float:
    return 1.0 if b == bins - 1 and bins == SEND_BINS else (b + 0.5) / 10


def send_share(model: Model) -> float:
    """The share of its garrison a strike sends, on average."""
    weights = [model.send[b] + PRIOR_WEIGHT * PRIOR_SEND[b] for b in range(SEND_BINS)]
    return sum(w * _centre(b, SEND_BINS) for b, w in enumerate(weights)) / sum(weights)


def guard_share(model: Model) -> float:
    """What a frontier system that launched kept home, against its largest
    adjacent enemy garrison, at the median."""
    weights = [model.guard[b] + PRIOR_WEIGHT * PRIOR_GUARD[b] for b in range(GUARD_BINS)]
    half, run = sum(weights) / 2, 0.0
    for b, w in enumerate(weights):
        run += w
        if run >= half:
            return _centre(b, GUARD_BINS)
    return _centre(GUARD_BINS - 1, GUARD_BINS)


def evac_rate(model: Model) -> float:
    left, stayed = model.evac
    return (left + PRIOR_WEIGHT * PRIOR_EVAC) / (left + stayed + PRIOR_WEIGHT)


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
    curves: dict[tuple[int, int, int], list[float]] = {}
    out: list[Threat] = []
    for sid in sorted(snap.owner):
        q = snap.owner[sid]
        garrison = snap.ships[sid]
        if q in (0, pid) or garrison <= 0:
            continue
        neighbours = sorted(state.systems[sid].neighbors)
        if not any(n in mine for n in neighbours):
            continue
        model = models.get(q, EMPTY)
        pressed = 1 if hostile.get(sid, 0) > 0 else 0
        options = []
        for n in neighbours:
            holder = snap.owner[n]
            if holder == q:
                continue
            kind = NEUTRAL if holder == 0 else PLAYER
            if (q, kind, pressed) not in curves:
                curves[(q, kind, pressed)] = strike_curve(model, kind, pressed)
            turns = state.travel_turns(sid, n) or 1
            p = curves[(q, kind, pressed)][_ratio_bin(
                garrison / _effective(state, snap, n, turns))]
            options.append((n, turns, p))
        total = sum(p for _n, _t, p in options)
        scale = 1.0 / total if total > 1.0 else 1.0
        ships = send_share(model) * garrison
        for n, turns, p in options:
            if n in mine:
                out.append(Threat(q, sid, n, turns, ships, p * scale))
    return out

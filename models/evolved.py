"""evolved: a blind bot whose choices come from trained weights, not rules.

The bot reads only the board. It never reads or runs another seat's code: no
``IS_ORACLE``, no ``ai.STRATEGIES``, no imports from other bots, no borrowed
openings. Offline, other bots served as sparring partners while the weights
were trained (``tools/evo_train.py``); what ships is only the weights in
``_evolved_weights.py`` plus this board-reading policy.

Each turn every owned system with ships above its guard is offered a set of
priced moves, and a scorer over size-free features picks among them greedily.
Each move or feature encodes a known principle of war or games; evolution
decides how much each matters. The moves:

- **strike** a neighbour with the ask that wins the worst roll on arrival
  (garrison, production on the way and its owner's landing fleets, times
  ``combat.edge_attacking``, floored at garrison + 1): Lanchester's square law
  priced in;
- **heavy** strike: everything above the guard, when that clears the ask
  (overwhelming force);
- **pool**: neighbours landing on one target on the same turn that clear it
  together where none does alone (concentration of force);
- **stagger**: the farthest sources launch now so nearer ones can follow and
  land with them (concentration across distance);
- **reinforce** an owned neighbour with the surplus (moving force to the
  front);
- **hold**: the bar every move must clear; the guard kept home is
  ``edge_defending`` times a learned mix of hostile fleets inbound and the
  largest rival stack next door (economy of force);
- **evacuate**: every ship out of a system that will fall next turn whatever
  we do (preserve the force); **recapture**: a strike timed to land just after
  a capture, against the attackers' survivors (the counter-attack, Sun Tzu's
  "strike when they are spent").

A small residual net (``head``, 8 tanh units) adds to the four strike kinds'
linear scores. Features that carry a principle by name: ``emptied``
(counter-strike the base a rival has just emptied), ``heart_dist`` ("the
enemy's gate is down": aim at the strongest rival's richest system),
``ship_ratio`` / ``rival_ahead`` (attrition when ahead, manoeuvre when
behind), ``pin`` (a fleet in being), ``src_left`` (don't strip the front),
``relief_in_time`` / ``relief_gap`` (defence in depth), ``race`` (the
expansion race), ``backed`` (hold what you take), ``value_per_ship`` (payback
period), ``dest_hub`` (interior lines), ``exchange`` (pot odds),
``piecemeal`` (Lanchester's linear law), and three of a melee, silent once
two seats remain: ``leader`` (attack the leader), ``busy`` (let rivals bleed
each other) and ``new_front`` (avoid a war on two fronts).

On top of the policy sits a short look-ahead (``aux``, Look-ahead: On by
default). It offers a few variants of the policy's move (one move banned, a
near miss forced, the hold bar shifted) and plays each six turns on a copy of
the board, our side continuing on the policy, every fight at the square law's
expected roll (no dice drawn, so no foresight of the game's own). Rivals are
modelled by two fixed blind rules from doctrine, never by their code: the
**most likely course of action** (they hold; no blind model of the roster's
launches predicted them better) and the **most dangerous** (every rival stack
strikes all-out now at a system of ours it can take). The plan with the best
likely outcome wins unless the dangerous case punishes it more than it does
the policy's own move.

Stateless and deterministic: nothing is drawn from ``state.rng`` or the global
``random``; ties break on ids. See ``docs/design/evolved.md``.
"""
from __future__ import annotations

import copy
import heapq
import importlib.util
import math
import random
import sys
import time
from pathlib import Path

from starconquest import combat, engine
from starconquest.model import Order

TUNED_SWING = 1.1 / 0.9

STRIKE_FEATURES = (
    "bias", "rate", "neutral", "rival", "ask_frac", "ask_abs", "travel",
    "exposure", "reach_risk", "kill", "contest", "degree", "src_threat",
    "turn", "inc_share", "ship_share", "value_per_ship", "front_gain",
    "joining", "partial",
    "emptied", "heart_dist", "ship_ratio", "rival_ahead",
    "src_left",
    "race", "backed",
    "exchange", "piecemeal",
    "leader", "busy", "new_front",
)
REINFORCE_FEATURES = (
    "bias", "dist_gain", "dest_threat", "src_threat", "dest_front",
    "amount", "travel", "turn", "inc_share",
    "dest_best_rate", "enables",
    "dest_hub", "dest_heart_dist", "ship_ratio",
    "relief_in_time",
)
HOLD_FEATURES = (
    "bias", "src_threat", "inbound", "front", "ships", "turn", "inc_share",
    "rate", "deficit",
    "ship_ratio", "pin",
    "relief_gap",
)
GUARD_PARAMS = ("guard_inbound", "guard_adjacent")
# Losing a system well. Evacuate pulls every ship out of a system that will
# fall next turn whatever we do (the guard would keep them there to die);
# recapture strikes such a system to land after the capture, against the
# attacker's survivors. Evacuate has its own weights; recapture scores as a
# strike plus a learned residual over the same features, less ENTRY. All-zero
# weights switch either off.
ENTRY = 1.0
EVAC_FEATURES = (
    "bias", "doom", "ships", "rate", "dest_threat", "dest_front", "travel",
    "ship_ratio", "dest_rival_adj",
)

# The head: a small residual net on top of the linear strike scores, shared by
# the four strike types (their features plus a one-hot type), HEAD_HIDDEN tanh
# units. Its output weights start at zero, so it starts out playing exactly as
# the linear scorer does.
HEAD_HIDDEN = 8
# The head reads a fixed prefix of the strike features, so adding a feature
# leaves trained head weights valid.
HEAD_STRIKE_N = 25
HEAD_IN = HEAD_STRIKE_N + 4
HEAD_PARAMS = tuple(f"h{i}" for i in range(HEAD_IN * HEAD_HIDDEN + 2 * HEAD_HIDDEN + 1))
STRIKE_KINDS = ("strike", "heavy", "pool", "stagger")

LAYOUT = (
    ("strike", STRIKE_FEATURES),
    ("heavy", STRIKE_FEATURES),
    ("pool", STRIKE_FEATURES),
    ("stagger", STRIKE_FEATURES),
    ("reinforce", REINFORCE_FEATURES),
    ("hold", HOLD_FEATURES),
    ("guard", GUARD_PARAMS),
    ("head", HEAD_PARAMS),
    ("evacuate", EVAC_FEATURES),
    ("recapture", STRIKE_FEATURES),
)
N_WEIGHTS = sum(len(f) for _, f in LAYOUT)


def split(vec) -> dict[str, list[float]]:
    """A flat genome as one weight list per option type, in ``LAYOUT`` order."""
    if len(vec) != N_WEIGHTS:
        raise ValueError(f"expected {N_WEIGHTS} weights, got {len(vec)}")
    out, i = {}, 0
    for name, feats in LAYOUT:
        out[name] = [float(x) for x in vec[i:i + len(feats)]]
        i += len(feats)
    return out


def _load_weights():
    return _weights_module().WEIGHTS


def _weights_module():
    name = "sc_evolved_weights"
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve().parent / "_evolved_weights.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _dot(w, phi) -> float:
    return sum(a * b for a, b in zip(w, phi))


def _produced(sys_, turns: int) -> int:
    """Ships ``sys_`` adds over ``turns`` turns (production runs before arrivals)."""
    if sys_.production <= 0 or turns <= 0:
        return 0
    return (sys_.prod_progress + turns) // sys_.production


class _Board:
    """Everything one decide reads, computed once."""

    def __init__(self, state, pid: int) -> None:
        self.state = state
        self.pid = pid
        systems = state.systems
        self.systems = systems
        self.travel = {sid: {n: state.travel_turns(sid, n) for n in s.neighbors}
                       for sid, s in systems.items()}
        lanes = [t for d in self.travel.values() for t in d.values()]
        self.mean_lane = max(1.0, sum(lanes) / max(1, len(lanes)))

        # Fleets in flight, by destination: (owner, ships, turns_remaining).
        self.inbound: dict[int, list[tuple[int, int, int]]] = {}
        fleet_ships: dict[int, int] = {}
        for f in state.fleets:
            self.inbound.setdefault(f.dest_id, []).append(
                (f.owner_id, f.ships, f.turns_remaining))
            fleet_ships[f.owner_id] = fleet_ships.get(f.owner_id, 0) + f.ships

        income: dict[int, float] = {}
        ships: dict[int, int] = dict(fleet_ships)
        for s in systems.values():
            if s.owner_id == 0:
                continue
            income[s.owner_id] = income.get(s.owner_id, 0.0) + _rate(s)
            ships[s.owner_id] = ships.get(s.owner_id, 0) + s.ships
        tot_inc = sum(income.values()) or 1.0
        tot_ships = sum(ships.values()) or 1
        self.inc_share = income.get(pid, 0.0) / tot_inc
        self.ship_share = ships.get(pid, 0) / tot_ships
        self.turn_f = min(state.turn, 300) / 100.0
        self.edge = combat.edge_attacking(TUNED_SWING)
        self.edge_def = combat.edge_defending(TUNED_SWING)

        # The largest rival stack adjacent to each system, and the most rival
        # ships that can reach it within t turns (by neighbour).
        self.rival_adj: dict[int, int] = {}
        for sid, s in systems.items():
            best = 0
            for n in s.neighbors:
                o = systems[n]
                if o.owner_id not in (0, pid) and o.owner_id != s.owner_id:
                    best = max(best, o.ships)
            self.rival_adj[sid] = best

        # Hostile ships inbound to each of our systems, and when the first lands.
        self.hostile_in: dict[int, int] = {}
        self.hostile_eta: dict[int, int] = {}
        for dest, lst in self.inbound.items():
            self.hostile_in[dest] = sum(n for o, n, _ in lst if o != pid)
            etas = [tr for o, _, tr in lst if o != pid]
            if etas:
                self.hostile_eta[dest] = min(etas)

        # Turn-distance from each system to the nearest one we do not own.
        self.front_dist = _dijkstra(self.travel,
                                    [sid for sid, s in systems.items() if s.owner_id != pid])
        self.front = {sid: any(systems[n].owner_id != pid for n in s.neighbors)
                      for sid, s in systems.items()}

        # Ahead or behind: log ratios against the strongest rival (attrition
        # when ahead, manoeuvre when behind).
        alive = [p.id for p in state.players.values()
                 if not p.is_neutral and p.alive and p.id != pid]
        riv = max(alive, key=lambda q: (income.get(q, 0.0), ships.get(q, 0), -q)) if alive else None
        self.rival = riv
        self.melee = len(alive) >= 2
        self.ship_ratio = max(-3.0, min(3.0, math.log(
            (ships.get(pid, 0) + 1.0) / (ships.get(riv, 0) + 1.0)))) if riv else 3.0

        # The enemy's gate is down: distance to the strongest rival's richest
        # system, the heart of its production.
        heart = None
        if riv is not None:
            theirs = [s for s in systems.values() if s.owner_id == riv]
            if theirs:
                heart = max(theirs, key=lambda s: (_rate(s), s.ships, -s.id)).id
        self.heart_dist = _dijkstra(self.travel, [heart]) if heart is not None else {}

        # An emptied garrison: ships a rival system has just sent away and that
        # are still in flight, i.e. not there to defend it.
        self.emptied: dict[int, int] = {}
        for f in state.fleets:
            src = systems.get(f.source_id)
            if (src is not None and f.owner_id not in (0, pid) and src.owner_id == f.owner_id
                    and f.turns_total - f.turns_remaining <= 2):
                self.emptied[f.source_id] = self.emptied.get(f.source_id, 0) + f.ships

        # Interior lines: how many of our frontier systems each of ours can
        # reach within one and a half average lanes (a reserve's hub).
        reach = 1.5 * self.mean_lane
        fronts = [sid for sid, s in systems.items() if s.owner_id == pid and self.front[sid]]
        self.hub: dict[int, int] = {}
        if fronts:
            for sid, s in systems.items():
                if s.owner_id == pid:
                    self.hub[sid] = 0
            for f in fronts:
                for sid in _dijkstra(self.travel, [f], reach):
                    if sid in self.hub:
                        self.hub[sid] += 1

        self.income = income
        self.tot_inc = tot_inc
        self.ships_of = ships
        # Rival seats we already border.
        self.bordering = {systems[n].owner_id for s in systems.values() if s.owner_id == pid
                          for n in s.neighbors if systems[n].owner_id not in (0, pid)}
        # Each seat's ships bound for a third seat's systems (not ours, not neutral).
        self.busy: dict[int, int] = {}
        for f in state.fleets:
            dest = systems.get(f.dest_id)
            if (dest is not None and f.owner_id not in (0, pid)
                    and dest.owner_id not in (0, pid, f.owner_id)):
                self.busy[f.owner_id] = self.busy.get(f.owner_id, 0) + f.ships

    def threat(self, sid: int) -> float:
        s = self.systems[sid]
        return (self.rival_adj[sid] + self.hostile_in.get(sid, 0)) / (s.ships + 1.0)

    def defenders_at(self, target, t: int) -> tuple[int, int, int]:
        """``(defenders, ours_landing, rival_landing)`` at target by turn ``t``.

        Defenders: the garrison plus its production and its owner's fleets that
        land by then, thinned by the square law by any of our fleets landing
        *before* ``t`` (they fight alone). Ours: our fleets landing on turn
        ``t`` itself, which join the strike. Rival: other seats' fleets landing
        there by then (they contest a neutral).
        """
        owner = target.owner_id
        d = target.ships + (_produced(target, t) if owner != 0 else 0)
        ours = rival = early = 0
        for o, n, tr in self.inbound.get(target.id, ()):
            if tr > t:
                continue
            if o == owner:
                d += n
            elif o == self.pid:
                if tr == t:
                    ours += n
                else:
                    early += n
            else:
                rival += n
        if early:
            d = math.ceil(math.sqrt(max(0, d * d - early * early)))
        return d, ours, rival


def _rate(s) -> float:
    return 1.0 / s.production if s.production > 0 else 0.0


def _dijkstra(travel, seeds, limit: float = math.inf) -> dict[int, float]:
    """Travel distance from the nearest seed. With ``limit``, only systems
    within it are returned (exact there, since every lane takes a turn or more);
    read the rest as ``math.inf``."""
    dist = {sid: math.inf for sid in travel} if limit == math.inf else {}
    heap = []
    for s in seeds:
        dist[s] = 0.0
        heap.append((0.0, s))
    heapq.heapify(heap)
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue
        for v, t in travel[u].items():
            nd = d + t
            if nd <= limit and nd < dist.get(v, math.inf):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist


def _guard(b: _Board, src, w_guard) -> int:
    g_in = max(0.0, w_guard[0])
    g_adj = max(0.0, w_guard[1])
    raw = g_in * b.hostile_in.get(src.id, 0) + g_adj * b.rival_adj[src.id]
    if raw <= 0:
        return 0
    return min(src.ships, math.ceil(raw * combat.edge_defending(TUNED_SWING)))


def _ask(b: _Board, target, t: int, edge: float) -> tuple[int, int, int, int]:
    """``(ask, defenders, rival, ours)``: ships to send now so the target falls
    on arrival in the worst roll, net of our fleets landing the same turn."""
    d, ours, rival = b.defenders_at(target, t)
    if target.owner_id == 0 and rival:
        d = max(d, rival)
    need = max(d + 1, math.ceil(d * edge) + 1) - ours
    return max(1, need), d, rival, ours


def _strike_phi(b: _Board, src, target, t: int, ask: int, d: int, rival: int,
                ours: int = 0, partial: float = 1.0) -> list[float]:
    pid = b.pid
    systems = b.systems
    exposure = 0
    reach = 0
    exposure_eta = math.inf
    race_eta = math.inf
    for n in target.neighbors:
        o = systems[n]
        if o.owner_id in (0, pid) or n == target.id:
            continue
        exposure = max(exposure, o.ships)
        tt = b.travel[n].get(target.id) or 1
        exposure_eta = min(exposure_eta, tt)
        if tt <= t and o.owner_id == target.owner_id:
            reach += o.ships
        if target.owner_id == 0 and o.ships > target.ships * b.edge + 1:
            race_eta = min(race_eta, tt)
    # Race: a rival that can take this neutral and land within a lane of us.
    race = 0.0
    if race_eta < math.inf:
        race = max(0.0, min(3.0, (t + b.mean_lane - race_eta) / b.mean_lane))
    # Backed: what we would hold there against the stack next door, the
    # survivors plus our ships that can land before that stack does.
    backed = 0.0
    survivors = math.sqrt(max(0, (ask + ours) ** 2 - d * d))
    if exposure:
        support = sum(systems[n].ships - (ask if n == src.id else 0) for n in target.neighbors
                      if systems[n].owner_id == pid
                      and (b.travel[n].get(target.id) or 1) <= exposure_eta)
        backed = min(3.0, (survivors + max(0, support)) / (exposure * b.edge_def + 1.0))
    # Pot odds, priced by the square law: rival ships this kills over the ships
    # it should lose (survivors sqrt(A^2 - D^2)); nothing for a neutral.
    exchange = 0.0
    if target.owner_id not in (0, pid):
        a = ask + ours
        lost = a - math.sqrt(max(0, a * a - d * d))
        exchange = min(3.0, d / (lost + 1.0))
    # Piecemeal (Lanchester's linear law): ours already bound for this target
    # that land on another turn and so fight apart from this strike.
    apart = sum(n for o, n, tr in b.inbound.get(target.id, ()) if o == pid and tr != t)
    piecemeal = min(3.0, apart / (ask + 1.0))
    rate = _rate(target)
    gain = b.front_dist.get(src.id, 0.0)
    owner = target.owner_id
    rival_target = owner not in (0, pid)
    leader = busy = 0.0
    # Three principles of a melee, silent once only two seats remain.
    if rival_target and b.melee:
        # Attack the leader.
        leader = max(-1.0, min(1.0, (b.income.get(owner, 0.0) - b.income.get(pid, 0.0)) / b.tot_inc))
        # Let them bleed each other: the owner's ships committed against third seats.
        busy = min(1.0, b.busy.get(owner, 0) / (b.ships_of.get(owner, 0) + 1.0))
    # Avoid a war on two fronts: rival seats next to the target that we don't
    # yet border.
    new_front = len({systems[n].owner_id for n in target.neighbors
                     if systems[n].owner_id not in (0, pid, owner)} - b.bordering) if b.melee else 0
    return [
        1.0,
        rate,
        1.0 if target.owner_id == 0 else 0.0,
        1.0 if target.owner_id not in (0, pid) else 0.0,
        ask / (src.ships + 1.0),
        ask / 10.0,
        t / b.mean_lane,
        min(3.0, exposure / (ask + 1.0)),
        min(3.0, reach / (ask + 1.0)),
        (d / 10.0) if target.owner_id not in (0, pid) else 0.0,
        min(3.0, rival / (ask + 1.0)),
        len(target.neighbors) / 4.0,
        min(3.0, b.threat(src.id)),
        b.turn_f,
        b.inc_share,
        b.ship_share,
        min(3.0, rate * 20.0 / (ask + 1.0)),
        min(3.0, gain / b.mean_lane),
        min(3.0, ours / (ask + 1.0)),
        partial,
        min(3.0, b.emptied.get(target.id, 0) / (target.ships + 1.0)),
        min(5.0, b.heart_dist.get(target.id, 5.0 * b.mean_lane) / b.mean_lane),
        b.ship_ratio,
        b.ship_ratio if rival_target else 0.0,
        # Don't strip the front: what the source keeps against the stack next door.
        max(-3.0, min(3.0, (src.ships - ask) / (b.rival_adj[src.id] * b.edge + 1.0))),
        race,
        backed,
        exchange,
        piecemeal,
        leader,
        busy,
        min(3.0, new_front),
    ]


def make_policy(vec):
    """``policy(state, pid)`` -> ``(orders, chosen)``: ``chosen`` lists the
    keys of the candidates taken, best first; a key is ``(kind, sources, dest)``.
    """
    w = split(vec)
    ws, wh, wp, wt, wr, wk, wg = (w["strike"], w["heavy"], w["pool"], w["stagger"],
                                  w["reinforce"], w["hold"], w["guard"])
    w_evac, w_recap = w["evacuate"], w["recapture"]
    hw = w["head"]
    n1 = HEAD_IN * HEAD_HIDDEN
    w1 = [hw[i * HEAD_IN:(i + 1) * HEAD_IN] for i in range(HEAD_HIDDEN)]
    b1 = hw[n1:n1 + HEAD_HIDDEN]
    w2 = hw[n1 + HEAD_HIDDEN:n1 + 2 * HEAD_HIDDEN]
    b2 = hw[-1]
    head_on = any(w2) or b2
    by_vec = {id(ws): 0, id(wh): 1, id(wp): 2, id(wt): 3}

    def opt_score(wv, phi):
        """Linear score, plus the residual head for the strike kinds."""
        v = _dot(wv, phi)
        k = by_vec.get(id(wv))
        if head_on and k is not None:
            x = list(phi[:HEAD_STRIKE_N]) + [1.0 if j == k else 0.0 for j in range(4)]
            v += b2 + sum(o * math.tanh(bb + _dot(row, x)) for o, bb, row in zip(w2, b1, w1))
        return v

    def policy(state, pid, trace=None, force=(), ban=(), near=None):
        """``trace``, a list, collects every candidate as (score, kind,
        parts, dest) plus each system's avail (offline analysis only).
        ``force``/``ban`` (candidate keys) put a move first or drop it;
        ``near``, a list, collects the best moves that missed the bar."""
        b = _Board(state, pid)
        systems = b.systems
        mine = sorted((s for s in systems.values() if s.owner_id == pid), key=lambda s: s.id)
        if not mine:
            return [], []
        edge = combat.edge_attacking(TUNED_SWING)
        edge_def = combat.edge_defending(TUNED_SWING)

        def need_at(sid: int) -> float:
            """Ships ``sid`` lacks to survive the hostile fleets inbound."""
            hin = b.hostile_in.get(sid, 0)
            if not hin:
                return 0.0
            s_ = systems[sid]
            return max(0.0, hin * edge_def - s_.ships - _produced(s_, b.hostile_eta[sid]))

        def relief_need(dest: int, t: int, amount: int) -> float:
            """Relief in time: the shortfall at ``dest`` this move can cover,
            when it lands before the strike does."""
            need = need_at(dest)
            if not need or t > b.hostile_eta[dest]:
                return 0.0
            return min(3.0, need / (amount + 1.0))

        def relief_gap(s_) -> float:
            """A strike inbound that our neighbours cannot relieve in time."""
            need = need_at(s_.id)
            if not need:
                return 0.0
            eta = b.hostile_eta[s_.id]
            relief = sum(systems[n].ships for n in s_.neighbors
                         if systems[n].owner_id == pid and (b.travel[n].get(s_.id) or 1) <= eta)
            return min(3.0, max(0.0, need - relief) / (s_.ships + 1.0))

        ask_cache: dict[tuple[int, int], tuple[int, int, int, int]] = {}

        def ask_of(src_id: int, dest_id: int):
            key = (src_id, dest_id)
            if key not in ask_cache:
                t = b.travel[src_id][dest_id] or 1
                ask_cache[key] = _ask(b, systems[dest_id], t, edge)
            return ask_cache[key]

        avail: dict[int, int] = {}
        hold: dict[int, float] = {}
        for s in mine:
            avail[s.id] = s.ships - _guard(b, s, wg)
            hin = b.hostile_in.get(s.id, 0)
            deficit = 0.0
            if hin:
                own = s.ships + _produced(s, b.hostile_eta[s.id])
                deficit = max(-3.0, min(3.0, (hin * edge_def - own) / (s.ships + 1.0)))
            hold[s.id] = _dot(wk, [
                1.0,
                min(3.0, b.threat(s.id)),
                min(3.0, b.hostile_in.get(s.id, 0) / (s.ships + 1.0)),
                1.0 if b.front[s.id] else 0.0,
                s.ships / 10.0,
                b.turn_f,
                b.inc_share,
                _rate(s),
                deficit,
                b.ship_ratio,
                # Fleet in being: a stack next to rivals that they must answer.
                min(3.0, s.ships / (b.rival_adj[s.id] * edge + 1.0)) if b.rival_adj[s.id] else 0.0,
                relief_gap(s),
            ])

        # (score, tiebreak, kind, [(src, ships)], dest)
        cands = []
        asks: dict[tuple[int, int], tuple[int, int, int, int]] = {}
        for s in mine:
            if avail[s.id] <= 0:
                continue
            for n in s.neighbors:
                tgt = systems[n]
                t = b.travel[s.id][n] or 1
                if tgt.owner_id == pid:
                    amount = avail[s.id]
                    gain = b.front_dist[s.id] - b.front_dist[n]
                    best_rate = enables = 0.0
                    for m in tgt.neighbors:
                        far = systems[m]
                        if far.owner_id == pid:
                            continue
                        r = _rate(far)
                        best_rate = max(best_rate, r)
                        k_ask = ask_of(n, m)[0]
                        if max(0, avail.get(n, 0)) < k_ask <= max(0, avail.get(n, 0)) + amount:
                            enables = max(enables, r)
                    phi = [
                        1.0,
                        max(-3.0, min(3.0, gain / b.mean_lane)),
                        min(3.0, b.threat(n)),
                        min(3.0, b.threat(s.id)),
                        1.0 if b.front[n] else 0.0,
                        amount / 10.0,
                        t / b.mean_lane,
                        b.turn_f,
                        b.inc_share,
                        best_rate,
                        enables,
                        b.hub.get(n, 0) / 4.0,
                        min(5.0, b.heart_dist.get(n, 5.0 * b.mean_lane) / b.mean_lane),
                        b.ship_ratio,
                        relief_need(n, t, amount),
                    ]
                    cands.append((_dot(wr, phi) - hold[s.id], s.id, n, "reinforce",
                                  [(s.id, amount)], n))
                    continue
                ask, d, rival, ours = ask_of(s.id, n)
                asks[(s.id, n)] = (ask, d, rival, t)
                phi = _strike_phi(b, s, tgt, t, ask, d, rival, ours)
                if ask <= avail[s.id]:
                    cands.append((opt_score(ws, phi) - hold[s.id], s.id, n, "strike",
                                  [(s.id, ask)], n))
                    if avail[s.id] > ask:
                        phi_h = _strike_phi(b, s, tgt, t, avail[s.id], d, rival, ours)
                        cands.append((opt_score(wh, phi_h) - hold[s.id], s.id, n, "heavy",
                                      [(s.id, avail[s.id])], n))

        # Pools: sources that land together on one target and clear it jointly.
        by_target: dict[tuple[int, int], list[int]] = {}
        for (sid, n), (ask, d, rival, t) in asks.items():
            if ask > avail[sid]:
                by_target.setdefault((n, t), []).append(sid)
        for (n, t), _ in sorted(by_target.items()):
            srcs = [sid for (sid, nn), v in asks.items() if nn == n and v[3] == t]
            if len(srcs) < 2:
                continue
            tgt = systems[n]
            ask, d, rival, ours = _ask(b, tgt, t, edge)
            total = sum(max(0, avail[sid]) for sid in srcs)
            if total < ask:
                continue
            srcs.sort(key=lambda sid: (-avail[sid], sid))
            parts, left = [], ask
            for sid in srcs:
                if left <= 0:
                    break
                take = min(left, avail[sid])
                if take > 0:
                    parts.append((sid, take))
                    left -= take
            if len(parts) < 2:
                continue
            lead = systems[parts[0][0]]
            phi = _strike_phi(b, lead, tgt, t, ask, d, rival, ours)
            bar = max(hold[sid] for sid, _ in parts)
            cands.append((opt_score(wp, phi) - bar, parts[0][0], n, "pool", parts, n))

        # Staggers: sources at different distances that could take a target
        # together. The farthest launch now; the nearer ones see that fleet
        # landing next turn (``ours`` in the ask) and can follow it in.
        by_dest: dict[int, list[tuple[int, int]]] = {}
        for (sid, n), (ask, d, rival, t) in asks.items():
            if avail[sid] > 0:
                by_dest.setdefault(n, []).append((t, sid))
        for n, lst in sorted(by_dest.items()):
            if len({t for t, _ in lst}) < 2:
                continue
            t_max = max(t for t, _ in lst)
            far = sorted(sid for t, sid in lst if t == t_max)
            ask, d, rival, ours = _ask(b, systems[n], t_max, edge)
            now = sum(avail[sid] for sid in far)
            total = sum(avail[sid] for _, sid in lst)
            if not now < ask <= total:
                continue
            parts = [(sid, avail[sid]) for sid in far]
            lead = systems[far[0]]
            phi = _strike_phi(b, lead, systems[n], t_max, ask, d, rival, ours,
                              partial=now / ask)
            bar = max(hold[sid] for sid, _ in parts)
            cands.append((opt_score(wt, phi) - bar, far[0], n, "stagger", parts, n))

        # Doomed: hostile ships land next turn and nothing of ours can make up
        # the shortfall in time (our own fleets landing by then included).
        doomed: dict[int, tuple[float, int]] = {}
        for s in mine if any(w_evac) or any(w_recap) else ():
            eta = b.hostile_eta.get(s.id)
            need = need_at(s.id)
            if eta is None or not need:
                continue
            own_in = sum(n for o, n, tr in b.inbound.get(s.id, ()) if o == pid and tr <= eta)
            relief = sum(systems[n].ships for n in s.neighbors
                         if systems[n].owner_id == pid and (b.travel[n].get(s.id) or 1) <= eta)
            if need - own_in - (relief if eta > 1 else 0) > 0:
                doomed[s.id] = (need - own_in, eta)

        # Evacuate: on the last turn, everything to an owned neighbour.
        for sid, (need, eta) in sorted(doomed.items()) if any(w_evac) else ():
            s = systems[sid]
            if eta != 1 or s.ships <= 0:
                continue
            for n in s.neighbors:
                dest = systems[n]
                if dest.owner_id != pid:
                    continue
                phi = [
                    1.0,
                    min(3.0, need / (s.ships + 1.0)),
                    s.ships / 10.0,
                    _rate(s),
                    min(3.0, b.threat(n)),
                    1.0 if b.front[n] else 0.0,
                    (b.travel[sid][n] or 1) / b.mean_lane,
                    b.ship_ratio,
                    min(3.0, b.rival_adj[n] / (s.ships + dest.ships + 1.0)),
                ]
                cands.append((_dot(w_evac, phi) - hold[sid], sid, n, "evacuate",
                              [(sid, s.ships)], n))

        # Recapture: land the turn after the capture or later, against the
        # attacker's survivors (no defender advantage: the worst case for us)
        # plus what the captor adds there by then.
        for sid, (need, eta) in sorted(doomed.items()) if any(w_recap) else ():
            tgt = systems[sid]
            hostile = [(o, n, tr) for o, n, tr in b.inbound.get(sid, ()) if o != pid]
            attack = sum(n for _, n, tr in hostile if tr <= eta)
            captor = max(hostile, key=lambda f: (f[1], -f[0]))[0]
            for src_id in tgt.neighbors:
                src = systems[src_id]
                if src.owner_id != pid or avail.get(src_id, 0) <= 0:
                    continue
                t = b.travel[src_id][sid] or 1
                if t <= eta:
                    continue
                garrison = tgt.ships + _produced(tgt, eta)
                surv = math.ceil(math.sqrt(max(0, attack * attack - garrison * garrison)))
                d = (surv + _produced(tgt, t - eta)
                     + sum(n for o, n, tr in hostile if o == captor and eta < tr <= t))
                ask = max(1, max(d + 1, math.ceil(d * edge) + 1))
                if ask > avail[src_id]:
                    continue
                phi = _strike_phi(b, src, tgt, t, ask, d, 0, 0)
                score = opt_score(ws, phi) + _dot(w_recap, phi) - ENTRY - hold[src_id]
                cands.append((score, src_id, sid, "recapture", [(src_id, ask)], sid))

        def key(c):
            return (c[3], tuple(sid for sid, _ in c[4]), c[5])

        if ban:
            cands = [c for c in cands if key(c) not in ban]
        cands.sort(key=lambda c: (key(c) not in force, -c[0], c[1], c[2], c[3]))
        if trace is not None:
            trace.append(dict(avail))
            trace.extend((c[0], c[3], c[4], c[5]) for c in cands)
        orders: list[Order] = []
        guard_of = {s.id: s.ships - avail[s.id] for s in mine}
        own_dest = ("reinforce", "evacuate")
        struck: set[int] = set()
        chosen = []
        for c in cands:
            score, _, _, kind, parts, dest = c
            if score <= 0 and key(c) not in force:
                if near is not None and len(near) < LOOK_FORCES and kind != "reinforce":
                    near.append(key(c))
                continue
            if kind not in own_dest and dest in struck:
                continue
            if kind == "evacuate":
                left = avail[parts[0][0]] + guard_of[parts[0][0]]
                if left <= 0:
                    continue
                parts = [(parts[0][0], left)]
            elif any(avail[sid] < n for sid, n in parts):
                if kind != "reinforce":
                    continue
                parts = [(sid, avail[sid]) for sid, _ in parts if avail[sid] > 0]
                if not parts:
                    continue
            for sid, n in parts:
                avail[sid] -= n
                orders.append(Order(pid, sid, dest, n))
            chosen.append(key(c))
            if kind not in own_dest:
                struck.add(dest)

        # Merge orders on the same lane.
        merged: dict[tuple[int, int], int] = {}
        for o in orders:
            merged[(o.source_id, o.dest_id)] = merged.get((o.source_id, o.dest_id), 0) + o.ships
        return ([Order(pid, s, d, n) for (s, d), n in sorted(merged.items()) if n > 0],
                chosen)

    return policy


def _clone(state):
    """A board ``engine.end_turn`` can play on without touching ``state``.
    Lanes and adjacency never change in play, so they are shared; every seat is
    marked a bot. Nothing is drawn from ``state.rng``, and the copy rolls dice
    of its own, never the game's."""
    new = copy.copy(state)
    new.systems = {k: copy.copy(s) for k, s in state.systems.items()}
    new.fleets = [copy.copy(f) for f in state.fleets]
    new.players = {}
    for k, p in state.players.items():
        q = copy.copy(p)
        q.is_human = False
        new.players[k] = q
    new.rng = random.Random(0)
    # Its own dice: a copy keeps the board's seed and turn, so with the board's
    # salt it would roll the game's real dice. The bot must not foresee them.
    new.dice_salt = f"{state.dice_salt}:evolved-rollout"
    return new


def _value(state, pid: int) -> float:
    """``pid``'s share of production and ships, from the board alone."""
    if state.winner is not None:
        return 1.0 if state.winner == pid else 0.0
    inc: dict[int, float] = {}
    ships: dict[int, int] = {}
    for s in state.systems.values():
        if s.owner_id:
            inc[s.owner_id] = inc.get(s.owner_id, 0.0) + _rate(s)
            ships[s.owner_id] = ships.get(s.owner_id, 0) + s.ships
    for f in state.fleets:
        ships[f.owner_id] = ships.get(f.owner_id, 0) + f.ships
    ti = sum(inc.values()) or 1.0
    ts = sum(ships.values()) or 1
    return 0.6 * inc.get(pid, 0.0) / ti + 0.4 * ships.get(pid, 0) / ts


def _danger(me: int):
    """``decide`` for every seat but ``me``: the most dangerous course of
    action, an all-out strike now on whatever of ours each stack can take on
    arrival (by its own count, ``DEFENDER_ADVANTAGE`` and production in), then
    nothing. Capabilities, not intentions."""
    advantage = combat._advantage()

    def play(state, p):
        if state.turn != play.root:
            return []
        out = []
        for s in state.systems.values():
            if s.owner_id != p or s.ships <= 0:
                continue
            best = None
            for n in s.neighbors:
                tgt = state.systems[n]
                if tgt.owner_id != me:
                    continue
                t = state.travel_turns(s.id, n) or 1
                d = (tgt.ships + _produced(tgt, t)) * advantage
                if s.ships > d and (best is None or (d / s.ships, n) < best):
                    best = (d / s.ships, n)
            if best:
                out.append(Order(p, s.id, best[1], s.ships))
        return out

    play.root = -1
    return play


# The look-ahead (``aux``): plan against the most likely course of action
# (MLCOA: rivals hold; no blind model of the roster's launches predicted them
# better), checked against the most dangerous (MDCOA: an all-out strike on us
# now). 0 is Off (the plain policy), 1 On, the default an untouched seat reads.
AUX_LABEL = "Look-ahead"
AUX_RANGE = (0, 1, 1)
AUX_INT = True
AUX_NAMES = ("Off", "On")
LOOK_TURNS = 6
LOOK_BANS = 4
LOOK_FORCES = 4
LOOK_POSTURE = 0.5
LOOK_SLACK = 0.002
LOOK_BUDGET_S = 0.050
# Every fight in a rollout is the square law's expected outcome (each roll
# dealt as 0.0, the middle of the jitter): no noise and no foresight of the
# game's own dice, so one rollout per plan is enough.
EXPECTED_DICE = [0.0] * 4096
BUDGET_SCALE = 1.0
# decide_ms: the 75th-percentile ms of a decide with the look-ahead on, by
# system count, the worse of two and five seats (native CPython, guards lifted,
# against marshal): 8.3 / 11.6 / 21.1 / 34.3 ms at 11 / 18 / 31 / 40 systems.
COST_REF_NODES = 40
COST_DECIDE_MS = 34.0
COST_NODES_EXP = 1.1


def decide_ms(settings, seat) -> float:
    """Typical CPU ms of one decide on this setup (`ai.decide_ms`): knower's
    Search runs a rival's decide on every turn it looks ahead. With the
    look-ahead off the policy answers in well under a millisecond."""
    if int(round(settings.seat_params(seat).aux)) == 0:
        return 0.0
    drawn = getattr(settings, "custom_map", None)
    nodes = len(drawn.nodes) if drawn is not None else settings.nodes
    return COST_DECIDE_MS * (max(1, nodes) / COST_REF_NODES) ** COST_NODES_EXP


def _shift(vec, group: str, feature: str, delta: float) -> list[float]:
    out, i = list(vec), 0
    for name, feats in LAYOUT:
        if name == group:
            out[i + feats.index(feature)] += delta
        i += len(feats)
    return out


def make_decide(vec):
    """The bot's ``decide`` for a genome: the plain policy, or (``aux`` 1, the
    default) the look-ahead over a few variants of its move. Rivals in it are
    modelled by fixed blind rules (hold; strike all-out), never by another
    bot's code."""
    policy = make_policy(vec)
    postures = [make_policy(_shift(vec, "hold", "bias", d)) for d in (-LOOK_POSTURE, LOOK_POSTURE)]

    def plain(state, pid):
        return policy(state, pid)[0]

    def rollout(state, pid, orders, danger):
        """Our value LOOK_TURNS turns on after ``orders``, our side then
        playing the policy and the rivals holding (or, with ``danger``,
        striking all-out on the first turn), every fight at the expected roll."""
        board = _clone(state)
        root = state.turn
        rival = _danger(pid) if danger else None
        if rival:
            rival.root = root

        def play(st, p):
            if p == pid:
                return orders if st.turn == root else plain(st, p)
            return rival(st, p) if rival else []

        while board.winner is None and board.turn < root + LOOK_TURNS:
            seats = sorted(q for q, pl in board.players.items()
                           if not pl.is_neutral and not board.is_defeated(q))
            script = engine.TurnRecord(
                orders=[o for q in seats for o in play(board, q) if o.owner_id == q],
                dice=EXPECTED_DICE)
            engine.end_turn(board, script=script)
        return _value(board, pid)

    def decide(state, pid):
        p = state.players.get(pid)
        if p is not None and int(round(p.ai_params.aux)) == 0:
            return plain(state, pid)
        deadline = time.perf_counter() + LOOK_BUDGET_S * BUDGET_SCALE
        near: list = []
        base, chosen = policy(state, pid, near=near)
        plans = [base]
        seen = {tuple((o.source_id, o.dest_id, o.ships) for o in base)}

        def add(orders):
            sig = tuple((o.source_id, o.dest_id, o.ships) for o in orders)
            if sig not in seen:
                seen.add(sig)
                plans.append(orders)

        # The variants: one chosen move banned, one near miss forced, the
        # hold bar shifted either way.
        for k in [k for k in chosen if k[0] != "reinforce"][:LOOK_BANS]:
            add(policy(state, pid, ban={k})[0])
        for k in near[:LOOK_FORCES]:
            add(policy(state, pid, force={k})[0])
        for pol in postures:
            add(pol(state, pid)[0])
        if len(plans) == 1:
            return base
        # The best likely outcome, unless the dangerous case punishes it more
        # than it punishes the policy's own move.
        best, best_v, floor = base, -1.0, None
        for i, orders in enumerate(plans):
            if time.perf_counter() > deadline:
                break
            v = rollout(state, pid, orders, False)
            vd = rollout(state, pid, orders, True)
            if i == 0:
                floor = vd - LOOK_SLACK
            elif vd < floor:
                continue
            if v > best_v + 1e-9:
                best, best_v = orders, v
        return best

    return decide


decide = make_decide(_load_weights())

"""hunter — actuary's projected ledger, guarding only against strikes that come.

hunter is a fork of `models/actuary.py`: the same projection (each system's
owner and garrison per turn to a horizon, from the fleets already flying,
production and the engine's pile-up fold), the same ledger (ships and held
income at the horizon, ours against theirs) and the same greedy over single
launches and same-arrival coalitions. What it changes is the risk term, the
part that stops a launch leaving a system too thin.

  * **Edge risk.** actuary prices the threat next door as every rival ship that
    could land, summed over every arrival turn, at the nominal roll. hunter
    prices only the strikes a rival actually launches: one system next door, or
    several whose lanes take the same time, that beats what we hold on landing
    *on the worst roll*, launched now or on any later turn of the horizon.
    actuary's own launches meet that rule 98-100% of the time
    (`tools/hunt_predict.py`), so a garrison above it is safe from actuary, and
    the ships actuary would keep beside it go to the front instead. The
    shortfall share costs `EDGE_RISK_WEIGHT` of the stake.
  * **Gathering, on fast boards.** Where the median lane is at most
    `EDGE_GATHER_LANE` turns, a rival system next door also counts the rival
    garrisons beside it that could have joined it by the launch turn.
  * **Softer strikes, on slow boards.** Where the median lane is at least
    `CAPTURE_LANE` turns, a strike on a rival-held system is priced at the
    nominal roll (`CAPTURE_SWING`) rather than the worst: actuary guards only to
    the nominal roll next door.
  * `REINFORCE_WEIGHT` 0.45 (actuary 0.3): a rival's ships next door to a system
    we strike are assumed to relieve it a little more.
  * **Look-ahead.** Our plan and the ledger at other postures are each played
    `LOOK_PLIES` turns on a private board (its own dice), every rival on
    hunter's plain plan, and the best final ledger wins: 68.2% against actuary,
    +11.4 over plain hunter; +3.5 against marshal. Bounded by work
    (`LOOK_WORK`), so it runs on boards up to about 40 systems and plays the
    same on any machine.

Against actuary, paired by seed with seats swapped: without the look-ahead
55.5%, 55.3% and 55.1% on three fresh blocks of 100 seeds; with it 68.2%. Against the rest of the roster it is stronger than actuary
too (marshal +5.7 pts, knower at Predict +3.6). Every switch off is actuary
exactly (`tests/test_hunter.py`). It runs no other bot's code and reads nothing
but the board; actuary's Style: Learning is not carried over, so hunter keeps no
memory between turns. Measurements: docs/design/hunter.md.

The seat's ``ai_params.aux`` knob (``AUX_LABEL``: *Style*) is actuary's opening
choice: *Planned*, the default, plans the land-grab on a map of
`OPENING_GATE_SYSTEMS` or more with fast lanes, and *Greedy* opens with the
ledger everywhere. A value at actuary's third stop or past it reads as Planned.

Contract: ``decide(state, pid) -> list[Order]``. Reads the state, never mutates
it, and draws nothing from ``state.rng``: every tie breaks on system id.
"""

from __future__ import annotations

import copy
import heapq
import itertools
import math
import random
import time
from collections import defaultdict

from starconquest import combat, config, engine
from starconquest.model import Order

# --- tunables -------------------------------------------------------------- #
TUNED_SWING = 1.1 / 0.9     # the +/-10% swing the margins were fitted at: a floor
                            # under the live edge's jitter half, never an answer
HORIZON_PAD = 3             # turns projected past the longest lane on the board
HORIZON_MAX = 40            # an iteration bound for a pathological hand map
TAIL_TURNS = 24.0           # a held system is worth this many turns of its income
FRONT_BONUS = 0.5           # a ship standing on a front is worth 1 + this...
FRONT_DECAY = 0.5           # ...falling by this factor per turn of travel back
RISK_WEIGHT = 0.6           # share of a system's worth lost to a full shortfall
REINFORCE_WEIGHT = 0.45     # share of a rival's adjacent ships assumed to relieve
                            # a system we strike, if they can land in time (actuary 0.3;
                            # 0.45-0.7 is a plateau, ~+2 pts against actuary)
RELIEF_IN_FLIGHT = 0.0      # ...and of a rival's fleet landing next door that could
                            # turn round and land in time (actuary 0.3, from main's
                            # 5bfdc0c; 0 is hunter as measured)
MIN_GAIN = 1e-9             # a candidate must move the ledger by more than this; any
                            # floor costs, most in the slow regime ("The 2026-10 sweep"
                            # in docs/design/actuary.md)
MAX_COMMITS = 64            # an iteration bound on the greedy
SELF_RELIEF = 0.8           # share of our own neighbouring garrisons counted as
                            # able to relieve a system the risk term reads (see
                            # "A front stack pins the whole side" in actuary.md)...
SELF_RELIEF_REACTS = True   # ...counting a neighbour only if its lane is shorter than
                            # the shortest rival lane into the system, so relief
                            # launched after the strike is seen still lands first

# --- hunter's changes (docs/design/hunter.md); all off is actuary exactly ------- #
EDGE_RISK = True            # price a threat as actuary launches one: a source next door,
                            # or several landing on the same turn, that wins the worst
                            # roll (tools/hunt_predict.py: recall 0.98-1.00)...
EDGE_LOOK = 40              # ...launched this turn or up to this many turns later (1
                            # measured -10 pts: a front thins faster than it refills)...
EDGE_RISK_WEIGHT = 1.0      # ...a full shortfall costing this share of the stake (2.5
                            # and the fast-board 1.5 measured worse)
EDGE_SWING = 0.0            # >0: the swing a rival's strike must clear in place of the
                            # worst roll's (1.0 = the nominal roll); 1.0 and 1.1 were
                            # within noise against four bots
EDGE_GATHER_LANE = 2        # on a board whose median lane is at most this many turns, a
                            # rival system next door also counts, by launch turn k, the
                            # rival garrisons beside it that could have joined it by
                            # then (18:18 +8.2 pts; on every board -3.2); 0 = never
CAPTURE_SWING = 1.0         # >0: the swing our strike on a rival-held system is priced
                            # at (1.0 = it wins the nominal roll) in place of the worst
                            # roll: actuary guards only to the nominal roll next door...
CAPTURE_LANE = 6            # ...on a board whose median lane is at least this many turns
                            # (13:3 +7.8 pts; on 7:6 it cost 7-13); 0 = every board

# --- look-ahead (docs/design/hunter.md, "Look-ahead") ------------------------- #
LOOKAHEAD = True            # roll each candidate plan forward and keep the best
LOOK_PLIES = 6              # turns each candidate is played on before it is scored
LOOK_WEIGHTS = (0.4, 2.5)   # candidate postures besides ours: edge risk weight times these...
LOOK_CAPTURE = True         # ...and our plan with the nominal-roll strike on every board
LOOK_DECIDED = 1e6          # a rollout that ends the game scores this, won or lost
LOOK_WORK = 4000            # work per decide: a rollout ply costs systems ** 1.3 x seats
                            # (how a ledger decide grows); a line that would pass it is not
                            # rolled. Work, not time, so a seat plays the same on any machine
LOOK_BUDGET_S = 1.0         # a catastrophe guard on the wall clock, as knower's: never the
                            # budget that decides. The base plan is rolled first, so running
                            # out of either only ever falls back to plain hunter
BUDGET_SCALE = 1.0          # `ai.set_budget_scale` multiplies LOOK_BUDGET_S by this

# --- the pull (actuary's Style: Field; docs/design/field.md) ------------------ #
FIELD = True                # pull ships to where the balance of force is closest, as
                            # actuary's Field does; False is Greedy's front everywhere
FIELD_DECAY = 0.45          # influence kept per median lane of travel
FIELD_REACH = 3.0           # ...counted out to this many median lanes
CONTEST_WIDTH = 0.5         # the gap, as a share of both sides' influence, at
                            # which a system's stake falls to 1/e
SLOW_LANE = 5               # a median lane this many turns or more is slow: there
                            # the pull is half the field and half Greedy's front
                            # (0 = never)...
SLOW_FRONT = 1.0            # ...and Greedy's front is this share of it there (actuary
                            # 0.5; 1, the front alone, measured +6.9 pts over hunter
                            # without the field, 0.5 null and 0 -1.9: on slow boards the
                            # look-ahead wants the front)

# What one decide costs (`decide_ms`): 75th-percentile CPU ms on native CPython
# (M3), hunter against actuary over 18-120 systems, 3-18 ly/turn and 2 or 4 seats
# (tools/hunt_cost.py; docs/design/hunter.md, "What it costs"). The plain ledger
# grows as COST_DECIDE_MS x (systems / COST_REF_NODES) ** COST_NODES_EXP; the
# look-ahead adds COST_LOOK_MS per unit of the work it is allowed (LOOK_WORK), so
# the dearest boards are the mid-sized ones it still runs on.
COST_REF_NODES = 40
COST_DECIDE_MS = 10.2
COST_NODES_EXP = 1.28
COST_LOOK_MS = 0.025

# --- the opening ------------------------------------------------------------ #
GREEDY, PLANNED = 0, 1      # the Style knob's stops; anything unreadable, or at or past
                            # actuary's third stop (Learning), is PLANNED
OPENING_CLOCK_SCALE = 2.0       # the contact clock, as a multiple of the earliest strike
OPENING_INCOME_CLOCKS = 2.0     # income at contact is worth this many clocks of it, in ships
OPENING_CLOCK_MAX = 60          # an iteration bound on the plan
OPENING_GATE_SYSTEMS = 24       # the plan runs only on a map at least this big...
OPENING_FAST_LANE = 2           # ...whose median lane is at most this many turns
                                # (see "Planned against Greedy again" in actuary.md)
OPENING_MIN_SIDE = 7            # a side (held + region) smaller than this is left to the ledger
OPENING_ORDERS = ("value", "near", "cheap")
OPENING_SENDS = ("lean", "mass")
OPENING_SKIPS = (True, False)

# What the AI tab's generic aux slider is called when this bot holds the seat (read
# by `ai.aux_spec`/`ai.aux_names`; see models/README.md). The default 1.0 is Planned.
AUX_LABEL = "Style"
AUX_RANGE = (GREEDY, PLANNED, 1)
AUX_INT = True
AUX_NAMES = ("Greedy", "Planned")


def decide(state, pid):
    if not any(s.owner_id == pid for s in state.systems.values()):
        return []
    if _stop_of(state.players[pid]) != GREEDY and _plans(state):
        orders = opening(state, pid)
        if orders is not None:
            return orders
    field = FIELD and _stop_of(state.players[pid]) != GREEDY
    if LOOKAHEAD:
        return _look(state, pid, field)
    return _Ledger(state, pid, field=field).plan()


def _plans(state) -> bool:
    """Whether Planned opens with the plan on this map: only where
    lanes are fast and the map is big. Everywhere else the ledger opens, which
    measured level or better than the plan at 3 and 6 ly/turn at every size."""
    if len(state.systems) < OPENING_GATE_SYSTEMS:
        return False
    lanes = sorted(state.travel_turns(a, b) for a, s in state.systems.items()
                   for b in s.neighbors if a < b)
    if not lanes:
        return False
    mid = len(lanes) // 2
    median = lanes[mid] if len(lanes) % 2 else (lanes[mid - 1] + lanes[mid]) / 2
    return median <= OPENING_FAST_LANE


def _stop_of(player) -> int:
    """This seat's Style stop. Anything at or past a stop is that stop, and
    anything unreadable is the default, so a hand-edited token cannot take the
    bot down."""
    try:
        aux = float(player.ai_params.aux)
    except Exception:                     # noqa: BLE001
        return PLANNED
    return PLANNED if aux >= PLANNED else GREEDY


def _opening_of(player) -> int:
    """Whether this seat plans its opening."""
    return _stop_of(player)


def decide_ms(settings, seat) -> float:
    """Typical CPU ms of one decide on this setup (`ai.decide_ms`). knower runs a
    rival bot's decide on every turn it looks ahead, so this is what lets its
    menu warning count us."""
    drawn = getattr(settings, "custom_map", None)
    nodes = max(1, len(drawn.nodes) if drawn is not None else settings.nodes)
    plain = COST_DECIDE_MS * (nodes / COST_REF_NODES) ** COST_NODES_EXP
    return plain + COST_LOOK_MS * _look_work(nodes, max(2, settings.players))


def _look_work(systems: int, seats: int) -> float:
    """The work units one look-ahead decide spends on this board: whole lines,
    up to one per posture, while they fit LOOK_WORK, and none unless two do."""
    if not LOOKAHEAD:
        return 0.0
    line = LOOK_PLIES * seats * systems ** 1.3
    lines = min(1 + len(LOOK_WEIGHTS) + (1 if LOOK_CAPTURE and CAPTURE_SWING > 0 else 0),
                int(LOOK_WORK // line))
    return lines * line if lines >= 2 else 0.0


class _Ledger:
    """The projection and its value, with candidate launches tried against it."""

    def __init__(self, state, pid, weight=None, capture=None, field=False):
        self.state, self.pid = state, pid
        sysmap = state.systems
        self.ids = sorted(sysmap)
        # The two edges are adv * swing and swing / adv, so both halves come back
        # out of them: no constant stands in for either slider.
        attack, defend = combat.edge_attacking(TUNED_SWING), combat.edge_defending(TUNED_SWING)
        self.swing, self.advantage = math.sqrt(attack * defend), math.sqrt(attack / defend)
        self.rivals = [p.id for p in state.players.values()
                       if not p.is_neutral and p.id != pid and p.alive]
        longest = max((state.travel_turns(a, b) for a in self.ids
                       for b in sysmap[a].neighbors), default=1)
        self.horizon = max(2, min(HORIZON_MAX, longest + HORIZON_PAD))
        lanes = sorted(state.travel_turns(a, b) for a in self.ids for b in sysmap[a].neighbors if a < b)
        median = (lanes[len(lanes) // 2] + lanes[(len(lanes) - 1) // 2]) / 2 if lanes else 0
        self.capture = CAPTURE_SWING if CAPTURE_SWING > 0 and median >= CAPTURE_LANE else 0.0
        if capture is not None:
            self.capture = capture
        self.edge_weight = EDGE_RISK_WEIGHT if weight is None else weight
        self.gather = EDGE_GATHER_LANE > 0 and median <= EDGE_GATHER_LANE
        self.travel = {a: {b: state.travel_turns(a, b) for b in sorted(sysmap[a].neighbors)}
                       for a in self.ids}
        self.income = {sid: (1.0 / sysmap[sid].production if sysmap[sid].production > 0 else 0.0)
                       for sid in self.ids}

        # Arrivals already in the air: arrivals[sid][t] = {owner: ships}.
        self.arrivals = {sid: {} for sid in self.ids}
        for f in state.fleets:
            t = max(1, min(self.horizon, f.turns_remaining))
            slot = self.arrivals[f.dest_id].setdefault(t, {})
            slot[f.owner_id] = slot.get(f.owner_id, 0) + f.ships

        self.garrison = {sid: sysmap[sid].ships for sid in self.ids}
        self.front_value = self._pull() if field else self._front_values()
        self.relief = self._relief()
        self.lines = {sid: self._project(sid, self.garrison[sid], self.arrivals[sid])
                      for sid in self.ids}
        self.worth = {sid: self._worth(sid, self.lines[sid]) for sid in self.ids}
        self.risk = {sid: self._risk(sid) for sid in self.ids}
        self.orders: list[Order] = []

    # --- the plan ---------------------------------------------------------- #
    def plan(self):
        """Commit the best-paying launch until none pays.

        A commit changes the timelines of the systems it touches and nothing else,
        so every cache below is stamped with the versions of the systems its entry
        read, and only entries near the last commit are ever priced again. The
        answer is the same as re-pricing everything every round.
        """
        self.version = dict.fromkeys(self.ids, 0)
        self._near = {sid: self._reach(sid) for sid in self.ids}
        self._pairs, self._coalitions, self._prices, self._deps = {}, {}, {}, {}
        for _ in range(MAX_COMMITS):
            best, best_gain = None, MIN_GAIN
            for launch in self._candidates():
                gain = self._price(launch)
                if gain > best_gain:
                    best, best_gain = launch, gain
            if best is None:
                break
            self._commit(best)
        return self.orders

    def _reach(self, sid):
        """``sid``, its neighbours and theirs: what a launch touching ``sid`` reads
        (its worth, and the risk next door, which reads one hop further)."""
        out = {sid}
        for nbr in self.travel[sid]:
            out.add(nbr)
            out.update(self.travel[nbr])
        return out

    def _pair_deps(self, src, dst):
        key = (src, dst)
        deps = self._deps.get(key)
        if deps is None:
            deps = self._deps[key] = sorted(self._near[src] | self._near[dst])
        return deps

    def _stamp(self, sids):
        return tuple(self.version[sid] for sid in sids)

    def _price(self, launch):
        deps = (self._pair_deps(launch[0][0], launch[0][1]) if len(launch) == 1
                else sorted(set().union(*(self._near[x] for src, dst, _ in launch
                                          for x in (src, dst)))))
        stamp = self._stamp(deps)
        hit = self._prices.get(launch)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        gain = self._gain(launch)[0]
        self._prices[launch] = (stamp, gain)
        return gain

    def _spare(self, sid):
        """Ships ``sid`` can still launch this turn."""
        if self.state.systems[sid].owner_id != self.pid:
            return 0
        return self.garrison[sid]

    def _candidates(self):
        """Every launch worth pricing, as tuples of (source, dest, ships)."""
        seen = set()
        for src in self.ids:
            if self._spare(src) <= 0:
                continue
            for dst in self.travel[src]:
                stamp = self._stamp(self._pair_deps(src, dst))
                hit = self._pairs.get((src, dst))
                if hit is None or hit[0] != stamp:
                    hit = (stamp, self._singles(src, dst))
                    self._pairs[(src, dst)] = hit
                for launch in hit[1]:
                    if launch not in seen:
                        seen.add(launch)
                        yield launch

        # Same-arrival coalitions on a target nobody can take alone.
        for dst in self.ids:
            stamp = self._stamp([dst, *self.travel[dst]])
            hit = self._coalitions.get(dst)
            if hit is None or hit[0] != stamp:
                hit = (stamp, self._coalitions_on(dst))
                self._coalitions[dst] = hit
            for launch in hit[1]:
                if launch not in seen:
                    seen.add(launch)
                    yield launch

    def _singles(self, src, dst):
        if self._idle(src, dst):
            return []
        spare = self._spare(src)
        amounts = {spare, max(1, spare // 2)}
        need = self._enough(dst, ((src, self.travel[src][dst]),), spare)
        if need is not None:
            amounts.add(need)
        return [((src, dst, x),) for x in sorted(amounts)]

    def _idle(self, src, dst):
        """True when no launch from ``src`` to ``dst`` can pay: ``dst`` is ours
        throughout, nothing hostile lands there and nothing threatens it, and it
        stands no nearer a front. Then each ship moved is worth at most what it
        was where it stood, and the source can only lose by it. (A garrison that
        will be attacked is never idle: by the square law a bigger stack keeps
        more than the ships added to it.)"""
        if SELF_RELIEF > 0 and any(self.risk[n] for x in (src, dst) for n in self.travel[x]):
            return False        # moving them changes the relief a neighbour counts on
        return (self.front_value[dst] <= self.front_value[src]
                and self._quiet(src) and self._quiet(dst))

    def _quiet(self, sid):
        """Ours at every turn, nothing hostile landing, no risk priced: a garrison
        whose horizon count moves one for one with the ships it holds."""
        pid = self.pid
        return (self.risk[sid] == 0.0
                and all(o == pid for o in self.lines[sid][0])
                and all(who == pid for slot in self.arrivals[sid].values() for who in slot))

    def _coalitions_on(self, dst):
        if self.lines[dst][0][self.horizon] == self.pid:
            return []
        by_turns: dict[int, list[int]] = {}
        for src, d in self.travel[dst].items():
            if self._spare(src) > 0:
                by_turns.setdefault(d, []).append(src)
        out = []
        for d, srcs in sorted(by_turns.items()):
            if len(srcs) < 2:
                continue
            srcs.sort(key=lambda s: (-self._spare(s), s))
            launch = self._coalition(dst, d, srcs)
            if launch is not None:
                out.append(launch)
        return out

    def _enough(self, dst, legs, cap):
        """Fewest ships, at most ``cap``, that the last of ``legs`` must add for us
        to hold ``dst`` at the horizon (the earlier legs send their spare), or None
        when even ``cap`` is not enough or it is ours either way."""
        H, pid = self.horizon, self.pid
        if self.lines[dst][0][H] == pid:
            return None
        base = sum(self._spare(s) for s, _ in legs[:-1])
        d = legs[-1][1]

        def holds(x):
            arr = _with(self.arrivals[dst], d, pid, base + x)
            return self._project(dst, self.garrison[dst], arr)[0][H] == pid

        if not holds(cap):
            return None
        lo, hi = 1, cap
        while lo < hi:
            mid = (lo + hi) // 2
            if holds(mid):
                hi = mid
            else:
                lo = mid + 1
        return lo

    def _coalition(self, dst, d, srcs):
        total = 0
        for i, src in enumerate(srcs):
            spare = self._spare(src)
            if total + spare <= 0:
                continue
            need = self._enough(dst, tuple((s, d) for s in srcs[:i + 1]), spare)
            if need is not None:
                if i == 0:
                    return None          # one source suffices: a single launch already covers it
                return tuple((s, dst, self._spare(s)) for s in srcs[:i]) + ((src, dst, need),)
            total += spare
        return None

    # --- pricing ----------------------------------------------------------- #
    def _gain(self, launch):
        """How much ``launch`` moves the ledger, and the state it would leave: the
        changed garrisons, arrivals and timelines (only those), and every system
        whose risk it re-read."""
        garrison, arrivals = {}, {}
        for src, dst, x in launch:
            garrison[src] = garrison.get(src, self.garrison[src]) - x
            arr = arrivals.get(dst, self.arrivals[dst])
            arrivals[dst] = _with(arr, self.travel[src][dst], self.pid, x)
        touched = set(garrison) | set(arrivals)
        lines = {sid: self._project(sid, garrison.get(sid, self.garrison[sid]),
                                    arrivals.get(sid, self.arrivals[sid]))
                 for sid in touched}
        gain = 0.0
        for sid in touched:
            gain += self._worth(sid, lines[sid]) - self.worth[sid]
        # A neighbour's risk reads only the rival-held part of these timelines, so
        # it can move only if a rival holds the touched system at some turn, before
        # the launch or after it.
        exposed = set(touched)
        rivals = self.rivals
        for sid in touched:
            if any(o in rivals for o in self.lines[sid][0]) or any(o in rivals for o in lines[sid][0]) \
                    or (SELF_RELIEF > 0 and self.pid in self.lines[sid][0]):
                exposed.update(self.travel[sid])
        for sid in exposed:
            gain -= self._risk(sid, lines) - self.risk[sid]
        return gain, garrison, arrivals, lines, exposed

    def _commit(self, launch):
        _, garrison, arrivals, lines, exposed = self._gain(launch)
        self.garrison.update(garrison)
        self.arrivals.update(arrivals)
        self.lines.update(lines)
        for sid in exposed:
            self.worth[sid] = self._worth(sid, self.lines[sid])
            self.risk[sid] = self._risk(sid)
        for src, dst, x in launch:
            self.orders.append(Order(self.pid, src, dst, x))
            self.version[src] += 1
            self.version[dst] += 1

    def _worth(self, sid, line):
        """What ``sid`` is worth to us at the horizon, in ships."""
        owners, ships = line
        H, owner = self.horizon, owners[self.horizon]
        if owner == 0:
            return 0.0
        value = ships[H] + TAIL_TURNS * self.income[sid]
        if owner == self.pid:
            return ships[H] * self.front_value[sid] + TAIL_TURNS * self.income[sid]
        return -value

    def _risk(self, sid, lines=None):
        """Expected loss at ``sid``: `_edge_risk` with EDGE_RISK on, else
        actuary's, the worst a single rival could land there, as a shortfall share
        of what the system is worth times RISK_WEIGHT, priced at the nominal roll
        with the advantage ours. ``lines`` overrides some timelines (a launch
        being priced)."""
        lines = lines or {}
        owners, ships = lines.get(sid) or self.lines[sid]
        pid, H = self.pid, self.horizon
        if pid not in owners:
            return 0.0
        if EDGE_RISK:
            return self._edge_risk(sid, owners, ships, lines)
        worst = 0.0
        for rival in self.rivals:
            reach = None
            for nbr, d in self.travel[sid].items():
                n_owners, n_ships = lines.get(nbr) or self.lines[nbr]
                if rival not in n_owners:
                    continue
                if reach is None:
                    reach = [0] * (H + 1)
                for t in range(d, H + 1):
                    if n_owners[t - d] == rival:
                        reach[t] += n_ships[t - d]
            if reach is None:
                continue
            if SELF_RELIEF > 0:
                quickest = min(d for nbr, d in self.travel[sid].items()
                               if rival in (lines.get(nbr) or self.lines[nbr])[0])
                own = self._own_relief(sid, lines, quickest if SELF_RELIEF_REACTS else None)
            else:
                own = None
            for t in range(1, H + 1):
                if owners[t] != pid or reach[t] <= 0:
                    continue
                need = reach[t] / self.advantage
                held = ships[t] + (own[t] if own else 0.0)
                if need > held:
                    worst = max(worst, (need - held) / need)
        if worst <= 0.0:
            return 0.0
        stake = ships[H] * self.front_value[sid] + 2 * TAIL_TURNS * self.income[sid]
        return RISK_WEIGHT * worst * stake

    def _edge_risk(self, sid, owners, ships, lines):
        """The shortfall share against the strongest strike a rival would launch
        by actuary's rule: launched at turn k <= EDGE_LOOK from one system next
        door, or several whose lanes take the same time, only if it beats what we
        hold on landing on the worst roll. Our garrisons next door count as relief
        (SELF_RELIEF) only if they land before it."""
        pid, H = self.pid, self.horizon
        edge = self.advantage * (EDGE_SWING if EDGE_SWING > 0 else self.swing)
        travel, base = self.travel, self.lines
        # Each neighbour's timeline, read once (a lane is at least a turn, so a
        # launch after H - 1 cannot land inside the horizon).
        near = [(nbr, d) + (lines.get(nbr) or base[nbr]) for nbr, d in travel[sid].items()]
        last_k = min(EDGE_LOOK, H - 1)
        relief: dict = {}                 # quickest rival lane -> our relief per turn
        worst = 0.0
        for rival in self.rivals:
            theirs = [x for x in near if rival in x[2]]     # systems it ever holds
            if not theirs:
                continue
            for k in range(0, last_k + 1):
                by_d: dict[int, int] = {}
                for nbr, d, n_owners, n_ships in theirs:
                    if k + d > H:
                        continue
                    if n_owners[k] == rival and n_ships[k] > 0:
                        gathered = n_ships[k]
                        if self.gather and k > 0:
                            for far, e in travel[nbr].items():
                                if far == sid or e > k:
                                    continue
                                f_owners, f_ships = lines.get(far) or base[far]
                                if f_owners[0] == rival:
                                    gathered += f_ships[0]
                        by_d[d] = by_d.get(d, 0) + gathered
                if not by_d:
                    continue
                own = None
                if SELF_RELIEF > 0:
                    quickest = min(by_d) if SELF_RELIEF_REACTS else None
                    own = relief.get(quickest)
                    if own is None:
                        own = relief[quickest] = self._own_relief(sid, lines, quickest)
                for d, sent in by_d.items():
                    t = k + d
                    if owners[t] != pid:
                        continue
                    held = ships[t] + (own[t] if own else 0.0)
                    if sent > held * edge and sent > held:
                        worst = max(worst, 1.0 - held * edge / sent)
        if worst <= 0.0:
            return 0.0
        stake = ships[H] * self.front_value[sid] + 2 * TAIL_TURNS * self.income[sid]
        return self.edge_weight * worst * stake

    def _own_relief(self, sid, lines, under=None):
        """Per turn, ``SELF_RELIEF`` of what our garrisons next door could have
        landed on ``sid`` by then."""
        H, pid = self.horizon, self.pid
        own = [0.0] * (H + 1)
        for nbr, d in self.travel[sid].items():
            if under is not None and d >= under:
                continue
            n_owners, n_ships = lines.get(nbr) or self.lines[nbr]
            for t in range(d, H + 1):
                if n_owners[t - d] == pid:
                    own[t] += SELF_RELIEF * n_ships[t - d]
        return own

    # --- the projection ---------------------------------------------------- #
    def _project(self, sid, garrison, arrivals):
        """(owners, ships) for ``sid`` at turns 0..horizon, under the engine's phase
        order: production first, then whatever lands that turn."""
        node = self.state.systems[sid]
        owner, ships, progress = node.owner_id, garrison, node.prod_progress
        rate, H = node.production, self.horizon
        owners, counts = [owner], [ships]
        t = 0
        for land in sorted(arrivals):
            # Quiet turns up to and including the landing turn's production, in
            # closed form: k turns of progress build (progress + k) // rate ships.
            k = land - t
            if rate > 0 and (owner != 0 or config.NEUTRAL_PRODUCES):
                counts.extend(ships + (progress + i) // rate for i in range(1, k + 1))
                ships += (progress + k) // rate
                progress = (progress + k) % rate
            else:
                counts.extend([ships] * k)
            owners.extend([owner] * k)
            was = owner
            owner, ships = self._land(sid, land, owner, ships, arrivals[land])
            if owner != was:
                progress = 0
            owners[-1], counts[-1] = owner, ships
            t = land
        k = H - t
        if rate > 0 and (owner != 0 or config.NEUTRAL_PRODUCES):
            counts.extend(ships + (progress + i) // rate for i in range(1, k + 1))
        else:
            counts.extend([ships] * k)
        owners.extend([owner] * k)
        return owners, counts

    def _land(self, sid, t, owner, ships, landing):
        """The engine's pile-up fold: garrison pooled with its own side's arrivals,
        attackers folded strongest-first, the survivor against the garrison last."""
        forces = {owner: ships}
        for who, n in landing.items():
            forces[who] = forces.get(who, 0) + n
        if owner not in (0, self.pid) and self.pid in landing:
            # A rival we strike may be relieved before we land.
            by_t, flying = self.relief[sid]
            forces[owner] += int(REINFORCE_WEIGHT * by_t.get(t, 0)
                                 + RELIEF_IN_FLIGHT * flying.get(t, 0))
        sides = [(o, n) for o, n in forces.items() if n > 0]
        if not sides:
            return 0, 0
        if len(sides) == 1:
            return sides[0]
        garrison = next((s for s in sides if s[0] == owner), None)
        attackers = sorted((s for s in sides if s[0] != owner), key=lambda s: (-s[1], s[0]))
        cur = attackers[0]
        for other in attackers[1:]:
            cur = self._fight(cur, other, defender=None)
        if garrison is not None:
            cur = self._fight(cur, garrison, defender=owner)
        return cur

    def _fight(self, a, b, defender):
        """``a`` against ``b``. Who wins is priced against us: our side fights at
        the low roll and theirs at the high one, the holder of the system gets the
        defender advantage, and we must outnumber a garrison we attack. What we
        keep when we win is the nominal roll's: the worst roll decides whether a
        launch is safe, not what it is worth, and pricing both at the worst roll
        makes every capture look like a loss once the jitter is wide. Survivors by
        the square law, capped at the winner's ships."""
        (ao, an), (bo, bn) = a, b
        pid, swing, adv = self.pid, self.swing, self.advantage
        a_nom, b_nom = float(an), float(bn)
        if bo == defender:
            b_nom *= adv
        a_eff, b_eff = a_nom, b_nom
        if ao == pid:
            if bo == defender and an <= bn:
                return b
            b_eff *= self.capture if self.capture > 0 and bo in self.rivals else swing
        elif bo == pid:
            a_eff *= swing
        if a_eff > b_eff:
            won, w_eff, l_eff, actual = ao, a_eff, b_eff, an
            if ao == pid:
                w_eff, l_eff = a_nom, b_nom
        elif b_eff > a_eff or bo == defender:
            won, w_eff, l_eff, actual = bo, b_eff, a_eff, bn
            if bo == pid:
                w_eff, l_eff = b_nom, a_nom
        else:
            won, w_eff, l_eff, actual = ao, a_eff, b_eff, an
        left = min(actual, int(math.sqrt(max(0.0, w_eff * w_eff - l_eff * l_eff))))
        return (won, left) if left > 0 else (0, 0)

    # --- board readings ---------------------------------------------------- #
    def _front_values(self):
        """1 + FRONT_BONUS on a system touching anything not ours, decaying by
        FRONT_DECAY per turn of travel back through our own systems."""
        sysmap, pid = self.state.systems, self.pid
        dist = {}
        for sid in self.ids:
            if any(sysmap[n].owner_id != pid for n in sysmap[sid].neighbors) \
                    or sysmap[sid].owner_id != pid:
                dist[sid] = 0
        frontier = sorted(dist)
        # Dijkstra over our own systems, by travel turns.
        heap = [(0, sid) for sid in frontier]
        while heap:
            d, sid = heapq.heappop(heap)
            if d > dist.get(sid, math.inf):
                continue
            for nbr, t in self.travel[sid].items():
                if sysmap[nbr].owner_id != pid:
                    continue
                nd = d + t
                if nd < dist.get(nbr, math.inf):
                    dist[nbr] = nd
                    heapq.heappush(heap, (nd, nbr))
        return {sid: 1.0 + FRONT_BONUS * FRONT_DECAY ** dist.get(sid, 99) for sid in self.ids}

    def _pull(self):
        """The Field pull: the field, or on a board whose median lane is
        `SLOW_LANE` turns or more, the field and Greedy's front mixed by
        `SLOW_FRONT`."""
        lanes = sorted(d for a in self.ids for b, d in self.travel[a].items() if a < b)
        field = self._field()
        if not lanes or SLOW_LANE <= 0 or lanes[len(lanes) // 2] < SLOW_LANE:
            return field
        front = self._front_values()
        return {sid: (1 - SLOW_FRONT) * field[sid] + SLOW_FRONT * front[sid] for sid in self.ids}

    def _field(self):
        """actuary's `_field`: the pull on a ship at each system, 1 to 1 +
        `FRONT_BONUS`, from how close the balance of force is within its reach."""
        sysmap, pid = self.state.systems, self.pid
        rivals = set(self.rivals)
        lanes = sorted(d for a in self.ids for b, d in self.travel[a].items() if a < b)
        unit = float(lanes[len(lanes) // 2]) if lanes else 1.0
        near = {sid: self._within(sid, FIELD_REACH * unit, unit) for sid in self.ids}

        mine, foe = defaultdict(float), defaultdict(float)
        for w in self.ids:
            node = sysmap[w]
            side = mine if node.owner_id == pid else foe if node.owner_id in rivals else None
            if side is None or node.ships <= 0:
                continue
            for s, d in near[w].items():
                side[s] += node.ships * FIELD_DECAY ** d
        for f in self.state.fleets:
            side = mine if f.owner_id == pid else foe if f.owner_id in rivals else None
            if side is None:
                continue
            for s, d in near[f.dest_id].items():
                side[s] += f.ships * FIELD_DECAY ** (d + f.turns_remaining / unit)

        stake = {}
        for s in self.ids:
            node = sysmap[s]
            if node.owner_id == pid and all(sysmap[n].owner_id == pid for n in node.neighbors):
                continue                    # our interior: nothing to contest
            standing = node.ships if node.owner_id in rivals else 0
            x = (mine[s] - foe[s]) / (CONTEST_WIDTH * max(1.0, mine[s] + foe[s]))
            stake[s] = (TAIL_TURNS * self.income[s] + standing) * math.exp(-x * x)

        pull = {}
        for w in self.ids:
            if sysmap[w].owner_id == pid:
                pull[w] = sum(stake[s] * FIELD_DECAY ** near[w][s] for s in stake if s in near[w])
        top = max(pull.values(), default=0.0)
        return {sid: (1.0 + FRONT_BONUS * pull[sid] / top if top > 0 else 1.0)
                if sid in pull else 1.0 + FRONT_BONUS
                for sid in self.ids}

    def _within(self, src, radius, unit):
        """Median lanes from ``src`` to every system within ``radius`` turns."""
        dist = {src: 0}
        heap = [(0, src)]
        while heap:
            d, sid = heapq.heappop(heap)
            if d > dist[sid]:
                continue
            for nbr, t in self.travel[sid].items():
                nd = d + t
                if nd <= radius and nd < dist.get(nbr, math.inf):
                    dist[nbr] = nd
                    heapq.heappush(heap, (nd, nbr))
        return {sid: d / unit for sid, d in dist.items()}

    def _relief(self):
        """relief[sid][t]: ships the holder of ``sid`` could land there by turn t,
        launching no sooner than next turn, as (from garrisons next door, from
        fleets landing next door) so each can carry its own weight."""
        sysmap = self.state.systems
        out = {}
        for sid in self.ids:
            owner = sysmap[sid].owner_id
            by_t, flying = {}, {}
            if owner not in (0, self.pid):
                for nbr, d in self.travel[sid].items():
                    if sysmap[nbr].owner_id == owner:
                        for t in range(d + 1, self.horizon + 1):
                            by_t[t] = by_t.get(t, 0) + sysmap[nbr].ships
                if RELIEF_IN_FLIGHT > 0:
                    for t0, ships in self._relief_in_flight(sid, owner):
                        for t in range(t0, self.horizon + 1):
                            flying[t] = flying.get(t, 0) + ships
            out[sid] = (by_t, flying)
        return out

    def _relief_in_flight(self, sid, owner):
        """(turn, ships) for each of ``owner``'s pools landing next door to
        ``sid`` that could take its landing system if it must and come back: a
        pool landing on turn r relaunches then and reaches ``sid`` at r + d. A
        neighbour ``owner`` does not hold costs the pool its fight there, priced
        by `_fight` against its garrison now."""
        sysmap = self.state.systems
        pools = defaultdict(int)
        for f in self.state.fleets:
            if f.owner_id == owner and f.dest_id != sid and f.dest_id in self.travel[sid]:
                pools[(f.dest_id, max(1, f.turns_remaining))] += f.ships
        out = []
        for (nbr, r), ships in sorted(pools.items()):
            t0 = r + self.travel[sid][nbr]
            if t0 > self.horizon:
                continue
            held = sysmap[nbr]
            if held.owner_id != owner:
                won, ships = self._fight((owner, ships), (held.owner_id, held.ships),
                                         defender=held.owner_id)
                if won != owner:
                    continue
            out.append((t0, ships))
        return out


def _with(arrivals, t, owner, ships):
    """A copy of ``arrivals`` with ``ships`` more of ``owner``'s landing at ``t``."""
    out = dict(arrivals)
    slot = dict(out.get(t, {}))
    slot[owner] = slot.get(owner, 0) + ships
    out[t] = slot
    return out


# --------------------------------------------------------------------------- #
# Look-ahead
#
# Our candidate plans are hunter's own and the same ledger at other postures.
# Each is played on a private board for LOOK_PLIES turns, every seat after the
# first ply (and every rival from the first) playing hunter's plain plan from
# its own seat: no other bot's code is run, so this is a self-model, not an
# oracle. The copy rolls its own dice (`dice_salt` + ":hunter-rollout"), so no
# line foresees the game's. Every line rolls the same dice, so a score gap is
# the plan's, not luck's. The score is the ledger's value of the final board
# from our seat, which already projects every fleet still flying.
# --------------------------------------------------------------------------- #
def _look(state, pid, field=False):
    deadline = time.perf_counter() + LOOK_BUDGET_S * BUDGET_SCALE
    seats = sum(1 for p in state.players.values() if not p.is_neutral and p.alive)
    line = LOOK_PLIES * seats * len(state.systems) ** 1.3     # the work one line costs
    if 2 * line > LOOK_WORK:
        return _Ledger(state, pid, field=field).plan()   # not two lines' worth: nothing to compare
    plans, seen = [], set()
    for weight, capture in _postures():
        orders = _Ledger(state, pid, weight, capture, field).plan()
        key = tuple(sorted((o.source_id, o.dest_id, o.ships) for o in orders))
        if key not in seen:
            seen.add(key)
            plans.append(orders)
        if time.perf_counter() > deadline:
            break
    if len(plans) == 1:
        return plans[0]
    # Every rival decides its first turn from this same board in every line, so
    # its orders there are worked out once, not once per line.
    first = {q: _Ledger(state, q, field=field).plan() for q, p in state.players.items()
             if q != pid and not p.is_neutral and any(x.owner_id == q for x in state.systems.values())}
    lines = []
    for i, orders in enumerate(plans):
        if (i + 1) * line > LOOK_WORK and i > 0:
            break                         # the next line would pass the work budget
        lines.append(_Line(state, pid, orders, first, field))
    best, best_score = plans[0], None
    for ln in lines:
        score = ln.play(LOOK_PLIES, deadline)
        if score is None:
            break                         # out of time mid-line: keep the best scored
        if best_score is None or score > best_score:
            best, best_score = ln.orders, score
    return best

def _postures():
    yield None, None
    for factor in LOOK_WEIGHTS:
        yield EDGE_RISK_WEIGHT * factor, None
    if LOOK_CAPTURE and CAPTURE_SWING > 0:
        yield None, CAPTURE_SWING


class _Line:
    """One candidate played forward on a private board, a few turns at a time."""

    def __init__(self, state, pid, orders, first_orders=None, field=False):
        self.board, self.pid, self.orders = _clone(state), pid, orders
        self.first_orders, self.plies, self.field = first_orders, 0, field

    def _decide(self, s, q):
        if self.plies == 0:
            if q == self.pid:
                return self.orders
            if self.first_orders is not None:
                return self.first_orders.get(q, [])
        if not any(x.owner_id == q for x in s.systems.values()):
            return []
        return _Ledger(s, q, field=self.field).plan()

    def play(self, upto, deadline=math.inf):
        """The line's score once it has played ``upto`` turns in all, or ``None``
        if the clock ran out first (checked before each, so an overrun is one ply)."""
        board, pid = self.board, self.pid
        while self.plies < upto and board.winner is None:
            if time.perf_counter() > deadline:
                return None
            engine.end_turn(board, decide=self._decide)
            self.plies += 1
        if board.winner is not None:
            return LOOK_DECIDED if board.winner == pid else -LOOK_DECIDED
        if not any(x.owner_id == pid for x in board.systems.values()):
            return -LOOK_DECIDED
        return sum(_Ledger(board, pid, field=self.field).worth.values())


def _rollout(state, pid, orders, deadline=math.inf, first_orders=None, field=False):
    """The score of ``orders`` after LOOK_PLIES turns, or ``None`` if the clock ran
    out before the last of them."""
    return _Line(state, pid, orders, first_orders, field).play(LOOK_PLIES, deadline)


def _clone(state):
    """A private board: every mutable container rebuilt (as knower's `_clone`),
    its own rng, every seat driven by `decide`, and its own dice."""
    clone = copy.copy(state)
    clone.systems = {sid: copy.copy(s) for sid, s in state.systems.items()}
    for s in clone.systems.values():
        s.neighbors = list(s.neighbors)
    clone.fleets = [copy.copy(f) for f in state.fleets]
    clone.players = {q: copy.copy(p) for q, p in state.players.items()}
    for p in clone.players.values():
        p.ai_params = copy.copy(p.ai_params)
        p.is_human = False
    clone.adjacency = {sid: dict(n) for sid, n in state.adjacency.items()}
    clone.rng = random.Random(0)
    clone.dice_salt = f"{state.dice_salt}:hunter-rollout"
    return clone


# --------------------------------------------------------------------------- #
# The planned opening (Opening: Planned)
# --------------------------------------------------------------------------- #
def opening(state, pid):
    """This turn's orders under the planned opening until `_Opening.over`, else
    ``None`` (and the ledger plays). Measurements behind it:
    "The planned opening" in docs/design/actuary.md."""
    plan = _Opening(state, pid)
    if plan.over():
        return None
    return plan.orders()


class _Opening:
    """The land-grab as a one-player puzzle. The region is the neutrals strictly
    nearer us than any rival, in lane turns. The clock is the earliest turn a
    rival's ships could land on our side, times `OPENING_CLOCK_SCALE`. Each
    policy is scored by ships held at the clock plus the income rate there,
    weighted by `OPENING_INCOME_CLOCKS` clocks of it."""

    def __init__(self, state, pid):
        self.state, self.pid = state, pid
        sysmap = state.systems
        self.rivals = {p.id for p in state.players.values()
                       if not p.is_neutral and p.id != pid and p.alive}
        self.owned = sorted(sid for sid, s in sysmap.items() if s.owner_id == pid)
        self.attack = combat.edge_attacking(TUNED_SWING)
        self.advantage = math.sqrt(self.attack / combat.edge_defending(TUNED_SWING))
        self.travel = {a: {b: state.travel_turns(a, b) for b in sorted(sysmap[a].neighbors)}
                       for a in sysmap}
        self.ours = self._dijkstra({sid: 0 for sid in self.owned})
        self.theirs = self._dijkstra(
            {sid: 0 for sid, s in sysmap.items() if s.owner_id in self.rivals})
        self.region = {sid for sid, s in sysmap.items() if s.owner_id == 0
                       and self.ours.get(sid, math.inf) < self.theirs.get(sid, math.inf)}
        self.clock = self._clock()
        self.weight = OPENING_INCOME_CLOCKS * self.clock

    def _dijkstra(self, starts):
        dist = dict(starts)
        heap = [(t, sid) for sid, t in starts.items()]
        heapq.heapify(heap)
        while heap:
            t, sid = heapq.heappop(heap)
            if t > dist[sid]:
                continue
            for nbr, turns in self.travel[sid].items():
                if t + turns < dist.get(nbr, math.inf):
                    dist[nbr] = t + turns
                    heapq.heappush(heap, (t + turns, nbr))
        return dist

    def _clock(self):
        """The earliest turn a rival's ships could land on a system on our side."""
        best = min((max(self.ours.get(a, math.inf), self.theirs.get(a, math.inf))
                    for a in set(self.owned) | self.region), default=math.inf)
        if math.isinf(best):
            return OPENING_CLOCK_MAX
        return max(1, min(OPENING_CLOCK_MAX, round(OPENING_CLOCK_SCALE * best)))

    def over(self):
        """Bordering a live rival, a rival fleet heading for us, or our side of
        the map (what we hold plus the region) under `OPENING_MIN_SIDE`."""
        sysmap = self.state.systems
        for sid in self.owned:
            if any(sysmap[n].owner_id in self.rivals for n in self.travel[sid]):
                return True
        if any(f.owner_id in self.rivals and f.dest_id in self.owned for f in self.state.fleets):
            return True
        return not self.region or len(self.owned) + len(self.region) < OPENING_MIN_SIDE

    def orders(self):
        best, best_score = [], -math.inf
        for policy in itertools.product(OPENING_ORDERS, OPENING_SENDS, OPENING_SKIPS):
            sim = _OpeningSim(self, policy)
            score = sim.run()
            if score > best_score:
                best, best_score = sim.first, score
        return best

    def need(self, garrison):
        """Ships that take ``garrison`` at the worst roll; at least one more."""
        return max(math.floor(garrison) + 1, math.floor(self.attack * garrison) + 1)

    def survivors(self, ships, garrison):
        enemy = garrison * self.advantage
        return math.sqrt(max(0.0, ships * ships - enemy * enemy))


class _OpeningSim:
    """Our side of the map played forward under one policy, nobody else moving.
    Ships and production are continuous; captures are at the nominal roll, while
    a launch is sized for the worst one.

    A policy is (which neutral first: most valuable, nearest or cheapest; send just
    enough or everything; skip a capture that does not pay, or not). A system with
    nothing to take sends its ships one hop towards the nearest of ours that has."""

    def __init__(self, plan, policy):
        self.plan = plan
        self.order, self.send, self.skip = policy
        sysmap = plan.state.systems
        self.ships = {sid: float(sysmap[sid].ships) for sid in plan.owned}
        self.garrison = {sid: float(sysmap[sid].ships) for sid in plan.region}
        self.income = {sid: (1.0 / s.production if s.production > 0 else 0.0)
                       for sid, s in sysmap.items()}
        self.fleets = [(f.turns_remaining, f.dest_id, float(f.ships))
                       for f in plan.state.fleets if f.owner_id == plan.pid]
        self.first: list[Order] = []
        self.t = 0
        self._feed_to = None

    def run(self):
        while self.t < self.plan.clock:
            self._decide()
            self._advance()
        held = sum(self.ships.values()) + sum(s for _, _, s in self.fleets)
        rate = sum(self.income[sid] for sid in self.ships)
        return held + self.plan.weight * rate

    def _launch(self, src, dst, ships):
        if ships <= 0:
            return
        self.ships[src] -= ships
        self.fleets.append((self.plan.travel[src][dst], dst, float(ships)))
        if self.t == 0:
            self.first.append(Order(self.plan.pid, src, dst, int(ships)))

    def _pending(self, dst):
        return sum(s for _, d, s in self.fleets if d == dst)

    def _decide(self):
        travel = self.plan.travel
        feed = self._feed()
        for src in sorted(self.ships):
            avail = math.floor(self.ships[src])
            targets = [n for n in travel[src] if n in self.garrison]
            if not targets:
                nxt = feed.get(src)
                if nxt is not None and avail > 0:
                    self._launch(src, nxt, avail)
                continue
            for dst in self._rank(src, targets):
                if avail <= 0:
                    break
                need = self.plan.need(self.garrison[dst]) - self._pending(dst)
                if need <= 0:
                    continue
                if need > avail:
                    if self.send == "mass":
                        break
                    continue
                if self.skip and self._worth(src, dst, need) <= 0:
                    continue
                ships = avail if self.send == "mass" else math.ceil(need)
                self._launch(src, dst, ships)
                avail -= ships

    def _worth(self, src, dst, ships):
        land = self.t + self.plan.travel[src][dst]
        lost = ships - self.plan.survivors(ships, self.garrison[dst])
        income = self.income[dst]
        return income * max(0, self.plan.clock - land) + self.plan.weight * income - lost

    def _rank(self, src, targets):
        travel = self.plan.travel[src]
        if self.order == "near":
            key = lambda n: (travel[n], -self.income[n], n)
        elif self.order == "cheap":
            key = lambda n: (self.garrison[n], travel[n], n)
        else:
            key = lambda n: (-self._worth(src, n, self.plan.need(self.garrison[n])), n)
        return sorted(targets, key=key)

    def _feed(self):
        """For each of our systems with nothing to take, its next hop towards the
        nearest of ours that has. Rebuilt only when what we own changes."""
        key = (frozenset(self.ships), frozenset(self.garrison))
        if self._feed_to is not None and self._feed_to[0] == key:
            return self._feed_to[1]
        travel = self.plan.travel
        front = [sid for sid in self.ships if any(n in self.garrison for n in travel[sid])]
        dist = {sid: 0 for sid in front}
        hop: dict[int, int] = {}
        heap = [(0, sid) for sid in sorted(front)]
        heapq.heapify(heap)
        while heap:
            t, sid = heapq.heappop(heap)
            if t > dist[sid]:
                continue
            for nbr, turns in travel[sid].items():
                if nbr in self.ships and t + turns < dist.get(nbr, math.inf):
                    dist[nbr] = t + turns
                    hop[nbr] = sid
                    heapq.heappush(heap, (t + turns, nbr))
        self._feed_to = (key, hop)
        return hop

    def _advance(self):
        for sid in self.ships:
            self.ships[sid] += self.income[sid]
        landed, flying = {}, []
        for turns, dst, ships in self.fleets:
            if turns <= 1:
                landed[dst] = landed.get(dst, 0.0) + ships
            else:
                flying.append((turns - 1, dst, ships))
        self.fleets = flying
        for dst, ships in sorted(landed.items()):
            if dst in self.ships:
                self.ships[dst] += ships
            elif dst in self.garrison:
                left = self.plan.survivors(ships, self.garrison[dst])
                if left > 0:
                    del self.garrison[dst]
                    self.ships[dst] = left
                else:
                    held = self.garrison[dst] * self.plan.advantage
                    self.garrison[dst] = math.sqrt(max(0.0, held * held - ships * ships)) \
                        / self.plan.advantage
        self.t += 1


def configure(overrides: dict) -> None:
    """Set module constants by name, for the bench's variants (tools/hunt_bench.py)."""
    g = globals()
    for name, value in overrides.items():
        if name not in g:
            raise KeyError(name)
        if isinstance(g[name], tuple):
            g[name] = tuple(value) if isinstance(value, (tuple, list)) else (value,)
        else:
            g[name] = type(g[name])(value) if isinstance(g[name], (int, float, bool)) else value

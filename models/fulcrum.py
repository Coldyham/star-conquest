"""fulcrum — every ship goes where the balance of force is closest.

actuary and marshal play the same middle game. Their stacks move whole every
turn, a doomed garrison steps into whatever its striker emptied, and half of all
strikes on a rival land on an empty system: two dancers trading places. What
decides where a ledger bot's ships go is the pull on them, and actuary's pull is
the plainest there is: every system touching anything not ours is a front, and a
ship there is worth 1 + `FRONT_BONUS`, falling off by distance behind it. Take
the pull away and actuary wins 6 games in 100 against itself.

fulcrum keeps actuary's ledger for pricing: every system's timeline from the
fleets in flight and production, fights priced at the worst roll and kept at the
nominal one, the risk next door, and the greedy over single launches and
same-arrival coalitions that commits whichever raises the board's worth most. It
replaces the pull with an **influence field**:

  * **Influence.** Every garrison, and every fleet in flight at the system it will
    land on, projects its ships over the systems it could reach, keeping
    `FIELD_DECAY` of them per median lane of travel, out to `FIELD_REACH` median
    lanes. Lanes, not turns: the same field on a board of one-turn lanes and on
    one of six.
  * **Stake.** A system is worth what the ledger says it is worth: `TAIL_TURNS`
    of its income, and the ships standing on it if they are a rival's (a
    neutral's garrison is only in the way). Our own interior has no stake,
    since nothing there is contested.
  * **Contest.** Where one side's influence swamps the other's, one more ship
    changes nothing; where the two balance, it changes who holds the system. A
    stake counts for exp(-x^2), x the gap between the two influences over
    `CONTEST_WIDTH` of their sum.
  * **Pull.** A ship at one of our systems is worth 1 + `FRONT_BONUS` times the
    contested stake within its reach, decayed the same way, as a share of the
    most any of our systems has. Ships landing on a system we do not hold keep
    the full bonus, as in actuary.

What that changes. A front that nobody is contesting stops holding ships, and
the ships go where a few more of them tip a system: a rival stack's
neighbourhood, a neutral both sides can reach, the middle of the map before
first contact. In the opening fulcrum launches half as much again of its
garrison as actuary, half of it forward between its own systems; in the
contested middle more of what it launches goes at a rival system (68% against
50%). Measured against actuary and marshal, how differently it plays and how it
does against the roster: docs/design/fulcrum.md.

Contract: ``decide(state, pid) -> list[Order]``. Reads the state, never mutates
it, and draws nothing from ``state.rng``: every tie breaks on system id. It
predicts nobody and remembers nothing, so it is not an oracle.

Forked from ``models/actuary.py`` (its ledger, without the planned opening or
the Learning stop).
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict

from starconquest import combat, config
from starconquest.model import Order

# --- the ledger: actuary's, at actuary's values ------------------------------ #
TUNED_SWING = 1.1 / 0.9     # the +/-10% swing the margins were fitted at: a floor
                            # under the live edge's jitter half, never an answer
HORIZON_PAD = 3             # turns projected past the longest lane on the board
HORIZON_MAX = 40            # an iteration bound for a pathological hand map
TAIL_TURNS = 24.0           # a held system is worth this many turns of its income
FRONT_BONUS = 0.5           # the most a ship's place adds to it, in ships
RISK_WEIGHT = 0.6           # share of a system's worth lost to a full shortfall
REINFORCE_WEIGHT = 0.3      # share of a rival's adjacent ships assumed to relieve
                            # a system we strike, if they can land in time
RELIEF_IN_FLIGHT = 0.3      # ...and of a rival's fleet landing next door that
                            # could turn round and land in time
MIN_GAIN = 1e-9             # a candidate must move the ledger by more than this
MAX_COMMITS = 64            # an iteration bound on the greedy
SELF_RELIEF = 0.8           # share of our own neighbouring garrisons counted as
                            # able to relieve a system the risk term reads
SELF_RELIEF_REACTS = True   # ...counting a neighbour only if its lane is shorter
                            # than the shortest rival lane into the system

# --- the field --------------------------------------------------------------- #
FIELD_DECAY = 0.45          # influence kept per median lane of travel
FIELD_REACH = 3.0           # ...counted out to this many median lanes
CONTEST_WIDTH = 0.5         # a balance still counts as contested this far apart,
                            # as a share of the influence present

# What one decide costs (`decide_ms`), on native CPython: the ledger's cost,
# which the field barely adds to. See "What it costs" in docs/design/fulcrum.md.
COST_REF_NODES = 40
COST_DECIDE_MS = 8.3
COST_NODES_EXP = 0.77


def decide(state, pid):
    if not any(s.owner_id == pid for s in state.systems.values()):
        return []
    return _Ledger(state, pid).plan()


def decide_ms(settings, seat) -> float:
    """Typical CPU ms of one decide on this setup (`ai.decide_ms`), so knower's
    setup warning counts us when it runs this bot in its search."""
    drawn = getattr(settings, "custom_map", None)
    nodes = len(drawn.nodes) if drawn is not None else settings.nodes
    return COST_DECIDE_MS * (max(1, nodes) / COST_REF_NODES) ** COST_NODES_EXP


class _Ledger:
    """The projection and its value, with candidate launches tried against it."""

    def __init__(self, state, pid):
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
        self.front_value = self._field()
        self.relief = self._relief()
        self.lines = {sid: self._project(sid, self.garrison[sid], self.arrivals[sid])
                      for sid in self.ids}
        self.worth = {sid: self._worth(sid, self.lines[sid]) for sid in self.ids}
        self.risk = {sid: self._risk(sid) for sid in self.ids}
        self.orders: list[Order] = []

    # --- the field --------------------------------------------------------- #
    def _field(self):
        """The pull on a ship at each system, 1 to 1 + `FRONT_BONUS`: see the
        module docstring. Reads the board as it stands; the greedy then prices
        every launch against these values without recomputing them."""
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
        throughout, nothing hostile lands there and nothing threatens it, and its
        pull is no stronger. Then each ship moved is worth at most what it was
        where it stood, and the source can only lose by it. (A garrison that will
        be attacked is never idle: by the square law a bigger stack keeps more
        than the ships added to it.)"""
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
        if owner == self.pid:
            return ships[H] * self.front_value[sid] + TAIL_TURNS * self.income[sid]
        return -(ships[H] + TAIL_TURNS * self.income[sid])

    def _risk(self, sid, lines=None):
        """Expected loss at ``sid`` to the worst a single rival could land there:
        the shortfall, as a share of what the system is worth, times RISK_WEIGHT.
        Priced at the nominal roll with the advantage ours: a threat is ships that
        *could* come, and demanding the worst roll against all of them at once
        hoards every garrison when the jitter is wide. ``lines`` overrides some
        timelines (a launch being priced)."""
        lines = lines or {}
        owners, ships = lines.get(sid) or self.lines[sid]
        pid, H = self.pid, self.horizon
        if pid not in owners:
            return 0.0
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
        keep when we win is the nominal roll's. Survivors by the square law,
        capped at the winner's ships."""
        (ao, an), (bo, bn) = a, b
        pid, swing, adv = self.pid, self.swing, self.advantage
        a_nom, b_nom = float(an), float(bn)
        if bo == defender:
            b_nom *= adv
        a_eff, b_eff = a_nom, b_nom
        if ao == pid:
            if bo == defender and an <= bn:
                return b
            b_eff *= swing
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

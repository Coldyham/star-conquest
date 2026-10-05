"""convoy — plans where every ship will be on every turn, then sends only what must leave now.

Every other bot in the roster decides one source, or one target, at a time.
convoy treats the turn as a routing problem over time:

  * **Supply.** Every ship we will have, and when: each garrison now, each hull
    our systems will build, and each of our fleets landing on a system of ours.
  * **Demand.** Objectives, each "land N ships on system T at turn t": strikes on
    systems we do not hold, a defence against fleets already inbound, a guard
    against the rival garrisons next door. N is what wins the fight at the worst
    roll, against the target's projected garrison at t.
  * **Allocation.** A supply at system s, ready at turn r, can serve (T, t) when r
    plus the travel through our own systems from s to T is at most t. Objectives
    are committed greedily, the best value per ship first, each at its earliest
    feasible turn. A planned capture adds its survivors and its production as
    supply, so the plan can take one system and strike the next from it.
  * **Launch at the last moment.** Of all that, the only orders issued are for
    ships that must leave now to arrive on time, one hop along their path. Ships
    from every distance land on the same turn, and a ship with slack stays home,
    where it still counts as a guard.

Ships no objective wants move one hop towards the front, and a garrison that
cannot be held leaves before the blow lands.

Contract: ``decide(state, pid) -> list[Order]``. Reads the state, never mutates
it, and draws nothing from ``state.rng``: every tie breaks on system id.
Measurements behind the constants: docs/design/convoy.md.
"""

from __future__ import annotations

import heapq
import math

from starconquest import combat, config
from starconquest.model import Order

# --- tunables -------------------------------------------------------------- #
TUNED_SWING = 1.1 / 0.9     # floor under the live edge's jitter half
HORIZON_PAD = 3             # turns planned past the longest lane on the board
HORIZON_MAX = 40            # an iteration bound for a pathological hand map
TAIL_TURNS = 24.0           # a held system is worth this many turns of its income
REINFORCE_WEIGHT = 0.3      # share of a rival's adjacent ships assumed to relieve
                            # a system we strike, if they can land in time
GUARD = 0.55                # a frontier system keeps this share of the largest
                            # adjacent rival garrison, at the defending edge
STRIKE_PAD = 1.25           # a strike takes this multiple of the ships it needs
MAX_OBJECTIVES = 64         # an iteration bound on the greedy


def decide(state, pid):
    if not any(s.owner_id == pid for s in state.systems.values()):
        return []
    return _Plan(state, pid).orders()


class _Plan:
    def __init__(self, state, pid):
        self.state, self.pid = state, pid
        sysmap = state.systems
        self.ids = sorted(sysmap)
        attack, defend = combat.edge_attacking(TUNED_SWING), combat.edge_defending(TUNED_SWING)
        self.swing, self.advantage = math.sqrt(attack * defend), math.sqrt(attack / defend)
        self.defend_edge = defend
        self.rivals = {p.id for p in state.players.values()
                       if not p.is_neutral and p.id != pid and p.alive}
        self.travel = {a: {b: state.travel_turns(a, b) for b in sorted(sysmap[a].neighbors)}
                       for a in self.ids}
        longest = max((d for a in self.ids for d in self.travel[a].values()), default=1)
        self.horizon = max(2, min(HORIZON_MAX, longest + HORIZON_PAD))
        self.income = {sid: (1.0 / sysmap[sid].production if sysmap[sid].production > 0 else 0.0)
                       for sid in self.ids}
        self.owned = [sid for sid in self.ids if sysmap[sid].owner_id == pid]
        self.owned_set = set(self.owned)

        self.arrivals = {sid: {} for sid in self.ids}
        for f in state.fleets:
            t = max(1, min(self.horizon, f.turns_remaining))
            slot = self.arrivals[f.dest_id].setdefault(t, {})
            slot[f.owner_id] = slot.get(f.owner_id, 0) + f.ships
        self.relief = self._relief()
        self.front = self._front_distance()

    # --- supply ------------------------------------------------------------ #
    def _supplies(self, doomed):
        """[ready, system, ships, expires] for every ship we will have. A system
        lost at turn L builds nothing from L on, and its ships must leave before L."""
        sysmap, H, pid = self.state.systems, self.horizon, self.pid
        out = []
        for sid in self.owned:
            lost_at = doomed.get(sid, math.inf)
            node = sysmap[sid]
            out.append([0, sid, node.ships, lost_at])
            if node.production > 0:
                for r in range(1, min(H + 1, lost_at)):
                    if (node.prod_progress + r) // node.production \
                            > (node.prod_progress + r - 1) // node.production:
                        out.append([r, sid, 1, lost_at])
            for r, slot in sorted(self.arrivals[sid].items()):
                if slot.get(pid) and r < lost_at:
                    out.append([r, sid, slot[pid], lost_at])
        return out

    # --- routes -------------------------------------------------------------- #
    def _routes(self, start, through):
        """Turns from ``start`` to every system, moving through ``through`` only
        (the last hop may leave it), and the first hop on each route."""
        dist, hop = {start: 0}, {start: None}
        heap = [(0, start)]
        while heap:
            d, sid = heapq.heappop(heap)
            if d > dist[sid] or (sid != start and sid not in through):
                continue
            for nbr, turns in self.travel[sid].items():
                if d + turns < dist.get(nbr, math.inf):
                    dist[nbr] = d + turns
                    hop[nbr] = hop[sid] if hop[sid] is not None else nbr
                    heapq.heappush(heap, (d + turns, nbr))
        return dist, hop

    # --- the plan ----------------------------------------------------------- #
    def orders(self):
        sysmap, pid, H = self.state.systems, self.pid, self.horizon
        self.routes = {sid: self._routes(sid, self.owned_set) for sid in self.owned}
        threats = sorted(((sid, *hit) for sid in self.owned
                          if (hit := self._threat(sid)) is not None),
                         key=lambda h: (h[1], -self._stake(h[0]), h[0]))

        # Defences decide which systems are doomed, and a doomed system's supply
        # shrinks, so run them until no new system is given up.
        doomed: dict[int, int] = {}
        while True:
            self.supplies, self.alloc = self._supplies(doomed), []
            fresh = False
            for sid, t, need in threats:
                if sid not in doomed and not self._take(sid, t, need, home=sid):
                    doomed[sid] = t
                    fresh = True
            if not fresh:
                break

        # Guards are reservations, not allocations: a strike may not spend a
        # frontier garrison below its guard against any rival neighbour other
        # than the one being struck (see "The guard against the target" in
        # docs/design/convoy.md).
        self.guards = {sid: self._guards(sid) for sid in self.owned if sid not in doomed}

        # Strikes, best value per ship first, each at its earliest feasible turn.
        captured: dict[int, int] = {}
        self.needs: dict[tuple[int, int], tuple | None] = {}
        targets = [sid for sid in self.ids if sysmap[sid].owner_id != pid]
        for _ in range(MAX_OBJECTIVES):
            best = None
            for dst in targets:
                if dst in captured:
                    continue
                pick = self._earliest(dst)
                if pick is None:
                    continue
                t, x, value, _ = pick
                score = (value / x, -t, -dst)
                if best is None or score > best[0]:
                    best = (score, dst, pick)
            if best is None:
                break
            _, dst, (t, x, value, left) = best
            self._take(dst, t, x)
            captured[dst] = t
            # The capture's survivors and its production join the supply.
            self.supplies.append([t, dst, left, math.inf])
            node = sysmap[dst]
            if node.production > 0:
                self.supplies.extend([r, dst, 1, math.inf]
                                     for r in range(t + node.production, H + 1, node.production))
            self.routes[dst] = self._routes(dst, self.owned_set | set(captured))

        # This turn's launches: only ships that must leave now to arrive on time.
        sends: dict[tuple[int, int], int] = {}
        spare = {sid: sysmap[sid].ships for sid in self.owned}
        for sup, x, dst, t in self.alloc:
            ready, src = sup[0], sup[1]
            if ready != 0 or src not in self.owned_set:
                continue
            spare[src] -= x                    # spoken for, launched now or later
            if src == dst:
                continue
            dist, hop = self.routes[src]
            if t - dist[dst] == 0:
                key = (src, hop[dst])
                sends[key] = sends.get(key, 0) + x

        # Ships nobody wants: off a doomed system, else one hop towards the front.
        for sid in self.owned:
            ships = spare[sid]
            if ships <= 0:
                continue
            if sid in doomed:
                to = self._evacuate(sid, doomed)
            elif self.front.get(sid, 0) > 0:
                to = self._forward(sid)
            else:
                continue
            if to is not None:
                sends[(sid, to)] = sends.get((sid, to), 0) + ships

        return [Order(pid, src, dst, x) for (src, dst), x in sorted(sends.items()) if x > 0]

    def _free(self, sup, dst):
        """Ships of ``sup`` a strike on ``dst`` may spend: a garrison keeps its
        guard against every rival neighbour but ``dst``."""
        if sup[0] != 0 or not getattr(self, "guards", None):
            return sup[2]
        keep = max((need for nbr, need in self.guards.get(sup[1], ()) if nbr != dst), default=0)
        return max(0, sup[2] - keep)

    def _usable(self, sup, dst, t):
        d = self.routes[sup[1]][0].get(dst, math.inf)
        return self._free(sup, dst) > 0 and sup[0] + d <= t and t - d < sup[3]

    def _take(self, dst, t, need, home=None):
        """Allocate ``need`` ships able to land on ``dst`` by ``t``, or nothing.
        Hulls not yet built go before ships that exist, and the interior before
        the front; a defence or guard uses its own system's ships first."""
        pool = [s for s in self.supplies if self._usable(s, dst, t)]
        if sum(self._free(s, dst) for s in pool) < need:
            return False
        pool.sort(key=lambda s: (s[1] != home, -s[0], -self.front.get(s[1], 0), s[1]))
        left = need
        for s in pool:
            if left <= 0:
                break
            x = min(self._free(s, dst), left)
            s[2] -= x
            left -= x
            self.alloc.append((s, x, dst, t))
        return True

    def _earliest(self, dst):
        """(turn, ships, value, survivors) of the earliest turn the free supply can
        take ``dst`` at a profit, or None."""
        landing: dict[int, int] = {}
        for s in self.supplies:
            free = self._free(s, dst)
            if free <= 0:
                continue
            d = self.routes[s[1]][0].get(dst, math.inf)
            t = max(s[0] + d, 1)
            if t <= self.horizon and t - d < s[3]:
                landing[t] = landing.get(t, 0) + free
        if not landing:
            return None
        free = 0
        for t in range(min(landing), self.horizon + 1):
            free += landing.get(t, 0)
            entry = self._need(dst, t)
            if entry is not None and entry[0] <= free and entry[1] > 0:
                return (t, *entry)
        return None

    # --- objectives --------------------------------------------------------- #
    def _need(self, dst, t):
        """(ships, value, survivors) of the leanest strike landing on ``dst`` at
        ``t`` that holds it at the worst roll, padded by STRIKE_PAD; None if
        nothing takes it, or it is ours then anyway."""
        key = (dst, t)
        if key in self.needs:
            return self.needs[key]
        pid = self.pid
        node = self.state.systems[dst]
        base = self._project(dst, node.ships, self.arrivals[dst])
        out = None
        if base[0][t] != pid:
            lo = self._lean(dst, t)
            if lo is not None:
                x = max(lo, math.ceil(lo * STRIKE_PAD))
                line = self._project(dst, node.ships, _with(self.arrivals[dst], t, pid, x))
                if line[0][t] == pid:
                    left = line[1][t]
                    tail = self.income[dst] * max(0.0, TAIL_TURNS - t)
                    value = (left - x) + tail
                    if base[0][t] in self.rivals:
                        value += base[1][t] + tail
                    out = (x, value, left)
        self.needs[key] = out
        return out

    def _lean(self, dst, t, cap=None):
        """Fewest ships landing on ``dst`` at ``t`` that take it, or None."""
        pid = self.pid
        node = self.state.systems[dst]
        cap = cap or max(8, 4 * (sum(n for slot in self.arrivals[dst].values() for n in slot.values())
                                  + node.ships + self.horizon))

        def holds(x):
            line = self._project(dst, node.ships, _with(self.arrivals[dst], t, pid, x))
            return line[0][t] == pid

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

    def _threat(self, sid):
        """(turn, ships) for the first hostile landing on ``sid``: the garrison it
        must have then to hold at the worst roll. None when nothing is inbound."""
        hostile = {t: {o: n for o, n in slot.items() if o != self.pid}
                   for t, slot in self.arrivals[sid].items()}
        hostile = {t: slot for t, slot in hostile.items() if slot}
        if not hostile:
            return None
        t = min(hostile)
        worst = max(sum(slot.values()) for slot in hostile.values())
        return t, math.floor(worst * self.defend_edge) + 1

    def _guards(self, sid):
        """[(rival neighbour, ships)]: what a frontier system keeps against each."""
        sysmap = self.state.systems
        return [(nbr, math.ceil(GUARD * sysmap[nbr].ships * self.defend_edge))
                for nbr in self.travel[sid] if sysmap[nbr].owner_id in self.rivals]

    def _stake(self, sid):
        return self.state.systems[sid].ships + TAIL_TURNS * self.income[sid]

    # --- where spare ships go ------------------------------------------------ #
    def _front_distance(self):
        """Turns from each owned system back to the nearest one touching anything
        not ours (0 on the frontier)."""
        sysmap, pid = self.state.systems, self.pid
        dist = {sid: 0 for sid in self.owned
                if any(sysmap[n].owner_id != pid for n in self.travel[sid])}
        heap = [(0, sid) for sid in sorted(dist)]
        while heap:
            d, sid = heapq.heappop(heap)
            if d > dist[sid]:
                continue
            for nbr, t in self.travel[sid].items():
                if nbr in self.owned_set and d + t < dist.get(nbr, math.inf):
                    dist[nbr] = d + t
                    heapq.heappush(heap, (d + t, nbr))
        return dist

    def _forward(self, sid):
        here = self.front.get(sid, 0)
        best = None
        for nbr, d in self.travel[sid].items():
            if nbr in self.owned_set:
                there = self.front.get(nbr, math.inf) + d
                if there <= here and (best is None or (self.front.get(nbr, math.inf), nbr) < best[0]):
                    best = ((self.front.get(nbr, math.inf), nbr), nbr)
        return best[1] if best else None

    def _evacuate(self, sid, doomed):
        """A doomed garrison's way out: the safest owned neighbour, else none."""
        sysmap = self.state.systems
        options = [n for n in self.travel[sid] if n in self.owned_set and n not in doomed]
        if not options:
            return None
        return min(options, key=lambda n: (-sysmap[n].ships, n))

    # --- the projection ------------------------------------------------------ #
    def _relief(self):
        sysmap = self.state.systems
        out = {}
        for sid in self.ids:
            owner = sysmap[sid].owner_id
            by_t = {}
            if owner in self.rivals:
                for nbr, d in self.travel[sid].items():
                    if sysmap[nbr].owner_id == owner:
                        for t in range(d + 1, self.horizon + 1):
                            by_t[t] = by_t.get(t, 0) + sysmap[nbr].ships
            out[sid] = by_t
        return out

    def _project(self, sid, garrison, arrivals):
        """(owners, ships) for ``sid`` at turns 0..horizon: production first, then
        whatever lands that turn, in the engine's fold."""
        node = self.state.systems[sid]
        owner, ships, progress = node.owner_id, garrison, node.prod_progress
        rate, H = node.production, self.horizon
        owners, counts = [owner], [ships]
        for t in range(1, H + 1):
            if rate > 0 and (owner != 0 or config.NEUTRAL_PRODUCES):
                progress += 1
                if progress >= rate:
                    progress -= rate
                    ships += 1
            landing = arrivals.get(t)
            if landing:
                was = owner
                owner, ships = self._land(sid, t, owner, ships, landing)
                if owner != was:
                    progress = 0
            owners.append(owner)
            counts.append(ships)
        return owners, counts

    def _land(self, sid, t, owner, ships, landing):
        forces = {owner: ships}
        for who, n in landing.items():
            forces[who] = forces.get(who, 0) + n
        if owner in self.rivals and self.pid in landing:
            forces[owner] += int(REINFORCE_WEIGHT * self.relief[sid].get(t, 0))
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
        """Who wins is priced against us (our low roll, their high, the holder's
        advantage, and we must outnumber a garrison we attack); what we keep when
        we win is the nominal roll's."""
        (ao, an), (bo, bn) = a, b
        pid, swing, adv = self.pid, self.swing, self.advantage
        a_nom, b_nom = float(an), float(bn)
        if bo == defender:
            b_nom *= adv
        elif ao == defender:
            a_nom *= adv
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


def _with(arrivals, t, owner, ships):
    out = dict(arrivals)
    slot = dict(out.get(t, {}))
    slot[owner] = slot.get(owner, 0) + ships
    out[t] = slot
    return out

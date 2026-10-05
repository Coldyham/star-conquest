"""actuary — prices every launch against a projected ledger of the whole board.

Every other bot in the roster from claudebot up is a sequence of phases over the
systems it owns: budget each one, defend, strike, flow the rest to the front.
actuary has no phases and no per-system target loop. It keeps one number, the
board's worth in ships, and asks of each possible launch how much that number
moves.

  * **A forecast, not a snapshot.** Each system gets a timeline: owner and garrison
    for every turn up to a horizon, from the fleets already flying, production
    and the engine's own fold order at a pile-up. Fights in it are priced
    through `combat.edge_attacking`/`edge_defending`: a launch only counts as a
    capture if it wins the worst roll, and then keeps what the nominal roll
    would leave it. Lanes never interact, so
    a system's timeline depends only on what lands there: a candidate launch
    re-projects its source and destination and nothing else.
  * **One ledger.** At the horizon every ship we hold is worth one ship (a little
    more the nearer it stands to a front), every system we hold is worth
    `TAIL_TURNS` turns of its income, and a rival's are worth the same against
    us. Income before the horizon is already in the garrisons it built. A rival's
    garrison next door is a risk to each system we hold: the shortfall against
    everything it could land, priced as a share of what that system is worth.
  * **Greedy on the margin.** Candidates are single launches (all the spare
    ships, half, or exactly enough to change who holds the destination) and
    same-arrival coalitions on a target no single source can take. The one that
    raises the ledger most is committed and the projection updated, until no
    candidate pays. Defence, evacuation, strikes and logistics are not written
    anywhere; they are what the margin chooses.

The seat's ``ai_params.aux`` knob (``AUX_LABEL``: *Opening*) picks how the seat
plays until it first borders a rival. *Planned*, the default, treats the
land-grab as a one-player puzzle: it takes the neutrals nearer us than any rival,
and scores a dozen expansion plans by the ships and income they would hold when a
rival could first strike. It plays the first turn of the best plan, and the
ledger takes over at contact. *Greedy* is the ledger from the first turn.

Contract: ``decide(state, pid) -> list[Order]``. Reads the state, never mutates
it, and draws nothing from ``state.rng``: every tie breaks on system id.
Measurements behind the constants: docs/design/actuary.md.
"""

from __future__ import annotations

import heapq
import itertools
import math

from starconquest import combat, config
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
REINFORCE_WEIGHT = 0.3      # share of a rival's adjacent ships assumed to relieve
                            # a system we strike, if they can land in time
MIN_GAIN = 0.05             # a candidate must move the ledger by at least this
MAX_COMMITS = 64            # an iteration bound on the greedy

# What one decide costs (`decide_ms`): 75th-percentile CPU ms on native CPython,
# fitted to a grid of 18-120 systems, 3-18 ly/turn and 2-5 seats (see "Cost, and
# the caches that make it affordable" in docs/design/actuary.md). Seats and ship
# speed barely move it, so only the system count is in the fit.
COST_REF_NODES = 40
COST_DECIDE_MS = 5.0
COST_NODES_EXP = 0.55

# --- the opening ------------------------------------------------------------ #
GREEDY, PLANNED = 0, 1          # the Opening knob's stops; anything unreadable is PLANNED
OPENING_CLOCK_SCALE = 2.0       # the contact clock, as a multiple of the earliest strike
OPENING_INCOME_CLOCKS = 2.0     # income at contact is worth this many clocks of it, in ships
OPENING_CLOCK_MAX = 60          # an iteration bound on the plan
OPENING_ORDERS = ("value", "near", "cheap")
OPENING_SENDS = ("lean", "mass")
OPENING_SKIPS = (True, False)

# What the AI tab's generic aux slider is called when this bot holds the seat (read
# by `ai.aux_spec`/`ai.aux_names`; see models/README.md). The default 1.0 is Planned.
AUX_LABEL = "Opening"
AUX_RANGE = (GREEDY, PLANNED, 1)
AUX_INT = True
AUX_NAMES = ("Greedy", "Planned")


def decide(state, pid):
    if not any(s.owner_id == pid for s in state.systems.values()):
        return []
    if _opening_of(state.players[pid]) == PLANNED:
        orders = opening(state, pid)
        if orders is not None:
            return orders
    return _Ledger(state, pid).plan()


def _opening_of(player) -> int:
    """This seat's Opening stop. Anything at or past Planned is Planned, and
    anything unreadable is the default, so a hand-edited token cannot take the
    bot down."""
    try:
        return PLANNED if float(player.ai_params.aux) >= PLANNED else GREEDY
    except Exception:                     # noqa: BLE001
        return PLANNED


def decide_ms(settings, seat) -> float:
    """Typical CPU ms of one decide on this setup (`ai.decide_ms`). knower runs a
    rival bot's decide on every turn it looks ahead, so this is what lets its
    menu warning count us."""
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
        self.front_value = self._front_values()
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
            if any(o in rivals for o in self.lines[sid][0]) or any(o in rivals for o in lines[sid][0]):
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
        """Expected loss at ``sid`` to the worst a single rival could land there:
        the shortfall, as a share of what the system is worth, times RISK_WEIGHT.
        Priced at the nominal roll with the advantage ours: a threat is ships that
        *could* come, and demanding the worst roll against all of them at once
        hoards every garrison when the jitter is wide.
        ``lines`` overrides some timelines (a launch being priced)."""
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
            for t in range(1, H + 1):
                if owners[t] != pid or reach[t] <= 0:
                    continue
                need = reach[t] / self.advantage
                if need > ships[t]:
                    worst = max(worst, (need - ships[t]) / need)
        if worst <= 0.0:
            return 0.0
        stake = ships[H] * self.front_value[sid] + 2 * TAIL_TURNS * self.income[sid]
        return RISK_WEIGHT * worst * stake

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

    def _relief(self):
        """relief[sid][t]: ships the holder of ``sid`` has next door that could land
        there by turn t, launching no sooner than next turn."""
        sysmap = self.state.systems
        out = {}
        for sid in self.ids:
            owner = sysmap[sid].owner_id
            by_t = {}
            if owner not in (0, self.pid):
                for nbr, d in self.travel[sid].items():
                    if sysmap[nbr].owner_id == owner:
                        for t in range(d + 1, self.horizon + 1):
                            by_t[t] = by_t.get(t, 0) + sysmap[nbr].ships
            out[sid] = by_t
        return out


def _with(arrivals, t, owner, ships):
    """A copy of ``arrivals`` with ``ships`` more of ``owner``'s landing at ``t``."""
    out = dict(arrivals)
    slot = dict(out.get(t, {}))
    slot[owner] = slot.get(owner, 0) + ships
    out[t] = slot
    return out


# --------------------------------------------------------------------------- #
# The planned opening (Opening: Planned)
# --------------------------------------------------------------------------- #
def opening(state, pid):
    """This turn's orders under the planned opening while the seat borders no
    rival, else ``None`` (and the ledger plays). Measurements behind it:
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
        """Bordering a live rival, a rival fleet heading for us, or nothing left
        in the region."""
        sysmap = self.state.systems
        for sid in self.owned:
            if any(sysmap[n].owner_id in self.rivals for n in self.travel[sid]):
                return True
        if any(f.owner_id in self.rivals and f.dest_id in self.owned for f in self.state.fleets):
            return True
        return not self.region

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

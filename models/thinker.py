"""thinker — a 'flow to the front' conqueror that leapfrogs forward.

Four behaviours, each tuned to rules this game actually rewards:

  * **Flow to the richest front.** ``production`` is *turns-per-ship* (lower is
    richer), so a high-output system has a *low* ``production`` value. thinker
    scores every capturable target by richness and strikes the richest first,
    and its rear logistics stream surplus toward the frontier facing the richest
    prizes — the whole army drifts toward where production is highest.
  * **Abandon the lost.** A system that can't be held even by pulling every
    reinforcement that could arrive in time does not die in place. It evacuates:
    forward into a system it can still take, else back to its most defensible
    friend. Only a genuinely *overwhelmed* system with nowhere to go throws a
    denial strike — otherwise it stays put, because defence-favoured combat
    destroys more enemy ships from behind a garrison than a hopeless sortie does.
  * **Focus fire, even across distances.** Fleets that reach a node on the *same*
    turn resolve together, and the square law pays for mass. Since a fleet
    launched now arrives in ``travel_turns``, thinker converges waves from
    *different* distances by launching the farther one earlier: it reads
    ``turns_remaining`` on fleets already in flight, picks the soonest arrival at
    which its committed force overwhelms the target, launches the far wave now,
    and lets later turns add the near waves so everything lands together.
  * **Leapfrog.** The frontier attacks outward while the second line moves up to
    fill the gap on the *same* turn, so the spearhead stays two systems deep and
    both waves advance in lockstep. Generalised, every rear layer steps one hop
    toward the front each turn — a rolling column that never leaves ships idle.

Contract: ``decide(state, pid) -> list[Order]``. Reads state, never mutates it,
and routes every tie-break through ``state.rng`` so games stay reproducible.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from starconquest.model import Order

# --- tunables -------------------------------------------------------------- #
# Worst-case combat swing is 0.9*attacker vs 1.1*defender, so 1.1/0.9 ~= 1.222x
# is the break-even edge. Massing past it is cheaper by the square law (a 1.5x
# attack loses ~25% of its force), but a coordinate-descent sweep + grid search
# vs claudebot on held-out maps found leaner, sooner strikes beat over-massing
# against a competent foe. Most strikes are short-lane, where the margin is
# ENEMY_NEAR, so a cheap near-strike (just above the 1.222x jitter-safe floor)
# rising to a padded ENEMY_FAR on the rare long lane scored best.
_EDGE = 1.1 / 0.9
DEFEND_MARGIN = _EDGE + 0.05    # to hold a system through a strike this turn
NEUTRAL_MARGIN = 1.3            # neutrals are static — a flat cushion suffices
ENEMY_NEAR = 1.3                # enemy margin for a 1-turn strike (little time to react)
ENEMY_FAR = 1.9                 # ...rising toward this as the strike lands later
OVERWHELM = 2.0                 # a doomed system only sorties if this out-numbered

RESERVE_FLOOR = 1               # never strip an unthreatened system below this
FRONTIER_GUARD = 0.3            # a frontier system keeps this fraction of its
                                # scariest adjacent enemy garrison home as a guard


def decide(state, pid):
    sysmap = state.systems
    owned = [sid for sid, s in sysmap.items() if s.owner_id == pid]
    if not owned:
        return []

    max_prod = max(s.production for s in sysmap.values())
    frontier = {sid for sid in owned
                if any(sysmap[n].owner_id != pid for n in sysmap[sid].neighbors)}

    orders: list[Order] = []

    # --- Phase 0: base budgets — spendable ships after each system's guard --- #
    budget: dict[int, int] = {}
    for sid in owned:
        s = sysmap[sid]
        if sid in frontier:
            guard = math.ceil(FRONTIER_GUARD * _max_adjacent_enemy(state, pid, s))
            budget[sid] = max(0, s.ships - max(RESERVE_FLOOR, guard))
        else:
            budget[sid] = max(0, s.ships - RESERVE_FLOOR)

    # --- Phase 1: arrival-aware defence -------------------------------------- #
    # A system hit along a long lane can amass defence over several turns, so we
    # schedule reinforcements by *when* the blow lands rather than only pulling
    # one-hop help. Threatened systems that even that can't save are marked doomed.
    threatened = [sid for sid in owned if _incoming(state, sid, pid, hostile=True) > 0]
    threatened_set = set(threatened)
    threatened.sort(key=lambda sid: (_first_strike(state, pid, sid),
                                     -_incoming(state, sid, pid, hostile=True)))
    doomed: list[int] = []
    for sid in threatened:
        s = sysmap[sid]
        # The garrison we must have present, and the turn that demand binds.
        worst, t_bind = 0, 1
        for t, ecum in _enemy_arrivals(state, pid, sid):
            deficit = (math.ceil(ecum * DEFEND_MARGIN)
                       - _production_by(s, t) - _inbound(state, sid, pid, t))
            if deficit > worst:
                worst, t_bind = deficit, t

        if worst <= 0:
            continue  # production + ships already inbound cover it — keep base guard
        if worst <= s.ships:
            budget[sid] = min(budget.get(sid, 0), max(0, s.ships - worst))
            continue

        # Need outside help that can *arrive by* the binding strike. Pull from
        # neighbours within that range, never from a neighbour also under threat.
        need = worst - s.ships
        helpers = sorted(
            (n for n in s.neighbors
             if sysmap[n].owner_id == pid and n not in threatened_set
             and budget.get(n, 0) > 0 and (state.travel_turns(sid, n) or 99) <= t_bind),
            key=lambda n: -budget[n],
        )
        if sum(budget[n] for n in helpers) < need:
            doomed.append(sid)        # can't be saved in time — abandon it below
            continue
        budget[sid] = 0               # hold the whole garrison and pull the rest
        for h in helpers:
            if need <= 0:
                break
            send = min(budget[h], need)
            orders.append(Order(pid, h, sid, send))
            budget[h] -= send
            need -= send

    # --- Phase 2: abandon the doomed ----------------------------------------- #
    for sid in doomed:
        order = _evacuate(state, pid, sysmap[sid], max_prod)
        if order is not None:
            orders.append(order)
        budget[sid] = 0  # whether it retreated or holds to inflict casualties, don't drain it

    # --- Phase 3: focus fire with staggered pincers -------------------------- #
    targets = [sysmap[n] for n in
               {n for sid in frontier for n in sysmap[sid].neighbors
                if sysmap[n].owner_id != pid}]
    targets.sort(key=lambda t: (-_richness(t, max_prod), t.ships, state.rng.random()))

    for target in targets:
        # Our budgeted systems adjacent to the target, and how far off each is.
        nbrs = [(state.travel_turns(sid, target.id), sid) for sid in owned
                if target.id in sysmap[sid].neighbors and budget.get(sid, 0) > 0]
        if not nbrs:
            continue

        # Soonest arrival horizon H at which committed force (plus fleets already
        # inbound by then) overwhelms the target. Nearer distances are tried
        # first, so we strike as early as we can and only stagger when massing
        # demands the farther systems too.
        chosen_h, shortfall = None, 0
        for h in sorted({d for d, _ in nbrs}):
            req = _required(state, pid, target, h)
            inbound = _inbound(state, target.id, pid, h)
            committable = sum(budget[sid] for d, sid in nbrs if d <= h)
            if inbound + committable >= req:
                chosen_h, shortfall = h, req - inbound
                break
        if chosen_h is None:
            continue  # can't crack it even at full stretch — leave the ships to mass

        # Launch only the far wave (dist == H) now, covering the part the nearer
        # waves won't; those nearer waves launch on later turns and converge,
        # because next turn this fleet shows up in the target's inbound tally.
        nearer = sum(budget[sid] for d, sid in nbrs if d < chosen_h)
        need = max(0, shortfall - nearer)
        for sid in sorted((sid for d, sid in nbrs if d == chosen_h), key=lambda s: -budget[s]):
            if need <= 0:
                break
            send = min(budget[sid], need)
            orders.append(Order(pid, sid, target.id, send))
            budget[sid] -= send
            need -= send

    # --- Phase 4: leapfrog / flow to the richest front ----------------------- #
    parent = _flow_to_front(state, set(owned), frontier, pid, max_prod)
    for sid in sorted(owned):
        if sid in frontier:
            continue  # the front's leftover stays home as the standing reserve
        b = budget.get(sid, 0)
        if b > 0 and sid in parent:
            orders.append(Order(pid, sid, parent[sid], b))
            budget[sid] = 0

    return orders


# --- helpers --------------------------------------------------------------- #
def _richness(system, max_prod: int) -> int:
    """Value of a system: higher output (lower ``production``) scores higher."""
    return max_prod - system.production + 1


def _incoming(state, sid, pid, hostile: bool) -> int:
    """Ships inbound to ``sid``: hostile (owner != pid) or friendly (owner == pid)."""
    return sum(f.ships for f in state.fleets
               if f.dest_id == sid and (f.owner_id != pid) == hostile)


def _first_strike(state, pid, sid) -> int:
    """Turns until the first enemy fleet reaches ``sid`` (large if none is coming)."""
    return min((f.turns_remaining for f in state.fleets
                if f.dest_id == sid and f.owner_id != pid), default=99)


def _enemy_arrivals(state, pid, sid) -> list[tuple[int, int]]:
    """Enemy ships reaching ``sid`` by each strike turn, as (turn, cumulative)."""
    by_turn: dict[int, int] = defaultdict(int)
    for f in state.fleets:
        if f.dest_id == sid and f.owner_id != pid:
            by_turn[max(1, f.turns_remaining)] += f.ships
    cum, out = 0, []
    for t in sorted(by_turn):
        cum += by_turn[t]
        out.append((t, cum))
    return out


def _inbound(state, dest, owner, within) -> int:
    """Ships owned by ``owner`` reaching ``dest`` within ``within`` turns."""
    return sum(f.ships for f in state.fleets
               if f.dest_id == dest and f.owner_id == owner and f.turns_remaining <= within)


def _production_by(s, turns: int) -> int:
    """Ships ``s`` will build over ``turns`` turns at its current progress."""
    if s.production <= 0 or turns <= 0:
        return 0
    return (s.prod_progress + turns) // s.production


def _max_adjacent_enemy(state, pid, sysobj) -> int:
    """Largest hostile (non-neutral) garrison next door — neutrals never attack."""
    best = 0
    for n in sysobj.neighbors:
        o = state.systems[n]
        if o.owner_id != pid and o.owner_id != 0:
            best = max(best, o.ships)
    return best


def _required(state, pid, target, dist: int) -> int:
    """Ships needed to be *sure* of taking ``target`` when arriving in ``dist`` turns."""
    if target.owner_id == 0:  # static neutral garrison — no production, no reinforcement
        return max(target.ships + 1, math.ceil(target.ships * NEUTRAL_MARGIN))
    # Enemy: fold in the reinforcements and production that land before we arrive,
    # and pad more the later we strike (more time for the enemy to react).
    reinforcements = sum(f.ships for f in state.fleets
                         if f.dest_id == target.id and f.owner_id == target.owner_id
                         and f.turns_remaining <= dist)
    defence = target.ships + reinforcements + _production_by(target, dist)
    margin = min(ENEMY_FAR, ENEMY_NEAR + 0.1 * (dist - 1))
    return max(target.ships + 1, math.ceil(defence * margin))


def _evacuate(state, pid, s, max_prod: int):
    """Route a doomed system's whole garrison to the most useful place — or hold."""
    sysmap = state.systems
    ships = s.ships
    if ships <= 0:
        return None

    # (a) Step forward into the richest system we can still take with what's here.
    caps = []
    for n in s.neighbors:
        o = sysmap[n]
        if o.owner_id != pid:
            dist = state.travel_turns(s.id, n) or 1
            if ships >= _required(state, pid, o, dist):
                caps.append((_richness(o, max_prod), -o.ships, n))
    if caps:
        caps.sort(reverse=True)
        return Order(pid, s.id, caps[0][2], ships)

    # (b) Retreat to the most defensible friend (biggest garrison, richest front).
    friends = [n for n in s.neighbors if sysmap[n].owner_id == pid]
    if friends:
        friends.sort(
            key=lambda n: (sysmap[n].ships, _front_pull(state, pid, n, max_prod),
                           state.rng.random()),
            reverse=True,
        )
        return Order(pid, s.id, friends[0], ships)

    # (c) Cornered. Only sortie if truly overwhelmed; otherwise hold — a garrison
    #     kills more attackers than a doomed strike on a weak neighbour would.
    enemy_in = _incoming(state, s.id, pid, hostile=True)
    friend_in = _incoming(state, s.id, pid, hostile=False)
    if enemy_in > OVERWHELM * (ships + friend_in):
        enemies = [n for n in s.neighbors if sysmap[n].owner_id != pid]
        if enemies:
            weakest = min(enemies, key=lambda n: (sysmap[n].ships, state.rng.random()))
            return Order(pid, s.id, weakest, ships)
    return None


def _front_pull(state, pid, sid, max_prod: int) -> int:
    """How rich a prize the front at ``sid`` faces — its richest non-owned neighbour."""
    best = 0
    for n in state.systems[sid].neighbors:
        o = state.systems[n]
        if o.owner_id != pid:
            best = max(best, _richness(o, max_prod))
    return best


def _flow_to_front(state, owned, frontier, pid, max_prod) -> dict[int, int]:
    """Multi-source BFS over owned territory: rear node -> next hop toward the front.

    Seeds are ordered by the richness of the prize each frontier faces, so a rear
    node adjacent to two fronts flows toward the *richer* one. Sorted throughout,
    so a rear system's next hop stays stable while the frontier does, rather than
    oscillating turn to turn.
    """
    parent: dict[int, int] = {}
    seen = set(frontier)
    seeds = sorted(frontier, key=lambda sid: (-_front_pull(state, pid, sid, max_prod), sid))
    queue = deque(seeds)
    while queue:
        cur = queue.popleft()
        for nbr in sorted(state.systems[cur].neighbors):
            if nbr in owned and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur
                queue.append(nbr)
    return parent

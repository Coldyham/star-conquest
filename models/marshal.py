"""marshal — thinker's planner, but it commits.

A non-oracle bot: it sees exactly what thinker sees, fleets already on a lane and
never this turn's launches. The four phases and the helpers below ``_richness``
are knower's blind planner (``_plan(state, pid, None)``), which measures 80%-20%
over thinker; the oracle, the rollout search and the ``Posture`` threading are all
gone. ``Posture`` existed because knower predicts *itself* and patching globals
would corrupt its own self-model — marshal predicts nobody, so plain module
constants are safe.

What it changes, in descending order of measured value:

  * **It commits its surplus.** Combat is Lanchester's square law, so the ships an
    attack consumes are ``A - sqrt(A^2 - B^2)``, which *decreases* in ``A``.
    Phase 3 still strikes at exactly thinker's price — raising the bar is what
    thinker's own sweep and knower's pile-up pricing both proved wrong — and
    Phase 3b then pours whatever is left into a strike already going in, instead
    of parking it at a frontier system.

  * **It won't be the wall between two rivals.** ``_wedge`` discounts a target by
    how many rivals *past the first* it borders: taking it replaces a border they
    were contesting with two borders they contest with you. Gated on the size of
    the field (``WEDGE_MIN_PLAYERS``), where the payoff is sharply non-monotonic.

  * **A stagger's nearer wave is reserved.** Phase 3 launches the far sources of a
    pincer and relies on the nearer ones firing next turn; reserving their budget
    stops a later, poorer target — or Phase 3b — spending it first.

Contract: ``decide(state, pid) -> list[Order]``. Reads state, never mutates it,
and draws nothing from ``state.rng`` — every tie-break is deterministic. There is
no module state at all; never add any, because knower calls this function dozens
of times per real turn on fictional boards (``_predict_seat``, ``_rollout_decide``)
and would poison it.

**Everything measured about this bot lives in `docs/bot-design.md` under
"``models/marshal.py`` and what the measurements deleted"**: where it stands
against the roster, the guard and margin sweeps, which term of ``_enemy_margin``
is even live at a given ship speed, the four ideas that were built, measured and
then deleted, and the known hole in its own guard. Don't re-add one of those, or
re-tune a constant below, without a measurement — and read the note there on
paired null cells before running one, because the older tables were measured
against a null that drifted between 43% and 52%.

Forked from ``models/knower.py``.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from starconquest import combat
from starconquest.model import Order

# --- margins ---------------------------------------------------------------- #
# The pads sit over `combat`'s live jitter-safe edge; the absolutes below win at
# the default jitter, where the edge is 1.222. Past that the floor takes over —
# and `TUNED_SWING` is the floor the other way, so a *gentler* jitter than the one
# these were fitted at cannot thin them.
# `ENEMY_NEAR`, `ENEMY_FAR` and `FRONTIER_GUARD` are marshal's own, no longer
# thinker's: see "The 2026-09 tuning sweep" in `docs/bot-design.md` for what each
# was measured at, and note that the guard's gain is a default-ship-speed result.
TUNED_SWING = 1.1 / 0.9         # the +/-10% swing these margins were fitted at:
                                # a floor under the live edge, never an answer
DEFEND_PAD = 0.05
NEUTRAL_PAD = 0.05
NEAR_PAD = 0.02

NEUTRAL_MARGIN = 1.3            # neutrals are static — a flat cushion suffices
ENEMY_NEAR = 1.15               # enemy margin for a 1-turn strike
ENEMY_FAR = 1.5                 # ...rising toward this as the strike lands later
OVERWHELM = 2.0                 # a doomed system only sorties if this out-numbered
RESERVE_FLOOR = 0               # never strip an unthreatened system below this
FRONTIER_GUARD = 0.55           # fraction of the scariest adjacent enemy held home
BEYOND_DECAY = 0.35             # weight on the richest system one hop past a target

RIVAL_WEDGE = 1.0               # penalty per rival past the first bordering a target
WEDGE_MIN_PLAYERS = 4           # ...applied only in a field this crowded
COMMIT_SURPLUS = True           # Phase 3b: pour leftovers into a strike going in
RESERVE_PINCER = True           # hold a stagger's nearer wave for its own target


# --------------------------------------------------------------------------- #
# Margins, read live from config
#
# `combat.edge_attacking`/`edge_defending` are the break-even multiples for the
# two sides of a fight, straight off the combat code, so a knob moving mid-match
# moves these with it. The pads sit on top; the tuned absolutes floor them.
# --------------------------------------------------------------------------- #
def _defend_margin() -> float:
    return combat.edge_defending(TUNED_SWING) + DEFEND_PAD


def _neutral_margin() -> float:
    return max(NEUTRAL_MARGIN, combat.edge_attacking(TUNED_SWING) + NEUTRAL_PAD)


def _enemy_margin(dist: int) -> float:
    return max(combat.edge_attacking(TUNED_SWING) + NEAR_PAD,
               min(ENEMY_FAR, ENEMY_NEAR + 0.1 * (dist - 1)))


# --------------------------------------------------------------------------- #
# Valuation and fleet arithmetic
# --------------------------------------------------------------------------- #
def _wedge(state, pid, system) -> int:
    """Rivals *past the first* we would border by taking ``system``.

    Taking a node that touches two rivals makes us the wall between them, and
    replaces a border they were contesting with two borders they contest with us.
    Declining it leaves them adjacent and busy with each other, which is worth
    more than the node while our income is behind their combined income.

    Counted on the neighbours, i.e. on the position *after* the capture, so the
    node's current owner only matters where they also hold something next to it.
    Zero in a duel by construction — a two-player board can never put a second
    rival past the first.

    Gated on the size of the field, because the payoff is not monotonic in it.
    Paired free-for-alls, both variants in the same game, rotated through every
    seat, 30 nodes: 43% at three players, 57% at four, 55% at five, 50% at six.
    With a single pair of rivals there is no fight to stand aside from — ceding
    the node just feeds whichever of them takes it, and the lost income beats the
    diplomacy. Past five, everyone borders everyone and the term stops
    discriminating. It is switched on only where it measured positive.
    """
    live = sum(1 for p in state.players.values() if not p.is_neutral and p.alive)
    if live < WEDGE_MIN_PLAYERS:
        return 0
    rivals = {state.systems[n].owner_id for n in system.neighbors}
    rivals.discard(pid)
    rivals.discard(0)
    return max(0, len(rivals) - 1)


def _richness(state, pid, system, max_prod: int) -> float:
    """Value of a system: its own output, plus a discounted peek at the richest
    non-owned neighbour past it, less the fronts it would open.

    A poor system that opens onto a rich one is worth more than its own
    production alone says; one wedged between two rivals is worth less.
    """
    base = max_prod - system.production + 1
    beyond = max((max_prod - state.systems[n].production + 1
                  for n in system.neighbors if state.systems[n].owner_id != pid),
                 default=0)
    return (base + BEYOND_DECAY * beyond
            - RIVAL_WEDGE * _wedge(state, pid, system))


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
               if f.dest_id == dest and f.owner_id == owner
               and f.turns_remaining <= within)


def _production_by(s, turns: int) -> int:
    """Ships ``s`` will build over ``turns`` turns at its current progress."""
    if s.production <= 0 or turns <= 0:
        return 0
    return (s.prod_progress + turns) // s.production


def _max_adjacent_enemy(state, pid, sysobj) -> int:
    """Largest garrison next door belonging to a real rival. Neutrals never attack."""
    best = 0
    for n in sysobj.neighbors:
        o = state.systems[n]
        if o.owner_id != pid and o.owner_id != 0:
            best = max(best, o.ships)
    return best


def _required(state, pid, target, dist: int) -> int:
    """Ships needed to be *sure* of taking ``target`` when arriving in ``dist`` turns."""
    ships = target.ships
    if target.owner_id == 0:  # static neutral garrison — no production, no reinforcement
        return max(ships + 1, math.ceil(ships * _neutral_margin()))
    reinforcements = _inbound(state, target.id, target.owner_id, dist)
    defence = ships + reinforcements + _production_by(target, dist)
    return max(ships + 1, math.ceil(defence * _enemy_margin(dist)))


# --------------------------------------------------------------------------- #
# Movement
# --------------------------------------------------------------------------- #
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
                caps.append((_richness(state, pid, o, max_prod), -o.ships, n))
    if caps:
        caps.sort(reverse=True)
        return Order(pid, s.id, caps[0][2], ships)

    # (b) Retreat to the most defensible friend (biggest garrison, richest front).
    friends = [n for n in s.neighbors if sysmap[n].owner_id == pid]
    if friends:
        friends.sort(
            key=lambda n: (sysmap[n].ships, _front_pull(state, pid, n, max_prod), -n),
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
            weakest = min(enemies, key=lambda n: (sysmap[n].ships, n))
            return Order(pid, s.id, weakest, ships)
    return None


def _front_pull(state, pid, sid, max_prod: int) -> float:
    """How rich a prize the front at ``sid`` faces — its richest non-owned neighbour."""
    best = 0.0
    for n in state.systems[sid].neighbors:
        o = state.systems[n]
        if o.owner_id != pid:
            best = max(best, _richness(state, pid, o, max_prod))
    return best


def _flow_to_front(state, owned, frontier, pid, max_prod) -> dict[int, int]:
    """Multi-source BFS over owned territory: rear node -> next hop toward the front.

    Seeds are ordered by the richness of the prize each frontier faces, so a rear
    node adjacent to two fronts flows toward the richer one. Sorted throughout, so
    a rear system's next hop stays stable while the frontier does.
    """
    parent: dict[int, int] = {}
    seen = set(frontier)
    seeds = sorted(frontier,
                   key=lambda sid: (-_front_pull(state, pid, sid, max_prod), sid))
    queue = deque(seeds)
    while queue:
        cur = queue.popleft()
        for nbr in sorted(state.systems[cur].neighbors):
            if nbr in owned and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur
                queue.append(nbr)
    return parent


# --------------------------------------------------------------------------- #
# The planner
# --------------------------------------------------------------------------- #
def decide(state, pid):
    sysmap = state.systems
    owned = [sid for sid, s in sysmap.items() if s.owner_id == pid]
    if not owned:
        return []

    max_prod = max(s.production for s in sysmap.values())
    frontier = {sid for sid in owned
                if any(sysmap[n].owner_id != pid for n in sysmap[sid].neighbors)}

    # Every phase accumulates here and the orders are emitted once at the end, so
    # a source/destination pair can be topped up without issuing a second Order.
    sends: dict[tuple[int, int], int] = defaultdict(int)

    # --- Phase 0: base budgets — spendable ships after each system's guard --- #
    budget: dict[int, int] = {}
    for sid in owned:
        s = sysmap[sid]
        if sid in frontier and FRONTIER_GUARD > 0:
            guard = math.ceil(FRONTIER_GUARD * _max_adjacent_enemy(state, pid, s))
            budget[sid] = max(0, s.ships - max(RESERVE_FLOOR, guard))
        else:
            budget[sid] = max(0, s.ships - RESERVE_FLOOR)

    # --- Phase 1: arrival-aware defence -------------------------------------- #
    # A system hit along a long lane can amass defence over several turns, so
    # reinforcements are scheduled by when the blow lands. Sized for the worst
    # horizon but required by the *earliest* one showing a deficit: ships that
    # arrive early are still there later, and with no standing guard nothing else
    # absorbs an early wave when the worst horizon is a later one.
    threatened = [sid for sid in owned
                  if _incoming(state, sid, pid, hostile=True) > 0]
    threatened_set = set(threatened)
    threatened.sort(key=lambda sid: (_first_strike(state, pid, sid),
                                     -_incoming(state, sid, pid, hostile=True)))
    doomed: list[int] = []
    for sid in threatened:
        s = sysmap[sid]
        margin = _defend_margin()
        worst, t_bind = 0, None
        for t, ecum in _enemy_arrivals(state, pid, sid):
            deficit = (math.ceil(ecum * margin)
                       - _production_by(s, t) - _inbound(state, sid, pid, t))
            if deficit > 0 and t_bind is None:
                t_bind = t
            if deficit > worst:
                worst = deficit
        if worst <= 0:
            continue  # production + ships already inbound cover it
        if worst <= s.ships:
            budget[sid] = min(budget.get(sid, 0), max(0, s.ships - worst))
            continue

        need = worst - s.ships
        deadline = t_bind if t_bind is not None else 1
        helpers = sorted(
            (n for n in s.neighbors
             if sysmap[n].owner_id == pid and n not in threatened_set
             and budget.get(n, 0) > 0
             and (state.travel_turns(sid, n) or 99) <= deadline),
            key=lambda n: (-budget[n], n),
        )
        if sum(budget[n] for n in helpers) < need:
            doomed.append(sid)        # can't be saved in time — abandon it below
            continue
        budget[sid] = 0               # hold the whole garrison and pull the rest
        for h in helpers:
            if need <= 0:
                break
            send = min(budget[h], need)
            sends[(h, sid)] += send
            budget[h] -= send
            need -= send

    # --- Phase 2: abandon the doomed ----------------------------------------- #
    for sid in doomed:
        order = _evacuate(state, pid, sysmap[sid], max_prod)
        if order is not None:
            sends[(order.source_id, order.dest_id)] += order.ships
        budget[sid] = 0  # retreat or hold to inflict casualties — don't drain it

    # --- Phase 3: focus fire with staggered pincers -------------------------- #
    targets = [sysmap[n] for n in
               {n for sid in frontier for n in sysmap[sid].neighbors
                if sysmap[n].owner_id != pid}]
    targets.sort(key=lambda t: (-_richness(state, pid, t, max_prod), t.ships, t.id))

    struck: dict[int, int] = {}
    pincer_held: set[int] = set()
    for target in targets:
        nbrs = [(state.travel_turns(sid, target.id), sid) for sid in owned
                if target.id in sysmap[sid].neighbors
                and budget.get(sid, 0) > 0 and sid not in pincer_held]
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
            if inbound + committable < req:
                continue
            chosen_h, shortfall = h, req - inbound
            break
        if chosen_h is None:
            continue  # can't crack it even at full stretch — leave the ships to mass

        # Launch only the far wave (dist == H) now, covering the part the nearer
        # waves won't; those launch on later turns and converge, because next turn
        # this fleet shows up in the target's inbound tally.
        nearer = sum(budget[sid] for d, sid in nbrs if d < chosen_h)
        need = max(0, shortfall - nearer)
        for sid in sorted((sid for d, sid in nbrs if d == chosen_h),
                          key=lambda s: (-budget[s], s)):
            if need <= 0:
                break
            send = min(budget[sid], need)
            sends[(sid, target.id)] += send
            budget[sid] -= send
            need -= send

        struck[target.id] = chosen_h
        if RESERVE_PINCER:
            # Those nearer sources are promised to next turn's converging wave.
            # Unreserved, a later and poorer target spends them and the stagger
            # never materialises.
            pincer_held.update(sid for d, sid in nbrs if d < chosen_h)

    # --- Phase 3b: commit the surplus ---------------------------------------- #
    # By the square law a bigger strike costs fewer ships and holds the capture
    # afterwards, so ships with no other job this turn ride along with the wave
    # rather than parking. This raises no threshold: the target was already priced
    # and is already being attacked.
    # Note this cannot cost us a second front: Phase 3 has already priced and
    # launched at *every* affordable target, so a system that has just broken
    # through takes all the weak systems in front of it either way. All that is
    # decided here is where the remainder rides along.
    if COMMIT_SURPLUS:
        for target in targets:
            chosen_h = struck.get(target.id)
            if chosen_h is None:
                continue
            for sid in sorted(
                    (sid for sid in owned
                     if target.id in sysmap[sid].neighbors
                     and budget.get(sid, 0) > 0 and sid not in pincer_held
                     and (state.travel_turns(sid, target.id) or 99) == chosen_h),
                    key=lambda s: (-budget[s], s)):
                sends[(sid, target.id)] += budget[sid]
                budget[sid] = 0

    # --- Phase 4: leapfrog / flow to the richest front ----------------------- #
    parent = _flow_to_front(state, set(owned), frontier, pid, max_prod)
    for sid in sorted(owned):
        if sid in frontier:
            continue  # the front's leftover stays home as the standing reserve
        b = budget.get(sid, 0)
        if b > 0 and sid in parent:
            sends[(sid, parent[sid])] += b
            budget[sid] = 0

    return [Order(pid, src, dst, ships)
            for (src, dst), ships in sorted(sends.items()) if ships > 0]

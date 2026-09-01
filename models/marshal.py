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
    attack consumes are ``A - sqrt(A^2 - B^2)``, which *decreases* in ``A``:
    sending 11 at an 8-garrison costs 3 ships and leaves 8 holding it, sending 25
    costs 1 and leaves 24. Phase 3 still strikes at exactly thinker's price —
    raising the bar is what thinker's own sweep and knower's pile-up pricing both
    proved wrong — but Phase 3b then pours whatever is left into a strike already
    going in, instead of parking it. Measured over 705 player-turns, thinker keeps
    64.3% of its army sitting at frontier systems and only 23.1% in transit.

  * **It won't be the wall between two rivals.** In a crowded game, a node that
    touches two other players is worth less than its production says: taking it
    replaces a border *they* were contesting with two borders they contest with
    you, and while your income is behind their combined income that trade loses.
    ``_wedge`` prices it, and the payoff is sharply non-monotonic in the size of
    the field — 48% at three players (where the term is gated off, so that cell
    is a null and calibrates the harness), 64% at four, 62% at five. With a single
    pair of rivals there is no fight to stand aside from and ceding the node just
    feeds whoever takes it; past five everyone borders everyone and it stops
    discriminating.

  * **A stagger's nearer wave is reserved.** Phase 3 picks an arrival horizon,
    launches the far sources, and *relies* on the nearer ones firing next turn to
    converge. Their budget was never reserved, so a later, poorer target could
    spend it and the pincer silently failed to materialise. It can't now — which
    also stops Phase 3b eating its own second wave.

``FRONTIER_GUARD`` stays at thinker's 0.3, deliberately. A/B'd *alone* the guard
looks worthless — 85-85 on a 24-node mirror and negative at 40 nodes and at 18
ly/turn — and an earlier draft of this bot set it to 0.0 on that evidence. That
was wrong, and wrong in an instructive way: the guard and Phase 3b interact.
Committing the surplus is exactly what makes a garrison worth keeping, because a
system that just emptied itself into an attack is the one that needs cover. With
3b on, sweeping the guard against knower's depth-0 planner gives

    guard     18n   24n   30n   40n   mean   timeouts   turns
    0.0       38%   48%   62%   66%    53%         --      --
    0.30      56%   59%   66%   70%    63%         50     144
    0.45      60%   60%   72%   69%    65%         59     162
    0.55      60%   65%   74%   66%    66%         80     182
    0.70      59%   60%   77%   71%    67%        114     195

Past 0.3 the win rate is flat while games stretch 35% longer and timeouts more
than double — the stalemate failure mode `models/README.md` warns about — so the
extra points are not worth taking. Holding thinker's exact value also keeps every
point of the margin attributable to a mechanism rather than to a re-tune.

Two inherited bugs are fixed on the way past:

  * ``_EDGE = 1.1 / 0.9`` hardcoded ``COMBAT_JITTER = 0.10`` and predates
    ``COMBAT_JITTER``'s companion knob entirely. Both are menu sliders
    (``settings._GLOBAL_KNOBS``), so the whole lineage silently drops below
    break-even the moment either moves. ``_edge_attacking`` / ``_edge_defending``
    read both live, and *in opposite directions*: ``DEFENDER_ADVANTAGE`` scales
    whoever holds the system, so it multiplies the price of taking one and
    divides the price of holding one. At the defaults (0.10, 1.0) the two
    collapse to the old constant and the tuned absolutes still floor them, so
    nothing measured here moves.
  * Phase 1 sized relief for the worst arrival horizon but scheduled it for that
    horizon's turn, and 16.1% of real deficits bind *later* than the first
    arrival. The standing guard used to absorb the early wave; nothing does now.
    Relief is sized for the worst horizon and required by the *earliest* one.

Measured against **knower at search depth 0** — the blind planner marshal forks,
which is the honest baseline for a non-oracle bot. Ladder, both seatings, 120
games a cell. The paired null (that planner against itself) reads 50%.

    nodes      18    24    30    40
    marshal   56%   59%   66%   70%

The wedge term, paired against a copy of marshal with it switched off — both in
the *same* game, rotated through every seat, so map and luck are shared:

    3 players, 30 nodes    52% (108-101)   gate off: identical bots, a null cell
    4 players, 30 nodes    61% (131-84)
    4 players, 40 nodes    66% (149-78)
    5 players, 40 nodes    60% (155-103)

Reading the two combat knobs live is worth nothing at their defaults and a great
deal off them — the rest of the roster still prices every fight at a fixed 1.222x.
Against thinker / claudebot, 60 games a cell, jitter 0.10:

    DEFENDER_ADVANTAGE 1.0     80% / 98%     (unchanged, by construction)
    DEFENDER_ADVANTAGE 1.25    91% / 98%
    DEFENDER_ADVANTAGE 1.5    100% / 100%    (the slider ceiling)

Against knower's oracle marshal has no answer, and depth is not the reason — the
oracle itself is the wall. 24 nodes, 80 games a cell:

    knower depth 0    52%      <- no oracle: marshal is ahead
    knower depth 1    41%
    knower depth 2    42%
    knower depth 4    29%

Switching the oracle *on* costs 11 points; deepening its search past 1 costs
nothing until depth 4. No heuristic buys back a rival that reads your orders
before you issue them — that needs prediction of its own, or deliberate
unpredictability. Full roster ladder, 900 games: knower 263, marshal 247,
thinker 175, claudebot 111, heuristic 52, rusherplus 15.

Four things deliberately *not* here — all three built, measured against the
configuration above, and removed rather than kept on the strength of the idea:

  * **Chokepoint weighting.** Normalised Brandes betweenness over the lane graph,
    folded into ``_richness`` so a corridor every path flows through outprices a
    cul-de-sac. Sound on paper: these maps are planar and sparse (average degree
    2.5-2.7) but 34-49% of nodes are cut vertices, and the measure discriminates
    well (top 1.00, median 0.21). It still loses. Swept at weight 0.10/0.20/0.40
    it scored 48/48/50% against leaving it out, was *negative* at 40 nodes, and
    consistently produced the longest games and the most timeouts — it buys
    corridors instead of winning. Production compounds; topology doesn't.
  * **Pocket-sealing.** Valuing a capture by how much of our frontier it removes.
    It barely discriminates: sampled over 105 candidate targets, 67% score
    identically, and only 6.7% actually seal. Measured 47% at either weight.
  * **Reinforceability-scaled guards.** Relax a frontier guard wherever a
    neighbour could genuinely relieve the system inside its warning window (a
    fleet down an L-turn lane is first visible with L-1 turns to spare, so relief
    R turns away arrives iff ``R <= L-1``). It frees a lot of ships — 3.5:1, and
    52.8% of assignments cost nothing at all — but in the moments the guard was
    actually load-bearing the relief was out of range 76.8% of the time. Measured
    46% against simply setting the guard to zero, which is the same idea taken to
    its limit and needs no machinery.

  * **Splitting a breakthrough's surplus.** After Phase 3 has priced three weak
    systems in front of a system holding an army, the leftover all rides with the
    richest strike — 4/6/18 rather than 8/8/12, so two of the three are taken
    with exactly their price and hold only 3 and 4 ships afterwards. Spreading it
    in proportion to richness looks obviously safer and is not: 63% / 64% / 63%
    at spread 0 / 0.5 / 1.0 against knower's depth-0 planner, 140 games a cell
    across four map sizes. The reason it doesn't matter is that the square law
    barely punishes overkill on a weak garrison, so total survivors are the same
    either way (24 against 25 on the traced board) — only their distribution
    moves. Worth knowing that *taking* all three was never at stake: Phase 3
    launches at every affordable target before 3b touches the remainder.

Don't re-add any of them without a measurement.

Contract: ``decide(state, pid) -> list[Order]``. Reads state, never mutates it,
and draws nothing from ``state.rng`` — every tie-break is deterministic. There is
no module state at all; never add any, because knower calls this function dozens
of times per real turn on fictional boards (``_predict_seat``, ``_rollout_decide``)
and would poison it.

Forked from ``models/knower.py``.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from starconquest import config
from starconquest.model import Order

# --- margins ---------------------------------------------------------------- #
# The pads sit over the live jitter-safe edge; the absolutes below are thinker's
# tuned figures and win at the default jitter, where the edge is 1.222. Past that
# the floor takes over.
DEFEND_PAD = 0.05
NEUTRAL_PAD = 0.05
NEAR_PAD = 0.02

NEUTRAL_MARGIN = 1.3            # neutrals are static — a flat cushion suffices
ENEMY_NEAR = 1.3                # enemy margin for a 1-turn strike
ENEMY_FAR = 1.9                 # ...rising toward this as the strike lands later
OVERWHELM = 2.0                 # a doomed system only sorties if this out-numbered
RESERVE_FLOOR = 0               # never strip an unthreatened system below this
FRONTIER_GUARD = 0.30           # fraction of the scariest adjacent enemy held home
BEYOND_DECAY = 0.35             # weight on the richest system one hop past a target

RIVAL_WEDGE = 1.0               # penalty per rival past the first bordering a target
WEDGE_MIN_PLAYERS = 4           # ...applied only in a field this crowded
COMMIT_SURPLUS = True           # Phase 3b: pour leftovers into a strike going in
RESERVE_PINCER = True           # hold a stagger's nearer wave for its own target


# --------------------------------------------------------------------------- #
# Margins, read live from config
# --------------------------------------------------------------------------- #
def _swing() -> float:
    """``(1+j)/(1-j)`` — the worst-roll ratio, our low against their high."""
    j = min(max(float(config.COMBAT_JITTER), 0.0), 0.95)
    return (1.0 + j) / (1.0 - j)


def _advantage() -> float:
    """``config.DEFENDER_ADVANTAGE``, clamped away from zero.

    ``combat._apply_advantage`` scales whichever side holds the system, *after*
    the jitter roll, so it lands on the swung strength rather than the nominal
    one — which is why it composes with `_swing` as a plain product.
    """
    return max(float(config.DEFENDER_ADVANTAGE), 0.01)


def _edge_attacking() -> float:
    """Break-even multiple to take a system: they hold it, so they get the bonus.

    We win the worst roll iff ``A(1-j) > B(1+j)*adv``, i.e. ``A > B * adv * swing``.
    """
    return _advantage() * _swing()


def _edge_defending() -> float:
    """Break-even multiple to hold one: *we* hold it, so the bonus is ours.

    We survive the worst roll iff ``D(1-j)*adv > E(1+j)``, i.e.
    ``D > E * swing / adv``. The advantage divides here and multiplies above —
    turning the slider up makes holding cheaper and taking dearer, and a bot that
    applied it in one direction only would be wrong in the other.
    """
    return _swing() / _advantage()


def _defend_margin() -> float:
    return _edge_defending() + DEFEND_PAD


def _neutral_margin() -> float:
    return max(NEUTRAL_MARGIN, _edge_attacking() + NEUTRAL_PAD)


def _enemy_margin(dist: int) -> float:
    return max(_edge_attacking() + NEAR_PAD,
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

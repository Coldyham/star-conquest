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

  * **A retreat is a move, not a shrug.** Three fixes in one place, worth 52.0%
    and 51.7% (z = +3.34, +3.21) against the bot without them. ``_evacuate`` ranked
    refuges by garrison size alone, so a doomed garrison could be posted down a
    six-turn lane to the biggest stack we own while a refuge one turn away went
    unused; it now goes to the nearest one first. And nothing in the doomed
    branch knew which *other* systems were being abandoned this turn — for each
    of two doomed neighbours the biggest friendly garrison is the other one, so
    the pair traded garrisons down the lane between them and both systems fell to
    fleets that were already on their way. ``_consolidate`` offers the doomed to
    each other first, since a garrison too small to save its own system is often
    exactly what the one next door is short of and is lost where it stands
    either way, and whatever is still being abandoned is passed to ``_evacuate`` as
    a place not to retreat into.

  * **It prices a target against whoever will be holding it.** ``_required`` reads
    the fleets a *third* player already has on the lane, not just the owner's own
    reinforcements, so a system its owner has evacuated ahead of an incoming stack
    is not mistaken for a free one. Structurally inert in a duel.

Contract: ``decide(state, pid) -> list[Order]``. Reads state, never mutates it,
and draws nothing from ``state.rng`` — every tie-break is deterministic. There is
no module state at all; never add any, because knower calls this function dozens
of times per real turn on fictional boards (``_predict_seat``, ``_rollout_decide``)
and would poison it.

**Everything measured about this bot lives in `docs/bot-design.md` under
"``models/marshal.py`` and what the measurements deleted"**: where it stands
against the roster, the guard and margin sweeps, why the attack margin no longer
carries a jitter premium (garrisons evacuate rather than fight, so it was paid on
a fight that mostly never happens), the ideas that were built, measured and then
deleted, the known hole in its own guard, why — under "Two doomed neighbours" —
the retreat rule leads on distance and the doomed are offered to each other
before anyone else, and — under "Racing a third player for the same system" —
why the third-party reprice stops at rival-held targets and is deliberately not
applied to neutral ones. Don't re-add one of those, or
re-tune a constant below, without a measurement — and read the note there on
paired null cells before running one, because the older tables were measured
against a null that drifted between 43% and 52%.

Forked from ``models/knower.py``.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque

from starconquest import combat, config
from starconquest.model import Order

# --- margins ---------------------------------------------------------------- #
# These now cover *defending and neutrals only*. The pads sit over `combat`'s live
# jitter-safe edge, the absolute wins at the default jitter where the edge is
# 1.222, and `TUNED_SWING` is the floor the other way, so a gentler jitter than
# the one they were fitted at cannot thin them.
# The **attack** margin has no pad, absolute or floor at all: it is the defender
# advantage and nothing else, because a garrison that can be beaten evacuates
# rather than fighting 86.7% of the time. See `_enemy_margin`.
# `FRONTIER_GUARD` is marshal's own, no longer thinker's: see "The 2026-09 tuning
# sweep" in `docs/bot-design.md` for what it was measured at, and note that its
# gain is a default-ship-speed result.
TUNED_SWING = 1.1 / 0.9         # the +/-10% swing these margins were fitted at:
                                # a floor under the live edge, never an answer
DEFEND_PAD = 0.05
NEUTRAL_PAD = 0.05

NEUTRAL_MARGIN = 1.3            # neutrals are static — a flat cushion suffices
OVERWHELM = 2.0                 # a doomed system only sorties if this out-numbered
RESERVE_FLOOR = 0               # never strip an unthreatened system below this
FRONTIER_GUARD = 0.55           # fraction of the scariest adjacent enemy held home
BEYOND_DECAY = 0.35             # weight on the richest system one hop past a target

RIVAL_WEDGE = 1.0               # penalty per rival past the first bordering a target
WEDGE_MIN_PLAYERS = 4           # ...applied only in a field this crowded
COMMIT_SURPLUS = True           # Phase 3b: pour leftovers into a strike going in
RESERVE_PINCER = True           # hold a stagger's nearer wave for its own target
CONSOLIDATE = True              # Phase 2: the doomed relieve each other
AVOID_ABANDONED = True          # ...and never retreat into one that is still doomed
RETREAT_NEAREST = True          # ...and retreat by travel time, not garrison size


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


def _enemy_margin() -> float:
    """The advantage multiplier alone: no jitter premium, no absolute, no ramp.

    A margin over the break-even edge is insurance against losing the fight — and
    against a garrison that can be beaten, there is usually no fight. Measured
    over 12k arrivals, **86.7% of out-matched garrisons are gone before the blow
    lands**: knower evacuates 97.9% of the time, thinker 95.0%, marshal itself
    94.9%, rusherplus 70.2%. Only claudebot stands (0.0%). So the *jitter* half of
    the edge is a premium on an event that mostly does not happen, and paying it
    on every strike buys nothing while costing about a quarter of every fleet.

    The *advantage* half is different and is kept: it is not insurance against the
    dice but against the ground, and it applies in full whenever a garrison does
    stand. Dropping it as well reads **8.6% (z = -12.35)** at `DEFENDER_ADVANTAGE
    1.5` — the single worst number ever measured on this bot — because a high
    advantage is exactly the setting at which a defender *can* hold and therefore
    does. Keeping it reads 58.0% there.

    Recovered from the two public edges rather than read off `config`, so this
    still cannot drift from the combat code: ``edge_attacking(1) = adv * swing``
    and ``edge_defending(1) = swing / adv``, so their ratio is ``adv^2``. Floored
    at parity, since requiring *less* than the garrison is never right, and
    ``_required`` floors the count itself at ``defence + 1`` because an exact tie
    breaks to the defender.

    This deletes `ENEMY_NEAR`, `ENEMY_FAR` and `NEAR_PAD`, all three of them
    measured figures — see "Garrisons run away, so the jitter premium buys almost
    nothing" in `docs/bot-design.md` for the ten cells behind that, and note that
    the ramp's own regime (3 ly/turn, where `ENEMY_FAR` was the only live term) is
    where removing it gains the *most*, at 63.1%.
    """
    return max(1.0, math.sqrt(combat.edge_attacking(1.0) / combat.edge_defending(1.0)))


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

    Deliberately carries **no term for who owns the target**, though taking a
    rival's system is a net +2 (one off them, one onto us) against a neutral's
    +1, and early on a rival's systems are the cheaper target as well (0.66x the
    garrison over the first twenty turns). Weighting that in measures null at
    every setting tried, because Phase 3 strikes at *every* affordable target, so
    a preference only binds when the budget forces a choice — 10.4% of the time.
    Inverting this sort entirely costs about two points, which caps what anything
    routed through it can be worth. See "Rushing the enemy" in
    `docs/bot-design.md`, and note the cap applies to the *sort*: `_wedge` is
    worth 12-14 points through this same function because it also steers
    `_front_pull` and the `_flow_to_front` seeding.
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


def _rival_waves(state, pid, sid, within: int) -> list[tuple[int, int, int]]:
    """``(turn, owner, ships)`` blocs landing on ``sid`` by ``within``, in arrival
    order, for every player that is neither us nor ``sid``'s current owner.

    One bloc per owner per turn, because that is how ``combat.resolve_arrival``
    totals them, and in arrival order because they are folded in that order. The
    target owner's own fleets are excluded — ``_inbound`` already counts those as
    reinforcement — so what is left is exactly the third parties racing us for the
    same node. Empty by construction in a duel, where the only player who can be
    sending ships at a rival's system is that rival.
    """
    owner = state.systems[sid].owner_id
    by_key: dict[tuple[int, int], int] = defaultdict(int)
    for f in state.fleets:
        if (f.dest_id == sid and f.owner_id != pid and f.owner_id != owner
                and f.turns_remaining <= within):
            by_key[(max(1, f.turns_remaining), f.owner_id)] += f.ships
    return sorted((t, o, n) for (t, o), n in by_key.items())


def _after_clash(garrison: int, striker: int) -> int:
    """Ships left standing on a system once ``striker`` has hit ``garrison``.

    The corner of the jitter square that leaves the *most* behind, whichever side
    that is: this feeds a requirement, so the pessimistic corner is the safe one.
    Straight off ``combat.preview_fight``, which runs the engine's own
    ``_apply_advantage``/``_resolve_effective`` pair and draws no rng, so the
    estimate cannot drift from the battle it predicts — and it is fed the *live*
    jitter and advantage rather than ``TUNED_SWING``, because this is a prediction
    of a real fight rather than a margin being floored against a knob.
    """
    if striker <= 0:
        return garrison
    p = combat.preview_fight(striker, garrison,
                             config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE)
    return max(p.nominal.survivors, p.best.survivors, p.worst.survivors)


def _required(state, pid, target, dist: int) -> int:
    """Ships needed to be *sure* of taking ``target`` when arriving in ``dist`` turns.

    Priced against whoever is standing there *when we land*, which is not always
    the player holding it now. ``_inbound`` counts only the owner's own
    reinforcements, so a **third** player's fleet already on the lane used to be
    invisible here: a system its owner had just evacuated read as free — nothing
    garrisoning it, nobody reinforcing it — and Phase 3b poured the whole surplus
    into a node a 12-stack took the turn before we arrived, losing the strike and
    leaving the source empty for the counter. ``_rival_waves`` makes those fleets
    visible and each is folded through the fight it is about to have, so what we
    are priced against is the *survivor* rather than the current garrison.

    Deliberately not applied to a neutral target, which stays static however many
    rivals are converging on it. That case measures worse, and the reason is
    Phase 3b: the price is a gate, not the size of the strike. Under-pricing a
    contested neutral opens the gate and the whole surplus goes in, which usually
    wins the race outright; pricing it honestly closes the gate and cedes the node
    to the rival. The gate is only worth shutting where the surplus would lose
    anyway, which is exactly the rival-held case above. Nor does the converse pay
    — deliberately arriving *after* a rival has broken a neutral and fighting the
    remnant is a real opportunity, about half a chance per game, and it measures
    null over 3500 games. Nor does it come alive with the gate taken away:
    ``COMMIT_SURPLUS = False`` makes this the "sends just enough" bot the tactic
    was supposed to reward, and it still reads 50.1% over 815 games there.

    The one case where the gate argument does not apply is a neutral already at
    ``ships == 0`` — there is no garrison to hold a discount against, so pricing
    it against a converging rival cannot cede a fight. Real waste (marshal loses
    51% of the races it walks into this way), but the fix cannot touch most of
    it: half of those races are a fleet marshal already launched before the node
    hit zero, and most of the rest are two seats independently striking the same
    freshly-emptied node on the same turn, which ``decide()`` cannot see for
    either side since every seat is priced against one shared, unmutated
    start-of-turn state. Repricing the strict remainder measures null. See
    "Racing a third player for the same system" in `docs/bot-design.md`, which
    also records why a pessimistic remnant estimate hides the converse
    opportunity entirely.
    """
    ships = target.ships
    if target.owner_id == 0:  # static neutral garrison — no production, no reinforcement
        return max(ships + 1, math.ceil(ships * _neutral_margin()))
    defence = (ships + _inbound(state, target.id, target.owner_id, dist)
               + _production_by(target, dist))
    for _turn, _owner, incoming in _rival_waves(state, pid, target.id, dist):
        defence = _after_clash(defence, incoming)
    return max(defence + 1, math.ceil(defence * _enemy_margin()))


# --------------------------------------------------------------------------- #
# Movement
# --------------------------------------------------------------------------- #
def _evacuate(state, pid, s, max_prod: int, abandoned=frozenset()):
    """Route a doomed system's whole garrison to the most useful place — or hold.

    ``abandoned`` is the rest of this turn's doomed set (minus whatever
    consolidation has just saved), and branch (b) will not retreat into it: a
    system whose own garrison is leaving, or is about to be over-run, is not a
    refuge. Left empty the pathology is mutual — ``_evacuate`` picks the friend
    with the biggest garrison, which for each of two doomed neighbours is the
    other, so the pair trades garrisons down one lane and both systems end the
    turn empty in front of the fleets that were already coming.
    """
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

    # (b) Retreat to the nearest refuge, then the most defensible one (biggest
    #     garrison, richest front). Distance leads because a retreat is the one
    #     move with no timing to it: the ships are not racing an arrival, they are
    #     simply out of the game until they land, and thinker's rule ranked on
    #     garrison size alone — so a whole garrison could be posted down a
    #     six-turn lane to the biggest stack we own while a refuge one turn away
    #     went unused. Worth more than the pooling below (52.0%, z = +3.34); see
    #     "Two doomed neighbours" in `docs/bot-design.md`.
    friends = [n for n in s.neighbors
               if sysmap[n].owner_id == pid and n not in abandoned]
    if friends:
        friends.sort(key=lambda n: (
            (state.travel_turns(s.id, n) or 99) if RETREAT_NEAREST else 0,
            -sysmap[n].ships, -_front_pull(state, pid, n, max_prod), n))
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


def _consolidate(state, pid, doomed, deficits, threatened_set, budget, sends):
    """Let the doomed relieve each other. Returns ``(saved, donated)``.

    Phase 1 sizes relief out of the *rear* only — its helper pool excludes every
    threatened system, since a garrison holding off its own siege is not spare —
    and gives a system up the moment that pool falls short. But a system it has
    already given up is not holding anything off: its garrison is lost where it
    stands, so those ships are free, and they are frequently exactly what the
    system next door is short of. Two doomed neighbours can often hold one of the
    two, which beats losing both and beats trading garrisons down the lane
    between them.

    Richest first, and a system that donates cannot also receive, so the trade
    can never happen: whichever of a pair is worth more is the one held, and the
    other empties into it. Donors are taken largest-first and only until the
    deficit is covered, so a garrison that isn't needed here is still free to
    take a system of its own back in ``_evacuate``. Phase 1's leftover rear
    budget is topped up on the end for the same reason it was passed over: it was
    left for Phase 3 to spend elsewhere, and holding a system beats spending it
    on a strike.

    Every donation is checked against the deadline Phase 1 measured — the
    *earliest* horizon showing a deficit — so ships that arrive after the system
    has already fallen are never counted as relief.
    """
    saved: set[int] = set()
    donated: set[int] = set()
    if not CONSOLIDATE:
        return saved, donated
    sysmap = state.systems
    doomed_set = set(doomed)
    order = sorted(doomed, key=lambda sid: (-sysmap[sid].production,
                                            -sysmap[sid].ships, sid))
    for sid in order:
        if sid in donated:
            continue
        need, deadline = deficits[sid]
        in_range = [n for n in sysmap[sid].neighbors
                    if (state.travel_turns(sid, n) or 99) <= deadline]
        donors = sorted((n for n in in_range
                         if n in doomed_set and n not in saved and n not in donated
                         and sysmap[n].ships > 0),
                        key=lambda n: (-sysmap[n].ships, n))
        rear = sorted((n for n in in_range
                       if sysmap[n].owner_id == pid and n not in threatened_set
                       and budget.get(n, 0) > 0),
                      key=lambda n: (-budget[n], n))
        if (sum(sysmap[n].ships for n in donors)
                + sum(budget[n] for n in rear) < need):
            continue                    # still cannot be held — abandon it below
        saved.add(sid)
        for n in donors:
            if need <= 0:
                break
            sends[(n, sid)] += sysmap[n].ships   # doomed: the whole garrison or nothing
            need -= sysmap[n].ships
            donated.add(n)
        for n in rear:
            if need <= 0:
                break
            send = min(budget[n], need)
            sends[(n, sid)] += send
            budget[n] -= send
            need -= send
    return saved, donated


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
    deficits: dict[int, tuple[int, int]] = {}   # doomed sid -> (ships short, deadline)
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
            # Can't be saved out of the rear. Phase 2 gets one more go at it with
            # the doomed themselves as donors, so keep what it would have to
            # cover and by when; failing that, it is abandoned there.
            doomed.append(sid)
            deficits[sid] = (need, deadline)
            continue
        budget[sid] = 0               # hold the whole garrison and pull the rest
        for h in helpers:
            if need <= 0:
                break
            send = min(budget[h], need)
            sends[(h, sid)] += send
            budget[h] -= send
            need -= send

    # --- Phase 2: consolidate what can still be held, abandon the rest ------- #
    # A garrison too small to save its own system is often exactly what the
    # system next door is short of, and it is free: those ships are lost where
    # they stand. `_consolidate` spends them that way where they hold a system,
    # and every system it does not save is passed to `_evacuate` as a place not
    # to retreat into.
    saved, donated = _consolidate(state, pid, doomed, deficits,
                                  threatened_set, budget, sends)
    abandoned = (frozenset(sid for sid in doomed if sid not in saved)
                 if AVOID_ABANDONED else frozenset())
    for sid in doomed:
        if sid not in saved and sid not in donated:
            order = _evacuate(state, pid, sysmap[sid], max_prod, abandoned)
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

"""knower — thinker, but it reads its opponents' orders before they issue them.

Every seat's strategy is public: ``Player.ai_strategy`` names a function in
``ai.STRATEGIES`` and ``Player.ai_params`` holds that seat's tuning. Turns also
resolve *simultaneously* — ``engine._collect_orders`` hands every player the same
unmutated start-of-turn state and applies nothing until all of them have decided
— so an opponent's orders **cannot** depend on ours. Those two facts together mean
a bot can clone the board, call each rival's own ``decide``, and read off exactly
what they are about to do. There is no game-theoretic fixed point to solve: one
forward pass of their real code *is* the answer.

Everything is then expressed through a single object, the **post-launch board**:
a clone of the start-of-turn state with every predicted enemy order applied (via
``engine.apply_order``, so the sequential ship-deduction clamp is the engine's own)
but *not* advanced. On that board ``systems[x].ships`` is the garrison a rival will
be left holding and ``fleets`` includes the launches nobody has seen yet, on the
same ``turns_remaining`` clock thinker's helpers already use. So the planner below
is thinker's, reading ``post`` where thinker read ``state``.

What that buys, in descending order of how much it actually wins:

  * **A turn of warning that thinker structurally cannot have.** thinker only sees
    fleets already on a lane, so a strike along an L-turn lane reaches it with L-1
    turns to spare — and a 1-turn strike is never visible *at all*, because
    ``end_turn`` launches, advances and resolves it in one call. How much that costs
    depends entirely on the Advanced menu's ship-speed slider (1-30 ly/turn), since
    ``travel_turns = ceil(length_ly / SHIP_LY_PER_TURN)``:
      - At the default 6 ly/turn no lane is shorter than 2 turns, so nothing is
        wholly invisible — but a blow landing next turn still cannot be answered,
        because the reinforcement filter ``travel_turns(sid, n) <= t_bind`` has no
        lane short enough to find. Measured over 12 thinker-vs-thinker games at 24
        nodes: 43.7% of the threats thinker detects sit at that horizon and *none*
        of them are reinforceable.
      - At 18 ly/turn, 72.5% of lanes are 1 turn; at 30, all of them are. There
        thinker cannot see most attacks until they have already landed.
    knower reads the launch on the turn it is issued, which fixes both regimes with
    the same mechanism.
  * **A guard only where it is needed.** thinker pins ``FRONTIER_GUARD`` of every
    frontier garrison against a hypothetical neighbour. knower knows which systems
    are really being attacked and how hard, so the rest of that army goes forward.
  * **Snipes.** ``apply_order`` deducts at launch, so a system that sent its army
    somewhere is genuinely empty *this turn*. ``_required`` prices a target off
    ``_garrison`` — what will actually be left defending it — so a strike thinker
    reads as hopeless is often nearly free.

Two things deliberately *not* here, both built, measured and then removed rather
than kept on the strength of the idea:

  * **Pricing three-way pile-ups.** The oracle knows who else lands on a node this
    turn, so knower can fold the pile-up with the jitter pinned against it and
    demand enough mass to survive it. Measured 71% vs 72% head-to-head over 200
    games and 33 vs 35 wins in a 3-player free-for-all — a wash, if anything worse.
    It makes knower skip attacks it cannot overpay for, and this game rewards the
    leaner strike (the same finding that set thinker's margins above).
  * **Striking perishable targets first.** A vacated garrison refills, so ordering
    targets by how much of theirs is leaving looks obviously right. It changes
    nothing: 118-45 vs 119-45 over 200 games, 35 vs 36 in the free-for-all. Pricing
    the target correctly is what wins; the order it happens in does not.

Don't re-add either without a measurement.

Against a **human** seat none of this holds — their orders come from the shell, not
from code. knower models them as another of itself one level shallower (a blind
``_plan``, i.e. thinker-strength self-play) and by default lets that model only
ever *raise* a threat: it never relaxes a guard, never believes a human vacated a
system, and never plans around a human fleet spending itself. A human who does
something unexpected therefore cannot be punished for it. See ``TRUST_HUMAN``.

Contract: ``decide(state, pid) -> list[Order]``. Reads state, never mutates it, and
deliberately draws **nothing** from ``state.rng`` — every tie-break here is
deterministic. That is not fussiness: it is what leaves the rng exactly where the
seats after us expect to find it, which is what makes their prediction bit-exact.

Measured against thinker, ladder, both seatings, 24-node random maps:

    default 6 ly/turn, 200 games   73% (119-45)
    18 ly/turn, 100 games          91% (88-9)     <- mostly 1-turn lanes
    full roster ladder             knower 143 > thinker 117 > claudebot 72
                                   > heuristic 33 > rusherplus 10

The gap between those first two rows *is* the thesis of this bot: the faster ships
are, the more of the game thinker cannot see, and the oracle scales with it.

Forked from ``models/thinker.py`` (commit f94ff20); the four phases and the helpers
below ``_richness`` are thinker's, changed only where the oracle changes them.
"""

from __future__ import annotations

import copy
import math
import random
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from starconquest import ai, engine
from starconquest.model import Order

# Lets a sibling oracle bot recognise us (and us it) so two of them proxy each
# other with the blind planner instead of recursing. See `_surrogate`.
IS_ORACLE = True

# --- tunables vendored unchanged from thinker ------------------------------- #
# Kept bit-identical on purpose: a knower-vs-thinker result should measure the
# oracle, not a re-tune. See thinker.py for how these were fitted.
_EDGE = 1.1 / 0.9
DEFEND_MARGIN = _EDGE + 0.05
NEUTRAL_MARGIN = 1.3
ENEMY_NEAR = 1.3
ENEMY_FAR = 1.9
OVERWHELM = 2.0
RESERVE_FLOOR = 1
FRONTIER_GUARD = 0.3            # now applied only against seats we *can't* predict

# --- knower's own ----------------------------------------------------------- #
# thinker pads its margins because it cannot see this turn's launches. Inside the
# known horizon the oracle already has every fleet that can arrive, so the only
# thing left to cover is the combat jitter itself.
KNOWN_MARGINS = True            # False reverts to thinker's padded margins
KNOWN_ATTACK = _EDGE + 0.02
KNOWN_DEFEND = _EDGE + 0.02

TRUST_HUMAN = False             # True lets a human-seat prediction relax guards and
                                # justify snipes, as if they were a bot. Off by
                                # default: a real human is not obliged to comply.

# A pathological opponent cannot be *interrupted* in pure Python (no threads, no
# signals on WASM), so the only defence is to stop asking the rest of them once the
# turn has already cost too much. Whatever we then failed to predict simply stays
# untrusted, and `_pessimistic_owners` hedges against it the way thinker would.
# Deliberately ~100x the measured cost of a real turn (knower 0.49 ms on a 24-node
# board; every other bot in the roster decides in 6-21 us), so this is dead code
# against any sane opponent. Tripping it does cost this game its bit-reproducibility,
# which beats freezing the browser tab.
ORACLE_BUDGET_S = 0.050

_TRUSTED, _UNTRUSTED = True, False

# Re-entrancy depth. Single-threaded codebase, so a module global is sound. This
# is the backstop that makes recursion impossible even between two *different*
# oracle modules, which `_surrogate`'s identity check cannot see.
_DEPTH = 0

# Last oracle built and last exception swallowed, for tests and debugging. Pure
# records — the planner reads neither. `LAST_ERROR` exists because `decide` has to
# catch everything (nothing upstream does), and a bug that silently downgrades
# knower to blind play would otherwise look exactly like a bot that is merely weak.
LAST_ORACLE = None
LAST_ERROR = None


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
@dataclass
class Oracle:
    """What the rest of this turn looks like, as far as it can be known."""

    post: object                      # start-of-turn board + every predicted launch
    trusted: set[int]                 # owners whose post-launch garrisons we believe
    launched: dict[int, int]          # system id -> ships it sends away this turn
    # Pure records, for tests and debugging — the planner reads neither.
    seats: dict[int, str] = field(default_factory=dict)      # pid -> how it was predicted
    orders: dict[int, list] = field(default_factory=dict)    # pid -> predicted orders


def decide(state, pid):
    """Plan against a forecast of every other seat. Never raises."""
    global _DEPTH, LAST_ERROR

    if _DEPTH:
        # Someone is predicting *us*. Answer as the blind planner: it terminates,
        # and it is the same self-model we use for seats we cannot read.
        return _plan(state, pid, None)

    _DEPTH += 1
    try:
        orc = _build_oracle(state, pid)
    except Exception as exc:              # noqa: BLE001 — an oracle is only a luxury
        orc, LAST_ERROR = None, exc
    finally:
        _DEPTH -= 1

    try:
        return _plan(state, pid, orc)
    except Exception as exc:              # noqa: BLE001
        # Nothing upstream catches a bot (engine.py:101, main.py:300 both call it
        # bare), so a crash here would take the whole game down.
        LAST_ERROR = exc
        try:
            return _plan(state, pid, None)
        except Exception as exc2:         # noqa: BLE001
            LAST_ERROR = exc2
            return []


# --------------------------------------------------------------------------- #
# The oracle
# --------------------------------------------------------------------------- #
def _build_oracle(state, me):
    """Run every other seat's decision function and fold the result into a board.

    Seats are predicted in the engine's own order (`engine._collect_orders`:
    ascending pid, skipping neutral/human/dead) so that seats *after* us share one
    rng that advances exactly as the real one will — making their orders
    bit-exact. Seats *before* us have already drawn from ``state.rng`` from a
    position we cannot recover, so they get a private rng and are right except
    where they hit a genuine tie (measured: 99.6% of turns).
    """
    global LAST_ORACLE

    deadline = time.perf_counter() + ORACLE_BUDGET_S
    post = _clone(state, _priv(state, me, 1))
    orc = Oracle(post=post, trusted={0}, launched=defaultdict(int))  # neutrals never launch

    # Seats before us: right logic, right inputs, unrecoverable rng position.
    for q in [q for q in sorted(state.players) if q < me]:
        _predict_seat(state, orc, me, q, _priv(state, q, 2), "likely")

    # Seats after us: one shared rng, positioned where they will really find it.
    shared = random.Random()
    shared.setstate(state.rng.getstate())
    for q in [q for q in sorted(state.players) if q > me]:
        player = state.players[q]
        if player.is_neutral or not player.alive:
            continue
        if time.perf_counter() > deadline:
            break                     # out of budget: the rest stay untrusted
        if player.is_human:
            # The engine never calls `decide` for a human seat (engine.py:99), so
            # this prediction must not advance the shared stream.
            _predict_seat(state, orc, me, q, _priv(state, q, 4), "modelled")
            continue
        if not _predict_seat(state, orc, me, q, shared, "exact"):
            # That seat raised, mutated its board or had to be proxied, so it
            # consumed a different number of draws than the real one will. Every
            # seat after it is off-position now — still worth predicting, but the
            # bit-exact chain is over.
            shared = _priv(state, q, 3)

    LAST_ORACLE = orc
    return orc


def _predict_seat(state, orc, me, q, rng, label):
    """Predict seat ``q`` and fold its launches into ``orc``. True if faithful."""
    player = state.players[q]
    if player.is_neutral or not player.alive:
        return True                       # the engine skips it too — no draws, no orders

    fn, trustworthy = _surrogate(player)
    probe = _clone(state, rng)
    fingerprint = _fingerprint(probe)

    try:
        orders = fn(probe, q) or []
    except Exception:                     # noqa: BLE001 — a broken rival is their problem
        orc.seats[q] = "raised"
        return False

    # Out of contract (models/README.md:22). Its orders may still be sane, but it
    # has proved it can corrupt a board, so believe nothing it implies.
    mutated = _fingerprint(probe) != fingerprint
    if mutated:
        trustworthy = _UNTRUSTED

    if trustworthy:
        orc.trusted.add(q)
        orc.seats[q] = label
    else:
        orc.seats[q] = "mutated" if mutated else "modelled"
    orc.orders[q] = list(orders)

    _apply_predicted(orc, me, q, orders)
    return bool(trustworthy)


def _surrogate(player):
    """(fn, trustworthy) — what will really decide this seat, and can we believe it.

    A human seat is decided by a person, not by code, so it is modelled as another
    knower one level shallower and never trusted. Our own strategy gets the same
    treatment — under whatever filename it was registered as, and likewise any
    sibling oracle — which is what keeps two knower seats from recursing.
    """
    if player.is_human:
        return _blind, (_TRUSTED if TRUST_HUMAN else _UNTRUSTED)
    # Resolve exactly as ai.decide does (ai.py:84), so even a stale strategy name
    # is predicted correctly: the engine will fall back to the heuristic too.
    fn = ai.STRATEGIES.get(player.ai_strategy, ai.compute_orders)
    if fn is decide or _is_oracle(fn):
        return _blind, _UNTRUSTED
    return fn, _TRUSTED


def _is_oracle(fn):
    module = sys.modules.get(getattr(fn, "__module__", "") or "")
    return bool(getattr(module, "IS_ORACLE", False))


def _blind(state, pid):
    """The self-model: knower with the oracle removed, i.e. thinker-strength."""
    return _plan(state, pid, None)


def _apply_predicted(orc, me, seat, orders):
    """Fold one seat's predicted orders into the post-launch board.

    Uses ``engine.apply_order`` rather than reimplementing it, so the validation and
    the running per-source clamp (engine.py:40-50) are the engine's own — a bot that
    over-commits one garrison is clamped here exactly as it will be for real.

    Orders naming anyone but ``seat`` as owner are dropped, mirroring the engine's
    ``_own_orders``: a seat commands its own ships and nothing else, so honouring
    them here would predict a fleet the engine is about to refuse. The redundant
    "never a system we own" check is deliberate belt-and-braces — the cost is nil and
    silently losing our own garrison off the planning board would be a bad failure.
    """
    for order in orders:
        try:
            if order.owner_id != seat or order.owner_id == me:
                continue
            src = orc.post.systems.get(order.source_id)
            if src is None or src.owner_id == me:
                continue
            before = src.ships
            if engine.apply_order(orc.post, order) is not None:
                orc.launched[src.id] += before - src.ships
        except Exception:                 # noqa: BLE001 — malformed order object
            continue


def _clone(state, rng):
    """A private copy of the board, cheap enough to make one per prediction.

    Per-object ``copy.copy`` rather than ``copy.deepcopy`` — measured 0.10 ms
    against 1.34 ms at 24 nodes, ~14x, which matters on the WASM build — with every
    mutable container rebuilt so a rival cannot reach the real state through one.
    ``lanes`` is shared by reference: it is immutable in practice, and nothing reads
    it but ``rebuild_topology``.
    """
    clone = copy.copy(state)
    clone.systems = {sid: copy.copy(s) for sid, s in state.systems.items()}
    for s in clone.systems.values():
        s.neighbors = list(s.neighbors)
    clone.fleets = [copy.copy(f) for f in state.fleets]
    clone.players = {pid: copy.copy(p) for pid, p in state.players.items()}
    for p in clone.players.values():
        p.ai_params = copy.copy(p.ai_params)
    clone.adjacency = {sid: dict(nbrs) for sid, nbrs in state.adjacency.items()}
    clone.rng = rng
    return clone


def _fingerprint(state):
    """Everything a prediction is allowed to leave untouched."""
    return (
        tuple((s.id, s.owner_id, s.ships, s.prod_progress) for s in state.systems.values()),
        tuple((f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
              for f in state.fleets),
    )


def _priv(state, pid, salt):
    """A private rng derived from the state alone.

    Never the clock, never ``id()``, never ``hash()`` of a string — all three
    would make a game unreproducible from its seed and break
    ``replay.reconstruct``, which re-runs ``decide`` (replay.py:259-264).
    """
    return random.Random((state.seed * 1000003 + state.turn * 9176 + pid * 31 + salt) & 0x7FFFFFFF)


# --------------------------------------------------------------------------- #
# The planner — thinker's four phases, reading the post-launch board
# --------------------------------------------------------------------------- #
def _plan(state, pid, orc):
    """thinker's plan. With ``orc`` it reads the future; with ``None`` it is thinker."""
    post = orc.post if orc is not None else state
    sysmap = post.systems
    owned = [sid for sid, s in sysmap.items() if s.owner_id == pid]
    if not owned:
        return []

    max_prod = max(s.production for s in sysmap.values())
    frontier = {sid for sid in owned
                if any(sysmap[n].owner_id != pid for n in sysmap[sid].neighbors)}
    guarded_against = _pessimistic_owners(post, orc, pid)

    orders: list[Order] = []

    # --- Phase 0: base budgets — spendable ships after each system's guard --- #
    # The blind hedge now only covers seats we could not read. With every rival
    # predicted that set is empty, and the standing guard — measured at 17.6% of
    # thinker's army, nearly all of it never contested — is freed for Phase 3.
    budget: dict[int, int] = {}
    for sid in owned:
        s = sysmap[sid]
        if sid in frontier:
            guard = math.ceil(FRONTIER_GUARD * _max_adjacent_enemy(post, orc, s, guarded_against))
            budget[sid] = max(0, s.ships - max(RESERVE_FLOOR, guard))
        else:
            budget[sid] = max(0, s.ships - RESERVE_FLOOR)

    # --- Phase 1: arrival-aware defence -------------------------------------- #
    # A system hit along a long lane can amass defence over several turns, so we
    # schedule reinforcements by *when* the blow lands rather than only pulling
    # one-hop help. Threatened systems that even that can't save are marked doomed.
    #
    # This is where the oracle pays for itself. Blind, a 2-turn strike is detected
    # with one turn to go and no lane is short enough to answer it; here the strike
    # is already in `post.fleets` at t=2, so `helpers` can actually reach it.
    threatened = [sid for sid in owned if _incoming(post, sid, pid, hostile=True) > 0]
    threatened_set = set(threatened)
    threatened.sort(key=lambda sid: (_first_strike(post, pid, sid),
                                     -_incoming(post, sid, pid, hostile=True)))
    doomed: list[int] = []
    for sid in threatened:
        s = sysmap[sid]
        known = _known_horizon(post, orc, sid)
        # The garrison we must have present, and the turn that demand binds.
        worst, t_bind = 0, 1
        for t, ecum in _enemy_arrivals(post, pid, sid):
            margin = KNOWN_DEFEND if (KNOWN_MARGINS and t <= known) else DEFEND_MARGIN
            deficit = (math.ceil(ecum * margin)
                       - _production_by(s, t) - _inbound(post, sid, pid, t))
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
             and budget.get(n, 0) > 0 and (post.travel_turns(sid, n) or 99) <= t_bind),
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
            orders.append(Order(pid, h, sid, send))
            budget[h] -= send
            need -= send

    # --- Phase 2: abandon the doomed ----------------------------------------- #
    # On the post-launch board this quietly gains its best move: the attacker's own
    # home is now visibly empty, so a system about to be overrun steps *into* it.
    for sid in doomed:
        order = _evacuate(post, orc, pid, sysmap[sid], max_prod)
        if order is not None:
            orders.append(order)
        budget[sid] = 0  # whether it retreated or holds to inflict casualties, don't drain it

    # --- Phase 3: focus fire with staggered pincers -------------------------- #
    targets = [sysmap[n] for n in
               {n for sid in frontier for n in sysmap[sid].neighbors
                if sysmap[n].owner_id != pid}]
    targets.sort(key=lambda t: (-_richness(t, max_prod), t.ships, t.id))

    for target in targets:
        # Our budgeted systems adjacent to the target, and how far off each is.
        nbrs = [(post.travel_turns(sid, target.id), sid) for sid in owned
                if target.id in sysmap[sid].neighbors and budget.get(sid, 0) > 0]
        if not nbrs:
            continue

        # Soonest arrival horizon H at which committed force (plus fleets already
        # inbound by then) overwhelms the target. Nearer distances are tried
        # first, so we strike as early as we can and only stagger when massing
        # demands the farther systems too.
        chosen_h, shortfall = None, 0
        for h in sorted({d for d, _ in nbrs}):
            req = _required(post, orc, pid, target, h)
            inbound = _inbound(post, target.id, pid, h)
            committable = sum(budget[sid] for d, sid in nbrs if d <= h)
            if inbound + committable < req:
                continue
            chosen_h, shortfall = h, req - inbound
            break
        if chosen_h is None:
            continue  # can't crack it even at full stretch — leave the ships to mass

        # Launch only the far wave (dist == H) now, covering the part the nearer
        # waves won't; those nearer waves launch on later turns and converge,
        # because next turn this fleet shows up in the target's inbound tally.
        nearer = sum(budget[sid] for d, sid in nbrs if d < chosen_h)
        need = max(0, shortfall - nearer)
        for sid in sorted((sid for d, sid in nbrs if d == chosen_h),
                          key=lambda s: (-budget[s], s)):
            if need <= 0:
                break
            send = min(budget[sid], need)
            orders.append(Order(pid, sid, target.id, send))
            budget[sid] -= send
            need -= send

    # --- Phase 4: leapfrog / flow to the richest front ----------------------- #
    parent = _flow_to_front(post, set(owned), frontier, pid, max_prod)
    for sid in sorted(owned):
        if sid in frontier:
            continue  # the front's leftover stays home as the standing reserve
        b = budget.get(sid, 0)
        if b > 0 and sid in parent:
            orders.append(Order(pid, sid, parent[sid], b))
            budget[sid] = 0

    return orders


# --------------------------------------------------------------------------- #
# Oracle-aware helpers
# --------------------------------------------------------------------------- #
def _pessimistic_owners(post, orc, pid):
    """Rivals whose intentions we do *not* know, and must therefore hedge against.

    Blind, that is everyone — which is exactly thinker's standing guard. With a
    full oracle it is empty. In between sit human seats and anything that raised,
    mutated its board or had to be modelled, and those keep the blind hedge.
    """
    rivals = {p.id for p in post.players.values() if not p.is_neutral and p.id != pid}
    if orc is None:
        return rivals
    return rivals - orc.trusted


def _trust(orc, owner):
    """Do we believe what the post-launch board says about ``owner``'s systems?"""
    return orc is not None and owner in orc.trusted


def _garrison(orc, target):
    """Defenders to expect at ``target`` — post-launch only where that is trusted.

    This is the whole of the "a prediction may only raise a threat" rule on the
    offensive side. For a human seat (or anything that raised, mutated its board or
    had to be modelled) the launches we predicted are added *back*, so knower prices
    the target as if it never moved. A human who does something we didn't foresee
    therefore cannot be punished for it, and a bot that we read exactly is.
    """
    if _trust(orc, target.owner_id):
        return target.ships
    return target.ships + (orc.launched.get(target.id, 0) if orc is not None else 0)


def _known_horizon(post, orc, sid):
    """Last arrival turn at ``sid`` whose fleets the oracle already holds in full.

    Everything launched this turn is in ``post.fleets``. A fleet launched *next*
    turn must still cross a whole lane from a direct neighbour, so it cannot land
    before ``1 + shortest lane into sid``. Every horizon up to that shortest lane
    is therefore complete, and needs no padding for surprises — only for jitter.
    """
    if orc is None:
        return 0
    lanes = [post.travel_turns(n, sid) or 99 for n in post.systems[sid].neighbors]
    return min(lanes) if lanes else 0


# --------------------------------------------------------------------------- #
# Helpers vendored from thinker — identical but for the oracle hooks
# --------------------------------------------------------------------------- #
def _richness(system, max_prod: int) -> int:
    """Value of a system: higher output (lower ``production``) scores higher."""
    return max_prod - system.production + 1


def _incoming(post, sid, pid, hostile: bool) -> int:
    """Ships inbound to ``sid``: hostile (owner != pid) or friendly (owner == pid)."""
    return sum(f.ships for f in post.fleets
               if f.dest_id == sid and (f.owner_id != pid) == hostile)


def _first_strike(post, pid, sid) -> int:
    """Turns until the first enemy fleet reaches ``sid`` (large if none is coming)."""
    return min((f.turns_remaining for f in post.fleets
                if f.dest_id == sid and f.owner_id != pid), default=99)


def _enemy_arrivals(post, pid, sid) -> list[tuple[int, int]]:
    """Enemy ships reaching ``sid`` by each strike turn, as (turn, cumulative)."""
    by_turn: dict[int, int] = defaultdict(int)
    for f in post.fleets:
        if f.dest_id == sid and f.owner_id != pid:
            by_turn[max(1, f.turns_remaining)] += f.ships
    cum, out = 0, []
    for t in sorted(by_turn):
        cum += by_turn[t]
        out.append((t, cum))
    return out


def _inbound(post, dest, owner, within) -> int:
    """Ships owned by ``owner`` reaching ``dest`` within ``within`` turns."""
    return sum(f.ships for f in post.fleets
               if f.dest_id == dest and f.owner_id == owner and f.turns_remaining <= within)


def _production_by(s, turns: int) -> int:
    """Ships ``s`` will build over ``turns`` turns at its current progress."""
    if s.production <= 0 or turns <= 0:
        return 0
    return (s.prod_progress + turns) // s.production


def _max_adjacent_enemy(post, orc, sysobj, owners) -> int:
    """Largest garrison next door belonging to a seat in ``owners``.

    thinker hedges against every non-neutral neighbour; ``owners`` narrows that to
    the seats whose orders we could not read. Neutrals never attack, and they are
    never in ``owners``. The garrison is the *pre*-launch one — for an unreadable
    seat we don't get to assume the army we're hedging against has gone somewhere.
    """
    best = 0
    for n in sysobj.neighbors:
        o = post.systems[n]
        if o.owner_id in owners:
            best = max(best, _garrison(orc, o))
    return best


def _required(post, orc, pid, target, dist: int) -> int:
    """Ships needed to be *sure* of taking ``target`` when arriving in ``dist`` turns.

    ``target.ships`` is read off the post-launch board, so a system that has just
    sent its army away is priced at what it will actually be defending with. Inside
    the known horizon the margin drops to the bare jitter edge: thinker's ramp
    exists to cover reinforcements it cannot see, and here there are none left to
    see.
    """
    known = _known_horizon(post, orc, target.id)
    ships = _garrison(orc, target)
    if target.owner_id == 0:  # static neutral garrison — no production, no reinforcement
        return max(ships + 1, math.ceil(ships * NEUTRAL_MARGIN))
    # Enemy: fold in the reinforcements and production that land before we arrive,
    # and pad more the later we strike (more time for the enemy to react).
    reinforcements = _inbound(post, target.id, target.owner_id, dist)
    defence = ships + reinforcements + _production_by(target, dist)
    if KNOWN_MARGINS and dist <= known:
        margin = KNOWN_ATTACK
    else:
        margin = min(ENEMY_FAR, ENEMY_NEAR + 0.1 * (dist - 1))
    return max(ships + 1, math.ceil(defence * margin))


def _evacuate(post, orc, pid, s, max_prod: int):
    """Route a doomed system's whole garrison to the most useful place — or hold."""
    sysmap = post.systems
    ships = s.ships
    if ships <= 0:
        return None

    # (a) Step forward into the richest system we can still take with what's here.
    #     On the post-launch board that includes the home of whoever is attacking
    #     us, which they have just emptied to do it.
    caps = []
    for n in s.neighbors:
        o = sysmap[n]
        if o.owner_id != pid:
            dist = post.travel_turns(s.id, n) or 1
            if ships >= _required(post, orc, pid, o, dist):
                caps.append((_richness(o, max_prod), -o.ships, n))
    if caps:
        caps.sort(reverse=True)
        return Order(pid, s.id, caps[0][2], ships)

    # (b) Retreat to the most defensible friend (biggest garrison, richest front).
    friends = [n for n in s.neighbors if sysmap[n].owner_id == pid]
    if friends:
        friends.sort(
            key=lambda n: (sysmap[n].ships, _front_pull(post, pid, n, max_prod), -n),
            reverse=True,
        )
        return Order(pid, s.id, friends[0], ships)

    # (c) Cornered. Only sortie if truly overwhelmed; otherwise hold — a garrison
    #     kills more attackers than a doomed strike on a weak neighbour would.
    enemy_in = _incoming(post, s.id, pid, hostile=True)
    friend_in = _incoming(post, s.id, pid, hostile=False)
    if enemy_in > OVERWHELM * (ships + friend_in):
        enemies = [n for n in s.neighbors if sysmap[n].owner_id != pid]
        if enemies:
            weakest = min(enemies, key=lambda n: (sysmap[n].ships, n))
            return Order(pid, s.id, weakest, ships)
    return None


def _front_pull(post, pid, sid, max_prod: int) -> int:
    """How rich a prize the front at ``sid`` faces — its richest non-owned neighbour."""
    best = 0
    for n in post.systems[sid].neighbors:
        o = post.systems[n]
        if o.owner_id != pid:
            best = max(best, _richness(o, max_prod))
    return best


def _flow_to_front(post, owned, frontier, pid, max_prod) -> dict[int, int]:
    """Multi-source BFS over owned territory: rear node -> next hop toward the front.

    Seeds are ordered by the richness of the prize each frontier faces, so a rear
    node adjacent to two fronts flows toward the *richer* one. Sorted throughout,
    so a rear system's next hop stays stable while the frontier does, rather than
    oscillating turn to turn.
    """
    parent: dict[int, int] = {}
    seen = set(frontier)
    seeds = sorted(frontier, key=lambda sid: (-_front_pull(post, pid, sid, max_prod), sid))
    queue = deque(seeds)
    while queue:
        cur = queue.popleft()
        for nbr in sorted(post.systems[cur].neighbors):
            if nbr in owned and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur
                queue.append(nbr)
    return parent

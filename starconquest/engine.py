"""The turn engine: the pure 'reducer' that advances a GameState by one turn.

Order issuing (``apply_order``) deducts ships from the source immediately and
puts a fleet on a lane, so issuing order has no bearing on outcomes. ``end_turn``
then resolves one turn deterministically, returning a ``TurnRecord`` of
everything the turn consumed — and accepting one back in ``script`` to replay it:

    1. AI phase        — each AI player's decisions are applied (injected via
                         ``decide`` so the engine never imports the AI). A seat may
                         only command its own ships: orders naming a different owner
                         are dropped (``_own_orders``), since ``apply_order`` alone
                         cannot tell which seat issued an order.
    2. Advance fleets  — every in-transit fleet counts down one turn.
    3. Lane battles    — (opt-in) enemy fleets whose paths cross in transit fight
                         pairwise, nearest crossing first; winners fly on as they were.
    4. Arrivals+combat — fleets that reach their destination are grouped by node
                         and resolved together (fair regardless of launch order).
    5. Production      — systems accrue toward their next ship (after combat, so
                         a system captured this turn produces for its new owner).
    6. Win check       — a player is alive if it holds a system or has a fleet in
                         transit; the game ends when <= 1 remain.
    7. turn += 1.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from itertools import combinations
from typing import Callable, Iterable, NamedTuple, Optional

from . import combat, config, turnfilm
from .model import Fleet, GameState, Order, lane_key

# What version of the *rules* a game is played under, stamped onto every log
# (`replay.GameLog.rules_version`).
#
# Bump this — by hand, in the same commit — whenever a change here, in `combat`,
# or in `mapgen` could make a previously recorded game replay to a different
# board: the phase order in `end_turn`, how a fight resolves, how the dice are
# drawn or consumed, how a map is generated from a seed. Do NOT bump it for a
# bot, a UI change, or a default that only affects new games: a stored log
# replays its own recorded orders and dice and never consults a strategy at all
# (`test_a_replay_does_not_consult_a_bot_even_a_deleted_one`).
#
# It buys one thing, and only for logs stamped before the change: the leaderboard's
# verifier can tell "this score does not check out" from "the rules moved under
# it", and say the second rather than accusing an honest player
# (`tools/verify_scores.py`). That is worth a hand-maintained integer; nothing
# here can detect such a change on its own.
RULES_VERSION = 1

# A decision function: given the state and a player id, return that player's orders.
DecideFn = Callable[[GameState, int], list[Order]]


@dataclass
class TurnRecord:
    """What one turn consumed that the board alone doesn't determine: every seat's
    orders, in the sequence they were applied, and the combat draws they produced.

    Handed back by ``end_turn`` and accepted again as its ``script``, so a match is
    replayable from its seed plus one of these per turn — whatever the AI did in
    between. That is the point: a bot may consult the clock, time out, or draw from
    a stream of its own, and the record still describes the game that was played.
    """

    orders: list[Order] = field(default_factory=list)
    dice: list[float] = field(default_factory=list)


class _Dice:
    """Combat's rng for one turn, keeping every draw it deals.

    Given ``recorded`` draws it deals those instead of rolling: a replay skips the
    AI entirely, so ``state.rng`` no longer sits where it did when the turn was
    first fought, and only the recorded values reproduce that battle. Rolling on
    past the end of a short (hand-edited) record keeps a replay playable rather
    than half-resolved.
    """

    def __init__(self, rng, recorded: Optional[Iterable[float]] = None) -> None:
        self._rng = rng
        self._recorded = deque(recorded or ())
        self.drawn: list[float] = []

    def uniform(self, a: float, b: float) -> float:
        value = self._recorded.popleft() if self._recorded else self._rng.uniform(a, b)
        self.drawn.append(value)
        return value


# --------------------------------------------------------------------------- #
# Issuing orders
# --------------------------------------------------------------------------- #
def apply_order(state: GameState, order: Order) -> Optional[Fleet]:
    """Validate and launch a fleet, deducting ships from the source at once.

    Returns the created Fleet, or None if the order is illegal (unknown/foreign
    source, non-adjacent destination, or no ships available).
    """
    src = state.systems.get(order.source_id)
    if src is None or src.owner_id != order.owner_id:
        return None
    turns = state.travel_turns(order.source_id, order.dest_id)
    if turns is None:  # destination not adjacent
        return None
    ships = min(order.ships, src.ships)
    if ships <= 0:
        return None

    src.ships -= ships
    fleet = Fleet(
        owner_id=order.owner_id,
        source_id=order.source_id,
        dest_id=order.dest_id,
        ships=ships,
        turns_total=turns,
        turns_remaining=turns,
    )
    state.fleets.append(fleet)
    return fleet


# --------------------------------------------------------------------------- #
# Turn resolution
# --------------------------------------------------------------------------- #
def end_turn(
    state: GameState,
    human_orders: Optional[list[Order]] = None,
    decide: Optional[DecideFn] = None,
    script: Optional[TurnRecord] = None,
    on_event: Optional[turnfilm.EventFn] = None,
) -> TurnRecord:
    """Resolve one turn with simultaneous decision-making.

    Every player's orders — the human's queued list plus each AI's decisions —
    are collected against the *same* unchanged start-of-turn state, then applied
    together. No player ever reacts to another's move made this same turn, so
    there is no turn-order advantage.

    ``script`` replays a turn that was already fought: its orders are applied
    verbatim (no seat is asked to decide) and its dice are dealt back to combat.
    Either way the turn's ``TurnRecord`` is returned, which is what a caller logs.
    """
    if state.winner is not None:
        return TurnRecord()

    watch = turnfilm.watcher(on_event)
    watch.open(state)

    orders = (list(script.orders) if script is not None
              else _collect_orders(state, human_orders, decide))
    for order in orders:  # order-independent: each system has a single owner
        watch.launched(state, apply_order(state, order))

    dice = _Dice(state.rng, script.dice if script is not None else None)
    _advance_fleets(state)
    watch.advanced(state)
    _resolve_lane_battles(state, dice, watch)
    _resolve_arrivals(state, dice, watch)
    watch.mark(state)
    _production(state)
    watch.produced(state)
    _check_win(state)
    state.turn += 1
    watch.ended(state)
    return TurnRecord(orders, dice.drawn)


def _collect_orders(state: GameState, human_orders: Optional[list[Order]], decide: Optional[DecideFn]) -> list[Order]:
    human = state.human()
    orders: list[Order] = _own_orders(human_orders, human.id if human else None)
    if decide is not None:
        for pid in sorted(state.players):
            player = state.players[pid]
            if player.is_neutral or player.is_human or not player.alive:
                continue
            orders.extend(_own_orders(decide(state, pid), pid))  # same pre-apply state
    return orders


def _own_orders(orders: Optional[list[Order]], seat: Optional[int]) -> list[Order]:
    """Keep only the orders ``seat`` is entitled to issue.

    A seat commands its own ships and nothing else. This has to be enforced here,
    at the point where orders are attributed to a seat, because ``apply_order``
    can't: it only checks that the *declared* owner holds the source, so an order
    naming another player as owner is perfectly valid on its own terms. Without
    this filter a drop-in AI could return ``Order(other_pid, their_system, ...)``
    and launch a rival's fleet for them.

    ``seat is None`` means there is no human seat to attribute ``human_orders`` to
    — a headless caller passing orders explicitly — so they are taken as given.
    Nothing a rival AI produces reaches that path.
    """
    if not orders:
        return []
    if seat is None:
        return list(orders)
    return [order for order in orders if order.owner_id == seat]


def _advance_fleets(state: GameState) -> None:
    for fleet in state.fleets:
        fleet.turns_remaining -= 1


class _Crossing(NamedTuple):
    """Two fleets meeting on one lane during this turn's step.

    ``when`` is the fraction of the step at which their gap reached zero and
    ``at`` the lane fraction, measured from ``min(lane_key)``, where that
    happened — the point the two fleets share at that instant.
    """

    when: float
    at: float
    a: int  # indices into `state.fleets`
    b: int


def _lane_span(fleet: Fleet, low_id: int) -> tuple[float, float]:
    """Where ``fleet`` was when this turn's step began and where it is now, as
    fractions of its lane measured from ``low_id``.

    Mirrors ``Fleet.progress`` — which is exactly what the map draws — and
    mirrors it from a single end of the lane so two fleets running opposite ways
    are measured on one ruler. A fleet that launched this turn starts at 0.0 (or
    1.0, heading the other way), i.e. on its source system.
    """
    if fleet.turns_total <= 0:
        return 1.0, 1.0
    was, now = fleet.progress_at(0.0), fleet.progress_at(1.0)
    return (was, now) if fleet.source_id == low_id else (1.0 - was, 1.0 - now)


def _lane_crossings(state: GameState, low_id: int, idxs: list[int]) -> list[_Crossing]:
    """Every enemy pair on one lane that meets during this turn's step, in the
    order the meetings happen.

    Each fleet covers a straight line along the lane over the turn, so the gap
    between any two is linear across the step: they meet exactly when that gap is
    zero at either end or changes sign in between — reaching the same position if
    they run at the same speed, passing through each other if they don't. The
    crossing point is where the gap hits zero, which is what orders the fights: a
    fleet running a defended lane meets what is nearest first and carries its
    losses into the next meeting.

    Simultaneous crossings break on fleet order, which is order of launch, so a
    replay fights them in the sequence the live game did.
    """
    crossings: list[_Crossing] = []
    for a_i, b_i in combinations(idxs, 2):
        a, b = state.fleets[a_i], state.fleets[b_i]
        if a.owner_id == b.owner_id:
            continue  # friendly traffic passes through itself
        a_was, a_now = _lane_span(a, low_id)
        b_was, b_now = _lane_span(b, low_id)
        gap_was, gap_now = a_was - b_was, a_now - b_now
        if gap_was * gap_now > 0:
            continue  # one stayed ahead of the other all step: they never met
        when = 0.0 if gap_was == gap_now else gap_was / (gap_was - gap_now)
        crossings.append(_Crossing(when, a_was + when * (a_now - a_was), a_i, b_i))
    # Sorted on an explicit key rather than the whole tuple: `at` must not reach
    # the comparison, or simultaneous crossings would break on where they met
    # instead of on fleet order, which is order of launch.
    crossings.sort(key=lambda c: (c.when, c.a, c.b))
    return crossings


def _resolve_lane_battles(state: GameState, dice: _Dice, watch: turnfilm.Watch) -> None:
    """Fight the fleets that actually meet in transit (opt-in via IN_LANE_BATTLES).

    Fleets in transit normally never interact. When enabled, two enemy fleets
    engage on the turn their paths touch or cross — sharing a lane is not enough,
    and nothing is dragged to a meeting point it never reached.

    Engagements are strictly pairwise and resolved in crossing order, so one
    strong fleet running a lane picks off the fleets strung along it one at a
    time, carrying its losses forward into each next fight, instead of facing
    their pooled strength at once. A winner keeps its own heading, speed and
    arrival turn and is only thinned, so a fleet is never merged into another and
    is only ever drawn where it really is.
    """
    if not config.IN_LANE_BATTLES:
        return

    by_lane: dict[frozenset[int], list[int]] = defaultdict(list)
    for i, fleet in enumerate(state.fleets):
        by_lane[lane_key(fleet.source_id, fleet.dest_id)].append(i)

    destroyed: set[int] = set()
    for lane, idxs in by_lane.items():
        if len({state.fleets[i].owner_id for i in idxs}) < 2:
            continue  # no enemy out here, so there is nobody to meet
        for crossing in _lane_crossings(state, min(lane), idxs):
            a_i, b_i = crossing.a, crossing.b
            if a_i in destroyed or b_i in destroyed:
                continue  # killed at an earlier crossing this same turn
            a, b = state.fleets[a_i], state.fleets[b_i]
            a_ships, b_ships = a.ships, b.ships
            winner, survivors = combat.resolve_lane_clash(state, a, b, dice)
            if winner == a.owner_id:
                a.ships, survivor, dead = survivors, a, (b,)
                destroyed.add(b_i)
            elif winner == b.owner_id:
                b.ships, survivor, dead = survivors, b, (a,)
                destroyed.add(a_i)
            else:  # matched forces, and no ground to break the tie
                survivor, dead = None, (a, b)
                destroyed.update((a_i, b_i))
            watch.clashed(crossing, lane, a, b, a_ships, b_ships,
                          survivor, survivors, dead)

    if destroyed:
        state.fleets = [f for i, f in enumerate(state.fleets) if i not in destroyed]


def _resolve_arrivals(state: GameState, dice: _Dice, watch: turnfilm.Watch) -> None:
    arrived: dict[int, list[Fleet]] = defaultdict(list)
    still_flying: list[Fleet] = []
    for fleet in state.fleets:
        if fleet.turns_remaining <= 0:
            arrived[fleet.dest_id].append(fleet)
        else:
            still_flying.append(fleet)
    state.fleets = still_flying

    for node_id, fleets in arrived.items():
        node = state.systems[node_id]
        was_owner, was_ships = node.owner_id, node.ships
        folds = watch.folds()
        combat.resolve_arrival(state, node_id, fleets, dice, on_step=folds)
        watch.landed(state, node_id, fleets, was_owner, was_ships, folds)


def _production(state: GameState) -> None:
    for sys in state.systems.values():
        if sys.owner_id == 0 and not config.NEUTRAL_PRODUCES:
            continue
        if sys.production <= 0:
            continue
        sys.prod_progress += 1
        while sys.prod_progress >= sys.production:
            sys.ships += 1
            sys.prod_progress -= sys.production


def _check_win(state: GameState) -> None:
    for player in state.players.values():
        if player.is_neutral:
            continue
        owns = any(s.owner_id == player.id for s in state.systems.values())
        flying = any(f.owner_id == player.id for f in state.fleets)
        player.alive = owns or flying

    alive = [p for p in state.non_neutral_players() if p.alive]
    if len(alive) <= 1:
        state.winner = alive[0].id if alive else 0  # 0 signals a draw

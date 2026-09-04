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
    3. Lane battles    — (opt-in) fleets of different owners sharing a lane clash
                         in transit; only the winning side flies on.
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
from typing import Callable, Iterable, Optional

from . import combat, config
from .model import Fleet, GameState, Order, lane_key

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

    orders = (list(script.orders) if script is not None
              else _collect_orders(state, human_orders, decide))
    for order in orders:  # order-independent: each system has a single owner
        apply_order(state, order)

    dice = _Dice(state.rng, script.dice if script is not None else None)
    _advance_fleets(state)
    _resolve_lane_battles(state, dice)
    _resolve_arrivals(state, dice)
    _production(state)
    _check_win(state)
    state.turn += 1
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


def _resolve_lane_battles(state: GameState, dice: _Dice) -> None:
    """Fight any lane held by two or more owners (opt-in via IN_LANE_BATTLES).

    Fleets in transit normally never interact; when enabled, every fleet sharing
    a lane clashes in open space and only the winning side survives. Its most
    advanced fleet (fewest turns remaining) carries the survivors on toward its
    destination, so the winner keeps its original heading and arrival time.
    """
    if not config.IN_LANE_BATTLES:
        return

    by_lane: dict[frozenset[int], list[Fleet]] = defaultdict(list)
    for fleet in state.fleets:
        by_lane[lane_key(fleet.source_id, fleet.dest_id)].append(fleet)

    kept: list[Fleet] = []
    for fleets in by_lane.values():
        if len({f.owner_id for f in fleets}) < 2:
            kept.extend(fleets)  # no enemy present -> lane untouched
            continue
        winner, survivors = combat.resolve_lane_clash(state, fleets, dice)
        if winner != 0 and survivors > 0:
            vanguard = min(
                (f for f in fleets if f.owner_id == winner),
                key=lambda f: f.turns_remaining,
            )
            vanguard.ships = survivors
            kept.append(vanguard)
    state.fleets = kept


def _resolve_arrivals(state: GameState, dice: _Dice) -> None:
    arrived: dict[int, list[Fleet]] = defaultdict(list)
    still_flying: list[Fleet] = []
    for fleet in state.fleets:
        if fleet.turns_remaining <= 0:
            arrived[fleet.dest_id].append(fleet)
        else:
            still_flying.append(fleet)
    state.fleets = still_flying

    for node_id, fleets in arrived.items():
        combat.resolve_arrival(state, node_id, fleets, dice)


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

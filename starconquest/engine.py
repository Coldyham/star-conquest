"""The turn engine: the pure 'reducer' that advances a GameState by one turn.

Order issuing (``apply_order``) deducts ships from the source immediately and
puts a fleet on a lane, so issuing order has no bearing on outcomes. ``end_turn``
then resolves one turn deterministically:

    1. AI phase        — each AI player's decisions are applied (injected via
                         ``decide`` so the engine never imports the AI). A seat may
                         only command its own ships: orders naming a different owner
                         are dropped (``_own_orders``), since ``apply_order`` alone
                         cannot tell which seat issued an order.
    2. Advance fleets  — every in-transit fleet counts down one turn.
    3. Arrivals+combat — fleets that reach their destination are grouped by node
                         and resolved together (fair regardless of launch order).
    4. Production      — systems accrue toward their next ship (after combat, so
                         a system captured this turn produces for its new owner).
    5. Win check       — a player is alive if it holds a system or has a fleet in
                         transit; the game ends when <= 1 remain.
    6. turn += 1.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable, Optional

from . import combat, config
from .model import Fleet, GameState, Order

# A decision function: given the state and a player id, return that player's orders.
DecideFn = Callable[[GameState, int], list[Order]]


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
) -> None:
    """Resolve one turn with simultaneous decision-making.

    Every player's orders — the human's queued list plus each AI's decisions —
    are collected against the *same* unchanged start-of-turn state, then applied
    together. No player ever reacts to another's move made this same turn, so
    there is no turn-order advantage.
    """
    if state.winner is not None:
        return

    orders = _collect_orders(state, human_orders, decide)
    for order in orders:  # order-independent: each system has a single owner
        apply_order(state, order)

    _advance_fleets(state)
    _resolve_arrivals(state)
    _production(state)
    _check_win(state)
    state.turn += 1


def _collect_orders(
    state: GameState, human_orders: Optional[list[Order]], decide: Optional[DecideFn]
) -> list[Order]:
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


def _resolve_arrivals(state: GameState) -> None:
    arrived: dict[int, list[Fleet]] = defaultdict(list)
    still_flying: list[Fleet] = []
    for fleet in state.fleets:
        if fleet.turns_remaining <= 0:
            arrived[fleet.dest_id].append(fleet)
        else:
            still_flying.append(fleet)
    state.fleets = still_flying

    for node_id, fleets in arrived.items():
        combat.resolve_arrival(state, node_id, fleets)


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

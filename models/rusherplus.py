"""A simple aggressive AI: every system attacks the weakest thing next door."""

from starconquest.model import Order


def decide(state, pid):
    orders = []
    for sys in state.systems.values():
        if sys.owner_id != pid or sys.ships <= 1:
            continue
        # Adjacent systems we don't already own.
        targets = [state.systems[n] for n in sys.neighbors
                   if state.systems[n].owner_id != pid]
        if not targets:  # prefer to send to not ours, but if all neighbours friendly, still send to weaker
            targets = [state.systems[n] for n in sys.neighbors]
        target = min(targets, key=lambda s: (s.ships, state.rng.random()))
        attacked_by = sum([f.ships for f in state.fleets_incoming(sys.id) if f.owner_id != pid])
        reinforced_by = sum([f.ships for f in state.fleets_incoming(sys.id) if f.owner_id == pid])
        if attacked_by > sys.ships + reinforced_by:
            send = sys.ships
            orders.append(Order(pid, sys.id, target.id, send))
            continue

        send = (sys.ships + reinforced_by - 1) - attacked_by
        if send > target.ships + 1:              # only commit when we'd likely win
            orders.append(Order(pid, sys.id, target.id, send))
    return orders

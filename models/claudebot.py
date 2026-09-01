"""claudebot — a coordinated, jitter-aware conqueror.

Improves on the built-in heuristic in three ways that this game's rules actually
reward:

  * **Focus fire.** Fleets that reach a node on the same turn are resolved
    together, and Lanchester's square law pays for mass (10 vs 6 leaves ~8, not
    4). So rather than let each system pick its own target, claudebot works
    *target-first*: it gathers every owned system the *same distance* from a
    target and only strikes when their combined force clears a jitter-safe win
    margin — concentrating force the way the square law rewards.
  * **Jitter-safe margins.** At the default swing of +/-10% the worst case for an
    attacker is 0.9*A vs 1.1*D, so A must beat D by 1.1/0.9 ~= 1.22x to be *sure*
    of the win. The margins below are derived from that break-even multiple rather
    than guessed, and take it from `combat.edge_attacking`/`edge_defending` so they
    follow the jitter and defender-advantage sliders instead of assuming defaults.
  * **Defence before greed.** A system about to be hit this turn holds its whole
    garrison and pulls one-hop reinforcements that land on the *same* turn to
    join the grouped fight; only the leftover surplus attacks, and rear systems
    stream their spare ships toward the front so nothing sits idle inland.

Contract: ``decide(state, pid) -> list[Order]``. Reads the state, never mutates
it, and routes every tie-break through ``state.rng`` so games stay reproducible.
"""

from __future__ import annotations

import math
from collections import deque

from starconquest import combat
from starconquest.model import Order

# --- tunables -------------------------------------------------------------- #
# Every margin is the break-even edge plus a task-specific cushion. The edge is
# `combat.edge_attacking`/`edge_defending`, read live: it is 1.222x at the default
# jitter with no defender advantage, and both menu sliders move it from there —
# attacking and defending in opposite directions, since the bonus goes to
# whoever holds the system.
# `TUNED_SWING` floors the edge's jitter half at the swing these pads were
# fitted against, so a knob only ever raises a margin: without it, a game set to
# zero jitter would thin every margin below what claudebot was measured at.
TUNED_SWING = 1.1 / 0.9  # the +/-10% swing these pads were fitted at
DEFEND_PAD = 0.05     # to hold: garrison must clear the known incoming force
NEUTRAL_PAD = 0.08    # neutrals are static, so a thin cushion suffices
ENEMY_PAD = 0.15      # enemies may produce/reinforce mid-attack — a modest pad
                      # (a fresh enemy launch always costs a turn we can react to)

RESERVE_FLOOR = 1     # never strip a system below this when it isn't threatened
FRONTIER_GUARD = 0.34  # a frontier system keeps this fraction of its scariest
                       # adjacent enemy garrison home as a standing guard


def _defend_margin() -> float:
    return combat.edge_defending(TUNED_SWING) + DEFEND_PAD


def _neutral_margin() -> float:
    return combat.edge_attacking(TUNED_SWING) + NEUTRAL_PAD


def _enemy_margin() -> float:
    return combat.edge_attacking(TUNED_SWING) + ENEMY_PAD


def decide(state, pid):
    sysmap = state.systems
    owned = {s.id for s in sysmap.values() if s.owner_id == pid}
    if not owned:
        return []

    max_prod = max(s.production for s in sysmap.values())
    frontier = {sid for sid in owned
                if any(sysmap[n].owner_id != pid for n in sysmap[sid].neighbors)}

    # Ships each system may spend this turn, after holding a defensive reserve.
    budget: dict[int, int] = {}
    under_attack: set[int] = set()
    for sid in owned:
        s = sysmap[sid]
        incoming = _imminent(state, pid, sid)
        if incoming > 0:
            # Being hit now: hold just enough to win *this* fight and spend the
            # rest, so a system under steady light pressure still fuels the war.
            hold = math.ceil(incoming * _defend_margin())
            help_now = _inbound(state, sid, pid, 1)
            if s.ships + help_now < hold:
                budget[sid] = 0            # can't hold alone — keep all, call for help
                under_attack.add(sid)
            else:
                budget[sid] = max(0, s.ships - max(0, hold - help_now))
        elif sid in frontier:
            guard = math.ceil(FRONTIER_GUARD * _max_adjacent_enemy(state, pid, s))
            budget[sid] = max(0, s.ships - max(RESERVE_FLOOR, guard))
        else:
            budget[sid] = max(0, s.ships - RESERVE_FLOOR)

    orders: list[Order] = []

    # 1) DEFENCE. For a system being hit this turn, pull one-hop help that lands
    #    on the same turn and joins the grouped fight, up to a jitter-safe hold.
    for sid in sorted(under_attack):
        s = sysmap[sid]
        incoming = _imminent(state, pid, sid)
        need = math.ceil(incoming * _defend_margin()) - (s.ships + _inbound(state, sid, pid, 1))
        if need <= 0:
            continue
        helpers = sorted(
            (n for n in s.neighbors
             if sysmap[n].owner_id == pid and budget.get(n, 0) > 0
             and state.travel_turns(n, sid) == 1),
            key=lambda n: -budget[n],
        )
        for h in helpers:
            if need <= 0:
                break
            send = min(budget[h], need)
            orders.append(Order(pid, h, sid, send))
            budget[h] -= send
            need -= send

    # 2) OFFENCE. Target-first focus fire: for each capturable neighbour, find the
    #    nearest distance-group of our systems whose *combined* budget clears the
    #    win margin, and commit just enough from that group so they arrive together.
    targets = [sysmap[n] for n in
               {n for sid in frontier for n in sysmap[sid].neighbors
                if sysmap[n].owner_id != pid}]
    targets.sort(key=lambda t: (-(max_prod - t.production + 1), t.ships, state.rng.random()))

    for target in targets:
        groups: dict[int, list[int]] = {}
        for sid in owned:
            if budget.get(sid, 0) > 0 and target.id in sysmap[sid].neighbors:
                groups.setdefault(state.travel_turns(sid, target.id), []).append(sid)

        for dist in sorted(groups):  # nearest first: sooner arrival, less time to reinforce
            need = _required(state, pid, target, dist) - _inbound(state, target.id, pid, dist)
            if need <= 0:
                break  # a fleet already in transit will take it — don't pile on
            srcs = sorted(groups[dist], key=lambda s: -budget[s])
            if sum(budget[s] for s in srcs) < need:
                continue  # this range can't crack it; a farther (weaker-timed) group might
            for s in srcs:
                if need <= 0:
                    break
                send = min(budget[s], need)
                orders.append(Order(pid, s, target.id, send))
                budget[s] -= send
                need -= send
            break

    # 3) LOGISTICS. Rear systems push their leftover surplus one hop toward the
    #    nearest frontier so interior production never stagnates.
    parent = _flow_to_front(state, owned, frontier)
    for sid in sorted(owned):
        if sid not in frontier and budget.get(sid, 0) > 0 and sid in parent:
            orders.append(Order(pid, sid, parent[sid], budget[sid]))
            budget[sid] = 0

    return orders


# --- helpers --------------------------------------------------------------- #
def _imminent(state, pid, sid) -> int:
    """Enemy ships arriving at ``sid`` *this* turn — they fight the garrison now."""
    return sum(f.ships for f in state.fleets
               if f.dest_id == sid and f.owner_id != pid and f.turns_remaining <= 1)


def _inbound(state, dest, owner, within) -> int:
    """Ships owned by ``owner`` reaching ``dest`` within ``within`` turns."""
    return sum(f.ships for f in state.fleets
               if f.dest_id == dest and f.owner_id == owner and f.turns_remaining <= within)


def _max_adjacent_enemy(state, pid, sysobj) -> int:
    """Largest hostile (non-neutral) garrison next door — neutrals never attack."""
    best = 0
    for n in sysobj.neighbors:
        o = state.systems[n]
        if o.owner_id != pid and o.owner_id != 0:
            best = max(best, o.ships)
    return best


def _required(state, pid, target, dist) -> int:
    """Ships needed to be *sure* of taking ``target`` when arriving in ``dist`` turns."""
    if target.owner_id == 0:  # static neutral garrison
        return max(target.ships + 1, math.ceil(target.ships * _neutral_margin()))
    # Enemy: fold in its own reinforcements that land by the time we arrive.
    defence = target.ships + sum(
        f.ships for f in state.fleets
        if f.dest_id == target.id and f.owner_id == target.owner_id and f.turns_remaining <= dist)
    return max(target.ships + 1, math.ceil(defence * _enemy_margin()))


def _flow_to_front(state, owned, frontier) -> dict[int, int]:
    """Multi-source BFS over owned territory: rear node -> next hop toward the front.

    Sorted seeds/neighbours keep the flow deterministic, so a rear system's next
    hop is stable while the frontier is, instead of oscillating turn to turn.
    """
    parent: dict[int, int] = {}
    seen = set(frontier)
    queue = deque(sorted(frontier))
    while queue:
        cur = queue.popleft()
        for nbr in sorted(state.systems[cur].neighbors):
            if nbr in owned and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur
                queue.append(nbr)
    return parent

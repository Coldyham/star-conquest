"""Combat resolution: Lanchester's square law with a slight random swing.

The square law makes the winner's losses sub-1:1, so concentrating force is
rewarded (10 vs 6 leaves ~8 survivors, not 4). A small +/- jitter on each side
adds tension without turning fights into coin flips. Everything stochastic is
drawn from ``state.rng`` so a seed reproduces every battle.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

from . import config
from .model import Fleet, GameState


def resolve_fight(
    rng,
    a_owner: int,
    a_ships: int,
    b_owner: int,
    b_ships: int,
    defender_owner: Optional[int] = None,
) -> tuple[int, int]:
    """One pairwise engagement. Returns (winning_owner, surviving_ships).

    Survivors are ``sqrt(W^2 - L^2)`` on the jittered strengths, capped at the
    winner's actual ship count so no ships are ever created. If survivors round
    to 0 the battle is mutual annihilation -> (0, 0) (system goes neutral).
    """
    j = config.COMBAT_JITTER
    a_eff = a_ships * (1.0 + rng.uniform(-j, j))
    b_eff = b_ships * (1.0 + rng.uniform(-j, j))

    if a_eff > b_eff:
        w_owner, w_actual, w_eff, l_eff = a_owner, a_ships, a_eff, b_eff
    elif b_eff > a_eff:
        w_owner, w_actual, w_eff, l_eff = b_owner, b_ships, b_eff, a_eff
    else:  # exact tie (only reachable with zero jitter): defender holds
        if b_owner == defender_owner:
            w_owner, w_actual, w_eff, l_eff = b_owner, b_ships, b_eff, a_eff
        else:
            w_owner, w_actual, w_eff, l_eff = a_owner, a_ships, a_eff, b_eff

    survivors = round(math.sqrt(max(w_eff * w_eff - l_eff * l_eff, 0.0)))
    survivors = max(0, min(survivors, w_actual))
    if survivors == 0:
        return 0, 0  # mutual annihilation -> neutral
    return w_owner, survivors


def resolve_arrival(state: GameState, node_id: int, arriving: list[Fleet]) -> tuple[int, int]:
    """Resolve every fleet arriving at ``node_id`` this turn against the defender.

    Handles reinforcement (single owner present), a straight attack (two owners),
    and the rare 3+-owner pile-up (fold strongest-first). Mutates the system in
    place and returns (final_owner, final_ships).
    """
    node = state.systems[node_id]
    old_owner = node.owner_id

    # Total each owner's strength: existing garrison + all their arriving fleets.
    forces: dict[int, int] = defaultdict(int)
    forces[node.owner_id] += node.ships
    for f in arriving:
        forces[f.owner_id] += f.ships

    # Only sides with ships can fight.
    sides = [(owner, ships) for owner, ships in forces.items() if ships > 0]

    if not sides:
        node.owner_id, node.ships = 0, 0
    elif len(sides) == 1:
        node.owner_id, node.ships = sides[0]
    else:
        # Fold strongest-first; defender breaks exact (zero-jitter) ties.
        sides.sort(key=lambda s: s[1], reverse=True)
        cur_owner, cur_ships = sides[0]
        for owner, ships in sides[1:]:
            cur_owner, cur_ships = resolve_fight(
                state.rng, cur_owner, cur_ships, owner, ships, defender_owner=old_owner
            )
        node.owner_id, node.ships = cur_owner, cur_ships

    # Attrition, per owner: everyone brought `forces[owner]` here (garrison plus
    # arrivals) and only the final owner keeps anything, so the difference is
    # exactly what they lost. Correct for a plain reinforcement (nothing lost), a
    # two-way attack and the 3+-owner pile-up alike. Draws no rng, so this cannot
    # perturb a seeded replay.
    for owner, brought in forces.items():
        kept = node.ships if owner == node.owner_id else 0
        if brought > kept and owner in state.players:
            state.players[owner].ships_lost += brought - kept

    if node.owner_id != old_owner:
        node.prod_progress = 0  # a captured system starts building fresh
    return node.owner_id, node.ships

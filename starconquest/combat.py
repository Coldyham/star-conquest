"""Combat resolution: Lanchester's square law with a slight random swing.

The square law makes the winner's losses sub-1:1, so concentrating force is
rewarded (10 vs 6 leaves ~8 survivors, not 4). A small +/- jitter on each side
adds tension without turning fights into coin flips. Everything stochastic is
drawn from the rng the engine hands in — ``state.rng`` while a game is live, so a
seed reproduces every battle; the recorded draws when a turn is being replayed.

``config.DEFENDER_ADVANTAGE`` scales the defender's jittered strength before the
square-law maths (1.0 is neutral); an exact tie still breaks to the defender
regardless of the multiplier.

Both knobs are menu sliders, so the multiple a fleet needs to be *sure* of a
fight is not a constant. ``edge_attacking``/``edge_defending`` read it off the
same arithmetic, for the AI to size an attack or a garrison against the rules
actually in force.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from . import config
from .model import Fleet, GameState

# --------------------------------------------------------------------------- #
# Shared fight arithmetic
#
# `resolve_fight` (which rolls the dice) and `preview_fight` (which supplies
# them) both run through these, so the square law, the tie rule, the rounding and
# the ships cap are written down exactly once and the menu's Combat page cannot
# drift from the fight it predicts.
# --------------------------------------------------------------------------- #


def _apply_advantage(
    a_owner: int,
    a_eff: float,
    b_owner: int,
    b_eff: float,
    defender_owner: Optional[int],
    advantage: float,
) -> tuple[float, float]:
    """Scale whichever side is holding the system. Applied *after* jitter, so the
    multiplier lands on the already-swung strength rather than the nominal one."""
    if a_owner == defender_owner:
        a_eff *= advantage
    elif b_owner == defender_owner:
        b_eff *= advantage
    return a_eff, b_eff


def _survivors(w_eff: float, l_eff: float, w_actual: int) -> int:
    """Lanchester survivors: ``sqrt(W^2 - L^2)`` on the *effective* strengths,
    capped at the winner's *actual* ships so a fight never creates one. 0 means
    mutual annihilation.

    ``round`` is Python's banker's rounding and must stay that way: it decides
    real battles, so swapping it would change every seeded replay.
    """
    n = round(math.sqrt(max(w_eff * w_eff - l_eff * l_eff, 0.0)))
    return max(0, min(n, w_actual))


def _resolve_effective(
    a_owner: int,
    a_ships: int,
    a_eff: float,
    b_owner: int,
    b_ships: int,
    b_eff: float,
    defender_owner: Optional[int],
) -> tuple[int, int]:
    """The whole of a fight *after* the dice and the advantage: strengths in,
    ``(owner, ships)`` out. ``a_eff``/``b_eff`` are post-``_apply_advantage``."""
    if a_eff > b_eff:
        w_owner, w_actual, w_eff, l_eff = a_owner, a_ships, a_eff, b_eff
    elif b_eff > a_eff:
        w_owner, w_actual, w_eff, l_eff = b_owner, b_ships, b_eff, a_eff
    else:  # exact tie (only reachable with zero jitter): defender holds
        if b_owner == defender_owner:
            w_owner, w_actual, w_eff, l_eff = b_owner, b_ships, b_eff, a_eff
        else:
            w_owner, w_actual, w_eff, l_eff = a_owner, a_ships, a_eff, b_eff

    survivors = _survivors(w_eff, l_eff, w_actual)
    if survivors == 0:
        return 0, 0  # mutual annihilation -> neutral
    return w_owner, survivors


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
    a_eff, b_eff = _apply_advantage(
        a_owner, a_eff, b_owner, b_eff, defender_owner, config.DEFENDER_ADVANTAGE
    )
    return _resolve_effective(a_owner, a_ships, a_eff, b_owner, b_ships, b_eff, defender_owner)


# --------------------------------------------------------------------------- #
# Break-even margins: what an attack needs to be *sure*
#
# The same arithmetic read backwards, for a bot sizing a fleet. Only the jitter
# roll and `_apply_advantage` stand between ship counts and the winner, so the
# multiple a side needs is exact, and belongs here rather than as a constant in
# every model file. Read `config` live at call time, like `resolve_fight` and
# unlike `preview_fight` (which is fed the menu's in-progress values instead).
# --------------------------------------------------------------------------- #
_JITTER_CAP = 0.95  # past here the worst-roll ratio runs away; keep it finite
_ADVANTAGE_FLOOR = 0.01  # ...and never divide by a zeroed advantage


def _swing() -> float:
    """``(1+j)/(1-j)`` — the worst roll: our low strength against their high."""
    j = min(max(float(config.COMBAT_JITTER), 0.0), _JITTER_CAP)
    return (1.0 + j) / (1.0 - j)


def edge_attacking(min_swing: float = 1.0) -> float:
    """Break-even multiple to take a system: they hold it, so they get the bonus.

    An attack of ``A`` on a garrison of ``B`` wins the worst roll iff
    ``A(1-j) > B(1+j)*adv``, i.e. ``A > B * adv * swing``. Ties break to the
    defender, so a caller wanting certainty must clear this *strictly* — and
    clearing it only guarantees the defender loses, not that anyone wins: near
    matched forces annihilate and the system goes neutral, which is why every
    bot also floors its ask at ``target.ships + 1``.

    ``min_swing`` floors the jitter half only (see ``edge_defending``).
    """
    return _advantage() * max(min_swing, _swing())


def edge_defending(min_swing: float = 1.0) -> float:
    """Break-even multiple to hold one: *we* hold it, so the bonus is ours.

    A garrison of ``D`` survives ``E`` arriving ships in the worst roll iff
    ``D(1-j)*adv > E(1+j)``, i.e. ``D > E * swing / adv``. The advantage divides
    here and multiplies above — turning the knob up makes holding cheaper and
    taking dearer, and a bot applying it in one direction only would be wrong in
    the other.

    ``min_swing`` floors the *swing*, not the edge: pass the swing a margin was
    tuned at and a gentler jitter than that can no longer thin it, while a wilder
    one still widens it. It deliberately leaves the advantage alone, so a bot
    keeping its tuned cushion still gets the whole of the advantage in both
    directions. The 1.0 default is the zero-jitter swing, i.e. no floor.
    """
    return max(min_swing, _swing()) / _advantage()


def _advantage() -> float:
    """``config.DEFENDER_ADVANTAGE``, clamped away from zero.

    ``_apply_advantage`` scales whichever side holds the system *after* the
    jitter roll, so it lands on the swung strength and composes with ``_swing``
    as a plain product.
    """
    return max(float(config.DEFENDER_ADVANTAGE), _ADVANTAGE_FLOOR)


# --------------------------------------------------------------------------- #
# Preview: what a fight *would* do, for the menu's Combat page
# --------------------------------------------------------------------------- #

# Stand-in owner ids for a hypothetical fight, so a preview roll runs through the
# same resolver the engine uses. NEUTRAL is 0 for the same reason the game's
# neutral player is: mutual annihilation vacates the system.
ATTACKER = 1
DEFENDER = 2
NEUTRAL = 0


@dataclass(frozen=True)
class Roll:
    """One fully determined engagement — what happens for one pair of dice."""

    winner: int  # ATTACKER / DEFENDER / NEUTRAL (mutual annihilation)
    survivors: int  # ships the winner keeps; 0 exactly when winner is NEUTRAL
    attacker_eff: float  # the strength the attacker actually fought at
    defender_eff: float  # ...and the defender's, advantage included

    @property
    def attacker_survivors(self) -> int:
        """What the attacker walks away with: 0 if it lost, and 0 if both sides
        died. Non-decreasing in the attacker's ship count, which is what makes
        the best/worst band an ordered range."""
        return self.survivors if self.winner == ATTACKER else 0

    @property
    def defender_survivors(self) -> int:
        return self.survivors if self.winner == DEFENDER else 0


@dataclass(frozen=True)
class CombatPreview:
    """Everything the menu's Combat page needs to explain one hypothetical fight.

    The three rolls are the *corners* of the jitter square, not samples:
    ``nominal`` is both dice at zero (exactly what ``resolve_fight`` produces at
    ``COMBAT_JITTER == 0``), ``best`` is the attacker's luckiest possible roll (it
    swings +j while the defender swings -j) and ``worst`` the reverse.

    Every real fight lands between them. The attacker's effective strength rises
    with its own roll and the defender's falls with it, so the corners bound both
    who wins *and* how many ships are left — which is what makes the band honest
    to draw as a range rather than decorative.
    """

    attacker: int
    defender: int
    jitter: float
    advantage: float
    nominal: Roll
    best: Roll
    worst: Roll

    @property
    def naive(self) -> int:
        """What subtraction would say. The teaching contrast: the square law
        leaves the winner far more than this — 10 v 6 keeps 8 ships, not 4."""
        return self.attacker - self.defender

    @property
    def certain(self) -> bool:
        """True when both corners agree, so no roll can change who ends up
        holding the system. False is an honest 'could go either way'."""
        return self.best.winner == self.worst.winner

    @property
    def annihilation(self) -> bool:
        """The nominal roll wipes out both sides and the system goes *neutral* —
        nobody's, not the attacker's. Only near-matched forces do this."""
        return self.nominal.winner == NEUTRAL

    @property
    def band(self) -> tuple[int, int]:
        """(worst, best) attacker survivors — the width of jitter, in ships."""
        return self.worst.attacker_survivors, self.best.attacker_survivors

    def roll(self, attacker_swing: float, defender_swing: float) -> Roll:
        """The outcome anywhere inside the jitter square, each swing given in
        ``[-1, +1]`` as a fraction of ``jitter``.

        The three stored rolls are just its named corners — ``roll(0, 0)`` is
        ``nominal``, ``roll(+1, -1)`` is ``best`` and ``roll(-1, +1)`` is
        ``worst`` — so a caller wanting the whole square (the menu draws a grid
        of it) samples the same maths rather than rebuilding it.
        """
        return _preview_roll(
            self.attacker,
            self.defender,
            attacker_swing * self.jitter,
            defender_swing * self.jitter,
            self.advantage,
        )


def _preview_roll(attacker: int, defender: int, a_roll: float, d_roll: float, advantage: float) -> Roll:
    """One corner of the preview: the arithmetic ``resolve_fight`` runs, with the
    swings supplied instead of drawn."""
    a_eff, d_eff = _apply_advantage(
        ATTACKER, attacker * (1.0 + a_roll), DEFENDER, defender * (1.0 + d_roll), DEFENDER, advantage
    )
    winner, survivors = _resolve_effective(ATTACKER, attacker, a_eff, DEFENDER, defender, d_eff, DEFENDER)
    return Roll(winner, survivors, a_eff, d_eff)


def preview_fight(attacker: int, defender: int, jitter: float, advantage: float) -> CombatPreview:
    """What would happen if ``attacker`` ships hit a system garrisoned by ``defender``.

    Draws no rng and reads no ``config``. Both are deliberate:

    * ``jitter``/``advantage`` are **parameters**, not ``config`` reads, because
      the menu previews the values the player is dragging *right now* — those
      only reach ``config.COMBAT_JITTER``/``config.DEFENDER_ADVANTAGE`` at game
      start, via ``settings._apply_globals``. Reading config here would preview
      the *previous* game's balance.
    * No rng, so ``menu.draw`` stays a pure read of ``Settings`` (its half of the
      draw/mutate split), and so a preview can never perturb a seeded replay.

    Every number comes from the same ``_apply_advantage``/``_resolve_effective``
    pair the engine uses, so the preview cannot drift from the fight it predicts.
    """
    j = max(0.0, jitter)
    return CombatPreview(
        attacker=attacker,
        defender=defender,
        jitter=j,
        advantage=advantage,
        nominal=_preview_roll(attacker, defender, 0.0, 0.0, advantage),
        best=_preview_roll(attacker, defender, +j, -j, advantage),
        worst=_preview_roll(attacker, defender, -j, +j, advantage),
    )


def _record_losses(state: GameState, forces: dict[int, int], owner_id: int, ships: int) -> None:
    """Charge every side what the engagement cost it.

    Everyone brought ``forces[owner]`` (a garrison plus arrivals at a system, or
    a lane's fleets), and only the final owner keeps anything, so the difference
    is exactly what they lost. Correct for a plain reinforcement (nothing lost),
    a two-way attack, the 3+-owner pile-up and a lane clash alike — and for
    mutual annihilation, where ``owner_id`` is neutral, holds nothing here, and
    so everyone is charged in full. Draws no rng, so this cannot perturb a seeded
    replay.
    """
    for owner, brought in forces.items():
        kept = ships if owner == owner_id else 0
        if brought > kept and owner in state.players:
            state.players[owner].ships_lost += brought - kept


def resolve_lane_clash(state: GameState, fleets: list[Fleet], rng=None) -> tuple[int, int]:
    """Resolve every fleet sharing one lane against each other in open space.

    No defender exists mid-lane, so this is a plain strongest-first fold of the
    per-owner totals via ``resolve_fight`` (mirrors the pile-up branch of
    ``resolve_arrival``, minus the ``defender_owner`` tie-break — nobody holds
    open space, so ``config.DEFENDER_ADVANTAGE`` applies to neither side and an
    exact tie annihilates instead of breaking to anyone). The jitter still
    applies: it is a property of the fight, not of the ground. Returns
    (winning_owner, surviving_ships); (0, 0) on mutual annihilation.

    ``rng`` is the turn's dice, for the same reason ``resolve_arrival`` takes
    them: a lane battle rolls, so a replay has to be dealt the recorded draws
    back or it fights a different one. ``state.rng`` when omitted.

    Losses are charged through the same ``_record_losses`` the arrival path uses,
    so ships killed in open space count toward ``Player.ships_lost`` — the
    challenge tie-break — exactly as ships killed at a system do.
    """
    rng = state.rng if rng is None else rng

    forces: dict[int, int] = defaultdict(int)
    for f in fleets:
        forces[f.owner_id] += f.ships

    sides = [(owner, ships) for owner, ships in forces.items() if ships > 0]
    if not sides:
        return 0, 0
    if len(sides) == 1:
        cur_owner, cur_ships = sides[0]
    else:
        sides.sort(key=lambda s: s[1], reverse=True)
        cur_owner, cur_ships = sides[0]
        for owner, ships in sides[1:]:
            cur_owner, cur_ships = resolve_fight(rng, cur_owner, cur_ships, owner, ships)

    _record_losses(state, forces, cur_owner, cur_ships)
    return cur_owner, cur_ships


def resolve_arrival(state: GameState, node_id: int, arriving: list[Fleet],
                    rng=None) -> tuple[int, int]:
    """Resolve every fleet arriving at ``node_id`` this turn against the defender.

    Handles reinforcement (single owner present), a straight attack (two owners),
    and the rare 3+-owner pile-up (fold strongest-first). Mutates the system in
    place and returns (final_owner, final_ships).

    ``rng`` is the turn's dice — ``engine`` passes one that records what it deals
    (and, on a replay, deals back what was recorded). ``state.rng`` when omitted.
    """
    node = state.systems[node_id]
    rng = state.rng if rng is None else rng
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
            cur_owner, cur_ships = resolve_fight(rng, cur_owner, cur_ships, owner, ships, defender_owner=old_owner)
        node.owner_id, node.ships = cur_owner, cur_ships

    _record_losses(state, forces, node.owner_id, node.ships)

    if node.owner_id != old_owner:
        node.prod_progress = 0  # a captured system starts building fresh
    return node.owner_id, node.ships

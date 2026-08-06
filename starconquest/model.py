"""Core data model: plain dataclasses plus small, pure helpers.

No game logic and no pygame here. Everything is driven from integer ids so the
state is easy to reason about, serialize, and feed to the AI. Neutral is a real
player with ``id == 0``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from . import config


def lane_key(a: int, b: int) -> frozenset[int]:
    """Canonical, order-independent key for the lane between systems a and b."""
    return frozenset((a, b))


@dataclass
class AiParams:
    """Per-seat tuning for the built-in heuristic AI.

    Defaults mirror the global ``config.AI_*`` constants, so a player left
    untuned behaves exactly as the AI always has. The menu edits these per seat;
    a custom strategy is free to ignore them (see ``ai.STRATEGIES``).
    """

    reserve_fraction: float = config.AI_RESERVE_FRACTION
    reserve_floor: int = config.AI_RESERVE_FLOOR
    expand_margin: float = config.AI_EXPAND_MARGIN
    attack_margin: float = config.AI_ATTACK_MARGIN
    reinforce_margin: int = config.AI_REINFORCE_MARGIN


@dataclass
class Player:
    id: int
    name: str
    color: tuple[int, int, int]
    is_human: bool = False
    is_neutral: bool = False
    alive: bool = True
    # Ships of this player's destroyed in combat, all match long — the attrition
    # half of a result (see `combat.resolve_arrival`, the one place ships die).
    ships_lost: int = 0
    # Which decision function drives this seat (key into ai.STRATEGIES) and its
    # tuning. Only used while the seat is AI-driven; ignored for a live human.
    ai_strategy: str = "heuristic"
    ai_params: AiParams = field(default_factory=AiParams)


@dataclass
class System:
    """A star system (graph node)."""

    id: int
    pos: tuple[float, float]  # world coordinates
    owner_id: int = 0  # 0 == neutral
    ships: int = 0  # current garrison
    production: int = 3  # "turns per ship"; lower is richer
    prod_progress: int = 0  # counts up each turn; emits a ship at >= production
    neighbors: list[int] = field(default_factory=list)


@dataclass
class Lane:
    """A spacelane (graph edge). ``a`` and ``b`` are stored canonically (a < b)."""

    a: int
    b: int
    length_ly: float
    travel_turns: int

    def other(self, sid: int) -> int:
        return self.b if sid == self.a else self.a


@dataclass
class Fleet:
    """A group of ships in transit along a single lane.

    Ships are removed from the source garrison at launch, so a fleet is "off the
    board" until it arrives — which is precisely why fleets on lanes never
    interact with each other. ``route`` is a reserved hook for later multi-hop
    movement; the MVP only ever uses single, adjacent-lane hops.
    """

    owner_id: int
    source_id: int
    dest_id: int
    ships: int
    turns_total: int
    turns_remaining: int
    route: Optional[list[int]] = None

    def progress(self) -> float:
        """Fraction of the journey completed, in [0, 1] — for rendering."""
        if self.turns_total <= 0:
            return 1.0
        return 1.0 - self.turns_remaining / self.turns_total


@dataclass
class Order:
    """A transient instruction to launch a fleet, produced by input or the AI."""

    owner_id: int
    source_id: int
    dest_id: int
    ships: int


@dataclass
class GameState:
    systems: dict[int, System] = field(default_factory=dict)
    lanes: dict[frozenset[int], Lane] = field(default_factory=dict)
    adjacency: dict[int, dict[int, int]] = field(default_factory=dict)  # src -> {nbr: travel_turns}
    fleets: list[Fleet] = field(default_factory=list)
    players: dict[int, Player] = field(default_factory=dict)
    turn: int = 0
    seed: int = 0
    mode: str = "random"
    winner: Optional[int] = None
    rng: random.Random = field(default_factory=random.Random)

    # -- construction ------------------------------------------------------- #
    @classmethod
    def new(cls, seed: int, mode: str = "random") -> "GameState":
        return cls(seed=seed, mode=mode, rng=random.Random(seed))

    def add_lane(self, a: int, b: int, length_ly: float, travel_turns: int) -> None:
        lo, hi = (a, b) if a < b else (b, a)
        self.lanes[lane_key(a, b)] = Lane(lo, hi, length_ly, max(1, travel_turns))

    def rebuild_topology(self) -> None:
        """Recompute ``adjacency`` and each system's ``neighbors`` from ``lanes``."""
        self.adjacency = {sid: {} for sid in self.systems}
        for sys in self.systems.values():
            sys.neighbors = []
        for lane in self.lanes.values():
            self.adjacency[lane.a][lane.b] = lane.travel_turns
            self.adjacency[lane.b][lane.a] = lane.travel_turns
            self.systems[lane.a].neighbors.append(lane.b)
            self.systems[lane.b].neighbors.append(lane.a)

    # -- queries ------------------------------------------------------------ #
    def travel_turns(self, a: int, b: int) -> Optional[int]:
        return self.adjacency.get(a, {}).get(b)

    def are_adjacent(self, a: int, b: int) -> bool:
        return b in self.adjacency.get(a, {})

    def systems_of(self, owner_id: int) -> list[System]:
        return [s for s in self.systems.values() if s.owner_id == owner_id]

    def non_neutral_players(self) -> list[Player]:
        return [p for p in self.players.values() if not p.is_neutral]

    def is_defeated(self, pid: int) -> bool:
        """Has ``pid`` been knocked out — no systems left and nothing in transit?

        Just the readable name for ``Player.alive``, which the engine's win check
        recomputes every turn. Tolerant of a pid that isn't a seat (never defeated),
        so the shell can ask about the human seat without guarding first.
        """
        player = self.players.get(pid)
        return player is not None and not player.alive

    def human(self) -> Optional[Player]:
        for p in self.players.values():
            if p.is_human:
                return p
        return None

    def fleets_incoming(self, dest_id: int) -> list[Fleet]:
        return [f for f in self.fleets if f.dest_id == dest_id]

"""Core data model: plain dataclasses plus small, pure helpers and graph queries.

No game logic and no pygame here. Everything is driven from integer ids so the
state is easy to reason about, serialize, and feed to the AI. Neutral is a real
player with ``id == 0``.
"""

from __future__ import annotations

import heapq
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from . import config


def lane_key(a: int, b: int) -> frozenset[int]:
    """Canonical, order-independent key for the lane between systems a and b."""
    return frozenset((a, b))


def flow_field(state: GameState, allowed: set[int], seeds: set[int],
               by_turns: bool = False) -> dict[int, int]:
    """Multi-source search outward from ``seeds``; returns node -> next hop toward
    the nearest seed. Expansion only ever enters ``allowed``.

    The one graph query the whole game shares: the AI uses it to stream rear ships
    toward the front line (``ai._flow_to_frontier``), and route mode to lay
    forwarding rules toward a destination or a set of rally points
    (``viewstate.Ui.recompute_route``).

    ``by_turns`` picks what "nearest" measures. The default counts **hops** — a
    plain BFS, and what the AI wants, since a frontier is a frontier however long
    the lane to it is. Route mode passes True to measure **travel turns** instead
    (Dijkstra over ``state.travel_turns``), because a supply chain is judged by how
    long ships take to arrive: two hops down two long lanes is a worse conveyor
    than three hops down three short ones. Turns are re-timed for the current turn,
    so a plan laid under ship-speed growth uses the speeds it will actually run at.

    Three properties callers rely on, true of both modes:

    * **``seeds`` need not be in ``allowed``.** Only expansion is restricted, so a
      seed may be a system the caller couldn't otherwise traverse — which is what
      lets route mode aim a supply chain at an enemy system while keeping every
      hop of the path inside its own territory.
    * **A seed never gets a parent**, so the returned map is exactly the nodes
      *other than* the seeds from which one is reachable through ``allowed``.
    * Since a parent edge always steps to a strictly nearer node (lane costs are
      at least 1), following the map from any node in it terminates at a seed —
      the walk can't loop.

    Seeds and neighbours are visited in sorted order so the flow is deterministic:
    where two seeds are equidistant a node keeps the same next hop every call
    instead of flip-flopping, which is what made rear AI ships oscillate.
    """
    if by_turns:
        return _dijkstra_by_turns(state, allowed, seeds)[1]
    parent: dict[int, int] = {}
    seen = set(seeds)
    queue = deque(sorted(seeds))
    while queue:
        cur = queue.popleft()
        for nbr in sorted(state.systems[cur].neighbors):
            if nbr in allowed and nbr not in seen:
                seen.add(nbr)
                parent[nbr] = cur  # move from nbr toward cur (closer to a seed)
                queue.append(nbr)
    return parent


def flow_costs(state: GameState, allowed: set[int], seeds: set[int]) -> dict[int, int]:
    """Travel turns from each node to the nearest of ``seeds`` (seeds themselves 0),
    over the same restricted expansion ``flow_field`` uses.

    The distances behind ``flow_field(by_turns=True)``, for callers that need to
    compare routes rather than just follow one — ``viewstate.Ui._plan_rally``
    balances rally-point load across the systems these costs tie.
    """
    return _dijkstra_by_turns(state, allowed, seeds)[0]


def _dijkstra_by_turns(state: GameState, allowed: set[int],
                       seeds: set[int]) -> tuple[dict[int, int], dict[int, int]]:
    """``(cost to nearest seed, next hop toward it)``, weighted by travel turns.

    Determinism comes from the heap key ``(distance, id)``: nodes settle in one
    fixed order, and a node reached at equal cost by two routes keeps the parent
    that got there first, so an equidistant system doesn't flip its next hop
    between recomputes.
    """
    dist: dict[int, int] = {sid: 0 for sid in seeds}
    parent: dict[int, int] = {}
    settled: set[int] = set()
    heap = [(0, sid) for sid in sorted(seeds)]
    heapq.heapify(heap)
    while heap:
        d, cur = heapq.heappop(heap)
        if cur in settled:
            continue
        settled.add(cur)
        for nbr in sorted(state.systems[cur].neighbors):
            if nbr not in allowed or nbr in settled:
                continue
            step = state.travel_turns(cur, nbr) or 1
            nd = d + step
            if nbr not in dist or nd < dist[nbr]:
                dist[nbr] = nd
                parent[nbr] = cur  # move from nbr toward cur (nearer a seed)
                heapq.heappush(heap, (nd, nbr))
    return dist, parent


@dataclass
class AiParams:
    """Per-seat tuning for the AI.

    Defaults mirror the global ``config.AI_*`` constants, so a player left
    untuned behaves exactly as the AI always has. The menu edits these per seat;
    a custom strategy is free to ignore them (see ``ai.STRATEGIES``).

    Every field but ``aux`` is read only by the built-in heuristic
    (``ai.compute_orders``). ``aux`` is the opposite: the core never interprets it,
    and each strategy is free to define its own meaning (``models/knower.py`` reads
    it as search depth). See ``models/README.md``.
    """

    reserve_fraction: float = config.AI_RESERVE_FRACTION
    reserve_floor: int = config.AI_RESERVE_FLOOR
    expand_margin: float = config.AI_EXPAND_MARGIN
    attack_margin: float = config.AI_ATTACK_MARGIN
    reinforce_margin: int = config.AI_REINFORCE_MARGIN
    aux: float = config.AI_AUX


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
    name: str = ""  # cosmetic star name (see starnames.py); mechanics use ``id``

    @property
    def label(self) -> str:
        """Full display name: ``"Vega (7)"``, or ``"System 7"`` when unnamed."""
        return f"{self.name} ({self.id})" if self.name else f"System {self.id}"

    @property
    def short(self) -> str:
        """Compact display name for tight rows: the star name, else the bare id."""
        return self.name or str(self.id)


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
    # Which parallel track of the lane this fleet flies on — see `free_lane_slot`,
    # which hands one out at launch. Held for the whole flight, and the only
    # cosmetic field here: nothing in the rules reads it, and it is deliberately
    # not on the wire to external bots (`botio`).
    lane_slot: int = 0

    def progress(self) -> float:
        """Fraction of the journey completed, in [0, 1] — for rendering."""
        return self.progress_at(1.0)

    def progress_at(self, t: float) -> float:
        """Progress part-way through this turn's step: where the fleet stood when
        the step began at ``t == 0``, and ``progress()`` at ``t == 1``.

        The single place this arithmetic lives. ``engine._lane_span`` measures an
        in-lane meeting with it and ``render`` draws with it, so a fight happens
        exactly where the triangles are seen to touch.
        """
        if self.turns_total <= 0:
            return 1.0
        return 1.0 - (self.turns_remaining + (1.0 - t)) / self.turns_total


def lane_slot_at(rank: int) -> int:
    """The ``rank``-th track outward from a lane's centre line: 0, -1, +1, -2, +2 …

    Outward from the centre rather than spread across however many fleets there
    are, so a track's position never depends on how many others exist.
    """
    if rank <= 0:
        return 0
    return -((rank + 1) // 2) if rank % 2 else (rank + 1) // 2


def free_lane_slot(fleets: list[Fleet], a: int, b: int) -> int:
    """The lowest unused track on the ``a``-``b`` lane, for a fleet launching now.

    A fleet is *given* a track and keeps it until it leaves the board, which is the
    whole point of storing one: a slot computed from a fleet's rank among whoever
    happens to be on the lane re-packs the moment a lane-mate launches or arrives,
    and every fleet still in transit visibly steps sideways. Since a track is only
    freed by the fleet holding it leaving, two fleets on a lane never share one.

    Both directions draw from the same pool, because fleets running opposite ways
    pass through each other and that is the case the separation exists for.

    The cost of holding a track is that a fleet whose lane-mates have gone keeps
    flying one step off the centre line rather than sliding back onto it — a
    stationary offset instead of a jump, and it heals as soon as the next fleet
    launches into the freed centre.

    Cannot be derived instead of stored: the launch turn is recoverable from
    ``turn - (turns_total - turns_remaining)``, but turn playback applies those two
    at different cues, so a track derived from it would shift mid-animation.
    """
    lane = lane_key(a, b)
    taken = {f.lane_slot for f in fleets
             if lane_key(f.source_id, f.dest_id) == lane}
    for rank in range(len(taken) + 1):   # distinct candidates, so one must be free
        slot = lane_slot_at(rank)
        if slot not in taken:
            return slot
    return 0  # pragma: no cover - unreachable by the count above


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
        """Turns to cross the a-b lane if launched *now*, or None if not adjacent.

        ``Lane.travel_turns`` (and ``adjacency``) hold the mapgen-time value; with
        ship-speed growth switched on this shortens as the game runs, so ask here
        rather than reading the lane directly. Re-times from the lane's real
        ``length_ly`` (``config.travel_turns_at_length``) rather than rescaling
        the already-rounded-up baked figure, so this always matches what's shown
        alongside it on screen (the lane's length and the current speed).
        """
        lane = self.lanes.get(lane_key(a, b))
        if lane is None:
            return None
        return config.travel_turns_at_length(lane.length_ly, self.turn)

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

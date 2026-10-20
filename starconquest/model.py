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


def cycle_nodes(graph: dict[int, list[int]], starts: set[int]) -> set[int]:
    """Every node on a cycle of ``graph`` (node -> successors) that can be reached
    from ``starts``: the members of each strongly connected component of more than
    one node, or of one with an edge to itself (Tarjan, iterative, so a long chain
    of rules can't hit the recursion limit)."""
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    stack: list[int] = []
    on_stack: set[int] = set()
    out: set[int] = set()

    def visit(node: int) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)

    for root in sorted(starts):
        if root in index:
            continue
        visit(root)
        work = [(root, iter(graph.get(root, ())))]
        while work:
            node, succ = work[-1]
            for nxt in succ:
                if nxt not in index:
                    visit(nxt)
                    work.append((nxt, iter(graph.get(nxt, ()))))
                    break
                if nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == node:
                            break
                    if len(comp) > 1 or node in graph.get(node, ()):
                        out.update(comp)
    return out


@dataclass
class AiParams:
    """Per-seat tuning for the AI.

    Defaults mirror the global ``config.AI_*`` constants, so a player left
    untuned behaves exactly as the AI always has. The menu edits these per seat;
    a custom strategy is free to ignore them (see ``ai.STRATEGIES``).

    Every field but ``aux`` is read only by the built-in heuristic
    (``ai.compute_orders``). ``aux`` is the opposite: the core never interprets it,
    and each strategy is free to define its own meaning (``models/knower.py`` reads
    it as its Oracle mode: Off, Predict or Search). See ``models/README.md``.
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
    route: list[int] | None = None
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
class ForwardRule:
    """A system's standing forwarding: hold back ``hold`` ships, then send each lane
    its share of what is left, every turn. ``shares`` maps destination id to a whole
    percent; they total at most 100, and whatever they leave stays home.

    Dict order is the order lanes were added; the share helpers below break ties
    by it, so it is part of the rule rather than an accident of storage.
    """

    shares: dict[int, int] = field(default_factory=dict)
    hold: int = 0

    def total(self) -> int:
        return sum(self.shares.values())


def _apportion(weights: list[int], total: int, start: int = 0) -> list[int]:
    """Whole numbers in proportion to ``weights`` summing to exactly ``total``
    (largest remainder). Equal remainders go to the earliest index counting round
    from ``start``. Integer arithmetic throughout, so no float can tip a tie."""
    wsum = sum(weights)
    n = len(weights)
    if total <= 0 or wsum <= 0:
        return [0] * n
    parts, rems = [], []
    for w in weights:
        q, r = divmod(w * total, wsum)
        parts.append(q)
        rems.append(r)
    order = sorted(range(n), key=lambda i: (-rems[i], (i - start) % n))
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    return parts


def _is_even(shares: dict[int, int]) -> bool:
    """An even split, to within the one percent rounding leaves (34/33/33)."""
    return not shares or max(shares.values()) - min(shares.values()) <= 1


def forward_split(surplus: int, shares: dict[int, int], turn: int) -> dict[int, int]:
    """Ships each lane of a rule sends this turn, out of ``surplus`` (the ships left
    once the hold is kept back). Whatever the shares leave is a share of its own, so
    it takes part in the rounding. A ship that could go either way rotates with
    ``turn``, so a 50/50 split of an odd surplus alternates rather than always
    favouring the first lane."""
    if not shares:
        return {}
    home = -1  # never a system id
    live = [(d, p) for d, p in [*shares.items(), (home, 100 - sum(shares.values()))] if p > 0]
    out = dict.fromkeys(shares, 0)
    if live:
        parts = _apportion([p for _, p in live], max(0, surplus), turn % len(live))
        out.update((d, n) for (d, _), n in zip(live, parts) if d != home)
    return out


def shares_with(shares: dict[int, int], dest: int) -> dict[int, int]:
    """``shares`` with a lane to ``dest`` added: it gets an even cut of what is being
    sent and the others shrink in proportion, so 100 becomes 50/50 becomes 34/33/33,
    and a lone 50% becomes 25/25 with the other half still home. A rule that sends
    nothing at all splits 100% instead, or the new lane would be born idle."""
    if dest in shares:
        return dict(shares)
    if not shares:
        return {dest: 100}
    n = len(shares)
    total = sum(shares.values()) or 100
    weights = [1] * (n + 1) if _is_even(shares) else [s * n for s in shares.values()] + [total]
    return dict(zip([*shares, dest], _apportion(weights, total)))


def shares_without(shares: dict[int, int], dest: int) -> dict[int, int]:
    """``shares`` with the lane to ``dest`` removed and its share handed back to the
    others, so removing a lane undoes adding it: 50/50 becomes 100, 34/33/33 becomes
    50/50."""
    rest = [d for d in shares if d != dest]
    if not rest:
        return {}
    weights = [shares[d] for d in rest]
    if _is_even(shares) or not any(weights):
        weights = [1] * len(rest)
    return dict(zip(rest, _apportion(weights, sum(shares.values()))))


def shares_set(shares: dict[int, int], dest: int, pct: int) -> dict[int, int]:
    """``shares`` with ``dest``'s share moved toward ``pct`` (0-100). Raising it
    takes from what stays home first, then from the other lanes in proportion, so
    there is never a "full" state to back out of; lowering it gives the difference
    back to staying home."""
    if dest not in shares:
        return dict(shares)
    out = dict(shares)
    cur = shares[dest]
    pct = max(0, min(100, pct))
    if pct <= cur:
        out[dest] = pct
        return out
    need = pct - cur
    from_home = min(need, max(0, 100 - sum(shares.values())))
    others = [d for d in shares if d != dest]
    pool = sum(shares[d] for d in others)
    taken = min(need - from_home, pool)
    out[dest] = cur + from_home + taken
    if taken:
        for d, s in zip(others, _apportion([shares[d] for d in others], pool - taken)):
            out[d] = s
    return out


def shares_even(shares: dict[int, int]) -> dict[int, int]:
    """``shares`` split evenly over the same total (100 when it was nothing)."""
    if not shares:
        return {}
    return dict(zip(shares, _apportion([1] * len(shares), sum(shares.values()) or 100)))


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
    winner: int | None = None
    rng: random.Random = field(default_factory=random.Random)
    # Which family of combat dice this board rolls (`engine._Dice`): a shared match
    # stamps its own (`pbp.seat_people`), so playing its seed alone shows nothing
    # of the dice it will roll.
    dice_salt: str = "dice"

    # -- construction ------------------------------------------------------- #
    @classmethod
    def new(cls, seed: int, mode: str = "random") -> GameState:
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
    def travel_turns(self, a: int, b: int) -> int | None:
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

    def human(self) -> Player | None:
        for p in self.players.values():
            if p.is_human:
                return p
        return None

    def humans(self) -> list[Player]:
        """Every seat a person holds, lowest id first.

        ``human()`` answers the single-seat question the shell has always asked
        and stays correct for it; this is the one to ask when more than one seat
        can be a person's (play-by-post, hotseat), where "the" human seat is not
        a well-formed question.
        """
        return [p for _, p in sorted(self.players.items()) if p.is_human]

    def fleets_incoming(self, dest_id: int) -> list[Fleet]:
        return [f for f in self.fleets if f.dest_id == dest_id]

"""A hand-authored map, as a serializable recipe — pure core, no pygame.

``mapgen`` draws a map from a seed; this is the other source: a list of systems
with concrete positions, production, garrisons and owners, plus the lanes between
them as index pairs. It rides on ``Settings.custom_map``, which is the whole
trick — save/load, share links, challenge links, resume, replay and the
leaderboard all carry it with no new plumbing, and nothing downstream has to know
a map was drawn by hand.

Two properties this module exists to guarantee:

**One tolerant gate.** ``from_dict`` is total and never raises: it pads short
rows, clamps out-of-range numbers, drops junk lanes, and returns ``None`` for a
recipe that cannot describe a playable map. ``mapgen.generate_custom`` is the
strict half and asserts. There is exactly one point where untrusted bytes become
a recipe, and past it every value is concrete and in range.

**An idempotent canonical form.** ``challenge_key`` hashes what ``to_dict``
writes, so if a sender committed a form the recipient's ``from_dict`` normalised
differently, the sender's own link would read as edited the moment it was opened
— the same trap ``settings._ai_from_dict`` documents for an int ``aux``.
``to_dict`` therefore emits ``normalised()``, and ``normalised`` is idempotent
(pinned by a test), so both sides of the wire agree by construction.

Star names are deliberately absent. ``mapgen._name_systems`` stamps them from
``state.rng`` last and serializes nothing, so a hand map's names do not exist
until it is built with a concrete seed — the editor shows ids instead. That is
the design, not an omission.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional

from . import config
from .geometry import Point, dist, point_segment_dist, segments_intersect
from .model import GameState

# Wire-format version, stamped into `to_dict` and checked nowhere yet: a reader
# that meets a version it does not know keeps parsing, since every rule below is
# a tolerance rather than a schema. It is here so a future breaking change has
# something to branch on.
FORMAT_VERSION = 1

# A malformed blob could name an absurd number of lanes; a complete graph over
# the node ceiling is the most any real map can hold, so anything past that is
# junk rather than a map to repair.
_MAX_LANES = config.CUSTOM_MAX_NODES * (config.CUSTOM_MAX_NODES - 1) // 2

BLOCK = "block"
WARN = "warn"


@dataclass(frozen=True)
class Problem:
    """One thing wrong with a recipe, addressed to the person drawing it.

    ``code`` is what the editor matches on to offer a one-press fix (the seat gap
    has a *Renumber seats* button); matching on ``text`` would break the moment
    the wording is improved. ``nodes``/``lanes`` are the offenders, so a problem
    row can select and centre what it is complaining about.
    """

    severity: str
    code: str
    text: str
    nodes: tuple[int, ...] = ()
    lanes: tuple[int, ...] = ()

    @property
    def blocks(self) -> bool:
        return self.severity == BLOCK


@dataclass
class MapNode:
    """One hand-placed system. Every value is concrete — nothing here is a
    sentinel resolved later, which is what lets the seed stop shaping a custom
    map at all (it drives combat dice and star names only)."""

    x: int = 0
    y: int = 0
    # Read live through a factory rather than as a plain default: a bare
    # `= config.HOME_PRODUCTION` would snapshot the constant at import, and every
    # module here reads config at call time (the Advanced menu tunes it).
    production: int = field(default_factory=lambda: config.HOME_PRODUCTION)
    ships: int = 0
    owner: int = 0

    @property
    def pos(self) -> Point:
        return (float(self.x), float(self.y))

    def clamped(self) -> "MapNode":
        """A copy with every field forced into range. Idempotent.

        An owner outside ``1..MAX_PLAYERS`` becomes neutral rather than being
        clamped *up* — clamping would invent a seat out of a typo, and a seat is
        the one field that changes how many players the game has.
        """
        size = int(config.WORLD_SIZE)
        owner = self.owner if 0 <= self.owner <= config.MAX_PLAYERS else 0
        return MapNode(
            x=_clamp(self.x, 0, size),
            y=_clamp(self.y, 0, size),
            production=_clamp(self.production, 0, config.CUSTOM_MAX_PRODUCTION),
            ships=_clamp(self.ships, 0, config.CUSTOM_MAX_SHIPS),
            owner=owner,
        )


@dataclass
class CustomMap:
    """A whole authored map: systems in placement order, lanes as index pairs.

    Positions plus index pairs, rather than a live ``GameState``, is a deliberate
    choice the editor depends on: ``Lane.length_ly`` and ``Lane.travel_turns`` are
    *stored* fields, cached again into ``GameState.adjacency`` and shipped to
    external bots as ``base_turns``, so moving a node in a live state means
    recomputing every incident lane and the topology cache — miss one and the
    board lies. Here the lanes follow their nodes for free, and undo is a
    ``copy()`` of plain data.

    The cost is that deleting node *i* shifts every later lane index, which is why
    deletion has exactly one implementation (``without_node``).
    """

    nodes: list[MapNode] = field(default_factory=list)
    lanes: list[tuple[int, int]] = field(default_factory=list)

    # -- shape ------------------------------------------------------------- #
    def seats(self) -> int:
        """How many seats this map plays: the highest owner id holding a system.

        The *highest*, not the count — a map with a gap (seats 1 and 3) is a
        blocker, and reporting 2 there would quietly describe a different game
        than the one drawn.
        """
        return max((n.owner for n in self.nodes), default=0)

    def owner_counts(self) -> dict[int, int]:
        """Systems held per owner id, including neutral (0). Empty seats absent."""
        out: dict[int, int] = {}
        for n in self.nodes:
            out[n.owner] = out.get(n.owner, 0) + 1
        return out

    def copy(self) -> "CustomMap":
        return CustomMap(nodes=[replace(n) for n in self.nodes], lanes=list(self.lanes))

    def neighbours(self, i: int) -> list[int]:
        return [b if a == i else a for a, b in self.lanes if i in (a, b)]

    # -- editing ------------------------------------------------------------ #
    def without_node(self, i: int) -> "CustomMap":
        """A copy with node ``i`` gone and every surviving lane re-indexed.

        The single implementation of deletion. Node identity is positional, so
        dropping one shifts every later index; doing this at more than one call
        site is how a lane ends up pointing at the wrong system.
        """
        if not 0 <= i < len(self.nodes):
            return self.copy()
        nodes = [replace(n) for j, n in enumerate(self.nodes) if j != i]
        lanes = [
            (a - (a > i), b - (b > i))
            for a, b in self.lanes
            if a != i and b != i
        ]
        return CustomMap(nodes=nodes, lanes=lanes)

    def normalised(self) -> "CustomMap":
        """The canonical form of this map: clamped nodes, canonical lanes.

        Lanes come out ordered ``a < b``, de-duplicated in either direction,
        sorted, and with self-lanes and out-of-range indices dropped. **Idempotent**
        — ``normalised(normalised(x)) == normalised(x)`` — which is what the
        setup digest rests on.
        """
        nodes = [n.clamped() for n in self.nodes]
        count = len(nodes)
        seen: set[tuple[int, int]] = set()
        for a, b in self.lanes:
            a, b = int(a), int(b)
            if a == b or not (0 <= a < count and 0 <= b < count):
                continue
            seen.add((min(a, b), max(a, b)))
        return CustomMap(nodes=nodes, lanes=sorted(seen))

    # -- validation --------------------------------------------------------- #
    def problems(self) -> list[Problem]:
        """Everything wrong with this map, worst first — the single validator.

        Two callers: the editor renders this live and gates Play on it, and
        ``from_dict`` refuses a recipe any of it blocks. Reads only constants no
        menu slider writes, so the answer does not depend on which game was set up
        last.
        """
        m = self.normalised()
        out: list[Problem] = []
        out.extend(_size_problems(m))
        out.extend(_seat_problems(m))
        out.extend(_spacing_problems(m))
        out.extend(_lane_problems(m))
        out.extend(_connectivity_problems(m))
        # Worst first: the Play gate quotes `blockers()[0]`, and a warning at the
        # top of the list would bury the thing actually stopping the game.
        out.sort(key=lambda p: 0 if p.blocks else 1)
        return out

    def blockers(self) -> list[Problem]:
        return [p for p in self.problems() if p.blocks]

    def is_playable(self) -> bool:
        return not self.blockers()

    # -- wire form ---------------------------------------------------------- #
    def to_dict(self) -> dict:
        """The compact wire form — positional rows, since this rides in every
        shared URL and a keyed object per node would roughly treble its length.

        Emits ``normalised()``, not whatever is in hand: one canonical form on the
        wire means a sender and a recipient always hash the same bytes (see the
        module docstring).
        """
        m = self.normalised()
        return {
            "v": FORMAT_VERSION,
            "n": [[n.x, n.y, n.production, n.ships, n.owner] for n in m.nodes],
            "l": [[a, b] for a, b in m.lanes],
        }

    @classmethod
    def from_dict(cls, data) -> Optional["CustomMap"]:
        """Parse a wire-form dict, tolerantly. Never raises; ``None`` if unusable.

        Repairs what is local and bounded — a short row, an out-of-range number, a
        junk lane index — and rejects what would mean inventing structure the
        author did not draw. Auto-linking a disconnected graph is cheap and
        deterministic, and that is exactly the problem: it would present a map
        nobody drew as theirs.

        A rejection is not silent even though nothing is raised: ``custom_map``
        stays ``None``, the setup falls back to a generated map, and
        ``challenge_key()`` no longer matches the key the sender stamped — so the
        menu's existing "this setup has been edited" banner fires with nothing
        added here.
        """
        if not isinstance(data, dict):
            return None
        raw_nodes = data.get("n")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            return None
        if len(raw_nodes) > config.CUSTOM_MAX_NODES:
            return None

        nodes: list[MapNode] = []
        for row in raw_nodes:
            node = _node_from_row(row)
            if node is None:
                return None     # a node with no readable position is not repairable
            nodes.append(node)

        raw_lanes = data.get("l")
        lanes: list[tuple[int, int]] = []
        if isinstance(raw_lanes, list):
            if len(raw_lanes) > _MAX_LANES:
                return None
            for row in raw_lanes:
                pair = _lane_from_row(row, len(nodes))
                if pair is not None:
                    lanes.append(pair)      # junk lanes drop; the map survives

        out = cls(nodes=nodes, lanes=lanes).normalised()
        return None if out.blockers() else out


# --------------------------------------------------------------------------- #
# Parsing helpers — every one of these is total
# --------------------------------------------------------------------------- #
def _clamp(v, lo: int, hi: int) -> int:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, n))


def _number(v) -> Optional[float]:
    """``v`` as a float, or None if it is not a number. Booleans are not numbers
    here: ``True`` would read as an x of 1, which is a parse accident rather than
    a coordinate anyone wrote."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _node_from_row(row) -> Optional[MapNode]:
    """One node from a positional row, padding a short one with defaults.

    ``[x, y]`` is a complete node — which is the payoff of positional rows over
    keyed objects: a truncated row is a free tolerance rather than a parse error.
    Non-numeric coordinates are not, since there is no defensible place to put
    that system.
    """
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return None
    x, y = _number(row[0]), _number(row[1])
    if x is None or y is None:
        return None
    out = MapNode(x=int(round(x)), y=int(round(y)))
    for idx, name in ((2, "production"), (3, "ships"), (4, "owner")):
        if len(row) > idx:
            v = _number(row[idx])
            if v is not None:
                setattr(out, name, int(round(v)))
    return out.clamped()


def _lane_from_row(row, count: int) -> Optional[tuple[int, int]]:
    """One canonical lane from a positional row, or None to drop it.

    Dropping rather than repairing is what keeps ``GameState.rebuild_topology``
    from ``KeyError``-ing on an index that names no system.
    """
    if not isinstance(row, (list, tuple)) or len(row) < 2:
        return None
    a, b = _number(row[0]), _number(row[1])
    if a is None or b is None:
        return None
    ai, bi = int(round(a)), int(round(b))
    if ai == bi or not (0 <= ai < count and 0 <= bi < count):
        return None
    return (min(ai, bi), max(ai, bi))


# --------------------------------------------------------------------------- #
# The validator, one rule per function
# --------------------------------------------------------------------------- #
def _size_problems(m: "CustomMap") -> list[Problem]:
    if not m.nodes:
        return [Problem(BLOCK, "empty", "Place some systems to build a map.")]
    if len(m.nodes) > config.CUSTOM_MAX_NODES:
        return [Problem(
            BLOCK, "too_many",
            f"{len(m.nodes)} systems — the limit is {config.CUSTOM_MAX_NODES}.",
        )]
    return []


def _seat_problems(m: "CustomMap") -> list[Problem]:
    """Seats must run 1..N with no gaps: the AI tab lists seats ``2..players``,
    ``challenge_keys`` blanks seats past ``players``, and ``GameState.players``
    needs a ``Player`` per owner id. A gap gets a message and a one-press fix
    rather than a silent renumber — the colour is part of what an author
    intended."""
    counts = m.owner_counts()
    held = sorted(pid for pid in counts if pid > 0)
    if len(held) < config.MIN_PLAYERS:
        return [Problem(
            BLOCK, "too_few_seats",
            f"Give at least {config.MIN_PLAYERS} seats a starting system.",
        )]
    missing = [pid for pid in range(1, held[-1]) if pid not in counts]
    if missing:
        gaps = ", ".join(str(p) for p in missing)
        word = "Seat" if len(missing) == 1 else "Seats"
        return [Problem(
            BLOCK, "seat_gap",
            f"{word} {gaps} hold no systems. Seats must run 1 to N with no gaps.",
        )]
    return []


def _spacing_problems(m: "CustomMap") -> list[Problem]:
    """Systems drawn closer than the clearance overlap on screen. The same figure
    governs node-vs-lane below, and sits under the tightest pair ``mapgen`` itself
    produces, so loading a generated map never lights up."""
    sep = config.CUSTOM_MIN_NODE_SEP_FRAC * config.WORLD_SIZE
    out: list[Problem] = []
    for i in range(len(m.nodes)):
        for j in range(i + 1, len(m.nodes)):
            if dist(m.nodes[i].pos, m.nodes[j].pos) < sep:
                out.append(Problem(
                    BLOCK, "too_close",
                    f"Systems #{i} and #{j} are too close together.",
                    nodes=(i, j),
                ))
    return out


def _lane_problems(m: "CustomMap") -> list[Problem]:
    """Crossing lanes and lanes running under a third system — the two rules
    ``mapgen`` already enforces when it generates (``_crosses_any`` and
    ``_grazes_other_node``), applied to a hand-drawn graph.

    They differ in severity, deliberately. A lane hidden *under* a system
    misrepresents the graph — you read the map wrong — so it blocks. Two lanes
    crossing in open space only looks busier: the engine, the AI and every bot
    are indifferent to planarity, so it is a **warning**. The creator's Planar
    toggle is what keeps the common path clean, by refusing to *draw* one; an
    imported or deliberately-drawn crossing map still plays.
    """
    pos = [n.pos for n in m.nodes]
    clearance = config.LANE_NODE_CLEARANCE_FRAC * config.WORLD_SIZE
    out: list[Problem] = []

    for li, (a, b) in enumerate(m.lanes):
        for lj in range(li + 1, len(m.lanes)):
            c, d = m.lanes[lj]
            if a in (c, d) or b in (c, d):
                continue    # lanes sharing a system are incident, not crossing
            if segments_intersect(pos[a], pos[b], pos[c], pos[d]):
                out.append(Problem(
                    WARN, "crossing",
                    f"Lanes #{a}-#{b} and #{c}-#{d} cross.",
                    nodes=(a, b, c, d), lanes=(li, lj),
                ))

    for li, (a, b) in enumerate(m.lanes):
        for c, p in enumerate(pos):
            if c in (a, b):
                continue
            if point_segment_dist(p, pos[a], pos[b]) < clearance:
                out.append(Problem(
                    BLOCK, "graze",
                    f"The lane #{a}-#{b} runs under system #{c}.",
                    nodes=(a, b, c), lanes=(li,),
                ))
    return out


def _connectivity_problems(m: "CustomMap") -> list[Problem]:
    """Every system must be reachable, or part of the map cannot be taken and the
    win condition can never be met."""
    if not m.nodes:
        return []
    adj: dict[int, list[int]] = {i: [] for i in range(len(m.nodes))}
    for a, b in m.lanes:
        adj[a].append(b)
        adj[b].append(a)
    seen = {0}
    stack = [0]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    if len(seen) == len(m.nodes):
        return []
    stranded = tuple(i for i in range(len(m.nodes)) if i not in seen)
    return [Problem(
        BLOCK, "disconnected",
        f"{len(stranded)} system(s) can't be reached — connect them with lanes.",
        nodes=stranded,
    )]


# --------------------------------------------------------------------------- #
# The other direction: a built board back to a recipe
# --------------------------------------------------------------------------- #
def from_state(state: GameState) -> CustomMap:
    """The recipe behind a built ``GameState`` — how "start from a generated map"
    works, and the inverse of ``mapgen.generate_custom``.

    System ids are dense and start at 0 in every board this game builds, but the
    mapping is built explicitly rather than assumed: a lane naming an id this does
    not know would otherwise index the wrong node silently.
    """
    order = sorted(state.systems)
    index = {sid: i for i, sid in enumerate(order)}
    nodes = [
        MapNode(
            x=int(round(state.systems[sid].pos[0])),
            y=int(round(state.systems[sid].pos[1])),
            production=state.systems[sid].production,
            ships=state.systems[sid].ships,
            owner=state.systems[sid].owner_id,
        )
        for sid in order
    ]
    lanes: list[tuple[int, int]] = []
    for key in state.lanes:
        a, b = sorted(key)
        if a in index and b in index:
            lanes.append((index[a], index[b]))
    return CustomMap(nodes=nodes, lanes=lanes).normalised()

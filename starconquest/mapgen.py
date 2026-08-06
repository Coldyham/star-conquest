"""Procedural map generation -> a fully populated, connected GameState.

RANDOM mode: jittered-grid node placement, a light repulsion relax, a Euclidean
minimum spanning tree for guaranteed connectivity (Euclidean MSTs are planar, so
its edges never cross), then a few extra short edges added with crossing
rejection to create loops without clutter.

SYMMETRIC mode is added at a later milestone.

All randomness flows through ``state.rng`` so a seed fully reproduces a map.
"""

from __future__ import annotations

import math
from collections import deque

from . import config
from .geometry import Point, bounds_of, dist, segments_intersect
from .model import GameState, Player, System


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def generate(
    seed: int,
    mode: str = "random",
    num_nodes: int = config.DEFAULT_NODES,
    num_players: int = config.DEFAULT_PLAYERS,
) -> GameState:
    if mode == "symmetric":
        return generate_symmetric(seed, num_nodes, num_players)
    return generate_random(seed, num_nodes, num_players)


def generate_random(
    seed: int,
    num_nodes: int = config.DEFAULT_NODES,
    num_players: int = config.DEFAULT_PLAYERS,
) -> GameState:
    state = GameState.new(seed, mode="random")
    num_players = max(2, num_players)
    num_nodes = max(num_players + 3, num_nodes)

    positions = _place_nodes(state, num_nodes)
    _relax(positions, state)

    for i, pos in enumerate(positions):
        state.systems[i] = System(id=i, pos=pos)

    _build_edges(state, positions)
    _assign_players_and_starts(state, num_players)
    _assign_production_and_garrisons(state)

    state.rebuild_topology()
    assert is_connected(state), "generated map is not connected"
    return state


def generate_symmetric(
    seed: int,
    num_nodes: int = config.DEFAULT_NODES,
    num_players: int = config.DEFAULT_PLAYERS,
) -> GameState:
    """Rotationally symmetric map: one base sector rotated N times about the centre.

    Nodes, edges, production and garrisons are generated once in a single angular
    sector and then rotated by ``k * 2*pi/N`` for each player, so every player
    faces a topologically and numerically identical position. A shared central
    node ties the sectors together and is the contested prize.
    """
    state = GameState.new(seed, mode="symmetric")
    num_players = max(2, num_players)
    per_player = max(2, round((num_nodes - 1) / num_players))

    cx = cy = config.WORLD_SIZE / 2.0
    r_outer = config.WORLD_SIZE / 2.0 - config.WORLD_MARGIN
    r_inner = 0.28 * r_outer
    sector = 2.0 * math.pi / num_players

    # --- base sector seeds (index 0 is the homeworld) -------------------- #
    base = _base_sector_seeds(state, per_player, sector, r_inner, r_outer, cx, cy)
    base_positions = [p for (p, _r) in base]
    innermost = min(range(per_player), key=lambda i: base[i][1])  # nearest the centre

    # base production/garrison, replicated identically to every sector
    base_prod, base_ships = _base_sector_values(state, per_player)

    _make_players(state, num_players)
    center_id = num_players * per_player

    # --- replicate nodes by rotation ------------------------------------- #
    for k in range(num_players):
        theta = k * sector
        for idx in range(per_player):
            nid = k * per_player + idx
            pos = _rotate(base_positions[idx], cx, cy, theta)
            is_home = idx == 0
            state.systems[nid] = System(
                id=nid,
                pos=pos,
                owner_id=(k + 1) if is_home else 0,
                ships=base_ships[idx],
                production=base_prod[idx],
            )

    # contested central system
    state.systems[center_id] = System(
        id=center_id,
        pos=(cx, cy),
        owner_id=0,
        production=min(config.PRODUCTION_WEIGHTS),  # richest
        ships=config.GARRISON_BASE + round(config.GARRISON_K / min(config.PRODUCTION_WEIGHTS)) + 2,
    )

    # --- replicate edges by rotation ------------------------------------- #
    base_edges = _planar_edges(base_positions)
    for k in range(num_players):
        base_off = k * per_player
        for i, j in base_edges:
            _add_lane_between(state, base_off + i, base_off + j)
        _add_lane_between(state, base_off + innermost, center_id)  # seam to centre

    state.rebuild_topology()
    assert is_connected(state), "symmetric map is not connected"
    return state


def _base_sector_seeds(state, per_player, sector, r_inner, r_outer, cx, cy):
    """Place base-sector nodes in polar coords; index 0 is the homeworld."""
    margin = sector * 0.18  # keep nodes off the seam lines
    seeds: list[tuple[Point, float]] = []

    # homeworld: mid-sector, near the periphery
    r_home = 0.8 * r_outer
    a_home = sector / 2.0
    seeds.append(((cx + r_home * math.cos(a_home), cy + r_home * math.sin(a_home)), r_home))

    # remaining neutral seeds, spaced out with a light rejection sampler
    for _ in range(per_player - 1):
        best = None
        for _try in range(24):
            r = state.rng.uniform(r_inner, r_outer)
            a = state.rng.uniform(margin, sector - margin)
            p = (cx + r * math.cos(a), cy + r * math.sin(a))
            nearest = min((dist(p, q) for q, _ in seeds), default=1e9)
            if best is None or nearest > best[1]:
                best = (p, nearest, r)
        seeds.append((best[0], best[2]))
    return seeds


def _base_sector_values(state, per_player) -> tuple[list[int], list[int]]:
    """Production & garrison for each base-sector node, replicated to all players."""
    values = list(config.PRODUCTION_WEIGHTS.keys())
    weights = list(config.PRODUCTION_WEIGHTS.values())
    prod = [config.HOME_PRODUCTION]
    ships = [config.HOME_START_SHIPS]
    for _ in range(per_player - 1):
        p = state.rng.choices(values, weights=weights, k=1)[0]
        prod.append(p)
        ships.append(config.GARRISON_BASE + round(config.GARRISON_K / p) + state.rng.randint(0, config.GARRISON_JITTER))
    return prod, ships


def _rotate(p: Point, cx: float, cy: float, theta: float) -> Point:
    dx, dy = p[0] - cx, p[1] - cy
    ct, st = math.cos(theta), math.sin(theta)
    return (cx + dx * ct - dy * st, cy + dx * st + dy * ct)


def _add_lane_between(state: GameState, a: int, b: int) -> None:
    d = dist(state.systems[a].pos, state.systems[b].pos)
    length_ly = d * config.LY_PER_WORLD_UNIT
    turns = max(1, math.ceil(length_ly / config.SHIP_LY_PER_TURN))
    state.add_lane(a, b, round(length_ly, 1), turns)


# --------------------------------------------------------------------------- #
# Node placement
# --------------------------------------------------------------------------- #
def _play_bounds() -> tuple[float, float, float, float]:
    m = config.WORLD_MARGIN
    return (m, m, config.WORLD_SIZE - m, config.WORLD_SIZE - m)


def _place_nodes(state: GameState, n: int) -> list[Point]:
    """Jittered grid: one node per randomly chosen cell -> even, readable spread."""
    lo_x, lo_y, hi_x, hi_y = _play_bounds()
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    cell_w = (hi_x - lo_x) / cols
    cell_h = (hi_y - lo_y) / rows

    cells = [(c, r) for r in range(rows) for c in range(cols)]
    state.rng.shuffle(cells)
    chosen = cells[:n]

    jitter = config.NODE_JITTER  # spread within the cell; higher -> more length variety
    points: list[Point] = []
    for c, r in chosen:
        cx = lo_x + (c + 0.5) * cell_w
        cy = lo_y + (r + 0.5) * cell_h
        dx = state.rng.uniform(-0.5, 0.5) * cell_w * jitter
        dy = state.rng.uniform(-0.5, 0.5) * cell_h * jitter
        points.append((cx + dx, cy + dy))
    return points


def _relax(points: list[Point], state: GameState) -> None:
    """A few passes of nearest-neighbour repulsion to even out clumps."""
    if config.LLOYD_PASSES <= 0 or len(points) < 2:
        return
    lo_x, lo_y, hi_x, hi_y = _play_bounds()
    # target separation ~ average of two cell dimensions
    area = (hi_x - lo_x) * (hi_y - lo_y)
    # only separate genuinely-close nodes so natural length variety survives
    min_sep = config.RELAX_MIN_SEP_FRAC * math.sqrt(area / len(points))
    for _ in range(config.LLOYD_PASSES):
        for i in range(len(points)):
            fx = fy = 0.0
            for j in range(len(points)):
                if i == j:
                    continue
                dx = points[i][0] - points[j][0]
                dy = points[i][1] - points[j][1]
                d = math.hypot(dx, dy) or 1e-6
                if d < min_sep:
                    push = (min_sep - d) / d * 0.5
                    fx += dx * push
                    fy += dy * push
            nx = min(hi_x, max(lo_x, points[i][0] + fx))
            ny = min(hi_y, max(lo_y, points[i][1] + fy))
            points[i] = (nx, ny)


# --------------------------------------------------------------------------- #
# Edges: Euclidean MST (connectivity, planar) + planar extra edges (loops)
# --------------------------------------------------------------------------- #
class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> bool:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.parent[ra] = rb
        return True


def _add_lane(state: GameState, positions: list[Point], a: int, b: int) -> None:
    d = dist(positions[a], positions[b])
    length_ly = d * config.LY_PER_WORLD_UNIT
    turns = max(1, math.ceil(length_ly / config.SHIP_LY_PER_TURN))
    state.add_lane(a, b, round(length_ly, 1), turns)


def _crosses_any(positions: list[Point], accepted: list[tuple[int, int]], a: int, b: int) -> bool:
    for c, d in accepted:
        if a in (c, d) or b in (c, d):
            continue  # edges sharing a node are incident, not crossing
        if segments_intersect(positions[a], positions[b], positions[c], positions[d]):
            return True
    return False


def _planar_edges(positions: list[Point]) -> list[tuple[int, int]]:
    """Euclidean MST (connected, crossing-free) + a few short extras for loops.

    Returns index pairs into ``positions``; the extras reject any edge that would
    cross an already-accepted one, keeping the graph planar-ish.
    """
    n = len(positions)
    pairs: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((dist(positions[i], positions[j]), i, j))
    pairs.sort()

    uf = _UnionFind(n)
    accepted: list[tuple[int, int]] = []
    for _, i, j in pairs:  # MST spans every node
        if uf.union(i, j):
            accepted.append((i, j))
    mst_count = len(accepted)

    max_len = config.MAX_EDGE_LENGTH_FRAC * config.WORLD_SIZE
    extra_budget = int(round(config.EXTRA_EDGE_FRACTION * mst_count))
    added = 0
    accepted_set = set(accepted)
    for d, i, j in pairs:
        if added >= extra_budget:
            break
        if (i, j) in accepted_set or d > max_len:
            continue
        if _crosses_any(positions, accepted, i, j):
            continue
        accepted.append((i, j))
        accepted_set.add((i, j))
        added += 1
    return accepted


def _build_edges(state: GameState, positions: list[Point]) -> None:
    for i, j in _planar_edges(positions):
        _add_lane(state, positions, i, j)


# --------------------------------------------------------------------------- #
# Players, starting positions, production & garrisons
# --------------------------------------------------------------------------- #
def _make_players(state: GameState, num_players: int) -> None:
    state.players[0] = Player(0, config.player_name(0), config.player_color(0), is_neutral=True)
    for pid in range(1, num_players + 1):
        state.players[pid] = Player(
            pid,
            config.player_name(pid),
            config.player_color(pid),
            is_human=(pid == 1),
        )


def _peripheral_starts(state: GameState, count: int) -> list[int]:
    """Homeworlds evenly spread around the map's rim, one per angular sector.

    Directions are equally spaced by angle (with a random overall rotation for
    variety), and for each direction we take the node furthest that way from the
    map centre. Every player thus gets a peripheral 'corner' start and nobody is
    boxed into the contested middle, so no seat is systematically disadvantaged.
    """
    ids = list(state.systems)
    cx = sum(state.systems[i].pos[0] for i in ids) / len(ids)
    cy = sum(state.systems[i].pos[1] for i in ids) / len(ids)
    base = state.rng.uniform(0.0, 2.0 * math.pi)

    chosen: list[int] = []
    used: set[int] = set()
    for i in range(count):
        ang = base + 2.0 * math.pi * i / count
        dx, dy = math.cos(ang), math.sin(ang)
        best_id, best_score = None, float("-inf")
        for sid in ids:
            if sid in used:
                continue
            px, py = state.systems[sid].pos
            score = (px - cx) * dx + (py - cy) * dy  # projection onto the target direction
            if score > best_score:
                best_score, best_id = score, sid
        used.add(best_id)
        chosen.append(best_id)
    return chosen


def _assign_players_and_starts(state: GameState, num_players: int) -> None:
    _make_players(state, num_players)
    homes = _peripheral_starts(state, num_players)
    for pid, sid in enumerate(homes, start=1):
        sys = state.systems[sid]
        sys.owner_id = pid
        sys.production = config.HOME_PRODUCTION
        sys.ships = config.HOME_START_SHIPS
        sys.prod_progress = 0


def _assign_production_and_garrisons(state: GameState) -> None:
    values = list(config.PRODUCTION_WEIGHTS.keys())
    weights = list(config.PRODUCTION_WEIGHTS.values())
    for sys in state.systems.values():
        if sys.owner_id != 0:
            continue  # homeworlds already configured
        sys.production = state.rng.choices(values, weights=weights, k=1)[0]
        sys.ships = config.GARRISON_BASE + round(config.GARRISON_K / sys.production) + state.rng.randint(0, config.GARRISON_JITTER)


# --------------------------------------------------------------------------- #
# Utilities
# --------------------------------------------------------------------------- #
def is_connected(state: GameState) -> bool:
    if not state.systems:
        return True
    start = next(iter(state.systems))
    seen = {start}
    queue = deque([start])
    while queue:
        cur = queue.popleft()
        for nbr in state.adjacency.get(cur, {}):
            if nbr not in seen:
                seen.add(nbr)
                queue.append(nbr)
    return len(seen) == len(state.systems)


def map_bounds(state: GameState) -> tuple[float, float, float, float]:
    return bounds_of([s.pos for s in state.systems.values()])

"""Pure geometry helpers with no game knowledge.

Two jobs: (1) segment maths used by the map generator to keep the graph
planar-ish, and (2) the world->screen fit transform used by the renderer and
input hit-testing. Nothing here imports pygame.
"""

from __future__ import annotations

import math

Point = tuple[float, float]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _orient(a: Point, b: Point, c: Point) -> float:
    """>0 if c is left of a->b, <0 if right, 0 if collinear (twice signed area)."""
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Point, b: Point, c: Point) -> bool:
    """Assuming c is collinear with a-b, is c within the segment's bounding box?"""
    return (
        min(a[0], b[0]) <= c[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= c[1] <= max(a[1], b[1])
    )


def segments_intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    """True if closed segments p1p2 and p3p4 intersect (including touching).

    The map generator only calls this for edges that do *not* share a node, so
    any intersection at all is a crossing we want to reject.
    """
    d1 = _orient(p3, p4, p1)
    d2 = _orient(p3, p4, p2)
    d3 = _orient(p1, p2, p3)
    d4 = _orient(p1, p2, p4)

    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)) and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0:
        return True

    if d1 == 0 and _on_segment(p3, p4, p1):
        return True
    if d2 == 0 and _on_segment(p3, p4, p2):
        return True
    if d3 == 0 and _on_segment(p1, p2, p3):
        return True
    if d4 == 0 and _on_segment(p1, p2, p4):
        return True
    return False


class WorldView:
    """Fits a world bounding box into a screen rectangle, preserving aspect.

    Adding pan/zoom later is just an extra offset + scale here; no model or
    render changes are needed elsewhere.
    """

    def __init__(
        self,
        world_bounds: tuple[float, float, float, float],
        screen_rect: tuple[float, float, float, float],
        padding: float = 40.0,
    ) -> None:
        wx0, wy0, wx1, wy1 = world_bounds
        sx, sy, sw, sh = screen_rect
        ww = max(1e-6, wx1 - wx0)
        wh = max(1e-6, wy1 - wy0)
        avail_w = max(1.0, sw - 2 * padding)
        avail_h = max(1.0, sh - 2 * padding)
        self.scale = min(avail_w / ww, avail_h / wh)
        # centre the scaled world inside the screen rect
        self.off_x = sx + padding + (avail_w - ww * self.scale) / 2 - wx0 * self.scale
        self.off_y = sy + padding + (avail_h - wh * self.scale) / 2 - wy0 * self.scale

    def to_screen(self, pos: Point) -> tuple[int, int]:
        return (
            int(round(pos[0] * self.scale + self.off_x)),
            int(round(pos[1] * self.scale + self.off_y)),
        )

    def to_world(self, screen_pos: Point) -> Point:
        return (
            (screen_pos[0] - self.off_x) / self.scale,
            (screen_pos[1] - self.off_y) / self.scale,
        )


def bounds_of(points: list[Point]) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box of a set of points (min_x, min_y, max_x, max_y)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))

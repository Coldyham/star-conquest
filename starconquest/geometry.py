"""Pure geometry helpers with no game knowledge.

Two jobs: (1) segment maths used by the map generator to keep the graph
planar-ish, and (2) the world->screen fit transform used by the renderer and
input hit-testing. Nothing here imports pygame.
"""

from __future__ import annotations

import math

from . import config

Point = tuple[float, float]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def lerp(a: Point, b: Point, t: float) -> Point:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def point_segment_dist(p: Point, a: Point, b: Point) -> float:
    """Shortest distance from point ``p`` to the closed segment ``a``-``b``.

    Used for hit-testing a click against a drawn lane/order arrow.
    """
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:            # degenerate segment: fall back to point distance
        return dist(p, a)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / denom
    t = max(0.0, min(1.0, t))     # clamp to the segment
    return dist(p, (ax + t * dx, ay + t * dy))


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
    """Fits a world bounding box into a screen rectangle, preserving aspect,
    with a user-adjustable camera (zoom + pan) on top of that fit.

    ``zoom == 1.0`` and no pan reproduces the original fit-to-viewport view
    exactly. ``to_screen``/``to_world`` are the only transform other modules
    should ever touch; ``scale``/``off_x``/``off_y`` are derived from the fit
    plus the current zoom/pan and shouldn't be written to directly — use
    ``zoom_at``/``pan``/``reset`` instead.
    """

    def __init__(
        self,
        world_bounds: tuple[float, float, float, float],
        screen_rect: tuple[float, float, float, float],
        padding: float = 40.0,
    ) -> None:
        self._world_bounds = world_bounds
        self._screen_rect = screen_rect
        self._padding = padding
        wx0, wy0, wx1, wy1 = world_bounds
        sx, sy, sw, sh = screen_rect
        ww = max(1e-6, wx1 - wx0)
        wh = max(1e-6, wy1 - wy0)
        avail_w = max(1.0, sw - 2 * padding)
        avail_h = max(1.0, sh - 2 * padding)
        self._fit_scale = min(avail_w / ww, avail_h / wh)
        self.zoom = 1.0
        # centre the scaled world inside the screen rect
        self.off_x = self._center_axis(self.scale, sx, sw, wx0, wx1)
        self.off_y = self._center_axis(self.scale, sy, sh, wy0, wy1)

    @property
    def scale(self) -> float:
        return self._fit_scale * self.zoom

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

    def zoom_at(self, screen_pos: Point, factor: float) -> None:
        """Multiply the current zoom by ``factor`` (>1 in, <1 out), keeping the
        world point currently under ``screen_pos`` fixed on screen. Clamped to
        ``config.ZOOM_MIN``/``ZOOM_MAX``, then re-clamped to keep the map on
        screen — at the zoomed-all-the-way-out extreme that pan-clamp
        intentionally wins over "keep the cursor point fixed" and recentres
        instead, which is expected, not a bug."""
        old_world = self.to_world(screen_pos)
        self.zoom = max(config.ZOOM_MIN, min(config.ZOOM_MAX, self.zoom * factor))
        new_scale = self.scale
        self.off_x = screen_pos[0] - old_world[0] * new_scale
        self.off_y = screen_pos[1] - old_world[1] * new_scale
        self._clamp()

    def pan(self, dx: float, dy: float) -> None:
        """Translate the view by ``(dx, dy)`` screen pixels, then clamp so the
        map can never be dragged fully off the viewport."""
        self.off_x += dx
        self.off_y += dy
        self._clamp()

    def reset(self) -> None:
        """Back to zoom == 1.0, centred exactly as at construction."""
        self.zoom = 1.0
        self._clamp()

    def _center_axis(self, scale: float, s0: float, slen: float, w0: float, w1: float) -> float:
        """The centred offset for one axis at ``scale`` — the same formula the
        constructor uses, generalised so ``reset``/``_clamp`` can reuse it."""
        avail = max(1.0, slen - 2 * self._padding)
        wlen = max(1e-6, w1 - w0)
        return s0 + self._padding + (avail - wlen * scale) / 2 - w0 * scale

    def _clamp(self) -> None:
        """Keep the map on screen: centre an axis whose zoomed content is
        smaller than the viewport (recreating the constructor's fit-centring at
        zoom == 1.0), else clamp that axis's offset so a content edge can never
        leave a visible gap."""
        scale = self.scale
        wx0, wy0, wx1, wy1 = self._world_bounds
        sx, sy, sw, sh = self._screen_rect
        ww, wh = max(1e-6, wx1 - wx0), max(1e-6, wy1 - wy0)

        if ww * scale <= sw:
            self.off_x = self._center_axis(scale, sx, sw, wx0, wx1)
        else:
            lo, hi = sx + sw - wx1 * scale, sx - wx0 * scale
            self.off_x = max(lo, min(hi, self.off_x))

        if wh * scale <= sh:
            self.off_y = self._center_axis(scale, sy, sh, wy0, wy1)
        else:
            lo, hi = sy + sh - wy1 * scale, sy - wy0 * scale
            self.off_y = max(lo, min(hi, self.off_y))


def bounds_of(points: list[Point]) -> tuple[float, float, float, float]:
    """Axis-aligned bounding box of a set of points (min_x, min_y, max_x, max_y)."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))

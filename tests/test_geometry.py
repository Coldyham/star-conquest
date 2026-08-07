"""Tests for WorldView's fit transform and its zoom/pan camera on top of it.

Pure and pygame-free, like the rest of geometry.py.
"""

from __future__ import annotations

from starconquest import config
from starconquest.geometry import WorldView

BOUNDS = (0.0, 0.0, 1000.0, 1000.0)
SCREEN = (0.0, 0.0, 800.0, 600.0)


def _view() -> WorldView:
    return WorldView(BOUNDS, SCREEN, padding=40.0)


def test_zoom_at_keeps_anchor_point_fixed():
    v = _view()
    anchor = (300, 250)
    v.zoom_at(anchor, 2.0)
    got = v.to_screen(v.to_world(anchor))
    assert abs(got[0] - anchor[0]) <= 1
    assert abs(got[1] - anchor[1]) <= 1


def test_zoom_clamped_at_min():
    v = _view()
    base_scale = v.scale
    for _ in range(20):
        v.zoom_at((400, 300), 0.1)
    assert v.zoom == config.ZOOM_MIN
    assert v.scale == base_scale
    # idempotent once at the floor
    v.zoom_at((400, 300), 0.5)
    assert v.zoom == config.ZOOM_MIN


def test_zoom_clamped_at_max():
    v = _view()
    for _ in range(20):
        v.zoom_at((400, 300), 10.0)
    assert v.zoom == config.ZOOM_MAX
    v.zoom_at((400, 300), 5.0)
    assert v.zoom == config.ZOOM_MAX


def test_pan_clamped_keeps_content_onscreen():
    """Content can never be dragged past a `pan_padding`-sized gap from either
    edge — the slack that keeps a boundary system's centre (and its fixed-
    pixel-radius circle, drawn independently of the world bounds) clear of
    the clip edge instead of sliced flush against it."""
    v = _view()
    v.zoom_at((400, 300), 3.0)
    pad = v._pan_padding

    v.pan(-100000, -100000)
    sx, sy, sw, sh = SCREEN
    x0, y0 = v.to_screen((BOUNDS[0], BOUNDS[1]))
    x1, y1 = v.to_screen((BOUNDS[2], BOUNDS[3]))
    assert x0 <= sx + pad and x1 >= sx + sw - pad
    assert y0 <= sy + pad and y1 >= sy + sh - pad

    v.pan(100000, 100000)
    x0, y0 = v.to_screen((BOUNDS[0], BOUNDS[1]))
    x1, y1 = v.to_screen((BOUNDS[2], BOUNDS[3]))
    assert x0 <= sx + pad and x1 >= sx + sw - pad
    assert y0 <= sy + pad and y1 >= sy + sh - pad


def test_pan_clamp_leaves_exactly_padding_gap_at_extreme():
    """The world-bounds corner itself (BOUNDS[0], BOUNDS[1] — where a boundary
    system typically sits) must land `pan_padding` px *inside* the clip edge at
    the clamped extreme, not flush against it — the concrete case the padding
    buffer exists to prevent."""
    v = _view()
    v.zoom_at((400, 300), 3.0)
    v.pan(100000, 100000)  # drag as far as it goes, revealing the left/top edge
    sx, sy, _, _ = SCREEN
    x0, y0 = v.to_screen((BOUNDS[0], BOUNDS[1]))
    assert abs(x0 - (sx + v._pan_padding)) < 1e-6
    assert abs(y0 - (sy + v._pan_padding)) < 1e-6


def test_boundary_never_crosses_the_margin_at_any_zoom():
    """At *every* zoom, panning to an extreme leaves the revealed boundary edge at
    least the fit padding inside the viewport.

    The regression guard for a sliced-circle bug that only showed at some zoom
    levels: `_clamp` centred an axis whenever its content fitted the bare viewport,
    but `_center_axis` divides up the *padded* span, so between those two widths its
    "centred" offset had a negative share to spread and pushed the outermost system
    back out through the margin — and at a big enough zoom, off screen entirely.
    """
    sx, sy, sw, sh = SCREEN
    fit = 40.0
    for zoom in (1.0, 1.05, 1.1, 1.2, 1.35, 1.5, 1.75, 2.0, 2.5, 3.0, 4.5, 6.0):
        for dx, dy in ((100000, 100000), (-100000, -100000),
                       (100000, -100000), (-100000, 100000), (0, 0)):
            v = WorldView(BOUNDS, SCREEN, padding=fit, pan_padding=110.0)
            v.zoom_at((400, 300), zoom)
            v.pan(dx, dy)
            x0, y0 = v.to_screen((BOUNDS[0], BOUNDS[1]))
            x1, y1 = v.to_screen((BOUNDS[2], BOUNDS[3]))
            where = f"zoom {zoom}, pan {dx},{dy}"
            if dx > 0:      # dragged right: the left edge is the one on show
                assert x0 >= sx + fit - 1e-6, f"left boundary inside the margin ({where})"
            if dx < 0:
                assert x1 <= sx + sw - fit + 1e-6, f"right boundary inside ({where})"
            if dy > 0:
                assert y0 >= sy + fit - 1e-6, f"top boundary inside ({where})"
            if dy < 0:
                assert y1 <= sy + sh - fit + 1e-6, f"bottom boundary inside ({where})"


def test_pan_padding_never_below_fit_padding():
    """A pan margin tighter than the fit's would let a system be dragged closer to
    the edge than the resting view puts it, and breaks `_clamp`'s bound ordering."""
    v = WorldView(BOUNDS, SCREEN, padding=80.0, pan_padding=10.0)
    assert v._pan_padding == 80.0


def test_larger_pan_padding_leaves_the_fit_view_alone():
    """Raising only the pan margin must not shrink or shift the zoom-1 view — that
    is the whole reason the two paddings are separate knobs."""
    tight = WorldView(BOUNDS, SCREEN, padding=40.0)
    roomy = WorldView(BOUNDS, SCREEN, padding=40.0, pan_padding=200.0)
    assert roomy.scale == tight.scale
    assert (roomy.off_x, roomy.off_y) == (tight.off_x, tight.off_y)
    roomy.zoom_at((400, 300), 3.0)
    roomy.reset()
    assert (round(roomy.off_x, 6), round(roomy.off_y, 6)) == (
        round(tight.off_x, 6), round(tight.off_y, 6),
    )


def test_pan_at_fit_zoom_is_a_noop():
    v = _view()
    off_x, off_y = v.off_x, v.off_y
    v.pan(500, 500)
    assert (v.off_x, v.off_y) == (off_x, off_y)


def test_reset_restores_fit_view():
    fresh = _view()
    v = _view()
    v.zoom_at((250, 500), 2.5)
    v.pan(37, -19)
    v.reset()
    assert v.zoom == 1.0
    assert v.scale == fresh.scale
    assert (round(v.off_x, 6), round(v.off_y, 6)) == (
        round(fresh.off_x, 6), round(fresh.off_y, 6),
    )


# --------------------------------------------------------------------------- #
# fit_to
# --------------------------------------------------------------------------- #
def test_fit_to_zooms_in_on_a_subset():
    """Framing a small region well inside the map zooms in past the full fit,
    and centres that region rather than the whole map."""
    v = _view()
    v.fit_to([(400.0, 400.0), (600.0, 600.0)])
    assert v.zoom > 1.0
    cx, cy = v.to_screen((500.0, 500.0))
    sx, sy, sw, sh = SCREEN
    assert abs(cx - (sx + sw / 2)) < 1
    assert abs(cy - (sy + sh / 2)) < 1


def test_fit_to_the_full_bounds_matches_reset():
    """Framing points that span exactly ``world_bounds`` is the same view as
    ``reset()`` — the whole point of keeping ``_fit_scale`` keyed to the full
    map rather than whatever was last framed."""
    v = _view()
    v.zoom_at((250, 500), 2.5)
    v.pan(37, -19)
    v.fit_to([(BOUNDS[0], BOUNDS[1]), (BOUNDS[2], BOUNDS[3])])
    fresh = _view()
    assert v.zoom == 1.0
    assert v.scale == fresh.scale
    assert (round(v.off_x, 6), round(v.off_y, 6)) == (
        round(fresh.off_x, 6), round(fresh.off_y, 6),
    )


def test_fit_to_a_single_point_clamps_at_zoom_max():
    v = _view()
    v.fit_to([(300.0, 200.0)])
    assert v.zoom == config.ZOOM_MAX


def test_fit_to_empty_points_falls_back_to_reset():
    v = _view()
    v.zoom_at((400, 300), 2.0)
    v.fit_to([])
    assert v.zoom == 1.0


def test_fit_to_keeps_the_full_map_reachable_by_zooming_out():
    """The whole point of the feature this backs: framing a subset must not
    lower the zoom-out floor, so the "-" button/scroll can always walk back out
    to the same full-map view ``reset()`` gives."""
    v = _view()
    v.fit_to([(400.0, 400.0), (600.0, 600.0)])
    assert v.zoom > config.ZOOM_MIN
    for _ in range(20):        # repeated zoom-out, like mashing the "-" button
        v.zoom_at((400, 300), 1 / 1.25)
    assert v.zoom == config.ZOOM_MIN

    fresh = _view()
    assert v.scale == fresh.scale
    assert (round(v.off_x, 6), round(v.off_y, 6)) == (
        round(fresh.off_x, 6), round(fresh.off_y, 6),
    )

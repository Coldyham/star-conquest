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
    """Content can never be dragged past a `padding`-sized gap from either
    edge — the slack that keeps a boundary system's centre (and its fixed-
    pixel-radius circle, drawn independently of the world bounds) clear of
    the clip edge instead of sliced flush against it."""
    v = _view()
    v.zoom_at((400, 300), 3.0)
    pad = v._padding

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
    system typically sits) must land `padding` px *inside* the clip edge at
    the clamped extreme, not flush against it — the concrete case the padding
    buffer exists to prevent."""
    v = _view()
    v.zoom_at((400, 300), 3.0)
    v.pan(100000, 100000)  # drag as far as it goes, revealing the left/top edge
    sx, sy, _, _ = SCREEN
    x0, y0 = v.to_screen((BOUNDS[0], BOUNDS[1]))
    assert abs(x0 - (sx + v._padding)) < 1e-6
    assert abs(y0 - (sy + v._padding)) < 1e-6


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

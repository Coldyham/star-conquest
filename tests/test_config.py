"""Palette helpers (contrast-based text colour) and the map-margin metrics."""

from __future__ import annotations

from starconquest import config


def test_map_paddings_always_clear_the_largest_node():
    """Both map margins stay at or above `node_clearance()` at every UI scale, so a
    boundary system's circle can never be sliced by the viewport edge. The bare
    constants are deliberately allowed to sit below it — the floor is what makes
    them safe once ui_scale grows the node radius past them."""
    try:
        for scale in (0.5, 1.0, 1.4, 2.1, 3.0, 4.0):
            config.apply_ui_scale(scale)
            clearance = config.node_clearance()
            assert config.map_fit_padding() >= clearance, f"fit margin at {scale}x"
            assert config.map_pan_padding() >= clearance, f"pan margin at {scale}x"
            # a zoomed-in view gets at least as much room as the resting one
            assert config.map_pan_padding() >= config.map_fit_padding()
    finally:
        config.apply_ui_scale(1.0)


def test_node_clearance_covers_the_selection_ring():
    """The widest thing drawn around a node is its selection ring: the ring's radius
    (node + NODE_RING_PAD) plus its stroke. render._draw_systems draws exactly that."""
    config.apply_ui_scale(1.0)
    widest = config.NODE_MAX_RADIUS + config.NODE_RING_PAD + config.s(3)
    assert config.node_clearance() >= widest


def test_text_on_extremes():
    assert config.text_on((255, 255, 255)) == config.COLOR_TEXT_DARK
    assert config.text_on((0, 0, 0)) == config.COLOR_TEXT


def test_text_on_light_players_use_dark():
    # yellow / green / orange were the unreadable ones with white text
    for bg in ((240, 230, 110), (95, 210, 130), (240, 170, 70)):
        assert config.text_on(bg) == config.COLOR_TEXT_DARK


def test_text_on_always_picks_higher_contrast():
    for pid in range(len(config.PLAYER_COLORS)):
        bg = config.player_color(pid)
        chosen = config.text_on(bg)
        other = config.COLOR_TEXT if chosen == config.COLOR_TEXT_DARK else config.COLOR_TEXT_DARK
        assert config._contrast_ratio(chosen, bg) >= config._contrast_ratio(other, bg)

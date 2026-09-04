"""Palette helpers (contrast-based text colour), map margins, and ship speed."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from starconquest import config


@contextmanager
def speed_growth(pct_per_turn: float):
    old = config.SHIP_SPEED_GROWTH_PCT
    config.SHIP_SPEED_GROWTH_PCT = pct_per_turn
    try:
        yield
    finally:
        config.SHIP_SPEED_GROWTH_PCT = old


def test_speed_is_flat_by_default():
    assert config.SHIP_SPEED_GROWTH_PCT == 0.0
    for turn in (0, 50, 500):
        assert config.ship_speed(turn) == config.SHIP_LY_PER_TURN
        assert config.travel_turns_at(4, turn) == 4


def test_speed_growth_compounds():
    with speed_growth(1.0):
        assert config.ship_speed(0) == config.SHIP_LY_PER_TURN
        # 1%/turn doubles in ln(2)/ln(1.01) ~= 70 turns, and doubles again by ~139
        assert config.ship_speed(70) == pytest.approx(2 * config.SHIP_LY_PER_TURN, rel=0.01)
        assert config.ship_speed(139) == pytest.approx(4 * config.SHIP_LY_PER_TURN, rel=0.01)


def test_speed_growth_shortens_lanes_but_never_below_one():
    with speed_growth(1.0):
        assert config.travel_turns_at(4, 0) == 4      # unchanged at the start
        assert config.travel_turns_at(4, 70) == 2     # twice the speed, half the time
        assert config.travel_turns_at(1, 70) == 1     # a one-turn lane can't get faster


def test_speed_growth_stops_at_the_slider_ceiling():
    """Also the guard against overflowing the power in an unbounded game."""
    with speed_growth(50.0):
        assert config.ship_speed(1_000_000) == config.SHIP_SPEED_MAX


def test_waiting_never_beats_launching_now():
    """The reason growth compounds rather than adding a flat amount: delaying a
    launch pays only for a trip longer than 1/ln(1+r) turns — ~50 at the slider's
    2% top — which no lane reaches, at any base speed the speed slider allows."""
    with speed_growth(2.0):
        for base_speed in (1.0, 6.0, 15.0):
            config.SHIP_LY_PER_TURN = base_speed
            try:
                for lane_turns in (1, 2, 5, 12, 30, 50):
                    now = config.travel_turns_at(lane_turns, 0)
                    for wait in range(1, 25):
                        later = wait + config.travel_turns_at(lane_turns, wait)
                        assert later >= now, (base_speed, lane_turns, wait)
            finally:
                config.SHIP_LY_PER_TURN = 6.0


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

"""Palette helpers — contrast-based text colour selection."""

from __future__ import annotations

from starconquest import config


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

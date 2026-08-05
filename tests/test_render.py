"""Headless render smoke test: prove render.draw doesn't crash with no display.

Uses SDL's dummy video/audio drivers so it runs in CI with no screen. This only
checks that drawing every UI state is exception-free — not how it looks.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from starconquest import ai, config, engine, fog, mapgen, render  # noqa: E402
from starconquest.geometry import WorldView  # noqa: E402
from starconquest.model import Order  # noqa: E402
from starconquest.viewstate import CHOOSING, SELECTED, Ui  # noqa: E402


def _make_ui(state):
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    # mirror main.new_ui: with fog off (the default) the whole map is visible
    ui.visible = set(state.systems)
    ui.seen = set(state.systems)
    return ui


def test_render_all_ui_states_no_crash():
    pygame.init()
    render._FONTS.clear()   # rebuild fonts under this session (an earlier test quit)
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)

        # plain frame
        render.draw(screen, state, ui)

        # a selection + a pending order + a choosing preview + a standing rule
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        ui.mode = SELECTED
        ui.selected = home
        nbr = state.systems[home].neighbors[0]
        ui.mode = CHOOSING
        ui.dest = nbr
        ui.chosen = 3
        ui.hover = nbr
        ui.pending.append(Order(1, home, nbr, 2))
        ui.auto_forward[home] = (nbr, 2)   # exercise dashed rule arrow + panel rule section
        render.draw(screen, state, ui)

        # send popup armed as a forward rule (exercises the toggled-button path)
        ui.forward_armed = True
        render.draw(screen, state, ui)
        ui.forward_armed = False

        # editing the standing rule: the on-map −/+ stepper is drawn, recording
        # clickable button rects just like a queued order's
        ui.mode = SELECTED
        ui.dest = None
        ui.sel_forward = home
        render.draw(screen, state, ui)
        assert ui.minus_rect[2] > 0 and ui.plus_rect[2] > 0
        ui.sel_forward = None

        # advance a few turns so fleets exist, then draw
        for _ in range(6):
            engine.end_turn(state, decide=ai.compute_orders)
        render.draw(screen, state, ui)

        # win overlay
        state.winner = 2
        render.draw(screen, state, ui)
    finally:
        pygame.quit()


def test_win_overlay_offers_sharing_only_for_an_earned_human_win():
    """The overlay's share button is the gate on what can become a challenge: the
    human's own win, with at least one turn they actually played."""
    pygame.init()
    render._FONTS.clear()   # rebuild fonts under this session (prev test quit pygame)
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        state.turn = 137
        state.players[1].ships_lost = 412

        state.winner = 1
        ui.hand_turns = 100
        render.draw(screen, state, ui)
        assert ui.share_button_rect[2] > 0

        ui.hand_turns = 0            # a pure autoplay demo is not a score
        render.draw(screen, state, ui)
        assert ui.share_button_rect[2] == 0

        ui.hand_turns = 100
        state.winner = 2             # someone else's win is not yours to send
        render.draw(screen, state, ui)
        assert ui.share_button_rect[2] == 0
    finally:
        pygame.quit()


def test_win_overlay_draws_every_challenge_verdict():
    """Beaten / missed / failed all render, including the losing branch that shows
    a verdict but no score."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        state.turn = 137
        state.players[1].ships_lost = 412
        ui.hand_turns = 137
        ui.challenge_by = "Andrew"

        state.winner = 1
        for target in (None, (200, 500), (100, 100), (137, 412)):
            ui.challenge_target = target
            render.draw(screen, state, ui)

        state.winner = 2             # "Challenge failed"
        ui.challenge_target = (200, 500)
        render.draw(screen, state, ui)

        state.winner = 0             # mutual annihilation, with a target set
        render.draw(screen, state, ui)
    finally:
        pygame.quit()


def test_scoreboard_full_table_and_eliminated():
    """Six seats (drops names) plus an eliminated player exercise both label paths."""
    pygame.init()
    render._FONTS.clear()   # rebuild fonts under this session (prev test quit pygame)
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=34, num_players=6)
        state.players[3].alive = False   # force the "out" branch
        render.draw(screen, state, _make_ui(state))
    finally:
        pygame.quit()


def test_render_fogged_states_no_crash():
    """Fogged silhouettes, hidden systems, a "?" panel, and a frozen scoreboard row
    all draw without a crash."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(3, num_nodes=18, num_players=3)
        ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
        ui.visible = {s.id for s in state.systems.values() if s.owner_id == 1}
        ui.seen = set(sorted(state.systems)[:10])         # some fogged, the rest hidden
        ui.player_intel = {2: fog.player_totals(state, 2)}  # a frozen (last-known) row
        ui.hover = next(iter(ui.seen - ui.visible), None)   # hover a fogged system
        render.draw(screen, state, ui)
    finally:
        pygame.quit()


def test_draw_resets_clip_after_map_layer():
    """The map layer is clipped to config.play_rect() (now that pan/zoom can
    push it past the viewport's edges) — guard against a missing
    set_clip(None) leaking that clip into the HUD/side panel."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        render.draw(screen, state, _make_ui(state))
        assert screen.get_clip() == screen.get_rect()
    finally:
        pygame.quit()


def test_selected_forward_rule_reports_its_lane():
    """A standing rule's own label sits on the lane it uses, hiding that lane's
    travel-time pill — so the panel has to be where its length and travel time are
    readable. A queued order already gets the same section."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]

        # no rule, nothing hovered or chosen: there is no lane to report
        assert render._panel_lane_target(state, ui, home) is None

        ui.auto_forward[home] = (nbr, 2)
        assert render._panel_lane_target(state, ui, home) == nbr
        ui.sel_forward = home
        assert render._panel_lane_target(state, ui, home) == nbr

        # a hovered neighbour still wins — that's the lane you're pointing at
        other = next((n for n in state.systems[home].neighbors if n != nbr), None)
        if other is not None:
            ui.selected, ui.hover = home, other
            assert render._panel_lane_target(state, ui, home) == other

        # and a rule whose destination has gone is not reported at all
        ui.selected = ui.hover = None
        ui.auto_forward[home] = (max(state.systems) + 99, 2)
        assert render._panel_lane_target(state, ui, home) is None
        render.draw(screen, state, ui)      # still draws
    finally:
        pygame.quit()


def test_forward_rule_label_clears_the_lane_travel_pill():
    """Both labels used to be centred on the lane midpoint, so the rule covered the
    lane length. They're stacked now — check they don't overlap at either scale, for
    the plain label and the taller stepper the selected rule gets."""
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))   # fonts need a video ctx
    try:
        for scale in (_desktop_scale, _touch_scale):
            scale()
            small = render._fonts()["small"]
            pa, pb = (100, 400), (900, 400)
            mid_y = 400
            # the travel-time pill is centred on the midpoint (see _draw_lanes)
            pill_bottom = mid_y + (small.get_height() + config.s(4)) // 2
            for font in (small, render._fonts()["normal"]):
                cy = render._rule_label_center(pa, pb, font)[1]
                label_top = cy - (font.get_height() + config.s(4)) // 2
                assert label_top > pill_bottom, (
                    f"rule label overlaps the travel-time pill at {config.ui_scale}x")
    finally:
        _desktop_scale()
        pygame.quit()


def _touch_scale():
    """Enter the mobile-web geometry: the browser framebuffer plus the touch boost,
    the combination (~2x fonts) that used to push HUD labels out of their boxes."""
    config.SCREEN_W, config.SCREEN_H = config.WEB_FB_W, config.WEB_FB_H
    fit = min(config.WEB_FB_W / config.BASE_SCREEN_W, config.WEB_FB_H / config.BASE_SCREEN_H)
    config.apply_ui_scale(fit * config.TOUCH_UI_SCALE, touch=True)
    render._FONTS.clear()


def _desktop_scale():
    config.SCREEN_W, config.SCREEN_H = config.BASE_SCREEN_W, config.BASE_SCREEN_H
    config.apply_ui_scale(1.0)
    render._FONTS.clear()


def _hud_rects(ui):
    """The bottom bar's recorded hit-rects, as pygame Rects (skipping zeroed ones)."""
    names = ("end_turn_rect", "play_pause_rect", "autoplay_button_rect",
             "history_button_rect", "restart_live_button_rect", "menu_button_rect",
             "clear_button_rect", "quit_button_rect", "exit_history_rect",
             "rewind_button_rect")
    return {n: pygame.Rect(*getattr(ui, n)) for n in names if getattr(ui, n)[2] > 0}


def test_hud_buttons_never_overlap_at_touch_scale():
    """Every bottom-bar button is sized from its measured label, so at the ~2x touch
    scale (where fixed widths used to be too narrow) they still tile without
    colliding and stay on screen — live play and history review alike."""
    pygame.init()
    _touch_scale()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        state.turn = 137                     # the widest turn counter, and enables History
        ui = _make_ui(state)
        for history, playing in ((False, False), (False, True), (True, False)):
            ui.history, ui.playing = history, playing
            ui.history_max, ui.history_turn = 12, 4
            render.draw(screen, state, ui)
            rects = _hud_rects(ui)
            assert rects, "the bottom bar drew no buttons at all"
            for name, r in rects.items():
                assert screen.get_rect().contains(r), f"{name} is off screen: {r}"
            pairs = list(rects.items())
            for i, (na, ra) in enumerate(pairs):
                for nb, rb in pairs[i + 1:]:
                    assert not ra.colliderect(rb), f"{na} overlaps {nb} ({ra} / {rb})"
    finally:
        _desktop_scale()
        pygame.quit()


def test_touch_build_drops_keyboard_only_text():
    """A phone has no keyboard, so the '(Esc)' suffixes and the shortcut lines in
    the help panel are dropped there — they were also what overflowed the labels."""
    pygame.init()
    try:
        _desktop_scale()
        assert render._key_hint("Quit", "Esc") == "Quit (Esc)"
        assert render.confirm_labels("Quit") == ("Quit (Y/Enter)", "Cancel (N/Esc)")
        _touch_scale()
        assert render._key_hint("Quit", "Esc") == "Quit"
        assert render.confirm_labels("Quit") == ("Quit", "Cancel")
        assert "Shift" not in render._LEGEND_TOUCH and "Enter" not in render._LEGEND_TOUCH
    finally:
        _desktop_scale()
        pygame.quit()


def test_wrapped_help_text_fits_the_panel():
    """The help panel reflows to the panel width and is cut off at its bottom rather
    than spilling under the End Turn button."""
    pygame.init()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        for scale in (_desktop_scale, _touch_scale):
            scale()
            font = render._fonts()["small"]
            width = config.HUD_RIGHT_W - config.PANEL_PAD * 2
            for text in (render._LEGEND_TOUCH, render._LEGEND_KEYS):
                for line in render._wrap(font, text, width):
                    assert font.size(line)[0] <= width, f"{line!r} overruns the panel"
            bottom = config.HUD_TOP_H + 200
            assert render._panel_legend(screen, 0, config.HUD_TOP_H, bottom) <= bottom
    finally:
        _desktop_scale()
        pygame.quit()


def test_production_rate_sums_inverse_production():
    state = mapgen.generate_random(2, num_nodes=18, num_players=3)
    for pid in (1, 2, 3):
        expected = sum(1.0 / s.production for s in state.systems.values()
                       if s.owner_id == pid and s.production > 0)
        assert fog.player_totals(state, pid)[2] == expected
    # a lone homeworld (production 3) makes 1/3 of a ship per turn
    home = next(s for s in state.systems.values() if s.owner_id == 1)
    assert abs(1.0 / home.production - 1.0 / config.HOME_PRODUCTION) < 1e-9

"""Headless render smoke test: prove render.draw doesn't crash with no display.

Uses SDL's dummy video/audio drivers so it runs in CI with no screen. This only
checks that drawing every UI state is exception-free — not how it looks.
"""

from __future__ import annotations

import math
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from starconquest import ai, config, engine, fog, mapgen, render, starnames, turnfilm  # noqa: E402
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
        ui.auto_forward[home] = (nbr, 2)   # exercise the rule chevron flow + panel rule section
        render.draw(screen, state, ui)

        assert ui.minus_rect[2] > 0 and ui.plus_rect[2] > 0 and ui.slider_rect[2] > 0

        # send popup armed as a forward rule (exercises the toggled-button path)
        ui.forward_armed = True
        render.draw(screen, state, ui)
        ui.forward_armed = False

        # reopening the standing rule draws the same popup, on its Forward tab
        ui.edit_forward(state, home)
        render.draw(screen, state, ui)
        assert ui.minus_rect[2] > 0 and ui.plus_rect[2] > 0 and ui.slider_rect[2] > 0

        # ...and so does reopening the queued order, on the Send tab
        ui.edit_order(state, 0)
        render.draw(screen, state, ui)
        assert ui.minus_rect[2] > 0 and ui.plus_rect[2] > 0 and ui.slider_rect[2] > 0
        ui.reset_selection()

        # route mode: every stage, plus a replaced rule, an unreachable pick, a
        # loop, and a live selection box — the whole preview in one frame
        ui.auto_forward[home] = (nbr, 2)
        ui.begin_route()
        render.draw(screen, state, ui)                  # empty group, "select"
        ui.route_sel = {sid for sid, s in state.systems.items() if s.owner_id == 1}
        ui.route_box = True
        ui.drag_start, ui.drag_pos = (60, 80), (400, 500)
        render.draw(screen, state, ui)
        ui.set_route_dest(state, nbr)
        render.draw(screen, state, ui)
        ui.route_unroutable = {home}                    # forced, to draw the marker
        ui.route_cycles = {home, nbr}
        render.draw(screen, state, ui)
        # rally: sink rings on the picks, no destination, and a cut-off pocket
        ui.set_route_rally(state, True)
        render.draw(screen, state, ui)                  # empty, "Rally"
        ui.route_tap(state, nbr)
        ui.route_unroutable = {home}
        render.draw(screen, state, ui)
        ui.set_route_rally(state, False)
        ui.reset_route()
        ui.auto_forward.clear()
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

        # ...and neither is someone else's win that we merely watched, which
        # arrives looking exactly like our own: their seat, their hand turns.
        state.winner = 1
        ui.watched = True
        render.draw(screen, state, ui)
        assert ui.share_button_rect[2] == 0
        assert ui.leaderboard_button_rect[2] == 0
    finally:
        pygame.quit()


def test_win_overlay_pairs_the_leaderboard_button_with_sharing(monkeypatch):
    """The leaderboard button rides the same gate as sharing, sits beside it
    without overlapping, and disappears entirely when no leaderboard is
    configured — so a fork with the URL cleared simply doesn't offer it."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        state.winner = 1
        ui.hand_turns = 100

        render.draw(screen, state, ui)
        share, board = ui.share_button_rect, ui.leaderboard_button_rect
        assert board[2] > 0
        assert share[1] == board[1]                      # one row
        assert share[0] + share[2] <= board[0]           # no overlap
        assert share[2] == board[2]                      # common width

        monkeypatch.setattr(render.paths, "LEADERBOARD_ORIGIN", "")
        render.draw(screen, state, ui)
        assert ui.leaderboard_button_rect[2] == 0
        assert ui.share_button_rect[2] > 0               # sharing still offered
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
        ui.challenge_by = "Name"

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


def test_result_lines_verdict_is_three_way():
    """A dead heat on turns *and* ships lost reads as a match, not a near miss."""
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    state.winner = 1
    state.turn = 31
    state.players[1].ships_lost = 16
    ui.hand_turns = 31
    ui.challenge_by = "Ada"

    def verdict(target):
        ui.challenge_target = target
        _kind, text, colour = render._result_lines(state, ui)[-1]
        return text, colour

    assert verdict((41, 14)) == ("Beat Ada's 41 turns / 14 lost", render._VERDICT_BEAT)
    assert verdict((31, 16)) == ("Matched Ada's 31 turns / 16 lost", render._VERDICT_TIE)
    assert verdict((31, 15)) == ("Short of Ada's 31 turns / 15 lost", render._VERDICT_MISS)

    ui.challenge_by = ""      # an anonymous challenge names no one
    assert verdict((31, 16))[0] == "Matched 31 turns / 16 lost"


def test_a_watched_result_says_whose_it_is():
    """The overlay is otherwise indistinguishable from our own win, and the two
    missing buttons would read as a broken feature rather than as the rule."""
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    state.winner, state.turn, ui.hand_turns = 1, 31, 31

    assert not any("replayed" in text for _k, text, _c in render._result_lines(state, ui))
    ui.watched = True
    lines = render._result_lines(state, ui)
    # It leads, qualifying the "X wins!" above it rather than trailing the score.
    assert "replayed" in lines[0][1]
    assert "Conquered in 31 turns" in lines[1][1]
    # ...and it is said on a loss too, where there is no score line at all.
    state.winner = 2
    assert [text for _k, text, _c in render._result_lines(state, ui)] == [lines[0][1]]


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


def test_forward_rule_label_sits_by_the_sending_system():
    """"keep N" describes the *source's* garrison, so it belongs at that end — and
    clear of both the source node and the lane's own travel-time pill (centred on
    the midpoint). Checked on a long lane, a short one, and a vertical one, at both
    scales, since the offset is perpendicular to the lane."""
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))   # fonts need a video ctx
    try:
        for scale in (_desktop_scale, _touch_scale):
            scale()
            small = render._fonts()["small"]
            for pa, pb in (((100, 400), (900, 400)),    # long, left-to-right
                           ((900, 400), (100, 400)),    # ...and the same lane reversed
                           ((400, 400), (480, 400)),    # short: the clamp takes over
                           ((400, 700), (400, 200))):   # vertical, running up-screen
                cx, cy = render._rule_label_center(pa, pb, small)
                at = f"lane {pa}->{pb} at {config.ui_scale}x"
                to_a = math.hypot(cx - pa[0], cy - pa[1])
                to_b = math.hypot(cx - pb[0], cy - pb[1])
                assert to_a < to_b, f"rule label is not at the sending end: {at}"
                assert to_a >= config.node_clearance(), f"rule label covers its source: {at}"
                mid = ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2)
                # the travel-time pill is centred on the midpoint (see _draw_lanes)
                pill = pygame.Rect(0, 0, small.size("99")[0] + config.s(8),
                                   small.get_height() + config.s(4))
                pill.center = (int(mid[0]), int(mid[1]))
                label = pygame.Rect(0, 0, small.size("keep 99")[0] + config.s(8),
                                    small.get_height() + config.s(4))
                label.center = (cx, cy)
                assert not label.colliderect(pill), f"rule label overlaps the travel pill: {at}"
    finally:
        _desktop_scale()
        pygame.quit()


def test_rule_chevrons_fill_the_whole_lane():
    """A rule at rest must read as covering its lane end to end — both nodes carry a
    chevron, at least two ride any lane, and the spacing is even. Checked across lane
    lengths and at both scales, since the pitch is only a target."""
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        for scale in (_desktop_scale, _touch_scale):
            scale()
            src_r, dest_r = config.node_radius(2), config.node_radius(9)
            head = src_r + config.ARROW_GAP + config.RULE_CHEVRON_SIZE
            for length in (120, 200, 337, 500, 900):
                if length - (dest_r + config.ARROW_GAP) - head <= config.RULE_CHEVRON_SIZE:
                    continue           # too short for a run; covered below
                at = f"lane of {length}px at {config.ui_scale}x"
                tail = length - (dest_r + config.ARROW_GAP)
                ds = render._rule_chevron_dists(length, src_r, dest_r, 0.0)
                assert len(ds) >= 3, f"a lane with room for a run gets one: {at}"
                assert ds[0] == pytest.approx(head), f"run starts short of the source: {at}"
                assert ds[-1] == pytest.approx(tail), f"run stops short of the destination: {at}"
                step = (tail - head) / (len(ds) - 1)
                assert all(b - a == pytest.approx(step) for a, b in zip(ds, ds[1:])), \
                    f"uneven spacing: {at}"

                # mid-cycle the run has walked forward by that spacing, one chevron
                # short: the one that reached the destination has left the lane
                moved = render._rule_chevron_dists(length, src_r, dest_r, 0.5)
                assert len(moved) == len(ds) - 1, f"lost or gained a chevron: {at}"
                for a, b in zip(ds, moved):
                    assert b - a == pytest.approx(step / 2), f"uneven crawl: {at}"

            # two systems all but touching: one chevron rather than an empty lane
            assert len(render._rule_chevron_dists(src_r + dest_r + 4, src_r, dest_r, 0.0)) == 1
    finally:
        _desktop_scale()
        pygame.quit()


def test_only_the_selected_forward_rule_animates(monkeypatch):
    """A standing rule's lane is a conveyor of chevrons; the selected one crawls so
    its direction is unmistakable, and every other rule holds still — a board full of
    rules shimmering would be unreadable."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        ui.auto_forward[home] = (state.systems[home].neighbors[0], 2)

        def frame(ms: int) -> bytes:
            monkeypatch.setattr(pygame.time, "get_ticks", lambda: ms)
            render.draw(screen, state, ui)
            return pygame.image.tobytes(screen, "RGB")

        half = config.RULE_FLOW_MS // 2
        assert frame(0) == frame(half), "an unselected rule must not animate"
        ui.sel_forward = home
        assert frame(0) != frame(half), "the selected rule's chevrons should have moved"
    finally:
        monkeypatch.undo()
        pygame.quit()


def _popup_rects(ui):
    """The send popup's recorded hit-rects, as pygame Rects (skipping zeroed ones)."""
    names = ("send_tab_rect", "forward_tab_rect", "minus_rect", "plus_rect",
             "slider_rect", "send_half_rect", "send_all_rect", "cancel_rect")
    return {n: pygame.Rect(*getattr(ui, n)) for n in names if getattr(ui, n)[2] > 0}


def test_send_popup_stays_inside_the_map_viewport():
    """The popup is drawn clipped to the play area, but input hit-tests the rects it
    recorded — so a row that fell outside would be invisible and still clickable, and
    the bottom row is the destructive Delete. Check it fits at the touch scale and on
    a screen short enough that the seven rows would otherwise overflow."""
    pygame.init()
    try:
        # The third case is the one that bites: apply_ui_scale runs once at boot, so
        # a window shrunk afterwards keeps the big screen's scale, and seven
        # tap-floored rows no longer fit the viewport they are clipped to.
        for scale, size in ((_touch_scale, None), (_desktop_scale, (800, 480)),
                            (_touch_scale, (1000, 640))):
            scale()
            if size is not None:
                config.SCREEN_W, config.SCREEN_H = size
            screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
            render._FONTS.clear()
            state = mapgen.generate_random(1, num_nodes=18, num_players=3)
            ui = _make_ui(state)
            home = next(s.id for s in state.systems.values() if s.owner_id == 1)
            nbr = state.systems[home].neighbors[0]
            ui.mode, ui.selected, ui.dest, ui.chosen = CHOOSING, home, nbr, 2
            ui.pending.append(Order(1, home, nbr, 2))

            for corner in (None, (0, 0), (config.SCREEN_W, config.SCREEN_H)):
                ui.popup_pos = corner       # auto-placed, then dragged hard each way
                render.draw(screen, state, ui)
                play = pygame.Rect(*config.play_rect())
                assert play.contains(pygame.Rect(*ui.popup_rect)), (
                    f"popup escapes the viewport at {config.ui_scale}x: {ui.popup_rect}")
                rects = _popup_rects(ui)
                assert len(rects) == 8, f"the popup drew only {sorted(rects)}"
                for name, r in rects.items():
                    assert play.contains(r), f"{name} is outside the viewport: {r}"
    finally:
        _desktop_scale()
        pygame.quit()


def test_popup_rows_tile_without_overlapping():
    """Seven rows laid out by hand from one running y — a slip in the arithmetic
    would stack two controls, so one of them could never be pressed. The slider's
    knob must also stay inside the panel at both ends of its travel."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.mode, ui.selected, ui.dest, ui.chosen = CHOOSING, home, nbr, 2
        ui.pending.append(Order(1, home, nbr, 2))

        for armed in (False, True):
            ui.forward_armed = armed
            render.draw(screen, state, ui)
            panel = pygame.Rect(*ui.popup_rect)
            rects = _popup_rects(ui)
            pairs = list(rects.items())
            for i, (na, ra) in enumerate(pairs):
                assert panel.contains(ra), f"{na} sits outside the popup: {ra}"
                for nb, rb in pairs[i + 1:]:
                    assert not ra.colliderect(rb), f"{na} overlaps {nb} ({ra} / {rb})"
            knob = config.SLIDER_KNOB_R
            sx, _sy, sw, _sh = ui.slider_rect
            assert sx + knob >= panel.left and sx + sw - knob <= panel.right
    finally:
        pygame.quit()


def test_popup_slider_survives_a_source_with_nothing_to_send():
    """`lo == hi` is the ordinary state on an empty system (a plain tap arms Forward
    there), and the knob's value->position maths must not divide by it."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        state.systems[home].ships = 0
        ui.mode, ui.selected, ui.dest = CHOOSING, home, nbr

        for armed in (True, False):
            ui.forward_armed = armed
            assert ui.slider_range(state)[0] == ui.slider_range(state)[1]
            render.draw(screen, state, ui)
            assert ui.slider_rect[2] > 0
    finally:
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
             "rewind_button_rect", "fast_forward_rect", "route_button_rect",
             "route_cancel_rect", "route_confirm_rect", "route_mode_rect",
             "route_auto_rect", "history_prev_rect", "history_next_rect")
    return {n: pygame.Rect(*getattr(ui, n)) for n in names if getattr(ui, n)[2] > 0}


def test_hud_buttons_never_overlap_at_touch_scale():
    """Every bottom-bar button is sized from its measured label, so at the ~2x touch
    scale (where fixed widths used to be too narrow) they still tile without
    colliding and stay on screen — live play, route mode and history review
    alike."""
    pygame.init()
    _touch_scale()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        state.turn = 137                     # the widest turn counter, and enables History
        ui = _make_ui(state)
        # The third case is the post-defeat footer, which carries one button more
        # (Fast forward) — the widest strip the bottom bar ever has to tile. History
        # stays last: it owns the whole bottom bar, so its scrubber rects linger on
        # the Ui afterwards (harmless live — input only reads them in history mode).
        for history, playing, defeated in ((False, False, False), (False, True, False),
                                           (False, True, True), (True, False, False)):
            ui.history, ui.playing = history, playing
            state.players[1].alive = not defeated
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


def test_route_mode_footer_never_overlaps_at_touch_scale():
    """Route mode swaps the bottom bar for a strip of its own, so it needs the same
    measured-label guarantee as live play — with and without the confirm occupying
    the End Turn slot."""
    pygame.init()
    _touch_scale()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        state.turn = 137
        ui = _make_ui(state)
        ui.begin_route()
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        # a front, so rally's Auto-route button is drawn and measured too — it is
        # the widest label the strip can carry
        state.systems[nbr].owner_id = 2
        cases = [(False, set(), None), (False, {home}, None), (False, {home}, nbr),
                 (True, set(), None), (True, {nbr}, None)]
        for rally, picked, dest in cases:
            ui.route_rally = rally
            ui.route_sel = set(picked)
            ui.route_dest = dest
            ui.recompute_route(state)
            render.draw(screen, state, ui)
            rects = _hud_rects(ui)
            assert rects, f"route mode drew no buttons at all ({rally}, {picked}, {dest})"
            for name, r in rects.items():
                assert screen.get_rect().contains(r), f"{name} is off screen: {r}"
            pairs = list(rects.items())
            for i, (na, ra) in enumerate(pairs):
                for nb, rb in pairs[i + 1:]:
                    assert not ra.colliderect(rb), f"{na} overlaps {nb} ({ra} / {rb})"
    finally:
        _desktop_scale()
        pygame.quit()


def test_fast_forward_button_only_exists_while_spectating():
    """The footer offers it exactly when ``Ui.can_fast_forward`` does: the human is
    knocked out and the match is still running. Before that there are turns to play;
    after it there is nothing left to hurry."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        render.draw(screen, state, ui)
        assert ui.fast_forward_rect[2] == 0, "no such control while still playing"

        state.players[1].alive = False
        render.draw(screen, state, ui)
        assert ui.fast_forward_rect[2] > 0

        state.winner = 2
        render.draw(screen, state, ui)
        assert ui.fast_forward_rect[2] == 0, "the game is decided; nothing to skip to"
    finally:
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


def test_star_name_labels_stay_in_the_map_and_off_the_nodes():
    """The name labels are flavour: each one lands inside the map viewport, clear of
    every system's disc, and a label that would collide is dropped rather than
    overlapped. Checked by capturing what _draw_node_names decides to place."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(3, num_nodes=24, num_players=3)
        ui = _make_ui(state)
        ui.selected = next(s.id for s in state.systems.values() if s.owner_id == 1)
        drawn: list[tuple[str, pygame.Rect]] = []
        real_text = render._text

        def spy(surface, font, s, color, **kw):
            if "topleft" in kw and s in {sys.name for sys in state.systems.values()}:
                drawn.append((s, pygame.Rect(kw["topleft"], font.size(s))))
            return real_text(surface, font, s, color, **kw)

        clip = pygame.Rect(config.play_rect())
        nodes = [pygame.Rect(0, 0, 2 * config.node_radius(sys.production), 2 * config.node_radius(sys.production))
                 for sys in state.systems.values()]
        for rect, sys in zip(nodes, state.systems.values()):
            rect.center = ui.view.to_screen(sys.pos)

        try:
            render._text = spy
            screen.set_clip(clip)
            render._draw_node_names(screen, state, ui)
        finally:
            render._text = real_text
            screen.set_clip(None)

        assert drawn, "no star names were drawn at all"
        assert any(name == state.systems[ui.selected].name for name, _ in drawn), \
            "the selected system's name must always be labelled"
        for name, rect in drawn:
            assert clip.contains(rect), f"{name} label spills outside the map"
            assert rect.collidelist(nodes) == -1, f"{name} label sits on a system"
        for i, (name, rect) in enumerate(drawn):
            others = [r for j, (_, r) in enumerate(drawn) if j != i]
            assert rect.collidelist(others) == -1, f"{name} label overlaps another"
    finally:
        pygame.quit()


def test_every_star_name_fits_the_info_panel():
    """The panel heading holds a name whose length we don't control, so it drops to
    the small font when the normal one would overrun — and at that size every name
    in the catalogue fits, at both UI scales."""
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        for scale in (_desktop_scale, _touch_scale):
            scale()
            render._FONTS.clear()
            small = render._fonts()["small"]
            width = render._panel_w()
            worst = max(starnames.NAMES, key=lambda n: small.size(n)[0])
            label = f"{worst} (99)"
            assert small.size(label)[0] <= width, f"{label!r} overruns the panel"
            # ...and a row with a name in it wraps rather than spilling
            for line in render._wrap(small, f"-> {label}, keep 12", width):
                assert small.size(line)[0] <= width, f"{line!r} overruns the panel"
    finally:
        _desktop_scale()
        render._FONTS.clear()
        pygame.quit()


def _filmed_turn(state, ui):
    """Resolve one turn on a copy, returning the reel the shell would draw."""
    before = turnfilm.copy_board(state)
    events: list[turnfilm.Event] = []
    engine.end_turn(state, decide=ai.decide, on_event=events.append)
    film = turnfilm.film(events)
    ui.film, ui.film_ms = film, 0.0
    return turnfilm.Reel(before, film)


def test_a_fleet_glides_rather_than_jumping():
    """Two moments inside the move beat must draw different pixels — that is the
    whole feature. Note there is no clock to monkeypatch: `film_ms` is a `Ui`
    field, so a test drives a frame by setting it."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(3, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        ui.visible, ui.seen = set(state.systems), set(state.systems)
        reel = None
        for _ in range(30):     # play on until a turn actually moves something
            reel = _filmed_turn(state, ui)
            if any(b.kind == "move" for b in reel.film.beats):
                break
            reel.run()
        move = next(b for b in reel.film.beats if b.kind == "move")

        def frame(ms: float) -> bytes:
            reel.run_to(ms)          # as the loop does: advance, then draw
            ui.film_ms = ms
            render.draw(screen, reel.board, ui)
            return pygame.image.tobytes(screen, "RGB")

        # early in the beat the fleets are barely off their sources; late in it
        # they have covered a whole turn's step
        assert frame(move.start + 1) != frame(move.end - 1)
    finally:
        pygame.quit()


def test_a_film_frame_draws_in_every_beat():
    """Every beat, plus the hold at the end, has to survive being drawn — bursts,
    caption, landed fleets held off their node, the lot."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(5, num_nodes=16, num_players=3)
        ui = _make_ui(state)
        ui.visible, ui.seen = set(state.systems), set(state.systems)
        drawn = 0
        for _ in range(40):
            reel = _filmed_turn(state, ui)
            ms = 0.0
            while ms <= reel.film.total_ms:
                reel.run_to(ms)
                ui.film_ms = ms
                render.draw(screen, reel.board, ui)
                drawn += 1
                ms += 24.0
            reel.run()
            ui.stop_film()
        assert drawn > 100
    finally:
        pygame.quit()


def test_end_turn_is_unreachable_while_a_film_plays():
    """Taking the button away rather than guarding the action, the way route mode
    does — a press during a playback must not be able to resolve another turn."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(7, num_nodes=14, num_players=2)
        ui = _make_ui(state)
        render.draw(screen, state, ui)
        assert ui.end_turn_rect[2] > 0
        reel = _filmed_turn(state, ui)
        render.draw(screen, reel.board, ui)
        assert ui.end_turn_rect[2] == 0
        ui.stop_film()
        render.draw(screen, state, ui)
        assert ui.end_turn_rect[2] > 0      # ...and comes straight back
    finally:
        pygame.quit()


def test_launching_another_fleet_does_not_shove_the_ones_already_flying():
    """Slots are claimed outward from the lane's centre in launch order, so an
    in-flight fleet holds the line it is on. Spreading the group symmetrically
    across its current size made every existing fleet step sideways whenever one
    more set off down the same lane."""
    state = mapgen.generate_random(2, num_nodes=16, num_players=2)
    src = next(s.id for s in state.systems.values() if s.owner_id == 1)
    dst = state.systems[src].neighbors[0]
    state.systems[src].ships = 30

    engine.apply_order(state, Order(1, src, dst, 3))
    first = render._lane_offsets(state)[0][0]
    engine.apply_order(state, Order(1, src, dst, 3))
    engine.apply_order(state, Order(1, src, dst, 3))
    after = render._lane_offsets(state)
    assert after[0][0] == first == 0            # unmoved, and still on the centre
    assert [after[i][0] for i in range(3)] == [0, -1, 1]


def test_fleets_running_opposite_ways_still_get_their_own_slots():
    """The reason the offsets exist at all: two fleets can occupy the same point
    on one lane heading in opposite directions."""
    state = mapgen.generate_random(2, num_nodes=16, num_players=2)
    a = next(s.id for s in state.systems.values() if s.owner_id == 1)
    b = state.systems[a].neighbors[0]
    state.systems[a].ships = state.systems[b].ships = 20
    state.systems[b].owner_id = 2

    engine.apply_order(state, Order(1, a, b, 4))
    engine.apply_order(state, Order(2, b, a, 4))
    slots = [render._lane_offsets(state)[i][0] for i in range(2)]
    assert slots[0] != slots[1], "one lane, both directions: they must not overlap"


def _flash_frame(screen, state, ui, landed) -> bytes:
    """Just the burst layer, over a blank field, at the instant the event fires."""
    film = turnfilm.film([landed])
    ui.film = film
    ui.film_ms = next(at for at, e in film.cues if e is landed) + 1
    screen.fill((0, 0, 0))
    render._draw_film_flashes(screen, state, ui)
    ui.stop_film()
    return pygame.image.tobytes(screen, "RGB")


def test_a_reinforcement_is_not_marked_as_a_fight():
    """Your own fleet arriving at your own system is not combat, so it must not
    draw combat's burst. `Landed.steps` holds the engagements that happened, and
    is empty exactly when nothing fought."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(2, num_nodes=16, num_players=2)
        ui = _make_ui(state)
        ui.visible, ui.seen = set(state.systems), set(state.systems)
        node = next(s.id for s in state.systems.values() if s.owner_id == 1)

        def landed(steps):
            return turnfilm.Landed(node_id=node, fleets=(), was_owner=1, was_ships=4,
                                   owner_id=1, ships=9, prod_progress=0, steps=steps)

        blank = pygame.Surface((config.SCREEN_W, config.SCREEN_H))
        blank.fill((0, 0, 0))
        nothing = pygame.image.tobytes(blank, "RGB")

        assert _flash_frame(screen, state, ui, landed(())) == nothing
        fight = (turnfilm.Fold(attacker=2, attacker_ships=7, defender=1,
                               defender_ships=4, winner=1, survivors=2),)
        assert _flash_frame(screen, state, ui, landed(fight)) != nothing
    finally:
        pygame.quit()


def test_a_playback_still_shows_what_the_turn_began_with():
    """`visible` is not monotone: a system lost this turn drops out of it. Without
    the union the fight that took it would play out under a grey "?" — the map
    layer's fog is both turns' (`Ui.sees`)."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(4, num_nodes=16, num_players=2)
        ui = _make_ui(state)
        lost = next(s.id for s in state.systems.values() if s.owner_id == 1)
        # seen but no longer in sight: exactly the system a fight just took
        ui.visible = set(state.systems) - {lost}
        ui.seen = set(state.systems)
        landed = turnfilm.Landed(node_id=lost, fleets=(), was_owner=1, was_ships=4,
                                 owner_id=2, ships=3, prod_progress=0,
                                 steps=(turnfilm.Fold(attacker=2, attacker_ships=7,
                                                      defender=1, defender_ships=4,
                                                      winner=2, survivors=3),))
        film = turnfilm.film([landed])
        ui.film_ms = next(at for at, e in film.cues if e is landed) + 1

        def frame(film_visible) -> bytes:
            ui.film, ui.film_visible = film, film_visible
            render.draw(screen, state, ui)
            ui.stop_film()
            return pygame.image.tobytes(screen, "RGB")

        assert frame(frozenset()) != frame(frozenset({lost})), \
            "the film must draw a system it could see when the turn began"
        # ...and the union is additive, so nothing has to be put back afterwards
        assert ui.film_visible == frozenset()
        assert lost not in ui.visible
    finally:
        pygame.quit()


def test_a_fight_is_labelled_with_what_it_cost():
    """The victor's own losses, over the burst. Two fights differing only in their
    survivors must draw differently — otherwise the label isn't reading `cost` at
    all."""
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(2, num_nodes=16, num_players=2)
        ui = _make_ui(state)
        ui.visible, ui.seen = set(state.systems), set(state.systems)
        node = next(s.id for s in state.systems.values() if s.owner_id == 1)

        def cost(survivors: int) -> bytes:
            fold = turnfilm.Fold(attacker=2, attacker_ships=9, defender=1,
                                 defender_ships=6, winner=2, survivors=survivors)
            return _flash_frame(screen, state, ui,
                                turnfilm.Landed(node_id=node, fleets=(), was_owner=1,
                                                was_ships=6, owner_id=2,
                                                ships=survivors, prod_progress=0,
                                                steps=(fold,)))

        assert cost(2) != cost(7)
    finally:
        pygame.quit()


def test_which_fights_get_a_mark_and_what_it_says():
    """`_flash_marks` is the one place a film's fights become places on screen —
    the bursts draw from it, and the star-name pass avoids what it reports. So the
    fog gate, the reinforcement gate and the loss figure all live here."""
    pygame.init()
    render._FONTS.clear()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(2, num_nodes=16, num_players=2)
        ui = _make_ui(state)
        node = next(s.id for s in state.systems.values() if s.owner_id == 1)
        fold = turnfilm.Fold(attacker=2, attacker_ships=9, defender=1,
                             defender_ships=6, winner=2, survivors=7)

        def marks(steps, visible):
            landed = turnfilm.Landed(node_id=node, fleets=(), was_owner=1,
                                     was_ships=6, owner_id=2, ships=7,
                                     prod_progress=0, steps=steps)
            ui.visible, ui.seen = visible, set(state.systems)
            ui.film = turnfilm.film([landed])
            ui.film_ms = next(at for at, e in ui.film.cues if e is landed) + 1
            out = list(render._flash_marks(state, ui))
            ui.stop_film()
            return out

        every = set(state.systems)
        # the attacker brought 9 and kept 7, so the label is its own 2 — not the
        # 8 that died between them, most of which is the beaten garrison
        assert [(m[3], m[4]) for m in marks((fold,), every)] == [(2, 2)]
        assert marks((), every) == []                  # a reinforcement is no fight
        assert marks((fold,), every - {node}) == []    # ...and neither is hearsay
    finally:
        pygame.quit()


def test_a_loss_label_only_stands_in_a_star_names_way_while_it_shows():
    """The name pass avoids the loss pill for the same reason it avoids lane times
    and a rule's "keep N" — but only for the moment one is up.

    Both halves matter. Nothing is reserved once the burst has faded, and nothing
    at all with turn animation off, since there is then no film to read: the name
    pass and `_draw_film_flashes` share `_flash_marks` precisely so the space
    reserved and the label drawn cannot come apart.
    """
    pygame.init()
    render._FONTS.clear()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(2, num_nodes=16, num_players=2)
        ui = _make_ui(state)
        node = next(s.id for s in state.systems.values() if s.owner_id == 1)
        landed = turnfilm.Landed(
            node_id=node, fleets=(), was_owner=1, was_ships=6, owner_id=2, ships=7,
            prod_progress=0,
            steps=(turnfilm.Fold(attacker=2, attacker_ships=9, defender=1,
                                 defender_ships=6, winner=2, survivors=7),))
        ui.film = turnfilm.film([landed])
        at = next(cue for cue, e in ui.film.cues if e is landed)

        ui.film_ms = at + 1
        assert list(render._flash_marks(state, ui))          # showing: reserve it
        ui.film_ms = at + config.FILM_FLASH_MS + 1
        assert list(render._flash_marks(state, ui)) == []    # faded: hand it back

        ui.stop_film()      # ...and with animation off there is never a film
        ui.film_ms = at + 1
        assert list(render._flash_marks(state, ui)) == []
    finally:
        pygame.quit()

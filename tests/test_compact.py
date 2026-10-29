"""The compact (phone) layout: the scale a touch browser gets, the game screen's
regions in portrait, the footer's overflow list, the docked send popup, and the
setup menu's own compact canvas with its scrolling settings panel.

Headless, like test_render and test_menu: SDL dummy drivers, rects recorded by a
draw and then pressed.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from starconquest import config, input as game_input, main, mapgen, menu, render
from starconquest.custommap import CustomMap, MapNode
from starconquest.geometry import WorldView
from starconquest.model import Order
from starconquest.settings import Settings
from starconquest.viewstate import CHOOSING, FOOTER_RECTS, Ui

# A phone: 412x870 CSS px at a device pixel ratio of 2.625, as the web build
# sizes its framebuffer for it (`main.web_framebuffer`).
_CSS = (412, 870)
_DPR = 2.625


def _phone(landscape: bool) -> tuple[int, int]:
    """Lay the UI out for the phone, held either way; returns the framebuffer."""
    css_w, css_h = _CSS[::-1] if landscape else _CSS
    fb = main.web_framebuffer(css_w, css_h, _DPR)
    config.SCREEN_W, config.SCREEN_H = fb
    config.apply_ui_scale(main.ui_scale_for(fb, True, 100, fb[0] / css_w), touch=True)
    render._FONTS.clear()
    return fb


@pytest.fixture
def screen_for():
    pygame.init()
    made = []

    def make(landscape: bool):
        fb = _phone(landscape)
        made.append(fb)
        return pygame.display.set_mode(fb)

    yield make
    config.SCREEN_W, config.SCREEN_H = config.BASE_SCREEN_W, config.BASE_SCREEN_H
    config.apply_ui_scale(1.0)
    render._FONTS.clear()
    menu._FONTS.clear()
    pygame.quit()


def _make_ui(state):
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    ui.visible = set(state.systems)
    ui.seen = set(state.systems)
    return ui


def _press(pos):
    return pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1)


def test_a_touch_browser_is_scaled_in_css_pixels():
    """A touch browser's scale is set in CSS pixels, whatever its device pixel
    ratio: a whole one per baseline pixel where the screen has room for it (a
    tablet), and less on a phone, just enough to leave it TOUCH_MIN_W x
    TOUCH_MIN_H baseline units."""
    tablet = main.web_framebuffer(1280, 800, 2.0)
    assert main.ui_scale_for(tablet, True, 100, 2.0) == pytest.approx(2.0 * config.TOUCH_CSS_SCALE)
    # a phone either way up, and under its address bar
    for css_w, css_h, dpr in ((870, 412, _DPR), (412, 870, _DPR), (822, 316, 3.0)):
        fb = main.web_framebuffer(css_w, css_h, dpr)
        scale = main.ui_scale_for(fb, True, 100, fb[0] / css_w)
        assert scale < dpr * config.TOUCH_CSS_SCALE
        assert min(fb[0] / scale / config.TOUCH_MIN_W, fb[1] / scale / config.TOUCH_MIN_H) == pytest.approx(1.0)
    # past the baseline the fit still grows it, as on the desktop
    assert main.ui_scale_for((2880, 1920), True, 100, 1.0) == pytest.approx(2.0 * config.TOUCH_CSS_SCALE)
    # no CSS ratio known (Android, or the desktop) keeps the old rule
    assert main.ui_scale_for(fb, True, 100) == pytest.approx(
        max(1.0, min(fb[0] / 1440, fb[1] / 960)) * config.TOUCH_UI_SCALE)


def test_only_a_small_touch_screen_is_compact():
    try:
        config.apply_ui_scale(1.0, touch=False, size=(900, 500))
        assert not config.compact            # a desktop window keeps its layout
        config.apply_ui_scale(1.0, touch=True, size=(1440, 960))
        assert not config.compact            # a tablet's worth of room
        config.apply_ui_scale(2.0, touch=True, size=(1740, 820))
        assert config.compact and not config.portrait
        config.apply_ui_scale(2.0, touch=True, size=(820, 1740))
        assert config.compact and config.portrait
    finally:
        config.apply_ui_scale(1.0)


@pytest.mark.parametrize("landscape", [True, False])
def test_the_game_screens_regions_tile_without_overlapping(screen_for, landscape):
    """Map, panel, button strip and End Turn are four regions that cover the
    screen below the top bar between them, and never overlap."""
    screen_for(landscape)
    w, h = config.SCREEN_W, config.SCREEN_H
    regions = [pygame.Rect(r) for r in (config.play_rect(), config.side_panel_rect(),
                                        config.footer_rect(), config.end_turn_rect())]
    for i, a in enumerate(regions):
        assert pygame.Rect(0, config.HUD_TOP_H, w, h - config.HUD_TOP_H).contains(a)
        for b in regions[i + 1:]:
            assert not a.colliderect(b), (a, b)
    assert sum(r.w * r.h for r in regions) == w * (h - config.HUD_TOP_H)
    if not landscape:
        play = pygame.Rect(config.play_rect())
        assert play.w == w and play.h > h // 3   # the map takes the full width


@pytest.mark.parametrize("landscape", [True, False])
def test_the_exits_fold_into_the_overflow_list_and_still_work(screen_for, landscape):
    """On a phone Quit, Menu and New map live behind the strip's "more" button:
    zeroed until the list is open, then pressable from it — and any press closes
    the list again."""
    screen = screen_for(landscape)
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    render.draw(screen, state, ui)
    assert ui.more_button_rect[2]
    assert not ui.quit_button_rect[2] and not ui.menu_button_rect[2]

    game_input.handle_event(_press(pygame.Rect(ui.more_button_rect).center), state, ui)
    assert ui.more_open
    render.draw(screen, state, ui)
    menu_rect = pygame.Rect(ui.more_menu_rect)
    for attr in ("quit_button_rect", "menu_button_rect", "restart_live_button_rect"):
        assert menu_rect.contains(pygame.Rect(getattr(ui, attr))), attr
    action = game_input.handle_event(_press(pygame.Rect(ui.menu_button_rect).center), state, ui)
    assert action == "menu" and not ui.more_open

    # open again, then press the map: the list closes and nothing else happens
    render.draw(screen, state, ui)
    game_input.handle_event(_press(pygame.Rect(ui.more_button_rect).center), state, ui)
    render.draw(screen, state, ui)
    sel = ui.selected
    game_input.handle_event(_press(pygame.Rect(config.play_rect()).center), state, ui)
    assert not ui.more_open and ui.selected == sel


def test_a_narrow_desktop_strip_folds_rather_than_drops():
    """The same list on a desktop window too narrow for the strip: every control
    the strip would have drawn is still one press (and a list) away."""
    pygame.init()
    try:
        config.SCREEN_W, config.SCREEN_H = 760, 600
        config.apply_ui_scale(1.0)
        render._FONTS.clear()
        screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = _make_ui(state)
        render.draw(screen, state, ui)
        assert ui.more_button_rect[2]
        ui.more_open = True
        render.draw(screen, state, ui)
        live = {a for a in FOOTER_RECTS if getattr(ui, a)[2]}
        assert {"quit_button_rect", "menu_button_rect", "clear_button_rect",
                "autoplay_button_rect", "play_pause_rect"} <= live
    finally:
        config.SCREEN_W, config.SCREEN_H = config.BASE_SCREEN_W, config.BASE_SCREEN_H
        config.apply_ui_scale(1.0)
        render._FONTS.clear()
        pygame.quit()


@pytest.mark.parametrize("landscape", [True, False])
def test_the_docked_popup_owns_its_presses_and_done_keeps_the_send(screen_for, landscape):
    """Docked, the popup stands where the panel (in portrait, the controls under
    the map) would be: nothing under it answers a press, and Done closes it with
    the send left standing."""
    screen = screen_for(landscape)
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    home = max(state.systems.values(), key=lambda s: len(s.neighbors)).id
    state.systems[home].owner_id = 1
    state.systems[home].ships = 9
    dest = state.systems[home].neighbors[0]
    ui.mode, ui.selected, ui.dest, ui.chosen = CHOOSING, home, dest, 9
    ui.pending.append(Order(1, home, dest, 9))
    render.draw(screen, state, ui)

    popup = pygame.Rect(ui.popup_rect)
    if landscape:   # beside the map, not over it
        assert not popup.colliderect(pygame.Rect(config.play_rect()))
    assert ui.done_rect[2] and popup.contains(pygame.Rect(ui.done_rect))
    if not landscape:
        assert not ui.end_turn_rect[2]       # under the sheet, so taken away
        assert not any(getattr(ui, a)[2] for a in FOOTER_RECTS)
    assert game_input.handle_event(_press(pygame.Rect(ui.done_rect).center), state, ui) is None
    assert ui.mode != CHOOSING
    assert [o.dest_id for o in ui.pending] == [dest]


def _menu_click(screen, ms, settings, key):
    menu.draw(screen, ms, settings)
    x, y = ms.rects[key].center
    k = ms.canvas_scale
    return menu.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(round(x * k), round(y * k)),
                                                button=1), ms, settings)


@pytest.mark.parametrize("landscape", [True, False])
def test_the_compact_menu_fits_its_canvas_and_still_starts(screen_for, landscape):
    """Every widget the compact menu records lies on its canvas, no two of the
    action buttons overlap, and Start still starts."""
    screen = screen_for(landscape)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    menu.draw(screen, ms, settings)
    canvas = pygame.Rect((0, 0), menu._CANVAS.get_size())
    assert canvas.w < config.BASE_SCREEN_W        # its own canvas, not the letterbox
    for key, rect in ms.rects.items():
        assert canvas.contains(rect), key
    actions = [ms.rects[k] for k, _l, _b in menu._compact_action_items(ms, settings) if k in ms.rects]
    for i, a in enumerate(actions):
        assert not any(a.colliderect(b) for b in actions[i + 1:])
    assert _menu_click(screen, ms, settings, "start") == "start"


@pytest.mark.parametrize("landscape", [True, False])
def test_the_basic_and_advanced_tabs_need_no_scroll(screen_for, landscape):
    """The settings panel keeps the desktop's width, so nothing on it stacks or
    rewraps, and the two tabs a game is set up from fit it whole."""
    screen = screen_for(landscape)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    for tab in ("basic", "advanced"):
        ms.tab = tab
        menu.draw(screen, ms, settings)
        assert ms.panel_view.w >= menu._C_PANEL_W
        assert ms.panel_content_h <= ms.panel_view.h, tab


def _drag(ms, settings, start, end):
    k = ms.canvas_scale
    for kind, pos in ((pygame.MOUSEBUTTONDOWN, start), (pygame.MOUSEMOTION, end), (pygame.MOUSEBUTTONUP, end)):
        data = {"pos": (round(pos[0] * k), round(pos[1] * k)), "button": 1}
        if kind == pygame.MOUSEMOTION:
            data = {"pos": data["pos"], "rel": (0, 0), "buttons": (1, 0, 0)}
        menu.handle_event(pygame.event.Event(kind, data), ms, settings)


def test_a_drag_up_from_a_slider_scrolls_and_one_across_moves_it(screen_for):
    """Sliders fill most of a scrolling tab, so a drag that starts on one goes by
    its direction: up or down scrolls the panel and leaves the value alone,
    across is the slider's own drag."""
    screen = screen_for(True)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    ms.tab = "combat"
    menu.draw(screen, ms, settings)
    assert ms.panel_content_h > ms.panel_view.h
    rect = ms.rects["preview_attacker"]
    before = ms.preview_attacker
    _drag(ms, settings, rect.center, (rect.centerx + 4, rect.centery - 60))
    assert ms.panel_scroll["combat"] > 0 and ms.preview_attacker == before
    menu.draw(screen, ms, settings)
    rect = ms.rects["preview_attacker"]
    _drag(ms, settings, (rect.x + 4, rect.centery), (rect.x + 4 + rect.w // 2, rect.centery + 3))
    assert ms.preview_attacker != before and ms.drag_key is None


def test_the_settings_panel_scrolls_and_shows_only_what_is_in_view(screen_for):
    """The panel is shorter than a tab: a drag on its blank space scrolls it, the
    scroll stops at the content's end, and no widget's rect strays outside the
    window onto what is scrolled away."""
    screen = screen_for(True)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    ms.tab = "combat"
    menu.draw(screen, ms, settings)
    view = ms.panel_view
    assert ms.panel_content_h > view.h
    k = ms.canvas_scale
    # blank space: the panel's far right margin, clear of every widget
    x = view.right - 3
    y = next(yy for yy in range(view.y + 4, view.bottom - 4)
             if not any(r.collidepoint(x, yy) for r in ms.rects.values()))
    for ev in (pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(round(x * k), round(y * k)), button=1),
               pygame.event.Event(pygame.MOUSEMOTION, pos=(round(x * k), round((y - 5000) * k)),
                                  rel=(0, 0), buttons=(1, 0, 0)),
               pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(round(x * k), round((y - 5000) * k)), button=1)):
        menu.handle_event(ev, ms, settings)
    assert ms.panel_scroll["combat"] == ms.panel_content_h - view.h
    menu.draw(screen, ms, settings)
    inner = view.inflate(-2, -2)
    panel_keys = [key for key in ms.rects if key.startswith(("adv_", "preview_")) or key == "in_lane_battles"]
    assert panel_keys and all(inner.contains(ms.rects[key]) for key in panel_keys)


def _own_chain(state):
    """Three of our systems in a row: a source with two of our neighbours."""
    a = max(state.systems.values(), key=lambda s: len(s.neighbors))
    b, c = a.neighbors[0], a.neighbors[1]
    for sid in (a.id, b, c):
        state.systems[sid].owner_id = 1
        state.systems[sid].ships = 10
    return a.id, b, c


def _tap_node(state, ui, sid):
    return game_input.handle_event(_press(ui.view.to_screen(state.systems[sid].pos)), state, ui)


def test_on_touch_a_send_is_finished_once_its_popup_closes(screen_for):
    """While the popup is open a tap on A's other neighbour C is a second send
    from A (a split). Done lets A go, lit only as the hover, so the misfire is
    gone: a tap on C then selects C and never queues A to C again."""
    screen = screen_for(True)
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    a, b, c = _own_chain(state)
    render.draw(screen, state, ui)
    _tap_node(state, ui, a)
    _tap_node(state, ui, b)
    assert ui.mode == CHOOSING and len(ui.pending) == 1
    ui.set_send_count(state, 4)
    render.draw(screen, state, ui)
    _tap_node(state, ui, c)
    assert [(o.source_id, o.dest_id) for o in ui.pending] == [(a, b), (a, c)]
    assert ui.selected == a and ui.mode == CHOOSING

    # Done: the sends stand and nothing stays selected
    render.draw(screen, state, ui)
    game_input.handle_event(_press(pygame.Rect(ui.done_rect).center), state, ui)
    assert ui.selected is None and ui.hover == a and len(ui.pending) == 2
    _tap_node(state, ui, c)
    assert ui.selected == c and ui.mode != CHOOSING and len(ui.pending) == 2


def _finger(kind, fid, x, y):
    return pygame.event.Event(kind, finger_id=fid, touch_id=1, x=x / config.SCREEN_W,
                              y=y / config.SCREEN_H, dx=0, dy=0)


def test_a_pinch_zooms_and_undoes_its_first_fingers_tap(screen_for):
    """The first finger arrives as a mouse press and may have queued a send; the
    second finger turns the gesture into a pinch, which takes that back, then
    zooms by the change in spread about the fingers' midpoint."""
    screen = screen_for(True)
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    a, b, _c = _own_chain(state)
    render.draw(screen, state, ui)
    _tap_node(state, ui, a)
    pb = ui.view.to_screen(state.systems[b].pos)
    # finger one: SDL's emulated mouse press lands on B (a send), then its finger event
    game_input.handle_event(_press(pb), state, ui)
    assert len(ui.pending) == 1
    game_input.handle_event(_finger(pygame.FINGERDOWN, 1, *pb), state, ui)
    other = (pb[0] + 120, pb[1])
    game_input.handle_event(_finger(pygame.FINGERDOWN, 2, *other), state, ui)
    assert ui.pending == [] and ui.selected == a and ui.mode != CHOOSING
    zoom = ui.view.zoom
    game_input.handle_event(_finger(pygame.FINGERMOTION, 1, pb[0] - 100, pb[1]), state, ui)
    game_input.handle_event(_finger(pygame.FINGERMOTION, 2, other[0] + 100, other[1]), state, ui)
    assert ui.view.zoom > zoom * 1.5
    # the emulated mouse is ignored until every finger is up
    game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pb, button=1), state, ui)
    game_input.handle_event(_finger(pygame.FINGERUP, 1, *pb), state, ui)
    game_input.handle_event(_press(pb), state, ui)
    assert ui.pending == []
    game_input.handle_event(_finger(pygame.FINGERUP, 2, *other), state, ui)
    assert not ui.pinch_lock and not ui.fingers


@pytest.mark.parametrize("landscape", [True, False])
def test_the_daily_list_takes_presses_over_what_it_covers(screen_for, landscape):
    """The compact menu's Daily list rises over Start Game: a press on Daily
    puzzle is the puzzle, not a start with the settings underneath."""
    screen = screen_for(landscape)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    _menu_click(screen, ms, settings, "daily_menu")
    assert ms.daily_open
    menu.draw(screen, ms, settings)
    under = [k for k, r in ms.rects.items() if k not in ("daily", "puzzle", "daily_menu")
             and r.colliderect(ms.rects["puzzle"])]
    assert under, "the case under test: the list over another control"
    ms.daily_open = True
    assert _menu_click(screen, ms, settings, "puzzle") == "puzzle"
    ms.daily_open = True
    assert _menu_click(screen, ms, settings, "daily") == "daily"


@pytest.mark.parametrize("web", [True, False])
@pytest.mark.parametrize("landscape", [True, False])
def test_the_compact_menus_actions_fill_whole_rows(screen_for, monkeypatch, landscape, web):
    """Six actions under Start, so no row is left with one: the board's two lists
    share a button, a drawn map's x sits inside Edit map, and there is no Quit."""
    monkeypatch.setattr(menu, "is_web", lambda: web)
    opened = []
    monkeypatch.setattr(menu.webstore, "open_url", lambda url: opened.append(url) or True)
    screen = screen_for(landscape)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    menu.draw(screen, ms, settings)
    keys = [k for k, _l, _b in menu._compact_action_items(ms, settings)]
    assert len(keys) == 6 and "quit" not in ms.rects
    rows = {ms.rects[k].y for k in keys}
    assert len(keys) % len(rows) == 0
    if web:
        assert "board_menu" in keys
        _menu_click(screen, ms, settings, "board_menu")
        assert ms.board_open
        menu.draw(screen, ms, settings)
        assert {"browse_configs", "browse_matches"} <= set(ms.rects)
        _menu_click(screen, ms, settings, "browse_matches")
        assert not ms.board_open and opened == [menu.webstore.leaderboard_url(menu.LEADERBOARD_LOBBY_PATH)]


def test_a_drawn_maps_clear_is_inside_its_edit_button(screen_for, monkeypatch):
    monkeypatch.setattr(menu, "is_web", lambda: True)
    screen = screen_for(True)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    settings.custom_map = CustomMap(nodes=[MapNode(200, 200, 3, 10, 1), MapNode(600, 600, 4, 8, 2)],
                                    lanes=[(0, 1)]).normalised()
    settings.players, settings.nodes = 2, 2
    menu.draw(screen, ms, settings)
    assert len(menu._compact_action_items(ms, settings)) == 6
    edit, clear = ms.rects["create_map"], ms.rects["clear_map"]
    assert edit.y == clear.y and clear.x > edit.right and not edit.colliderect(clear)


def test_the_zoom_buttons_ride_above_the_portrait_sheet(screen_for):
    """In portrait the docked popup's sheet covers the foot of the map, so the
    zoom buttons move up onto its top edge, as they do over history's graphs,
    and still zoom."""
    screen = screen_for(False)
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    a, b, _c = _own_chain(state)
    _tap_node(state, ui, a)
    _tap_node(state, ui, b)
    ui.set_forward_mode(state, True)
    render.draw(screen, state, ui)
    sheet = pygame.Rect(ui.popup_rect)
    play = pygame.Rect(config.play_rect())
    for r in (ui.zoom_plus_rect, ui.zoom_minus_rect, ui.reset_view_rect):
        zoom = pygame.Rect(r)
        assert zoom.w and zoom.bottom <= sheet.top and play.contains(zoom)
    zoom_before = ui.view.zoom
    game_input.handle_event(_press(pygame.Rect(ui.zoom_plus_rect).center), state, ui)
    assert ui.view.zoom > zoom_before


def test_landscape_fills_the_title_column_with_taller_buttons_and_a_map(screen_for):
    """Held sideways, the column under the title is slack: the buttons grow
    into some of it and a sketch of the setup's map takes the rest."""
    screen = screen_for(True)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    menu.pump(ms, settings)
    menu.draw(screen, ms, settings)
    assert ms.sketch is not None
    assert ms.rects["start"].h > menu._C_START_H and ms.rects["create_map"].h > menu._C_BTN_H


def test_portrait_keeps_one_panel_height_with_the_map_and_taller_buttons_under_it(screen_for):
    """In portrait the panel is as tall as its tallest tab whichever shows, so
    the map under it never moves as the tabs change; the buttons grow as in
    landscape, so turning the phone doesn't resize them."""
    screen = screen_for(False)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    menu.pump(ms, settings)
    views = {}
    for tab in ("basic", "combat", "advanced", "ai"):
        ms.tab = tab
        menu.draw(screen, ms, settings)
        views[tab] = ms.panel_view
        assert ms.panel_content_h <= ms.panel_view.h, tab   # no tab scrolls
    assert len(set(map(tuple, views.values()))) == 1
    assert ms.rects["start"].y - views["ai"].bottom > menu._C_SKETCH_MIN
    assert ms.rects["start"].h > menu._C_START_H and ms.rects["create_map"].h > menu._C_BTN_H

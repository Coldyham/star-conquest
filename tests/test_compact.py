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
    """One baseline pixel is TOUCH_CSS_SCALE CSS pixels on a phone, whatever its
    device pixel ratio — not the baseline fitted into its device pixels, which
    drew 18px text at 11 CSS px."""
    fb = main.web_framebuffer(870, 412, _DPR)
    px = fb[0] / 870
    assert main.ui_scale_for(fb, True, 100, px) == pytest.approx(px * config.TOUCH_CSS_SCALE)
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


def test_the_settings_panel_scrolls_and_shows_only_what_is_in_view(screen_for):
    """The panel is shorter than a tab: a drag on its blank space scrolls it, the
    scroll stops at the content's end, and no widget's rect strays outside the
    window onto what is scrolled away."""
    screen = screen_for(False)
    menu._FONTS.clear()
    ms, settings = menu.MenuState(), Settings.defaults()
    ms.tab = "advanced"
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
    assert ms.panel_scroll["advanced"] == ms.panel_content_h - view.h
    menu.draw(screen, ms, settings)
    inner = view.inflate(-2, -2)
    for key in ("adv_fog_sight", "neutral_produces", "randomise_adv"):
        if key in ms.rects:
            assert inner.contains(ms.rects[key]), key
    panel_keys = [key for key in ms.rects if key.startswith("adv_")]
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


def test_on_touch_a_send_is_finished_once_made(screen_for):
    """The misfire: send A to B, then tap A's other neighbour C meaning to give C
    orders. On a touch screen that selects C; it never queues A to C as well.
    Done (or a tap on empty map) lets A go."""
    screen = screen_for(True)
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    a, b, c = _own_chain(state)
    render.draw(screen, state, ui)
    _tap_node(state, ui, a)
    _tap_node(state, ui, b)
    assert ui.mode == CHOOSING and len(ui.pending) == 1
    render.draw(screen, state, ui)
    _tap_node(state, ui, c)
    assert [(o.source_id, o.dest_id) for o in ui.pending] == [(a, b)]
    assert ui.selected == c and ui.mode != CHOOSING

    # Done: the send stands and nothing stays selected
    ui.reset_selection()
    ui.pending.clear()      # A's ten ships went to B above
    _tap_node(state, ui, a)
    _tap_node(state, ui, c)
    render.draw(screen, state, ui)
    game_input.handle_event(_press(pygame.Rect(ui.done_rect).center), state, ui)
    assert ui.selected is None and (a, c) in [(o.source_id, o.dest_id) for o in ui.pending]


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


def test_the_portrait_sheet_owns_presses_over_the_zoom_buttons(screen_for):
    """In portrait the docked popup's sheet can rise over the map's zoom buttons;
    a press on a control of the sheet there is the sheet's, never a zoom."""
    screen = screen_for(False)
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = _make_ui(state)
    a, b, _c = _own_chain(state)
    _tap_node(state, ui, a)
    _tap_node(state, ui, b)
    ui.set_forward_mode(state, True)
    render.draw(screen, state, ui)
    sheet = pygame.Rect(ui.popup_rect)
    zoom = [pygame.Rect(r) for r in (ui.zoom_plus_rect, ui.zoom_minus_rect, ui.reset_view_rect)]
    covered = [z for z in zoom if sheet.colliderect(z)]
    assert covered, "the case under test: the sheet over the zoom buttons"
    controls = [pygame.Rect(r) for r in (ui.send_tab_rect, ui.forward_tab_rect, ui.hold_minus_rect,
                                         ui.hold_plus_rect, ui.cancel_rect, ui.done_rect) if r[2]]
    clash = next(((c, z) for c in controls for z in covered if c.colliderect(z)), None)
    assert clash is not None
    zoom_before = ui.view.zoom
    spot = clash[0].clip(clash[1]).center
    game_input.handle_event(_press(spot), state, ui)
    assert ui.view.zoom == zoom_before

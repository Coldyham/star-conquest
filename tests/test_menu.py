"""Drive the setup menu headlessly with synthetic pygame events.

Mirrors test_input/test_render: SDL dummy drivers, no real window. Widgets store
their rects during draw(), so each test draws first, then clicks a rect's centre.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

from starconquest import ai, config, menu  # noqa: E402
from starconquest.menu import MenuState  # noqa: E402
from starconquest.settings import Challenge, Settings  # noqa: E402


def _setup():
    pygame.init()
    # Fonts are cached module-globally and must belong to the live pygame
    # session; the per-test init/quit cycle would otherwise render with handles
    # freed by a previous quit(). (In the real app pygame inits exactly once.)
    menu._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    return screen, MenuState(), Settings.defaults()


def _click_key(screen, ms, settings, key):
    """Draw (to lay out rects), then left-click the centre of widget `key`."""
    menu.draw(screen, ms, settings)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=ms.rects[key].center, button=1)
    return menu.handle_event(ev, ms, settings)


def _keydown(ms, settings, k, unicode=""):
    ev = pygame.event.Event(pygame.KEYDOWN, key=k, unicode=unicode)
    return menu.handle_event(ev, ms, settings)


def _textinput(ms, settings, text):
    """Character entry as the OS / Android soft keyboard delivers it: TEXTINPUT."""
    ev = pygame.event.Event(pygame.TEXTINPUT, text=text)
    return menu.handle_event(ev, ms, settings)


def test_file_row_never_overlaps_on_the_web_layout():
    """The web File row carries an extra 'Get Link' button, which used to leave the
    name field narrower than its own default value — so a long name ran out over
    Save/Load. The field is now sized from what the buttons leave, and its text is
    clipped to the box."""
    screen, ms, settings = _setup()
    real_is_web = menu.is_web
    menu.is_web = lambda: True
    try:
        ms.filename = "x" * menu._FILENAME_MAX_LEN     # the longest name accepted
        menu.draw(screen, ms, settings)
        field = ms.rects["filename_field"]
        assert field.width > 0
        for key in ("get_link", "save_settings", "load_settings"):
            assert not field.colliderect(ms.rects[key]), f"name field runs into {key}"
        # ...and the default name has room without needing to be clipped at all
        assert menu._fonts()["normal"].size(menu._DEFAULT_FILENAME)[0] < field.width
    finally:
        menu.is_web = real_is_web
        pygame.quit()


def test_player_stepper_and_node_floor():
    screen, ms, settings = _setup()
    try:
        start = settings.players
        _click_key(screen, ms, settings, "players_inc")
        assert settings.players == start + 1
        _click_key(screen, ms, settings, "players_dec")
        assert settings.players == start

        # Clamp at MAX_PLAYERS and drag the node floor up with it.
        for _ in range(20):
            _click_key(screen, ms, settings, "players_inc")
        assert settings.players == config.MAX_PLAYERS
        assert settings.nodes >= settings.min_nodes()
    finally:
        pygame.quit()


def test_node_stepper_clamps():
    screen, ms, settings = _setup()
    try:
        for _ in range(100):
            _click_key(screen, ms, settings, "nodes_inc")
        assert settings.nodes == config.MAX_NODES
        for _ in range(100):
            _click_key(screen, ms, settings, "nodes_dec")
        assert settings.nodes == settings.min_nodes()
    finally:
        pygame.quit()


def test_mode_and_autoplay_toggle():
    screen, ms, settings = _setup()
    try:
        _click_key(screen, ms, settings, "mode_symmetric")
        assert settings.mode == "symmetric"
        _click_key(screen, ms, settings, "mode_random")
        assert settings.mode == "random"

        assert settings.autoplay is False
        _click_key(screen, ms, settings, "autoplay")
        assert settings.autoplay is True
    finally:
        pygame.quit()


def test_seed_random_and_text_entry():
    screen, ms, settings = _setup()
    try:
        assert settings.seed is None
        _click_key(screen, ms, settings, "seed_random")
        assert isinstance(settings.seed, int)

        # Focus the field, clear it with backspaces, type a new number.
        _click_key(screen, ms, settings, "seed_field")
        assert ms.editing_seed
        for _ in range(menu._SEED_MAX_LEN + 1):
            _keydown(ms, settings, pygame.K_BACKSPACE)
        assert ms.seed_text == "" and settings.seed is None
        _textinput(ms, settings, "1234")
        assert ms.seed_text == "1234" and settings.seed == 1234
    finally:
        pygame.quit()


def test_start_via_click_and_enter():
    screen, ms, settings = _setup()
    try:
        assert _click_key(screen, ms, settings, "start") == "start"
        assert _keydown(ms, settings, pygame.K_RETURN) == "start"
        assert _keydown(ms, settings, pygame.K_ESCAPE) == "quit"
    finally:
        pygame.quit()


def test_quit_button_click_returns_quit():
    """Touch/web equivalent of Esc: there's no keyboard on a phone, so without
    this a touch user has no way to leave the setup menu at all."""
    screen, ms, settings = _setup()
    try:
        assert _click_key(screen, ms, settings, "quit") == "quit"
    finally:
        pygame.quit()


def test_web_scale_boost_enlarges_menu(monkeypatch):
    """On web the menu's fixed 3:2 canvas pillarboxes against the wider 16:9
    WEB_FB frame; WEB_MENU_BOOST should noticeably shrink that waste (bigger
    scale), without ever exceeding the full-width fit."""
    screen, ms, settings = _setup()
    try:
        # A 16:9 frame (like config.WEB_FB_W/H) actually mismatches the menu's
        # 3:2 canvas, unlike the default SCREEN_W/H == BASE_SCREEN_W/H square
        # match _setup() uses — otherwise the boost has no pillarbox to reclaim.
        screen = pygame.display.set_mode((config.WEB_FB_W, config.WEB_FB_H))
        menu.draw(screen, ms, settings)
        base_scale = ms.canvas_scale

        monkeypatch.setattr(menu, "is_web", lambda: True)
        menu.draw(screen, ms, settings)
        assert ms.canvas_scale > base_scale
        cw, ch = menu._get_canvas().get_size()
        sw, _ = screen.get_size()
        assert ms.canvas_scale <= sw / cw
    finally:
        pygame.quit()


def test_all_tabs_clickable_and_switch():
    screen, ms, settings = _setup()
    try:
        _click_key(screen, ms, settings, "tab_advanced")
        assert ms.tab == "advanced"
        _click_key(screen, ms, settings, "tab_ai")
        assert ms.tab == "ai"
        _click_key(screen, ms, settings, "tab_basic")
        assert ms.tab == "basic"
    finally:
        pygame.quit()


def test_basic_fog_checkbox_toggles_preset():
    """The Basic 'Fog of war' checkbox switches between the fog preset and off."""
    screen, ms, settings = _setup()
    try:
        # defaults are fog off (both ranges at max)
        assert settings.fog_sight == config.FOG_MAX_HOPS
        assert settings.fog_scout == config.FOG_MAX_HOPS
        # click on -> applies the preset ranges
        _click_key(screen, ms, settings, "fog_of_war")
        assert (settings.fog_sight, settings.fog_scout) == (config.FOG_ON_SIGHT, config.FOG_ON_SCOUT)
        # click off -> both back to max (full visibility)
        _click_key(screen, ms, settings, "fog_of_war")
        assert settings.fog_sight == config.FOG_MAX_HOPS
        assert settings.fog_scout == config.FOG_MAX_HOPS
    finally:
        pygame.quit()


def test_basic_fog_checkbox_reflects_slider_state():
    """The checkbox is derived from the ranges, so an Advanced edit that leaves fog
    partially on reads as checked — clicking it then turns fog fully off."""
    screen, ms, settings = _setup()
    try:
        settings.fog_sight, settings.fog_scout = 2, 4   # as if set via Advanced sliders
        _click_key(screen, ms, settings, "fog_of_war")   # shows checked -> click = off
        assert settings.fog_sight == config.FOG_MAX_HOPS
        assert settings.fog_scout == config.FOG_MAX_HOPS
    finally:
        pygame.quit()


def _drag_slider(screen, ms, settings, key, frac):
    """Draw, then click a slider `frac` of the way along its track (0..1)."""
    menu.draw(screen, ms, settings)
    track = ms.rects[key]
    # clamp inside the rect: pygame collidepoint excludes the right/bottom edge
    px = min(track.x + int(track.w * frac), track.right - 1)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(px, track.centery), button=1)
    return menu.handle_event(ev, ms, settings)


def test_advanced_slider_sets_setting():
    screen, ms, settings = _setup()
    ms.tab = "advanced"
    try:
        # node jitter spans 0.0..1.0; clicking the far right pins it near 1.0
        _drag_slider(screen, ms, settings, "adv_node_jitter", 1.0)
        assert settings.node_jitter == 1.0
        _drag_slider(screen, ms, settings, "adv_node_jitter", 0.0)
        assert settings.node_jitter == 0.0
        # integer knob quantises to whole ships
        _drag_slider(screen, ms, settings, "adv_home_ships", 0.5)
        assert settings.home_start_ships == int(settings.home_start_ships)
        # neutral-produces checkbox toggles
        _click_key(screen, ms, settings, "neutral_produces")
        assert settings.neutral_produces is True
    finally:
        pygame.quit()


def test_ai_tab_per_seat_and_copy_reset():
    screen, ms, settings = _setup()   # 3 players by default -> AI seats 2,3
    ms.tab = "ai"
    try:
        # tune seat 2's reserve fraction to the far left (0.0)
        _click_key(screen, ms, settings, "seat_2")
        assert ms.ai_seat == 2
        _drag_slider(screen, ms, settings, "ai_reserve_frac", 0.0)
        assert settings.ai[1].reserve_fraction == 0.0
        # seat 3 is still at its default until we copy
        assert settings.ai[2].reserve_fraction != 0.0
        _click_key(screen, ms, settings, "copy_all")
        assert settings.ai[2].reserve_fraction == 0.0
        # reset all restores defaults
        _click_key(screen, ms, settings, "reset_all")
        assert settings.ai[1].reserve_fraction == menu.AiParams().reserve_fraction
    finally:
        pygame.quit()


def test_ai_seat_list_respects_autoplay():
    screen, ms, settings = _setup()
    settings.players, settings.autoplay = 4, False
    ms.tab = "ai"
    try:
        menu.draw(screen, ms, settings)
        assert "seat_1" not in ms.rects            # your seat hidden when playing
        assert {"seat_2", "seat_3", "seat_4"} <= set(ms.rects)
        settings.autoplay = True
        menu.draw(screen, ms, settings)
        assert "seat_1" in ms.rects                # revealed under autoplay
    finally:
        pygame.quit()


def test_ai_tab_strategy_dropdown_select():
    screen, ms, settings = _setup()   # 3 players by default -> AI seats 2,3
    ms.tab = "ai"
    ai.register("dropdown_test", lambda st, pid: [])
    try:
        _click_key(screen, ms, settings, "seat_2")
        # sliders show while closed; opening the dropdown lists the strategies
        _click_key(screen, ms, settings, "strategy")
        assert ms.strategy_open
        assert "dropdown_test" in ms.strategies
        i = ms.strategies.index("dropdown_test")
        _click_key(screen, ms, settings, f"strategy_opt_{i}")
        assert settings.ai_strategy[1] == "dropdown_test"   # seat 2 -> index 1
        assert not ms.strategy_open                          # selecting closes it
        # Esc closes an open dropdown instead of quitting
        _click_key(screen, ms, settings, "strategy")
        assert ms.strategy_open
        assert _keydown(ms, settings, pygame.K_ESCAPE) is None
        assert not ms.strategy_open
    finally:
        ai.STRATEGIES.pop("dropdown_test", None)
        pygame.quit()


class _FakeSoftKeyboard:
    """Stand-in for the browser's hidden DOM field (see softkeyboard.py): holds a
    value, knows whether it has focus, and can be 'dismissed' like a Done key."""

    def __init__(self):
        self.text = None          # None == closed (no field to read)
        self.opens = []
        self.gone = False

    def open(self, text):
        self.text, self.gone = text, False
        self.opens.append(text)

    def close(self):
        self.text = None

    def value(self, fallback):
        return fallback if self.text is None else self.text

    def set_value(self, text):
        self.text = text

    def dismissed(self):
        return self.gone


def test_soft_keyboard_opens_on_the_focused_field(monkeypatch):
    """Tapping a text field must focus the DOM input primed with that field's
    text — including when the caret moves straight from one field to the other,
    where a plain 'is anything being edited' flag wouldn't change."""
    screen, ms, settings = _setup()
    fake = _FakeSoftKeyboard()
    monkeypatch.setattr(menu, "softkeyboard", fake)
    try:
        settings.seed = 77
        _click_key(screen, ms, settings, "seed_field")
        assert fake.opens == ["77"]
        _click_key(screen, ms, settings, "filename_field")
        assert fake.opens == ["77", ms.filename]
        _click_key(screen, ms, settings, "start")           # focus lost
        assert fake.text is None
    finally:
        pygame.quit()


def test_pump_reads_soft_keyboard_text_and_filters_it(monkeypatch):
    """Soft-keyboard typing bypasses SDL entirely, so the per-frame pump is the
    only channel; rejected characters are pushed back so the field agrees."""
    screen, ms, settings = _setup()
    fake = _FakeSoftKeyboard()
    monkeypatch.setattr(menu, "softkeyboard", fake)
    try:
        _click_key(screen, ms, settings, "seed_field")
        fake.text = "12a3"                       # swipe-typed, one bad character
        menu.pump(ms, settings)
        assert ms.seed_text == "123" and settings.seed == 123
        assert fake.text == "123"                # filtered text written back

        fake.text = "9" * (menu._SEED_MAX_LEN + 4)
        menu.pump(ms, settings)
        assert len(ms.seed_text) == menu._SEED_MAX_LEN

        fake.gone = True                         # the keyboard's Done key
        menu.pump(ms, settings)
        assert not ms.editing_seed and fake.text is None
    finally:
        pygame.quit()


def test_pump_is_inert_without_a_soft_keyboard():
    """Desktop (and any browser where the DOM bridge is unavailable) keeps the
    plain SDL path: the pump must not disturb the edit buffers."""
    screen, ms, settings = _setup()
    try:
        _click_key(screen, ms, settings, "seed_field")
        _textinput(ms, settings, "1234")
        menu.pump(ms, settings)
        assert ms.seed_text == "1234" and settings.seed == 1234
        assert ms.editing_seed
        _click_key(screen, ms, settings, "filename_field")
        before = ms.filename
        menu.pump(ms, settings)
        assert ms.filename == before and ms.editing_filename
    finally:
        pygame.quit()


# --- the un-challenge confirm modal ------------------------------------------ #
def _challenged_setup():
    """A menu sitting on a challenge link, with the score still comparable."""
    screen, ms, settings = _setup()
    settings.seed, settings.nodes, settings.players = 4821, 18, 3
    settings.challenge = Challenge(turns=137, lost=412, hand=119, by="Andrew",
                                   key=settings.challenge_key())
    return screen, ms, settings


def test_editing_a_challenge_setup_raises_the_confirm_modal():
    screen, ms, settings = _challenged_setup()
    try:
        _click_key(screen, ms, settings, "nodes_inc")
        assert ms.confirm_unchallenge
        assert settings.nodes == 19          # the edit is applied, then queried
    finally:
        pygame.quit()


def test_keeping_the_challenge_reverts_the_edit():
    screen, ms, settings = _challenged_setup()
    try:
        _click_key(screen, ms, settings, "nodes_inc")
        _click_key(screen, ms, settings, "unchallenge_keep")
        assert not ms.confirm_unchallenge
        assert settings.nodes == 18
        assert settings.challenge is not None
        assert settings.challenge.matches(settings)
    finally:
        pygame.quit()


def test_changing_anyway_drops_the_challenge_and_keeps_the_edit():
    screen, ms, settings = _challenged_setup()
    try:
        _click_key(screen, ms, settings, "nodes_inc")
        _click_key(screen, ms, settings, "unchallenge_change")
        assert not ms.confirm_unchallenge
        assert settings.nodes == 19
        assert settings.challenge is None
        assert ms.status                      # told the player what happened
    finally:
        pygame.quit()


def test_modal_answers_on_the_keyboard_too():
    screen, ms, settings = _challenged_setup()
    try:
        _click_key(screen, ms, settings, "nodes_inc")
        _keydown(ms, settings, pygame.K_ESCAPE)
        assert settings.nodes == 18 and settings.challenge is not None

        _click_key(screen, ms, settings, "nodes_inc")
        _keydown(ms, settings, pygame.K_y)
        assert settings.nodes == 19 and settings.challenge is None
    finally:
        pygame.quit()


def test_modal_swallows_every_other_widget_until_answered():
    screen, ms, settings = _challenged_setup()
    try:
        _click_key(screen, ms, settings, "nodes_inc")
        players, nodes = settings.players, settings.nodes
        assert _click_key(screen, ms, settings, "players_inc") is None
        assert _click_key(screen, ms, settings, "start") is None, "must not start a game"
        assert (settings.players, settings.nodes) == (players, nodes)
        assert ms.confirm_unchallenge
    finally:
        pygame.quit()


def test_reverting_a_seed_edit_resyncs_the_text_field():
    """The seed box keeps its own edit buffer, so a revert has to put that back too
    or the field would still show the rejected number."""
    screen, ms, settings = _challenged_setup()
    try:
        _click_key(screen, ms, settings, "seed_field")
        _textinput(ms, settings, "9")
        assert ms.confirm_unchallenge and settings.seed == 48219
        _click_key(screen, ms, settings, "unchallenge_keep")
        assert settings.seed == 4821
        assert ms.seed_text == "4821"
    finally:
        pygame.quit()


def test_a_plain_config_never_raises_the_modal():
    screen, ms, settings = _setup()
    try:
        assert settings.challenge is None
        _click_key(screen, ms, settings, "nodes_inc")
        _click_key(screen, ms, settings, "players_inc")
        assert not ms.confirm_unchallenge
    finally:
        pygame.quit()


def test_slider_drag_is_not_interrupted_mid_gesture():
    """The modal waits for the slider to be released: asking on the first pixel of
    a drag would make the Advanced tab unusable on a challenge."""
    screen, ms, settings = _challenged_setup()
    try:
        ms.tab = "advanced"
        menu.draw(screen, ms, settings)
        track = ms.rects["adv_node_jitter"]
        down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=track.center, button=1)
        menu.handle_event(down, ms, settings)
        assert ms.drag_key == "adv_node_jitter"
        for x in (track.left + 10, track.centerx + 30, track.right - 10):
            move = pygame.event.Event(pygame.MOUSEMOTION, pos=(x, track.centery),
                                      rel=(1, 0), buttons=(1, 0, 0))
            menu.handle_event(move, ms, settings)
            assert not ms.confirm_unchallenge, "asked mid-drag"
        up = pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(track.right - 10,
                                                           track.centery), button=1)
        menu.handle_event(up, ms, settings)
        assert ms.confirm_unchallenge, "never asked after the drag ended"
        # and the revert restores the pre-drag value
        before = ms.challenge_snapshot["node_jitter"]
        _click_key(screen, ms, settings, "unchallenge_keep")
        assert settings.node_jitter == before
    finally:
        pygame.quit()

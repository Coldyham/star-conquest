"""Exercise the human interaction path (input.py) with synthetic pygame events.

This drives the real select -> target -> adjust -> end-turn flow headlessly so
the interactive path is covered without a window, and confirms a queued order
actually resolves through the engine.
"""

from __future__ import annotations

import json
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from starconquest import main
from starconquest import config, engine, mapgen, replay, turnfilm
from starconquest import input as game_input
from starconquest.geometry import WorldView
from starconquest.model import Fleet, ForwardRule
from starconquest.settings import Challenge, Settings
from starconquest.viewstate import CHOOSING, IDLE, SELECTED, Ui


def _setup():
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    return state, ui


def _click(state, ui, sid, button=1):
    pos = ui.view.to_screen(state.systems[sid].pos)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button)
    return game_input.handle_event(ev, state, ui)


def _click_pos(state, ui, pos, button=1):
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button)
    return game_input.handle_event(ev, state, ui)


def _lane_mid(state, ui, src, dst):
    a = ui.view.to_screen(state.systems[src].pos)
    b = ui.view.to_screen(state.systems[dst].pos)
    return ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)


def _drag(state, ui, src, dst):
    """Press on ``src``, drag past the threshold to ``dst``, release — the
    touch-friendly way to open a send/forward for that lane."""
    a = ui.view.to_screen(state.systems[src].pos)
    b = ui.view.to_screen(state.systems[dst].pos)
    game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=a, button=1), state, ui)
    game_input.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=b, rel=(b[0] - a[0], b[1] - a[1]),
                           buttons=(1, 0, 0)), state, ui)
    game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=b, button=1), state, ui)


# --------------------------------------------------------------------------- #
# Send popup: committing and retuning a one-shot send / forward rule
# --------------------------------------------------------------------------- #
def test_select_then_target_commits_send_all():
    """Clicking a neighbour commits a send-all order immediately — no confirm
    click — and opens the adjust popup (CHOOSING)."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]

        _click(state, ui, home)
        assert ui.mode == SELECTED and ui.selected == home

        _click(state, ui, nbr)
        assert ui.mode == CHOOSING and ui.dest == nbr
        # the order exists already, defaulting to all available
        assert len(ui.pending) == 1
        o = ui.pending[0]
        assert (o.owner_id, o.source_id, o.dest_id) == (1, home, nbr)
        assert o.ships == state.systems[home].ships == ui.chosen

        # the committed order resolves through the engine: ships leave as a fleet
        before = state.systems[home].ships
        engine.end_turn(state, human_orders=list(ui.pending))
        assert state.systems[home].ships < before
        assert any(f.owner_id == 1 and f.dest_id == nbr for f in state.fleets)
    finally:
        pygame.quit()


def test_popup_presets_retune_committed_order():
    """The Half and All presets (and −/+) edit the just-committed order in place."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        total = state.systems[home].ships

        ui.send_half_rect = (100, 100, 20, 20)
        _click_pos(state, ui, (110, 110))
        assert ui.pending[0].ships == total // 2 == ui.chosen

        ui.send_all_rect = (200, 100, 20, 20)
        _click_pos(state, ui, (210, 110))
        assert ui.pending[0].ships == total == ui.chosen
    finally:
        pygame.quit()


def test_popup_tabs_switch_between_send_and_forward():
    """The Send/Forward tabs convert the active send between one-shot and rule.
    Forwarding defaults to keep 0 (forward everything)."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        ui.set_send_count(state, 3)

        ui.forward_tab_rect = (100, 100, 60, 20)
        _click_pos(state, ui, (110, 110))       # -> Forward (keep 0)
        assert ui.forward_armed is True and ui.pending == []
        assert ui.auto_forward.get(home) == ForwardRule({nbr: 100}, 0)

        ui.send_tab_rect = (200, 100, 60, 20)
        _click_pos(state, ui, (210, 110))       # -> Send (restores the count)
        assert ui.forward_armed is False and home not in ui.auto_forward
        assert ui.pending and ui.pending[0].ships == 3
    finally:
        pygame.quit()


def test_forward_steppers_adjust_the_share_and_the_hold():
    """On the Forward tab the main −/+ move the lane's share a step at a time, and
    the hold row's −/+ set how many ships the system keeps back."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        ui.forward_tab_rect = (100, 100, 60, 20)
        _click_pos(state, ui, (110, 110))       # arm forwarding: all of it, hold 0
        assert ui.auto_forward[home] == ForwardRule({nbr: 100}, 0)

        ui.minus_rect = (200, 130, 20, 20)
        _click_pos(state, ui, (210, 140))
        _click_pos(state, ui, (210, 140))
        step = config.FORWARD_STEP_PCT
        assert ui.auto_forward[home] == ForwardRule({nbr: 100 - 2 * step}, 0)

        ui.plus_rect = (100, 130, 20, 20)
        _click_pos(state, ui, (110, 140))
        assert ui.auto_forward[home].shares == {nbr: 100 - step}

        ui.hold_plus_rect = (100, 160, 20, 20)
        ui.hold_minus_rect = (200, 160, 20, 20)
        _click_pos(state, ui, (110, 170))
        _click_pos(state, ui, (110, 170))
        assert ui.auto_forward[home].hold == 2
        for _ in range(5):                      # never below 0
            _click_pos(state, ui, (210, 170))
        assert ui.auto_forward[home].hold == 0

        # sending nothing is no rule at all: down to 0% deletes it
        for _ in range(100 // step):
            _click_pos(state, ui, (210, 140))
        assert home not in ui.auto_forward and not ui.forward_armed
    finally:
        pygame.quit()


def test_popup_cancel_discards_send():
    """Cancel on the Send tab discards the committed one-shot order."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        assert len(ui.pending) == 1

        ui.cancel_rect = (100, 130, 90, 20)
        _click_pos(state, ui, (110, 140))       # Cancel
        assert ui.pending == []
        assert ui.mode == SELECTED and ui.selected == home
    finally:
        pygame.quit()


def test_popup_cancel_discards_forward_rule():
    """Cancel on the Forward tab drops this source's standing rule."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        ui.forward_tab_rect = (100, 100, 60, 20)
        _click_pos(state, ui, (110, 110))       # arm forwarding
        assert ui.auto_forward.get(home) is not None

        ui.cancel_rect = (100, 130, 90, 20)
        _click_pos(state, ui, (110, 140))       # Cancel
        assert home not in ui.auto_forward
        assert ui.pending == []                 # no send left behind
        assert ui.mode == SELECTED and ui.selected == home
    finally:
        pygame.quit()


def test_clear_all_forwarding_button():
    state, ui = _setup()
    try:
        owned = [s.id for s in state.systems.values() if s.owner_id == 1]
        a = owned[0]
        b, c = state.systems[a].neighbors[0], state.systems[a].neighbors[-1]
        ui.auto_forward[a] = ForwardRule({b: 100}, 0)
        ui.auto_forward[b] = ForwardRule({c: 100}, 2)

        ui.clear_forward_rect = (100, 100, 120, 24)
        _click_pos(state, ui, (110, 110))
        assert ui.auto_forward == {}
    finally:
        pygame.quit()


def test_shift_click_neighbour_arms_forward_rule():
    """Shift+clicking a neighbour commits it straight as a forward-all rule,
    opening the popup on the Forward tab (keep 0)."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]

        _click(state, ui, home)   # select source
        pygame.key.set_mods(pygame.KMOD_SHIFT)
        try:
            _click(state, ui, nbr)    # shift+click destination
        finally:
            pygame.key.set_mods(0)

        assert ui.mode == CHOOSING and ui.forward_armed is True
        assert ui.auto_forward.get(home) == ForwardRule({nbr: 100}, 0)  # keep 0 == forward all
        assert ui.pending == []   # a rule, not a one-shot send
    finally:
        pygame.quit()


def test_drag_send_popup_repositions_it():
    """Grabbing the popup background (not a button) starts a drag; motion moves it
    and pins popup_pos, and button-up ends the drag."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        assert ui.mode == CHOOSING and ui.popup_pos is None   # auto-placed

        ui.popup_rect = (100, 100, 120, 200)   # normally recorded by render
        _click_pos(state, ui, (110, 110))      # grab the background
        assert ui.dragging_popup is True and ui.popup_drag_off == (10, 10)

        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=(160, 180)), state, ui
        )
        assert ui.popup_pos == (150, 170)      # moved by the drag, offset preserved

        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(160, 180), button=1), state, ui
        )
        assert ui.dragging_popup is False
    finally:
        pygame.quit()


def test_popup_button_click_does_not_start_drag():
    """A click on a popup button acts on that button and never begins a drag."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)

        ui.popup_rect = (100, 100, 120, 200)
        ui.cancel_rect = (110, 260, 100, 20)   # a button inside the popup
        _click_pos(state, ui, (150, 270))      # click the Cancel button
        assert ui.dragging_popup is False
        assert ui.pending == []                # cancelled, not dragged
    finally:
        pygame.quit()


def test_slider_drag_sets_the_count_and_never_moves_the_popup():
    """The slider sits on the popup's draggable background, so its press must be
    claimed first — otherwise grabbing it would slide the whole panel instead."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        state.systems[home].ships = 20
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        assert ui.mode == CHOOSING and ui.chosen == 20

        r = config.SLIDER_KNOB_R
        ui.popup_rect = (100, 100, 120, 200)          # normally recorded by render
        ui.slider_rect = (100 + r, 150, 100 + 2 * r, 30)   # 100px of travel inside it

        _click_pos(state, ui, (100 + 2 * r + 50, 160))     # grab it halfway along
        assert ui.dragging_slider is True
        assert ui.dragging_popup is False and ui.popup_pos is None
        assert ui.chosen == 10 and ui.pending[0].ships == 10

        # dragging tracks the pointer, and clamps at both ends of the travel
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=(100 + 2 * r + 75, 160)), state, ui
        )
        assert ui.chosen == 15
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=(9999, 160)), state, ui
        )
        assert ui.chosen == 20
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=(-9999, 160)), state, ui
        )
        assert ui.chosen == 1                          # a send is never zero ships
        assert ui.popup_pos is None                    # ...and the panel never moved

        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(0, 160), button=1), state, ui
        )
        assert ui.dragging_slider is False
    finally:
        pygame.quit()


def test_slider_follows_a_dragged_popup():
    """The slider's rect is re-recorded from the panel's position every frame, so
    it keeps working wherever the player parks the popup."""
    state, ui = _setup()
    try:
        from starconquest import render

        render._FONTS.clear()      # rebuild fonts under this session
        screen = pygame.display.get_surface()
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        state.systems[home].ships = 20
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)

        def offset_in_panel():
            return (ui.slider_rect[0] - ui.popup_rect[0],
                    ui.slider_rect[1] - ui.popup_rect[1],
                    ui.slider_rect[2], ui.slider_rect[3])

        render.draw(screen, state, ui)
        assert ui.slider_rect[2] > 0
        before, where = offset_in_panel(), ui.popup_rect

        ui.popup_pos = (config.HUD_PAD, config.HUD_TOP_H + config.HUD_PAD)
        render.draw(screen, state, ui)
        assert ui.popup_rect != where          # the panel really did move
        assert offset_in_panel() == before     # ...and the slider moved with it

        # and a press at the new location still drives the count
        x, y, w, h = ui.slider_rect
        _click_pos(state, ui, (x + w // 2, y + h // 2))
        assert ui.dragging_slider is True
        assert 1 <= ui.chosen < 20
    finally:
        pygame.quit()


def test_slider_is_inert_with_nothing_to_size():
    """An empty source still forwards a share of whatever it builds, so its slider
    sizes a percentage rather than ships; and a popup closed mid-drag gives the
    slider a zeroed rect, which must not divide by zero."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        state.systems[home].ships = 0
        _click(state, ui, home)
        _click(state, ui, nbr)          # empty source -> arms Forward, garrison 0
        assert ui.mode == CHOOSING and ui.forward_armed
        assert ui.slider_range(state) == (0, 100, 100)

        ui.slider_rect = (100, 150, 100, 30)
        _click_pos(state, ui, (180, 160))
        share = ui.edited_share()
        assert 0 < share < 100 and share % config.FORWARD_SNAP_PCT == 0

        # popup closed mid-drag: render zeroes the rect, motion must be a no-op
        ui.dragging_slider = True
        ui.slider_rect = (0, 0, 0, 0)
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=(180, 160)), state, ui
        )
        assert ui.edited_share() == share
    finally:
        pygame.quit()


def test_shift_click_arms_forward_rule_from_empty_system():
    """A system with no ships free right now can still get a standing rule set
    up in advance, so future production forwards automatically. Shift+click arms
    it on the Forward tab; no bogus one-shot order is created."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        state.systems[home].ships = 0

        _click(state, ui, home)
        assert ui.mode == SELECTED

        pygame.key.set_mods(pygame.KMOD_SHIFT)
        try:
            _click(state, ui, nbr)
        finally:
            pygame.key.set_mods(0)

        assert ui.mode == CHOOSING and ui.forward_armed is True
        assert ui.auto_forward.get(home) == ForwardRule({nbr: 100}, 0)
        assert ui.pending == []

        # switching to the Send tab on an empty source shows 0, not a phantom
        # 1-ship order
        ui.set_forward_mode(state, False)
        assert ui.forward_armed is False and ui.chosen == 0
        assert ui.pending == [] and home not in ui.auto_forward
    finally:
        pygame.quit()


def test_plain_click_from_empty_system_arms_forward():
    """An empty source has nothing to send now, so forwarding future production is
    the only useful action. Since Shift isn't available on touch, a plain neighbour
    click from an empty source arms the forward rule (no phantom one-shot order)."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = next(n for n in state.systems[home].neighbors
                   if state.systems[n].owner_id != 1)
        state.systems[home].ships = 0

        _click(state, ui, home)
        _click(state, ui, nbr)
        assert ui.mode == CHOOSING and ui.forward_armed is True
        assert ui.auto_forward.get(home) == ForwardRule({nbr: 100}, 0)
        assert ui.pending == []
    finally:
        pygame.quit()


def test_drag_from_source_to_neighbour_commits_send():
    """Press-drag-release from an owned source onto an adjacent neighbour opens
    the send popup and queues the send — the touch alternative to two taps."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values()
                    if s.owner_id == 1 and s.ships > 0)
        nbr = state.systems[home].neighbors[0]
        _drag(state, ui, home, nbr)
        assert ui.mode == CHOOSING
        assert any(o.source_id == home and o.dest_id == nbr for o in ui.pending)
        assert ui.drag_src is None and ui.drag_active is False   # gesture cleared
    finally:
        pygame.quit()


def test_drag_from_empty_source_arms_forward():
    """Dragging from an empty source arms a forward rule (same as a plain click),
    since there's nothing to send right now."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        state.systems[home].ships = 0
        _drag(state, ui, home, nbr)
        assert ui.mode == CHOOSING and ui.forward_armed is True
        assert ui.auto_forward.get(home) == ForwardRule({nbr: 100}, 0)
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Selection edge cases and top-level keys
# --------------------------------------------------------------------------- #
def test_a_foreign_system_is_selected_to_read_about_but_sends_nothing():
    state, ui = _setup()
    try:
        enemy = next(s.id for s in state.systems.values() if s.owner_id not in (0, 1))
        _click(state, ui, enemy)
        assert ui.mode == SELECTED and ui.selected == enemy
        assert ui.source(state) is None

        nbr = state.systems[enemy].neighbors[0]
        _click(state, ui, nbr)
        assert ui.mode == SELECTED and ui.selected == nbr, "a neighbour is selected, not sent to"
        assert not ui.pending and not ui.auto_forward

        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        _click(state, ui, home)
        assert ui.selected == home and ui.source(state) == home
    finally:
        pygame.quit()


def test_can_select_owned_system_with_zero_available_ships():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        state.systems[home].ships = 0

        _click(state, ui, home)
        assert ui.mode == SELECTED and ui.selected == home
    finally:
        pygame.quit()


def test_right_click_cancels():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        _click(state, ui, home)
        assert ui.mode == SELECTED
        _click(state, ui, home, button=3)  # right-click
        assert ui.mode == IDLE
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Camera pan/zoom
# --------------------------------------------------------------------------- #
# Inside WorldView's default padding margin at the default fit — no node/lane
# ever lands here for the seed-1 map _setup() builds, confirmed empty both at
# the default fit and after a 2x zoom centred elsewhere on the map.
_EMPTY_POS = (5, 45)


def test_empty_space_drag_pans_view():
    """A press on empty space (no node/lane/button under it) arms a camera pan
    instead of the drag-to-target gesture — panning is a no-op at the default
    fit zoom (content already fits the viewport), so zoom in first."""
    state, ui = _setup()
    try:
        ui.view.zoom_at((600, 460), 2.0)
        off_x, off_y = ui.view.off_x, ui.view.off_y

        _click_pos(state, ui, _EMPTY_POS)
        assert ui.pan_active

        dst = (_EMPTY_POS[0] + 20, _EMPTY_POS[1] + 15)
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=dst, rel=(20, 15),
                               buttons=(1, 0, 0)),
            state, ui,
        )
        assert (ui.view.off_x, ui.view.off_y) != (off_x, off_y)

        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONUP, pos=dst, button=1), state, ui
        )
        assert not ui.pan_active
    finally:
        pygame.quit()


def test_wheel_zooms_when_idle():
    state, ui = _setup()
    try:
        assert ui.view.zoom == 1.0
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui
        )
        assert ui.view.zoom > 1.0
    finally:
        pygame.quit()


def test_wheel_adjusts_count_when_choosing():
    """Regression guard: an active send/order/forward count still wins the
    wheel over camera zoom, exactly like before zoom existed."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        assert ui.mode == CHOOSING
        chosen_before, zoom_before = ui.chosen, ui.view.zoom

        # wheel down: send-all already commits at the source's full garrison
        # (the cap), so only a decrement has room to move and prove the wheel
        # reached step_count rather than being swallowed by camera zoom.
        game_input.handle_event(
            pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1), state, ui
        )
        assert ui.chosen == chosen_before - 1
        assert ui.view.zoom == zoom_before
    finally:
        pygame.quit()


def test_reset_view_button_click_resets_camera():
    """`_setup()` never calls `refresh_fog`, so `ui.seen` is empty here — the
    same degenerate case `WorldView.fit_to` falls back to `reset()` for, which
    keeps this the same full-map-fit assertion the button gave before fog
    framing existed."""
    state, ui = _setup()
    try:
        ui.view.zoom_at((600, 460), 2.0)
        assert ui.view.zoom != 1.0

        ui.reset_view_rect = (100, 100, 120, 24)
        _click_pos(state, ui, (110, 110))
        assert ui.view.zoom == 1.0
    finally:
        pygame.quit()


def test_reset_view_button_fits_only_what_fog_has_revealed():
    state, ui = _setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.seen = {home, nbr}
        ui.view.zoom_at((600, 460), 2.0)   # arbitrary manual zoom beforehand

        ui.reset_view_rect = (100, 100, 120, 24)
        _click_pos(state, ui, (110, 110))

        expected = WorldView(mapgen.map_bounds(state), config.play_rect())
        expected.fit_to([state.systems[home].pos, state.systems[nbr].pos])
        assert (ui.view.zoom, ui.view.off_x, ui.view.off_y) == (
            expected.zoom, expected.off_x, expected.off_y)
        assert ui.view.zoom > 1.0     # zoomed in past the full-map fit
    finally:
        pygame.quit()


def test_new_game_starts_framed_to_the_opening_sight_not_the_whole_map(monkeypatch):
    """`main.new_ui` is the "start of game" trigger — with fog tight enough to
    matter, turn one's camera should already be zoomed to what's visible from
    the home system, not spoil the map's full shape."""
    monkeypatch.setattr(config, "FOG_SIGHT", 1)
    monkeypatch.setattr(config, "FOG_SCOUT", 1)
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        ui = main.new_ui(state, False)

        assert ui.seen and ui.seen < set(state.systems)   # partial fog, not the whole map
        expected = WorldView(mapgen.map_bounds(state), config.play_rect(),
                             padding=config.map_fit_padding(), pan_padding=config.map_pan_padding())
        expected.fit_to([state.systems[sid].pos for sid in ui.seen])
        assert (ui.view.zoom, ui.view.off_x, ui.view.off_y) == (
            expected.zoom, expected.off_x, expected.off_y)
        assert ui.view.zoom > 1.0
    finally:
        pygame.quit()


def test_zoom_button_clicks_change_zoom():
    state, ui = _setup()
    try:
        ui.zoom_plus_rect = (100, 100, 44, 44)
        _click_pos(state, ui, (110, 110))
        assert ui.view.zoom > 1.0
        zoomed_in = ui.view.zoom

        ui.zoom_plus_rect = (0, 0, 0, 0)
        ui.zoom_minus_rect = (100, 100, 44, 44)
        _click_pos(state, ui, (110, 110))
        assert ui.view.zoom < zoomed_in
    finally:
        pygame.quit()


def test_enter_key_requests_end_turn():
    state, ui = _setup()
    try:
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN)
        assert game_input.handle_event(ev, state, ui) == "end_turn"
    finally:
        pygame.quit()


def test_space_key_requests_end_turn():
    state, ui = _setup()
    try:
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE)
        assert game_input.handle_event(ev, state, ui) == "end_turn"
        assert ui.playing is False   # Space is not the play toggle
    finally:
        pygame.quit()


def test_p_key_toggles_play():
    state, ui = _setup()
    try:
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)
        assert game_input.handle_event(ev, state, ui) == "toggle_play"
    finally:
        pygame.quit()


def test_play_pause_button_click_toggles_play():
    state, ui = _setup()
    try:
        ui.play_pause_rect = (100, 100, 120, 32)
        ev = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, pos=(110, 110), button=1)
        assert game_input.handle_event(ev, state, ui) == "toggle_play"
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Standing auto-forward rules: expansion into orders, clearing
# --------------------------------------------------------------------------- #
def test_forward_rule_expands_into_order():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]

        ui.auto_forward[home] = ForwardRule({nbr: 100}, 4)   # keep 4, forward the surplus
        orders = main.auto_forward_orders(state, ui)
        assert len(orders) == 1
        o = orders[0]
        assert (o.owner_id, o.source_id, o.dest_id) == (1, home, nbr)
        assert o.ships == state.systems[home].ships - 4

        # keeping the whole garrison forwards nothing
        ui.auto_forward[home] = ForwardRule({nbr: 100}, state.systems[home].ships)
        assert main.auto_forward_orders(state, ui) == []
    finally:
        pygame.quit()


def test_losing_a_system_deletes_its_forwarding_rule():
    """A rule dies with the system it forwards out of. Keeping it dormant meant it
    fired again — unannounced — the turn the system was recaptured. Rules *into* a
    system that changed hands are untouched: that's an attack, and still intended."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        back = next(n for n in state.systems[nbr].neighbors if n != home)
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)
        ui.auto_forward[nbr] = ForwardRule({back: 100}, 1)     # a rule aimed at a system we don't hold
        state.systems[home].owner_id = 2     # ...and the rule's own source, captured
        state.systems[nbr].owner_id = 1

        main.resolve_turn(state, ui, log=None)
        assert home not in ui.auto_forward
        assert ui.auto_forward.get(nbr) == ForwardRule({back: 100}, 1)
    finally:
        pygame.quit()


def test_forward_rules_all_die_when_the_human_is_knocked_out():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        ui.auto_forward[home] = ForwardRule({state.systems[home].neighbors[0]: 100}, 0)
        for s in state.systems.values():
            if s.owner_id == 1:
                s.owner_id = 0
        state.fleets = [f for f in state.fleets if f.owner_id != 1]

        main.resolve_turn(state, ui, log=None)
        assert ui.auto_forward == {}
    finally:
        pygame.quit()


def test_quit_button_click_returns_quit():
    """Touch/web equivalent of Esc: the live footer's Quit button, since there is
    no keyboard to press Escape on a phone."""
    state, ui = _setup()
    try:
        ui.quit_button_rect = (100, 100, 120, 24)
        assert _click_pos(state, ui, (110, 110)) == "quit"
    finally:
        pygame.quit()


def test_clear_button_click_clears_forward_rule():
    """The footer's Clear (X) button mirrors the X key: same context-sensitive
    cancel/clear, just reachable without a keyboard."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.selected = home
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)

        ui.clear_button_rect = (100, 100, 120, 24)
        assert _click_pos(state, ui, (110, 110)) is None
        assert home not in ui.auto_forward
    finally:
        pygame.quit()


def test_clear_button_click_is_noop_with_nothing_selected():
    state, ui = _setup()
    try:
        ui.clear_button_rect = (100, 100, 120, 24)
        assert _click_pos(state, ui, (110, 110)) is None
    finally:
        pygame.quit()


def test_game_over_quit_button_click_returns_quit():
    """The win overlay's Quit button (touch equivalent of Esc in the game-over
    state) — a separate code path from live play's, gated on state.winner."""
    state, ui = _setup()
    try:
        state.winner = 1
        ui.quit_button_rect = (100, 100, 120, 24)
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(110, 110), button=1)
        assert game_input.handle_event(ev, state, ui) == "quit"
    finally:
        pygame.quit()


def test_game_over_share_key_and_button_return_share():
    """C / the overlay's Challenge button ask main to publish the result. Input
    only reports the intent — whether there is a result worth sharing is decided
    by main and render, not here."""
    state, ui = _setup()
    try:
        state.winner = 1
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, mod=0, unicode="c")
        assert game_input.handle_event(key, state, ui) == "share"
        ui.share_button_rect = (100, 100, 120, 24)
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(110, 110), button=1)
        assert game_input.handle_event(ev, state, ui) == "share"
    finally:
        pygame.quit()


def test_share_is_only_a_game_over_action():
    """C during live play must not be swallowed as a share (nor do anything else)."""
    state, ui = _setup()
    try:
        assert state.winner is None
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_c, mod=0, unicode="c")
        assert game_input.handle_event(key, state, ui) != "share"
    finally:
        pygame.quit()


def test_game_over_leaderboard_key_and_button_return_leaderboard():
    """L / the overlay's leaderboard button are the other channel for the same
    result — a distinct action, so main can open a tab rather than copy a link."""
    state, ui = _setup()
    try:
        state.winner = 1
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_l, mod=0, unicode="l")
        assert game_input.handle_event(key, state, ui) == "leaderboard"
        ui.leaderboard_button_rect = (300, 100, 120, 24)
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(310, 110), button=1)
        assert game_input.handle_event(ev, state, ui) == "leaderboard"
    finally:
        pygame.quit()


def test_leaderboard_is_only_a_game_over_action():
    """L during live play must not be swallowed either."""
    state, ui = _setup()
    try:
        assert state.winner is None
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_l, mod=0, unicode="l")
        assert game_input.handle_event(key, state, ui) != "leaderboard"
    finally:
        pygame.quit()


def test_a_rewound_turn_rolls_what_the_straight_game_rolled(tmp_path, monkeypatch):
    """Rewinding to a turn and ending it again with the same orders lands on the
    board the game played straight through landed on: the turn's rng is derived
    (`replay.reseed`), not carried, so a rewind is not a free re-roll."""
    from starconquest.settings import build_state
    from tests.test_replay import _preserve_config, _snapshot

    monkeypatch.setattr(main.webstore, "animate_turns", lambda: False)
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        with _preserve_config():
            cfg = Settings(seed=3, nodes=12, players=3)
            state = build_state(cfg, 3)
            ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()),
                    human_id=1, autoplay=True)
            log = replay.new_log(cfg, 3, state)
            log.path = tmp_path / "game.json"
            boards = [_snapshot(state)]
            while state.winner is None and state.turn < 40:
                main.resolve_turn(state, ui, log)
                boards.append(_snapshot(state))
            fought = [i for i in range(log.turn_count) if log.script_for(i).dice]
            assert fought
            for turn in fought:
                rewound = replay.GameLog.from_dict(log.to_dict())
                rewound.truncate(turn)
                again, _ = replay.reconstruct(rewound)
                ui2 = Ui(view=WorldView(mapgen.map_bounds(again), config.play_rect()),
                         human_id=1, autoplay=True)
                main.resolve_turn(again, ui2, None)
                assert _snapshot(again) == boards[turn + 1], f"turn {turn} re-rolled"
    finally:
        pygame.quit()


def test_hand_turns_counts_only_manually_played_turns(tmp_path, monkeypatch):
    """A game played by hand and then autoplayed to its end reports the hand
    count, and the Ui's running tally agrees with the log's per-turn flags."""
    state, ui = _setup()
    try:
        # `save` mints a path when it has none, so the suite has to be pointed
        # somewhere other than the player's real games/ dir.
        monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
        log = replay.new_log(Settings(seed=1, nodes=18, players=3), 1)
        log.path = tmp_path / "game.json"
        for turn in range(6):
            ui.autoplay = turn >= 4           # decided: let it play out
            main.resolve_turn(state, ui, log)
        assert ui.hand_turns == 4
        assert main.hand_turns(log) == 4
    finally:
        pygame.quit()


def test_defeat_snaps_the_camera_out_once_not_every_turn(monkeypatch):
    """The turn the human is knocked out (but the match continues without
    them) should snap the camera to the whole map exactly once — a later turn
    spent merely spectating must leave a manual zoom alone.

    Pinned to animation off, which is what these two assert: with it on the snap
    waits for the film to land (see the deferred-snap tests below), so the
    machine's own stored preference must not decide which behaviour is tested.
    """
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: False)
    state, ui = _setup()
    try:
        for s in state.systems.values():
            if s.owner_id == 1:
                s.owner_id = 0            # strip every system from the human
        state.fleets = [f for f in state.fleets if f.owner_id != 1]

        main.resolve_turn(state, ui, log=None)
        assert state.winner is None              # others are still fighting it out
        assert state.is_defeated(1)
        assert ui.view.zoom == 1.0                 # snapped out on the defeat turn

        ui.view.zoom_at((600, 460), 2.0)           # spectator zooms in by hand
        main.resolve_turn(state, ui, log=None)     # another turn, still defeated
        assert state.winner is None
        assert ui.view.zoom == 2.0                  # untouched: no second snap
    finally:
        pygame.quit()


def test_resolve_turn_archives_marks_even_with_the_animate_preference_off(monkeypatch):
    """Turning the glide off must not take the "-N"/"+N" marks with it: a fight's
    cost is cheap to report and worth seeing regardless of whether the rest of the
    turn glides. `marking` (autoplay/fast-forward aside) gates this, strictly wider
    than `filming` (which also needs the animate-turns preference on)."""
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: False)
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        target = state.systems[home].neighbors[0]
        state.systems[target].owner_id, state.systems[target].ships = 2, 1
        state.fleets = [Fleet(owner_id=1, source_id=home, dest_id=target, ships=40,
                              turns_total=2, turns_remaining=1)]

        reel = main.resolve_turn(state, ui, log=None)

        assert reel is None and ui.film is None       # no glide
        assert state.systems[target].owner_id == 1    # the fight actually happened
        assert len(ui.fading_fights) == 1
        assert ui.fading_fights[0].node_id == target
    finally:
        pygame.quit()


def test_winning_snaps_the_camera_out_to_the_whole_map(monkeypatch):
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: False)
    state, ui = _setup()
    try:
        for s in state.systems.values():
            if s.owner_id in (2, 3):
                s.owner_id = 1            # hand every rival system to the human
        state.fleets = [f for f in state.fleets if f.owner_id == 1]

        ui.view.zoom_at((600, 460), 2.0)
        main.resolve_turn(state, ui, log=None)

        assert state.winner == 1
        assert ui.view.zoom == 1.0
    finally:
        pygame.quit()


def test_restart_only_carries_autoplay_out_of_a_pure_demo():
    """Autoplaying the tail of a hand-played game must not start the next map in
    autoplay too — but a demo that was never touched keeps running."""
    _state, ui = _setup()
    try:
        ui.autoplay = True
        assert main.carry_autoplay(ui) is True       # never played a turn: a demo
        ui.hand_turns = 12                           # ...played, then autoplayed
        assert main.carry_autoplay(ui) is False
        ui.autoplay = False
        assert main.carry_autoplay(ui) is False
    finally:
        pygame.quit()


def test_challenge_target_is_dropped_when_the_setup_no_longer_matches():
    """Judging a result against a score made on a different map is worse than
    saying nothing — this is what used to report "short of X" after the settings
    had been edited."""
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    try:
        settings = Settings(seed=4821, nodes=18, players=3)
        settings.challenge = Challenge(turns=137, lost=412,
                                       key=settings.challenge_key())
        state = mapgen.generate_random(1, num_nodes=18, num_players=3)
        assert main.new_ui(state, False, settings).challenge_target == (137, 412)

        settings.nodes = 22            # edited: the score no longer applies
        ui = main.new_ui(state, False, settings)
        assert ui.challenge_target is None
        assert ui.challenge_by == ""
    finally:
        pygame.quit()


def test_shared_link_is_remembered_without_its_challenge(monkeypatch):
    """A stored challenge would be read back at the next launch and its banner
    would haunt every later session, so only the setup is persisted."""
    stored: dict[str, str] = {}
    challenged = Settings(seed=4821, nodes=18, players=3)
    challenged.challenge = Challenge(turns=137, lost=412,
                                     key=challenged.challenge_key())
    monkeypatch.setattr(main.paths, "is_web", lambda: True)
    monkeypatch.setattr(main.webstore, "url_token", lambda: challenged.to_token())
    monkeypatch.setattr(main.webstore, "get", lambda key: "")
    monkeypatch.setattr(main.webstore, "set",
                        lambda key, value: stored.__setitem__(key, value) or True)

    settings = Settings.defaults()
    main._apply_shared_link(settings)
    # This session sees the challenge...
    assert settings.challenge is not None and settings.seed == 4821
    # ...but what's remembered for the next one carries only the setup.
    remembered = Settings.from_token(stored[main.paths.WEB_SHARED_SETTINGS_KEY])
    assert remembered.challenge is None
    assert remembered.seed == 4821 and remembered.nodes == 18


def test_shared_link_with_an_outdated_key_opens_as_its_challenge(monkeypatch):
    """A key no build recomputes for the setup it came with is re-stamped on
    load, so the menu doesn't read the untouched setup as edited."""
    challenged = Settings(seed=4821, nodes=18, players=3)
    challenged.challenge = Challenge(turns=137, lost=412, key="0123456789abcdef")
    monkeypatch.setattr(main.paths, "is_web", lambda: True)
    monkeypatch.setattr(main.webstore, "url_token", lambda: challenged.to_token())
    monkeypatch.setattr(main.webstore, "get", lambda key: "")
    monkeypatch.setattr(main.webstore, "set", lambda key, value: True)

    settings = Settings.defaults()
    main._apply_shared_link(settings)
    assert settings.challenge is not None and settings.challenge.matches(settings)
    assert (settings.challenge.turns, settings.challenge.lost) == (137, 412)


def test_share_challenge_prefers_the_clipboard_and_stores_nothing(monkeypatch):
    """In an installed PWA there is no address bar to read a link out of, so the
    clipboard is the channel — and nothing may be persisted."""
    calls: list[str] = []
    monkeypatch.setattr(main.webstore, "copy_link",
                        lambda token: calls.append("copy") or True)
    monkeypatch.setattr(main.webstore, "set_url_fragment",
                        lambda token: calls.append("url") or True)
    monkeypatch.setattr(main.webstore, "set",
                        lambda k, v: calls.append("store") or True)
    state, ui = _setup()
    try:
        settings = Settings(nodes=18, players=3)
        log = replay.new_log(settings, 77)
        log.path = None
        ui.hand_turns = 1
        state.turn = 50
        msg = main.share_challenge(settings, state, ui, 77, log)
        assert "copied" in msg
        assert calls == ["copy"], "the address bar and storage must be left alone"
    finally:
        pygame.quit()


def test_challenge_settings_pins_the_seed_and_stamps_the_key():
    """A challenge whose settings still say "roll a fresh seed" would send a
    different map, so the played seed is baked in."""
    state, ui = _setup()
    try:
        settings = Settings(nodes=18, players=3)     # seed None: roll at start
        log = replay.new_log(settings, 4821)
        log.path = None
        ui.hand_turns = 3
        state.turn = 137
        state.players[1].ships_lost = 412
        shared = main.challenge_settings(settings, state, ui, 4821, log)
        assert shared.seed == 4821
        assert settings.seed is None                 # the live config is untouched
        assert (shared.challenge.turns, shared.challenge.lost) == (137, 412)
        assert shared.challenge.matches(shared)
    finally:
        pygame.quit()


def test_share_challenge_falls_back_to_a_file_off_the_web(tmp_path, monkeypatch):
    """No address bar on desktop, so the token is saved rather than the feature
    simply being unavailable there."""
    monkeypatch.setattr(main.paths, "saves_dir", lambda: tmp_path)
    state, ui = _setup()
    try:
        settings = Settings(nodes=18, players=3)
        log = replay.new_log(settings, 77)
        log.path = None
        ui.hand_turns = 1
        state.turn = 50
        msg = main.share_challenge(settings, state, ui, 77, log)
        saved = tmp_path / "challenge_77.txt"
        assert saved.exists() and "challenge_77.txt" in msg
        restored = Settings.from_token(saved.read_text().strip())
        assert restored.seed == 77 and restored.challenge.turns == 50
    finally:
        pygame.quit()


def test_x_key_clears_forward_rule():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.selected = home
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)

        game_input.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui
        )
        assert home not in ui.auto_forward
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Editing / undoing queued orders
# --------------------------------------------------------------------------- #
from starconquest.model import Order


def test_click_queued_lane_reopens_the_popup_on_that_order():
    """Editing a queued order is the same interaction as composing one: the popup
    reopens on it, on the Send tab, at its current count."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))

        _click_pos(state, ui, _lane_mid(state, ui, home, nbr))
        assert ui.sel_order == 0
        assert ui.mode == CHOOSING and not ui.forward_armed
        assert (ui.selected, ui.dest) == (home, nbr)
        assert ui.chosen == 3
        assert ui.editing_existing          # so the popup says Delete, not Cancel
    finally:
        pygame.quit()


def test_wheel_edits_reopened_order_in_place():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.edit_order(state, 0)

        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.pending[0].ships == 4

        # can't exceed the source garrison, and never drops below 1
        for _ in range(50):
            game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.pending[0].ships == state.systems[home].ships
        for _ in range(50):
            game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1), state, ui)
        assert ui.pending[0].ships == 1
    finally:
        pygame.quit()


def test_x_key_removes_reopened_order():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.edit_order(state, 0)

        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui)
        assert ui.pending == []
        assert ui.sel_order is None
        assert ui.mode != CHOOSING
    finally:
        pygame.quit()


def test_closing_a_reopened_order_leaves_nothing_selected():
    """Reopening an order borrows `selected` to aim the popup at its source. If
    that survived the close, the next tap on a neighbour would silently queue a
    second fleet out of a system the player only meant to look at."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.edit_order(state, 0)
        assert ui.selected == home

        ui.close_send()
        assert ui.mode == IDLE
        assert ui.selected is None and ui.sel_order is None
        assert not ui.editing_existing
        assert len(ui.pending) == 1        # the order itself survives
    finally:
        pygame.quit()


def test_lane_click_cycles_orders_on_same_lane():
    """Two opposite-direction orders share one lane; repeat clicks cycle + wrap."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        # both out of `home`: an order whose source we don't hold isn't a state the
        # game can reach, and reopening one would aim the popup at a foreign system
        ui.pending.append(Order(1, home, nbr, 2))
        ui.pending.append(Order(1, home, nbr, 1))
        mid = _lane_mid(state, ui, home, nbr)

        _click_pos(state, ui, mid); first = ui.sel_order
        _click_pos(state, ui, mid); second = ui.sel_order
        _click_pos(state, ui, mid); third = ui.sel_order
        assert {first, second} == {0, 1}
        assert third == first          # wraps back around
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Editing standing auto-forward rules
# --------------------------------------------------------------------------- #
def test_click_rule_lane_reopens_the_popup_on_the_forward_tab():
    """Clicking a rule's chevron lane reopens the popup on it, just like a queued
    order's lane."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)

        _click_pos(state, ui, _lane_mid(state, ui, home, nbr))
        assert ui.sel_forward == home
        assert ui.sel_order is None
        assert ui.mode == CHOOSING and ui.forward_armed and ui.edited_share() == 100
        assert (ui.selected, ui.dest) == (home, nbr)
        assert ui.editing_existing
    finally:
        pygame.quit()


def test_popup_delete_removes_a_reopened_order_or_rule():
    """The popup's bottom button discards whatever it is editing — for a reopened
    subject that means deleting something that was already on the board."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)
        ui.cancel_rect = (110, 260, 100, 20)   # normally recorded by render

        ui.edit_order(state, 0)
        _click_pos(state, ui, (150, 270))
        assert ui.pending == [] and ui.mode != CHOOSING

        ui.edit_forward(state, home)
        _click_pos(state, ui, (150, 270))
        assert home not in ui.auto_forward and ui.mode != CHOOSING
    finally:
        pygame.quit()


def test_reopening_the_same_subject_keeps_a_dragged_popup_in_place():
    """A lane with one candidate cycles back to itself, so repeat clicks re-enter
    the edit — which must not throw away where the player parked the popup."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        mid = _lane_mid(state, ui, home, nbr)

        _click_pos(state, ui, mid)
        ui.popup_pos = (300, 300)          # as if dragged there
        _click_pos(state, ui, mid)
        assert ui.popup_pos == (300, 300)
        assert ui.sel_order == 0 and ui.editing_existing

        # same for a rule
        ui.close_send()
        ui.pending.clear()
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)
        _click_pos(state, ui, mid)
        ui.popup_pos = (300, 300)
        _click_pos(state, ui, mid)
        assert ui.popup_pos == (300, 300)
        assert ui.sel_forward == home
    finally:
        pygame.quit()


def test_dormant_rule_is_highlighted_but_never_opens_the_popup():
    """A rule whose source we no longer hold is still a rule until the turn prunes
    it, but the popup reads that source's garrison and would let the Send tab queue
    an order out of enemy territory — so it highlights only."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)
        state.systems[home].owner_id = 2          # captured

        ui.edit_forward(state, home)
        assert ui.sel_forward == home
        assert ui.mode != CHOOSING
        assert ui.selected is None and ui.dest is None

        # X still clears it
        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui)
        assert home not in ui.auto_forward
    finally:
        pygame.quit()


def test_lane_click_cycles_order_and_rule_on_same_lane():
    """An order and a rule can share one lane; repeat clicks cycle across both
    (and wrap), the same precedent as two orders sharing a lane."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 2))
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 1)
        mid = _lane_mid(state, ui, home, nbr)

        def picked():
            if ui.sel_order is not None:
                return ("order", ui.sel_order)
            return ("rule", ui.sel_forward)

        _click_pos(state, ui, mid); first = picked()
        _click_pos(state, ui, mid); second = picked()
        _click_pos(state, ui, mid); third = picked()
        assert {first, second} == {("order", 0), ("rule", home)}
        assert third == first          # wraps back around
    finally:
        pygame.quit()


def test_wheel_edits_reopened_rule_share_in_place():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = ForwardRule({nbr: 50}, 2)
        ui.edit_forward(state, home)

        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.auto_forward[home] == ForwardRule({nbr: 50 + config.FORWARD_STEP_PCT}, 2)

        # a share stops at 100%, and the hold is left alone
        for _ in range(50):
            game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.auto_forward[home] == ForwardRule({nbr: 100}, 2)
        # ...and wheeled down to nothing, the rule is gone
        for _ in range(50):
            game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1), state, ui)
        assert home not in ui.auto_forward
    finally:
        pygame.quit()


def test_popup_step_buttons_adjust_a_reopened_rule_share():
    """The popup's −/+ buttons adjust a reopened rule's share, same as a queued
    order's count — one editor for both, reached the same way."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        state.systems[home].ships = 10
        ui.auto_forward[home] = ForwardRule({nbr: 50}, 2)
        ui.edit_forward(state, home)
        ui.minus_rect = (100, 100, 20, 20)   # normally recorded by render each frame
        ui.plus_rect = (140, 100, 20, 20)

        _click_pos(state, ui, (150, 110))    # + button
        assert ui.auto_forward[home] == ForwardRule({nbr: 50 + config.FORWARD_STEP_PCT}, 2)
        _click_pos(state, ui, (110, 110))    # − button
        assert ui.auto_forward[home] == ForwardRule({nbr: 50}, 2)
    finally:
        pygame.quit()


def test_x_key_removes_reopened_rule():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)
        ui.edit_forward(state, home)

        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui)
        assert home not in ui.auto_forward
        assert ui.sel_forward is None
        assert ui.mode != CHOOSING
    finally:
        pygame.quit()


def test_editing_an_order_and_a_rule_are_mutually_exclusive():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.auto_forward[home] = ForwardRule({nbr: 100}, 2)

        ui.edit_order(state, 0)
        assert ui.sel_order == 0 and ui.sel_forward is None

        ui.edit_forward(state, home)
        assert ui.sel_forward == home and ui.sel_order is None
    finally:
        pygame.quit()


def test_right_click_closes_the_edit_popup_then_selecting_system_clears_it():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.edit_order(state, 0)

        # right-click closes the popup without discarding the order
        _click_pos(state, ui, _lane_mid(state, ui, home, nbr), button=3)
        assert ui.sel_order is None and ui.mode != CHOOSING
        assert len(ui.pending) == 1

        # and the source is free to be selected again, for a fresh send
        _click(state, ui, home)
        assert ui.sel_order is None and ui.selected == home

        # reopening on the order and then tapping its own source closes the popup,
        # exactly as tapping the source while composing does
        ui.edit_order(state, 0)
        _click(state, ui, home)
        assert ui.mode == IDLE and ui.selected is None and ui.sel_order is None
        assert len(ui.pending) == 1
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# The wheel over the side panel
# --------------------------------------------------------------------------- #
def test_wheel_over_the_panel_leaves_the_map_alone():
    """The wheel zooms over the map only; from the info panel a stray flick would
    throw the camera about."""
    state, ui = _setup()
    try:
        zoom = ui.view.zoom
        panel = (config.SCREEN_W - config.HUD_RIGHT_W // 2, config.SCREEN_H // 2)
        pygame.mouse.set_pos(panel)
        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1), state, ui)
        assert ui.view.zoom == zoom, "the wheel must not zoom the map from the panel"

        pygame.mouse.set_pos((config.SCREEN_W // 4, config.SCREEN_H // 2))
        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.view.zoom > zoom, "over the map the wheel still zooms"
    finally:
        pygame.quit()


def _knock_out_the_human(state) -> None:
    """Hand every human system to seat 2 and resolve a turn, so the engine's win
    check marks the human defeated exactly as a real loss would."""
    for sys in state.systems.values():
        if sys.owner_id == 1:
            sys.owner_id = 2
    engine.end_turn(state, decide=lambda s, pid: [])
    assert state.is_defeated(1) and state.winner is None   # out, but 2 v 3 plays on


def test_defeat_reveals_the_board_instead_of_fogging_it(monkeypatch):
    """A knocked-out human owns nothing to see *from*, so fog.observe returns
    nothing and every remembered system fell back to a grey "?" — the whole map,
    when fog was off. Losing now makes you a spectator with the board revealed."""
    state, ui = _setup()
    try:
        monkeypatch.setattr(config, "FOG_SIGHT", 1)
        monkeypatch.setattr(config, "FOG_SCOUT", 1)
        main.refresh_fog(state, ui)
        assert ui.visible != set(state.systems), "still playing: fog applies"

        _knock_out_the_human(state)
        main.refresh_fog(state, ui)
        assert ui.visible == set(state.systems)   # not a map full of "?"
        assert ui.seen == set(state.systems)
        assert ui.player_intel == {}, "everyone is in sight: live stats, not intel"
    finally:
        pygame.quit()


def test_fast_forward_only_applies_while_spectating_a_lost_game():
    """It is the 'just show me who wins' control, so it exists exactly between the
    human's defeat and the result — and pressing F elsewhere must change nothing."""
    state, ui = _setup()
    try:
        assert ui.can_fast_forward(state) is False
        main.toggle_fast_forward(state, ui)
        assert (ui.fast_forward, ui.playing) == (False, False), "no-op while in the game"

        _knock_out_the_human(state)
        assert ui.can_fast_forward(state) is True
        main.toggle_fast_forward(state, ui)
        assert ui.fast_forward is True
        assert ui.playing is True, "a paused board would make the button look broken"
        assert main.step_delay(ui, main.AUTOPLAY_MS) == main.FAST_FORWARD_MS

        main.toggle_fast_forward(state, ui)      # ...and back to watching it slowly
        assert ui.fast_forward is False
        assert main.step_delay(ui, main.AUTOPLAY_MS) == main.AUTOPLAY_MS

        state.winner = 2                         # decided: nothing left to rush past
        assert ui.can_fast_forward(state) is False
    finally:
        pygame.quit()


def test_fast_forward_is_reachable_by_key_and_by_button():
    """F and the footer button return the same action, and the button answers even
    under autoplay (like H/A/R/M) since that is when it is most wanted."""
    state, ui = _setup()
    try:
        key = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_f, mod=0, unicode="f")
        assert game_input.handle_event(key, state, ui) == "toggle_fast_forward"

        ui.autoplay = True
        ui.fast_forward_rect = (100, 100, 120, 24)
        assert _click_pos(state, ui, (110, 110)) == "toggle_fast_forward"
    finally:
        pygame.quit()


def test_challenge_settings_names_the_replay_the_score_was_made_in():
    """`Challenge.log` is what lets the leaderboard find the game behind a score,
    and it has to survive the link the player actually pastes."""
    state, ui = _setup()
    try:
        settings = Settings(nodes=18, players=3)
        log = replay.new_log(settings, 4821)
        log.path = None
        ui.hand_turns = 3
        state.turn = 40
        shared = main.challenge_settings(settings, state, ui, 4821, log)
        assert shared.challenge.log == log.match_id
        assert Settings.from_token(shared.to_token()).challenge.log == log.match_id
    finally:
        pygame.quit()


def test_posting_a_score_uploads_the_replay_behind_it(monkeypatch):
    """Pressing "Post to leaderboard" is what consents to the upload, so this is
    the one path that sends a log — and it sends it under the same setup key the
    score is filed against."""
    sent: list[tuple] = []
    monkeypatch.setattr(main.share, "post_log",
                        lambda log, key: sent.append((log.match_id, key)) or True)
    monkeypatch.setattr(main.webstore, "open_url", lambda url: True)
    state, ui = _setup()
    try:
        settings = Settings(nodes=18, players=3)
        log = replay.new_log(settings, 77)
        log.path = None
        ui.hand_turns = 1
        state.turn = 50
        main.post_to_leaderboard(settings, state, ui, 77, log)
        shared = main.challenge_settings(settings, state, ui, 77, log)
        assert sent == [(log.match_id, shared.challenge.key)]
    finally:
        pygame.quit()


def test_sharing_a_challenge_link_uploads_nothing(monkeypatch):
    """The other half of the consent rule: handing a friend a link is not posting
    a score, and must not put the game on anyone's server."""
    sent: list[tuple] = []
    monkeypatch.setattr(main.share, "post_log",
                        lambda log, key: sent.append((log, key)) or True)
    monkeypatch.setattr(main.webstore, "copy_link", lambda token: True)
    state, ui = _setup()
    try:
        settings = Settings(nodes=18, players=3)
        log = replay.new_log(settings, 77)
        log.path = None
        ui.hand_turns = 1
        state.turn = 50
        main.share_challenge(settings, state, ui, 77, log)
        assert sent == []
    finally:
        pygame.quit()


def test_a_checkpoint_and_a_posted_score_agree_on_the_setup_key():
    """A game uploaded mid-play and the score posted at the end must land on one
    key, or the replay behind a score could not be found from it."""
    state, ui = _setup()
    try:
        settings = Settings(nodes=18, players=3)     # seed None: rolled at start
        log = replay.new_log(settings, 4821)
        log.path = None
        ui.hand_turns = 2
        state.turn = 30
        shared = main.challenge_settings(settings, state, ui, 4821, log)
        assert log.setup_key() == shared.challenge.key
    finally:
        pygame.quit()


def _shared_game(monkeypatch, tmp_path, *, on: bool):
    """Play six turns with "Share replays" set to ``on``, capturing uploads."""
    sent: list[tuple] = []
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    monkeypatch.setattr(main.share.webstore, "share_games", lambda: on)
    monkeypatch.setattr(main.share, "post_log",
                        lambda log, key: sent.append((log.turn_count, key)) or True)
    monkeypatch.setattr(main.share, "CHECKPOINT_TURNS", 3)
    state, ui = _setup()
    settings = Settings(seed=1, nodes=18, players=3)
    log = replay.new_log(settings, 1)
    log.path = tmp_path / "game.json"
    for _ in range(6):
        main.resolve_turn(state, ui, log, settings)
    return sent, log


def test_a_shared_game_uploads_on_the_cadence(monkeypatch, tmp_path):
    """The point of checkpointing: a game abandoned rather than finished is still
    stored up to wherever it was left."""
    try:
        sent, log = _shared_game(monkeypatch, tmp_path, on=True)
        assert [turns for turns, _ in sent] == [3, 6]
        assert {key for _, key in sent} == {log.setup_key()}
    finally:
        pygame.quit()


def test_an_unshared_game_uploads_nothing_while_it_is_played(monkeypatch, tmp_path):
    """Off is off: the default sends nothing at all, whatever the cadence says."""
    try:
        sent, _ = _shared_game(monkeypatch, tmp_path, on=False)
        assert sent == []
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Watching a posted replay (`#log=<id>`)
# --------------------------------------------------------------------------- #
def test_a_log_fragment_is_read_as_a_replay_request(monkeypatch):
    """A settings token is base64url, which cannot contain '=' except as the
    padding the encoder strips — so the prefix is an unambiguous discriminator."""
    monkeypatch.setattr(main.webstore, "url_token", lambda: "log=00112233445566ff")
    assert main.replay_request() == "00112233445566ff"
    monkeypatch.setattr(main.webstore, "url_token", lambda: "eNrtVNtu4jAQ")
    assert main.replay_request() == ""
    monkeypatch.setattr(main.webstore, "url_token", lambda: "")
    assert main.replay_request() == ""


# --------------------------------------------------------------------------- #
# Opening a gauntlet link (`#g=<id>`, `#gauntlet=<payload>`)
# --------------------------------------------------------------------------- #
def _linked_gauntlet(gid="hard-mode-0123456789ab"):
    return {"id": gid, "name": "Hard mode",
            "stages": [{"id": 0, "title": "One", "settings": Settings(players=2, nodes=8, seed=3).to_dict()}]}


@pytest.fixture
def _gauntlet_store(monkeypatch, tmp_path):
    monkeypatch.setattr(main.webstore, "_file_path", lambda: tmp_path / "kv.json")
    return main.MenuState()


def test_a_gauntlet_fragment_is_read_as_a_gauntlet_request(monkeypatch):
    monkeypatch.setattr(main.webstore, "url_token", lambda: "g=hard-mode-0123456789ab")
    assert main.gauntlet_request() == ("hard-mode-0123456789ab", None)
    monkeypatch.setattr(main.webstore, "url_token", lambda: main.gauntlet.link_fragment(_linked_gauntlet()))
    assert main.gauntlet_request() == (None, _linked_gauntlet())
    monkeypatch.setattr(main.webstore, "url_token", lambda: "log=00112233445566ff")
    assert main.gauntlet_request() == (None, None)


def test_an_inline_gauntlet_opens_the_tutorial_list_on_it(_gauntlet_store):
    ms = _gauntlet_store
    assert main.open_linked_gauntlet(ms, Settings(), None, _linked_gauntlet()) is None
    assert ms.tutorial_open
    assert ms.gauntlets[ms.gauntlet_pick].id == "hard-mode-0123456789ab"


def test_a_gauntlet_already_kept_opens_without_asking_the_board(_gauntlet_store, monkeypatch):
    ms = _gauntlet_store
    main.gauntlet.remember(_linked_gauntlet())
    monkeypatch.setattr(main.gauntlet, "fetch", lambda gid: pytest.fail("fetched a kept gauntlet"))
    assert main.open_linked_gauntlet(ms, Settings(), "hard-mode-0123456789ab", None) is None
    assert ms.tutorial_open and ms.gauntlets[ms.gauntlet_pick].id == "hard-mode-0123456789ab"


def test_a_fetched_gauntlet_is_kept_and_opened(_gauntlet_store, monkeypatch):
    ms = _gauntlet_store
    monkeypatch.setattr(main.gauntlet, "fetch", lambda gid: "pending")
    assert main.open_linked_gauntlet(ms, Settings(), "hard-mode-0123456789ab", None) == "pending"
    assert ms.status == main.GAUNTLET_OPENING_MSG and not ms.tutorial_open
    reply = json.dumps({"id": "hard-mode-0123456789ab", "gauntlet": _linked_gauntlet()})
    main.land_gauntlet(ms, Settings(), main.pbp.OK, reply)
    assert ms.tutorial_open and ms.gauntlets[ms.gauntlet_pick].id == "hard-mode-0123456789ab"
    assert [g.id for g in main.gauntlet.remembered()] == ["hard-mode-0123456789ab"]


@pytest.mark.parametrize("state,body,msg", [
    ("missing", '{"error": "no such gauntlet"}', "GAUNTLET_MISSING_MSG"),
    ("error", "", "GAUNTLET_UNREACHABLE_MSG"),
    ("ok", "<html>", "GAUNTLET_UNREADABLE_MSG"),
])
def test_a_gauntlet_that_cannot_be_had_says_why(_gauntlet_store, state, body, msg):
    ms = _gauntlet_store
    main.land_gauntlet(ms, Settings(), state, body)
    assert ms.status == getattr(main, msg) and not ms.status_ok
    assert not ms.tutorial_open and main.gauntlet.remembered() == []


def test_no_board_means_the_gauntlet_link_says_so(_gauntlet_store, monkeypatch):
    ms = _gauntlet_store
    monkeypatch.setattr(main.gauntlet.webstore, "leaderboard_url", lambda path: "")
    assert main.open_linked_gauntlet(ms, Settings(), "hard-mode-0123456789ab", None) is None
    assert ms.status == main.GAUNTLET_UNREACHABLE_MSG


# --------------------------------------------------------------------------- #
# Generating a gauntlet from a spec link (`#gauntlet-spec=`)
# --------------------------------------------------------------------------- #
_SPEC = {"stages": 2, "seed": 9, "candidates": 1, "rules": {"players": {"fix": 2}, "systems": {"range": [8, 10]}},
         "opponents": ["rusherplus", "heuristic"]}


def test_a_spec_link_generates_a_frame_at_a_time_then_opens_the_list(_gauntlet_store, monkeypatch):
    ms, settings = _gauntlet_store, Settings(home_production=5)
    assert main.start_generation(ms, "g=hard-mode-0123456789ab") is None
    job = main.start_generation(ms, main.gauntletgen.spec_fragment(_SPEC))
    assert job is not None and ms.status.startswith("Generating a gauntlet: 0%")
    monkeypatch.setattr(main, "GAUNTLET_GEN_BUDGET_S", 0.0)
    frames = 0
    while job is not None:
        job = main.pump_generation(ms, settings, job)
        frames += 1
        # A probe's setup never leaks into what the menu draws.
        assert main.config.HOME_PRODUCTION == 5
    assert frames > 1 and ms.tutorial_open and ms.status_ok
    picked = ms.gauntlets[ms.gauntlet_pick]
    assert picked.id.startswith("gen-") and ms.status == f"Generated {picked.name}: {len(picked.stages)} stages"
    assert [g.id for g in main.gauntlet.remembered()] == [picked.id]


def test_a_spec_link_that_can_never_deal_says_why(_gauntlet_store):
    ms = _gauntlet_store
    frag = main.gauntletgen.spec_fragment({"opponents": []})
    assert main.start_generation(ms, frag) is None
    assert ms.status == "Can't generate that gauntlet: no opponents are allowed" and not ms.status_ok


def test_copy_link_sends_a_kept_gauntlet_as_a_whole_file(_gauntlet_store, monkeypatch):
    ms = _gauntlet_store
    copied = []
    monkeypatch.setattr(main.menu, "is_web", lambda: True)
    monkeypatch.setattr(main.webstore, "link_url", lambda token: f"https://x/game/#{token}")
    monkeypatch.setattr(main.webstore, "copy_to_clipboard", lambda text: copied.append(text) or True)
    main.gauntlet.remember(_linked_gauntlet())
    main.menu.open_gauntlet(ms, Settings(), "hard-mode-0123456789ab")
    g = ms.gauntlets[ms.gauntlet_pick]
    assert main.menu._shareable(ms, g)
    assert not main.menu._shareable(ms, main.gauntlet.builtin()[0])
    main.menu._copy_gauntlet(ms, g)
    token = copied[0].split("#", 1)[1]
    assert main.gauntlet.inline_data(token) == _linked_gauntlet() and ms.status_ok


def test_the_list_button_says_tutorial_until_there_is_another_gauntlet(_gauntlet_store, monkeypatch):
    monkeypatch.setattr(main.gauntlet, "user_dir", lambda: main.webstore._file_path().parent / "none")
    assert main.menu._gauntlets_label(main.MenuState()) == "Tutorial"
    main.gauntlet.remember(_linked_gauntlet())
    assert main.menu._gauntlets_label(main.MenuState()) == "Gauntlets"


def test_leaving_a_gauntlet_that_is_not_the_tutorial_says_gauntlet(_gauntlet_store):
    ms = _gauntlet_store
    ms.stage_at = ("tutorial", 0)
    assert main.menu._tutorial_labels(ms)[0] == "Leave tutorial"
    ms.stage_at = ("hard-mode-0123456789ab", 0)
    assert main.menu._tutorial_labels(ms)[0] == "Leave gauntlet"
    main.menu._leave_gauntlet(ms, Settings())
    assert "gauntlet" in ms.status and "tutorial" not in ms.status


def _watchable_log(tmp_path, monkeypatch, turns=6):
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    state, ui = _setup()
    settings = Settings(seed=1, nodes=18, players=3)
    log = replay.new_log(settings, 1)
    log.path = tmp_path / "game.json"
    for _ in range(turns):
        main.resolve_turn(state, ui, log, settings)
    return log


def test_a_downloaded_replay_opens_as_a_reviewable_game(tmp_path, monkeypatch):
    """The whole feature: the blob off the wire becomes the same object a resumed
    save is, so the existing scrubber reviews it and a rewind can fork it."""
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        settings = Settings()          # whatever the menu happened to be showing
        opened = main.open_replay(log.encoded(), settings)
        assert opened is not None
        state, _ui, restored = opened
        assert restored.match_id == log.match_id
        assert state.turn == log.turn_count
        # ...and the menu now describes the replay's setup, not its own.
        assert settings.seed == 1 and settings.nodes == 18
    finally:
        pygame.quit()


def test_a_watched_replay_is_not_ours_to_post(tmp_path, monkeypatch):
    """Somebody else's win, on our screen with their full score under it — the one
    result that arrives looking exactly like an earned one, because it *is* one.
    Without the mark, posting their score as ours is a fetched link and one press.
    """
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        state, ui, _ = main.open_replay(log.encoded(), Settings())
        state.winner = ui.human_id           # their win, replayed back in full
        assert ui.hand_turns > 0             # ...by hand, so only `watched` stops it
        assert ui.watched is True
        assert ui.can_post(state) is False
        # A retry or a rewind out of review builds a fresh Ui, and what is played
        # from there really is ours — see `Ui.can_post` on why that stays open.
        assert main.resume_game(log, Settings())[1].watched is False
    finally:
        pygame.quit()


def test_opening_a_replay_enters_review_at_its_opening_position(tmp_path, monkeypatch):
    """A watched game is one you have not seen, so it starts where it started.
    Landing on the final board would give away the ending and leave dragging all
    the way back as the only way to actually watch it."""
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        state, ui, _ = main.open_replay(log.encoded(), Settings())
        states, fog, _events, live = main.open_history(state, ui, log)
        assert ui.history and not ui.playing
        assert len(states) == len(fog) == log.turn_count + 1   # ...including turn 0
        assert ui.history_max == log.turn_count
        assert ui.history_turn == 0
        assert live is not None          # the live fog is stashed for the way out
    finally:
        pygame.quit()


def test_a_saved_game_file_opens_as_a_watched_replay(tmp_path, monkeypatch):
    """`--watch games/game_X.json` takes the downloaded path from the poll on, so
    a local file gets the same review, the same watched mark and the same fork."""
    from starconquest import share
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        assert log.path is not None and log.path.is_file()
        assert main.is_log_path(str(log.path))
        assert not main.is_log_path("00112233445566ff")
        status, body = share.Download.ready(main.read_game_log(str(log.path)).encoded()).poll()
        assert status == share.OK
        opened = main.open_replay(body, Settings())
        assert opened is not None
        state, ui, restored = opened
        assert restored.match_id == log.match_id
        assert state.turn == log.turn_count
        assert ui.watched is True
    finally:
        pygame.quit()


def test_settings_from_a_saved_game_reproduce_its_map(tmp_path, monkeypatch):
    """`--settings-from` hands the menu the log's setup with its concrete seed (the
    stored settings say `seed: null`), and never the score to beat it carried."""
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        challenged = Settings.from_dict(log.settings)
        challenged.challenge = Challenge(turns=9, lost=3)
        log.settings = dict(challenged.to_dict(), seed=None)
        log.save()
        settings = main.log_settings(main.read_game_log(str(log.path)))
        assert settings.seed == log.seed
        assert settings.nodes == 18 and settings.players == 3
        assert settings.challenge is None
    finally:
        pygame.quit()


def test_a_file_that_is_not_a_game_log_is_refused(tmp_path):
    """A typo, a saved setup and junk all fail with a message, never a traceback."""
    setup = tmp_path / "setup.json"
    Settings(nodes=20).save(setup)
    junk = tmp_path / "junk.json"
    junk.write_text("[1, 2, 3]")
    for raw in (str(tmp_path / "missing.json"), str(setup), str(junk)):
        try:
            main.read_game_log(raw)
        except ValueError:
            continue
        raise AssertionError(f"{raw} was read as a game log")


def test_reviewing_our_own_game_still_opens_where_we_are(tmp_path, monkeypatch):
    """The H key asks a different question — 'what just happened' — and the answer
    is one step back from the latest turn, not a whole match away from it."""
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        state, ui = main.resume_game(log, Settings())
        assert ui.watched is False
        main.open_history(state, ui, log)
        assert ui.history_turn == ui.history_max == log.turn_count
    finally:
        pygame.quit()


def test_resuming_a_match_always_lands_paused(tmp_path, monkeypatch):
    """You go back to a turn in order to *look* at it.

    Spotting a bot's blunder one turn too late, rewinding to it, and having the
    board start moving again before it can be read is the exact thing history is
    for. Waiting decides nothing now: autoplay is purely "is anything advancing",
    since the seat is claimed by ending a turn under manual control
    (`engine._claim_seat`) rather than by autoplay being off — so whether the bot
    plays on or the player takes over is a choice handed back with the clock
    stopped, from a resume, a rewind or a watched replay alike.
    """
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        settings = Settings(seed=1, nodes=18, players=3, autoplay=True)
        assert main.resume_game(log, settings)[1].autoplay is False
        assert main.apply_rewind(log, settings, 3)[1].autoplay is False
    finally:
        pygame.quit()


def test_junk_off_the_wire_is_not_a_game(tmp_path, monkeypatch):
    """A 404 body, a truncated download or an empty match all have to land as
    "no replay" rather than as an exception on the first frame."""
    try:
        _watchable_log(tmp_path, monkeypatch, turns=0)
        for blob in ("", "no replay", "not base64!!"):
            assert main.open_replay(blob, Settings()) is None
        empty = replay.new_log(Settings(seed=1), 1)
        assert main.open_replay(empty.encoded(), Settings()) is None
    finally:
        pygame.quit()


def test_an_outdated_replay_is_declined_rather_than_misreplayed(tmp_path, monkeypatch):
    """A log stamped under rules the engine has since moved past would silently
    rebuild a different game than the one actually played (`GameLog.is_current`),
    so it is turned away exactly like an unreadable blob — never shown as if it
    still reproduced the match."""
    try:
        log = _watchable_log(tmp_path, monkeypatch)
        log.rules_version = engine.RULES_VERSION + 1
        assert main.open_replay(log.encoded(), Settings()) is None
        # ...and told apart from a genuinely unreadable blob, for the status line.
        decoded = main._decode_log(log.encoded())
        assert decoded is not None and decoded.is_current is False
        assert main._decode_log("not base64!!") is None
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Skipping a turn playback
# --------------------------------------------------------------------------- #


def _a_film() -> turnfilm.Film:
    """A film with something in it, so `plays` and the beats are real."""
    return turnfilm.film([
        turnfilm.Advanced(((0, 2),)),
        turnfilm.Landed(node_id=0, fleets=(), was_owner=1, was_ships=3,
                        owner_id=1, ships=3, prod_progress=0, steps=()),
    ])


def test_a_press_skips_a_live_film_and_does_nothing_else():
    """The press is consumed. The footer is still drawn during a playback (the
    board's winner stays None until the turn closes), so a press that fell through
    could resolve a second turn underneath the one still being drawn."""
    state, ui = _setup()
    try:
        ui.film, ui.film_ms = _a_film(), 100.0
        ui.end_turn_rect = (0, 0, 0, 0)   # render takes the button away meanwhile
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r")
        assert game_input.handle_event(ev, state, ui) is None
        assert ui.film is None and ui.film_ms == 0.0
        # ...and a click is swallowed rather than selecting anything
        state, ui = _setup()
        ui.film = _a_film()
        assert _click(state, ui, 0) is None
        assert ui.film is None
        assert ui.selected is None and ui.mode == IDLE
    finally:
        pygame.quit()


def test_a_press_in_history_drops_the_film_and_still_seeks():
    """In review the film is a transition and the controls are a scrubber, so the
    press must fall through — swallowing it would mean a drag never started."""
    state, ui = _setup()
    try:
        ui.history, ui.history_max, ui.history_turn = True, 8, 3
        ui.film, ui.playing = _a_film(), True
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RIGHT, mod=0, unicode="")
        game_input.handle_event(ev, state, ui)
        assert ui.film is None            # dropped
        assert ui.history_turn == 4       # ...and the seek still happened
        assert ui.playing is False
    finally:
        pygame.quit()


def test_hovering_or_zooming_leaves_a_film_running():
    """Only a press skips. A wheel zoom or a mouse move must not throw away the
    playback you are watching."""
    state, ui = _setup()
    try:
        ui.film = _a_film()
        pos = ui.view.to_screen(state.systems[0].pos)
        game_input.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos,
                                                   rel=(1, 1), buttons=(0, 0, 0)),
                                state, ui)
        assert ui.film is not None
        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, y=1, x=0,
                                                   flipped=False, which=0),
                                state, ui)
        assert ui.film is not None
        game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos,
                                                   button=1), state, ui)
        assert ui.film is not None
    finally:
        pygame.quit()


def _a_moving_reel(state):
    """A reel whose film is one move beat, on a board with a fleet to move."""
    board = turnfilm.copy_board(state)
    home = next(s.id for s in board.systems.values() if s.owner_id == 1)
    board.fleets = [Fleet(owner_id=1, source_id=home,
                          dest_id=board.systems[home].neighbors[0], ships=3,
                          turns_total=3, turns_remaining=3)]
    return turnfilm.Reel(board, turnfilm.film([turnfilm.Advanced(((0, 2),))]))


def test_a_chained_film_starts_where_the_last_one_overran():
    """A frame steps the clock by a whole frame's worth, so the frame that lands
    a film almost always runs past its end. A run of turns is meant to be one
    continuous glide, so that overrun is paid into the film that follows rather
    than dropped — dropping it stalls every fleet for the rest of a frame at
    every join, which is the stutter that told autoplay apart from history."""
    state, ui = _setup()
    try:
        reel = _a_moving_reel(state)
        ui.film, ui.film_ms = reel.film, 0.0
        main._carry_into(ui, reel, 20.0)
        assert ui.film_ms == 20.0
        # ...and nothing to carry leaves the film at its own opening.
        reel = _a_moving_reel(state)
        ui.film, ui.film_ms = reel.film, 0.0
        main._carry_into(ui, reel, 0.0)
        assert ui.film_ms == 0.0
    finally:
        pygame.quit()


def test_a_carry_longer_than_the_film_lands_it_rather_than_overrunning():
    """A frame is capped (`config.MAX_FRAME_MS`) but a film is not bounded below
    by it, so the debt is clamped too: the next frame lands this film and chains
    on, instead of a playhead sitting past its own end."""
    state, ui = _setup()
    try:
        reel = _a_moving_reel(state)
        ui.film, ui.film_ms = reel.film, 0.0
        main._carry_into(ui, reel, reel.film.total_ms * 10)
        assert ui.film_ms == reel.film.total_ms
    finally:
        pygame.quit()


def test_the_camera_controls_leave_a_film_running_and_still_work():
    """Where you are looking changes nothing about the turn being played back, so
    the cluster is exempt from "any press skips" the way Play/Pause is.

    It is unusable otherwise under autoplay, where films chain back to back: the
    press would be spent skipping one, and the next turn's would already be up by
    the time a second arrived."""
    state, ui = _setup()
    try:
        ui.film = _a_film()
        ui.reset_view_rect, ui.zoom_minus_rect = (10, 10, 40, 40), (60, 10, 40, 40)
        ui.zoom_plus_rect = (110, 10, 40, 40)
        before = ui.view.zoom

        assert game_input.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(120, 20), button=1),
            state, ui) is None
        assert ui.film is not None          # still playing
        assert ui.view.zoom > before        # ...and the press zoomed

        ui.view.zoom_at((600, 460), 3.0)
        game_input.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r, mod=0, unicode="r"),
            state, ui)
        assert ui.film is not None
        assert ui.view.zoom < 3.0           # R re-framed without losing the film
    finally:
        pygame.quit()


def test_a_press_next_to_the_camera_cluster_still_skips():
    """The exemption is the three rects and the R key, not "anything on the map":
    a press that misses them is an ordinary press and skips as before."""
    state, ui = _setup()
    try:
        ui.film = _a_film()
        ui.reset_view_rect = (10, 10, 40, 40)
        ui.zoom_minus_rect = ui.zoom_plus_rect = (0, 0, 0, 0)
        assert game_input.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(200, 200), button=1),
            state, ui) is None
        assert ui.film is None
    finally:
        pygame.quit()


def test_take_control_stops_an_autoplaying_film_in_one_press():
    """Play/Pause is hidden under autoplay (it would be a no-op — the turn
    advance isn't gated on `ui.playing` there), which makes Autoplay / Take
    control the *only* way to stop the automatic advance — and films chain with
    no gap under autoplay, so it has to fire on the very press that lands on a
    running one, or the next turn's film is up before a second press arrives.

    Unlike Play/Pause it doesn't need to freeze anything: the film keeps
    playing (nothing is lost), and control is simply back the instant it lands,
    since `main`'s chaining re-reads `ui.autoplay` fresh at that point."""
    state, ui = _setup()
    try:
        ui.autoplay = True
        ui.autoplay_button_rect = (10, 10, 80, 30)
        ui.film = _a_film()

        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a, mod=0, unicode="a")
        assert game_input.handle_event(ev, state, ui) == "toggle_autoplay"
        assert ui.film is not None          # kept playing, not discarded

        ui.autoplay, ui.film = True, _a_film()   # click does the same
        assert game_input.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(20, 20), button=1),
            state, ui) == "toggle_autoplay"
        assert ui.film is not None
    finally:
        pygame.quit()


def _won_with_animation(monkeypatch, playing: bool = False):
    """A human win *worth watching*, with turn animation on: the reel plus its Ui.

    The last rival holds one system and a fleet is one turn out from it, so the
    turn that decides the game has a move and a fight in it. A board with nothing
    left in transit resolves to a film of instants (`Film.plays` is False) and
    would never defer anything — which is right, and no test of the deferral.

    ``playing`` resolves the turn as play mode's own loop does rather than as a
    press of End Turn, which is the one thing `resolve_turn` reads it for.
    """
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: True)
    state, ui = _setup()
    ui.playing = playing
    home = next(s.id for s in state.systems.values() if s.owner_id == 1)
    last = state.systems[home].neighbors[0]
    for s in state.systems.values():
        s.owner_id = 1                     # hand the human all but one system
    state.systems[last].owner_id, state.systems[last].ships = 2, 1
    state.fleets = [Fleet(owner_id=1, source_id=home, dest_id=last, ships=40,
                          turns_total=2, turns_remaining=1)]
    ui.view.zoom_at((600, 460), 2.0)       # ...watching the corner it happens in
    reel = main.resolve_turn(state, ui, log=None)
    assert state.winner == 1
    assert reel is not None and ui.film is not None
    return state, ui, reel


def test_resolve_turn_lets_its_combat_linger(monkeypatch):
    """A live End Turn asks `turnfilm.film` to linger (`main.resolve_turn`'s
    `linger=True`) — unlike history playback, which must glide straight through
    (see `main._next_history_film`'s own test)."""
    _state, ui, _reel = _won_with_animation(monkeypatch)
    combat = next(b for b in ui.film.beats if b.kind == "combat")
    assert combat.ms == config.FILM_LINGER_COMBAT_MS
    assert ui.film.total_ms > combat.end   # a trailing hold, not an instant end


def test_play_mode_asks_for_a_film_that_glides_instead_of_lingering(monkeypatch):
    """Play mode and history playback are one behaviour from two sources: a *run*
    of turns has to glide, so only a turn ended by hand lingers. Lingering here
    stopped the run dead for every fight (`FILM_LINGER_COMBAT_MS`) and held the
    board after it (`FILM_LINGER_HOLD_MS`), on top of the `PLAY_MS` gap the loop
    now chains past."""
    _state, ui, _reel = _won_with_animation(monkeypatch, playing=True)
    combat = next(b for b in ui.film.beats if b.kind == "combat")
    assert combat.ms == config.FILM_COMBAT_MS == 0
    assert ui.film.total_ms == combat.end      # nothing left to wait out


def test_a_reel_comes_back_already_at_its_first_instant(monkeypatch):
    """`main._primed`. A chained turn — play mode's or history's — is built inside
    the per-frame update, past the point where a running film is stepped, so it is
    drawn once before any `run_to` reaches it. Un-advanced, that frame draws every
    continuing fleet a whole turn's worth of progress behind where it just was."""
    _state, _ui, reel = _won_with_animation(monkeypatch, playing=True)
    due = [e for at, e in reel.film.cues if at <= 0.0]
    assert due and reel.at == len(due)
    assert reel.run_to(0.0) == []              # ...nothing of it left to apply
    flying = next(f for f in reel.board.fleets if f.owner_id == 1)
    assert flying.turns_remaining == 0         # advanced, not still one turn out


def test_the_deciding_turn_plays_out_before_the_camera_gives_the_map_away(monkeypatch):
    """The snap that reveals the whole board is the deciding turn's *ending*.
    Doing it first would play the last turn out on a map it had already given
    away, and yank the frame out from under the fight being watched."""
    state, ui, _reel = _won_with_animation(monkeypatch)
    try:
        assert ui.deferred_view_snap
        assert ui.view.zoom == 2.0          # still framed where you were watching
        main.land_film(state, ui)
        assert ui.view.zoom == 1.0          # ...and revealed once it lands
        assert not ui.deferred_view_snap and ui.film is None
    finally:
        pygame.quit()


def test_skipping_the_last_film_still_reveals_the_board(monkeypatch):
    """A skip must not be able to lose the reveal: `Ui.stop_film` deliberately
    leaves the debt, and `main.land_film` — which both endings pass through —
    pays it."""
    state, ui, _ = _won_with_animation(monkeypatch)
    try:
        ui.stop_film()                      # what a press does, in `input`
        assert ui.deferred_view_snap        # ...which is not the whole of it
        assert ui.view.zoom == 2.0
        main.land_film(state, ui)           # what the loop does on dropping the reel
        assert ui.view.zoom == 1.0
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Pausing a playback (rather than skipping it)
# --------------------------------------------------------------------------- #


def test_play_toggle_does_not_skip_a_running_film():
    """P (and its footer button) used to fall under the blanket "any press skips
    a playback" rule, so pausing lost the animation just like any other key would.
    It must not: `input` special-cases it so the film survives the press, and
    `main.apply_toggle_play` is what actually freezes it (see the tests below)."""
    state, ui = _setup()
    try:
        ui.film, ui.playing = _a_film(), True
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)
        assert game_input.handle_event(ev, state, ui) == "toggle_play"
        assert ui.film is not None

        ui.play_pause_rect = (100, 100, 120, 32)
        ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(110, 110), button=1)
        assert game_input.handle_event(ev, state, ui) == "toggle_play"
        assert ui.film is not None
    finally:
        pygame.quit()


def test_play_toggle_in_history_still_falls_through_to_the_scrubber():
    """The history dispatch must still see this press — it is what actually flips
    `ui.playing` — even though `input`'s top guard no longer discards the film for
    it."""
    state, ui = _setup()
    try:
        ui.history, ui.playing = True, True
        ui.film = _a_film()
        ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)
        assert game_input.handle_event(ev, state, ui) == "toggle_play"
        assert ui.film is not None
    finally:
        pygame.quit()


def test_pausing_an_active_playthrough_freezes_the_film():
    """Toggling play *off* while a film is running must not lose it — it should
    freeze in place instead, so a paused review still shows what the turn did
    rather than the plain board a skip leaves behind."""
    _state, ui = _setup()
    try:
        ui.film, ui.playing = _a_film(), True
        main.apply_toggle_play(ui)
        assert ui.playing is False
        assert ui.film is not None
        assert ui.film_paused is True
    finally:
        pygame.quit()


def test_starting_play_while_a_manual_film_runs_does_not_freeze_it():
    """A single manually-triggered film runs with `playing` False throughout (it
    was never "playing" a sequence). Toggling play *on* in that state must leave it
    running rather than pausing it — the button reads "Play", not "Pause"."""
    _state, ui = _setup()
    try:
        ui.film, ui.playing = _a_film(), False
        main.apply_toggle_play(ui)
        assert ui.playing is True
        assert ui.film is not None
        assert ui.film_paused is False
    finally:
        pygame.quit()


def test_stop_film_clears_a_pause_too():
    _state, ui = _setup()
    try:
        ui.film, ui.film_paused = _a_film(), True
        ui.stop_film()
        assert ui.film_paused is False
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Chaining animated history turns with no gap between them
# --------------------------------------------------------------------------- #


def test_next_history_film_chains_straight_into_an_animated_turn(monkeypatch):
    """Right after one turn's film lands, the turn after it should be ready to go
    immediately if it also has something to show — no `PLAY_MS` gap stitched
    between two animated turns."""
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: True)
    state, ui = _setup()
    try:
        dest = state.systems[0].neighbors[0]
        state.fleets = [Fleet(owner_id=1, source_id=0, dest_id=dest, ships=5,
                              turns_total=4, turns_remaining=2)]
        ui.history_turn, ui.history_max = 0, 2
        history_states = [state, state, state]
        history_fog = [(set(state.systems), set(), {})] * 3
        history_events = [[], [turnfilm.Advanced(((0, 2),))], []]

        result = main._next_history_film(ui, history_states, history_fog, history_events)
        assert result is not None
        assert ui.film is not None and ui.film.plays
        assert ui.film_ms == 0.0
        assert ui.film_paused is False
    finally:
        pygame.quit()


def test_next_history_film_does_not_snap_a_continuing_fleet_back_a_turn(monkeypatch):
    """A chained reel is handed straight to `render.draw` in the same frame it's
    built — unlike a live End Turn, which always gets a `run_to` call first (see
    `main`'s per-frame update) — so without applying this turn's launch/first
    advance immediately, a continuing fleet would draw one frame at *last*
    turn's un-advanced position: a visible snap back by a whole turn's worth of
    progress before the next frame caught it back up. `_next_history_film` must
    leave the reel already caught up to this instant.
    """
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: True)
    state, ui = _setup()
    try:
        dest = state.systems[0].neighbors[0]
        # At the end of the turn just watched: 2 of 4 steps remaining.
        state.fleets = [Fleet(owner_id=1, source_id=0, dest_id=dest, ships=5,
                              turns_total=4, turns_remaining=2)]
        end_of_last_turn_progress = state.fleets[0].progress_at(1.0)

        ui.history_turn, ui.history_max = 0, 1
        history_states = [state, state]
        history_fog = [(set(state.systems), set(), {})] * 2
        history_events = [[], [turnfilm.Advanced(((0, 1),))]]   # 2 -> 1 this turn

        reel = main._next_history_film(ui, history_states, history_fog, history_events)
        assert reel is not None
        travel = ui.film.travel(0.0)   # what `render._travel` reads on this exact frame
        assert reel.board.fleets[0].progress_at(travel) == end_of_last_turn_progress
    finally:
        pygame.quit()


def test_next_history_film_never_lingers_on_combat(monkeypatch):
    """History playback must glide continuously even through a fight — unlike a
    live End Turn (`test_resolve_turn_lets_its_combat_linger`), it never asks
    `turnfilm.film` to linger."""
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: True)
    state, ui = _setup()
    try:
        source = state.systems[0].neighbors[0]
        state.fleets = [Fleet(owner_id=2, source_id=source, dest_id=0, ships=7,
                              turns_total=3, turns_remaining=1)]
        landed = turnfilm.Landed(
            node_id=0, fleets=(0,), was_owner=1, was_ships=6, owner_id=2, ships=3,
            prod_progress=0,
            steps=(turnfilm.Fold(attacker=2, attacker_ships=7, defender=1,
                                 defender_ships=6, winner=2, survivors=3),))
        ui.history_turn, ui.history_max = 0, 1
        history_states = [state, state]
        history_fog = [(set(state.systems), set(), {})] * 2
        history_events = [[], [turnfilm.Advanced(((0, 0),)), landed]]

        assert main._next_history_film(ui, history_states, history_fog, history_events)
        combat = next(b for b in ui.film.beats if b.kind == "combat")
        assert combat.ms == 0.0
        assert ui.film.total_ms == combat.end   # no trailing hold either
    finally:
        pygame.quit()


def test_next_history_film_is_none_for_a_quiet_turn():
    """A turn with no events (or events that resolve to no watchable beats) must
    not manufacture a film — the caller falls back to stepping it instantly."""
    state, ui = _setup()
    try:
        ui.history_turn, ui.history_max = 0, 2
        history_states = [state, state, state]
        history_fog = [(set(state.systems), set(), {})] * 3
        history_events = [[], [], []]
        assert main._next_history_film(ui, history_states, history_fog, history_events) is None
        assert ui.film is None
    finally:
        pygame.quit()


def test_next_history_film_respects_the_animate_preference(monkeypatch):
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: False)
    state, ui = _setup()
    try:
        ui.history_turn, ui.history_max = 0, 2
        history_states = [state, state, state]
        history_fog = [(set(state.systems), set(), {})] * 3
        history_events = [[], [turnfilm.Advanced(((0, 2),))], []]
        assert main._next_history_film(ui, history_states, history_fog, history_events) is None
        assert ui.film is None
    finally:
        pygame.quit()


def test_next_history_film_archives_marks_even_with_the_animate_preference_off(monkeypatch):
    """Mirrors `test_resolve_turn_archives_marks_even_with_the_animate_preference_off`
    for history playback: no glide, but a fight's cost still gets its mark."""
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: False)
    state, ui = _setup()
    try:
        landed = turnfilm.Landed(
            node_id=0, fleets=(0,), was_owner=1, was_ships=6, owner_id=2, ships=3,
            prod_progress=0,
            steps=(turnfilm.Fold(attacker=2, attacker_ships=7, defender=1,
                                 defender_ships=6, winner=2, survivors=3),))
        ui.history_turn, ui.history_max = 0, 1
        history_states = [state, state]
        history_fog = [(set(state.systems), set(), {})] * 2
        history_events = [[], [landed]]

        assert main._next_history_film(ui, history_states, history_fog, history_events) is None
        assert ui.film is None                        # no glide
        assert len(ui.fading_fights) == 1 and ui.fading_fights[0].node_id == 0
    finally:
        pygame.quit()


def test_an_ordinary_turns_film_owes_no_snap(monkeypatch):
    """Only the turn that crosses into a decided game (or a knocked-out human)
    defers anything; every other film lands with the camera left alone."""
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: True)
    state, ui = _setup()
    try:
        ui.view.zoom_at((600, 460), 2.0)
        main.resolve_turn(state, ui, log=None)
        assert state.winner is None
        assert not ui.deferred_view_snap
        main.land_film(state, ui)
        assert ui.view.zoom == 2.0
    finally:
        pygame.quit()


def test_a_film_carries_the_fog_the_turn_began_with(monkeypatch):
    """`resolve_turn` refreshes the fog to the board it hands back, so the film
    needs the earlier one alongside it — `Ui.sees` draws the union."""
    monkeypatch.setattr(main.webstore, "animate_turns", lambda: True)
    state, ui = _setup()
    try:
        main.refresh_fog(state, ui)
        before = set(ui.visible)
        for _ in range(12):     # play on until a turn has something to watch
            reel = main.resolve_turn(state, ui, log=None)
            if reel is not None:
                break
            before = set(ui.visible)
        assert reel is not None
        assert ui.film_visible == frozenset(before)
        assert all(ui.sees(sid) for sid in before)
        main.land_film(state, ui)
        assert ui.film_visible == frozenset()   # additive: nothing to put back
    finally:
        pygame.quit()


def test_a_click_at_the_origin_never_ends_the_turn_on_an_undrawn_button():
    """Every rect test is guarded on the rect's width, including End Turn's.

    `(0, 0, 0, 0)` is what a control that did not draw this frame is left as, and
    `_point_in_rect` finds the point (0, 0) inside it — so an unguarded test is
    live at exactly one pixel. `Ui` starts that way, and the loop polls events
    before the first `render.draw`, so a press on the top-left pixel of the very
    first frame used to end turn 0.
    """
    state, ui = _setup()
    try:
        assert ui.end_turn_rect == (0, 0, 0, 0)
        assert ui.play_pause_rect == (0, 0, 0, 0)
        assert _click_pos(state, ui, (0, 0)) is None
    finally:
        pygame.quit()


def test_the_invite_overlay_is_modal_and_names_the_seat_it_copies():
    """A Copy row never touches the clipboard itself (that's main's job) — it
    only names the seat on `Ui` and returns the action that asks for it — and
    Continue clears the overlay outright, since nothing outside `Ui` needs
    telling that it closed."""
    state, ui = _setup()
    try:
        ui.pbp_invite = ((2, "https://game/#pbp=x:y"), (3, "https://game/#pbp=x:z"))
        ui.pbp_invite_rects = {2: (10, 10, 40, 20), 3: (10, 40, 40, 20)}
        ui.pbp_invite_close_rect = (10, 70, 40, 20)

        # A click elsewhere on the board must not fall through to selection —
        # the overlay is modal, exactly like history or a finished game.
        assert _click(state, ui, next(iter(state.systems))) is None
        assert ui.selected is None

        assert _click_pos(state, ui, (30, 20)) == "pbp_copy_seat"
        assert ui.pbp_copy_seat == 2

        assert _click_pos(state, ui, (30, 80)) is None   # Continue
        assert ui.pbp_invite == ()
        assert ui.pbp_invite_rects == {}
        assert ui.pbp_invite_close_rect == (0, 0, 0, 0)
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# UI size: the fit to the window, and the player's own size on top of it
# --------------------------------------------------------------------------- #
def test_ui_scale_fits_the_window_like_the_menu_does():
    """The board grows with the window, as the menu's canvas does, so the two
    stay in proportion; a window smaller than the baseline never shrinks it."""
    base = (config.BASE_SCREEN_W, config.BASE_SCREEN_H)
    assert main.ui_scale_for(base, False, 100) == 1.0
    assert main.ui_scale_for((base[0] * 2, base[1] * 2), False, 100) == 2.0
    assert main.ui_scale_for((base[0] * 2, base[1]), False, 100) == 1.0
    assert main.ui_scale_for((800, 600), False, 100) == 1.0


def test_ui_size_multiplies_the_fit_except_on_touch():
    big = (config.BASE_SCREEN_W * 3 // 2, config.BASE_SCREEN_H * 3 // 2)
    assert main.ui_scale_for(big, False, 125) == 1.5 * 1.25
    assert main.ui_scale_for(big, True, 125) == 1.5 * config.TOUCH_UI_SCALE


def test_ctrl_plus_minus_and_zero_set_the_ui_size():
    def key(k, mod=pygame.KMOD_CTRL):
        return main.ui_size_key(pygame.event.Event(pygame.KEYDOWN, key=k, mod=mod), 100)

    assert key(pygame.K_EQUALS) == key(pygame.K_KP_PLUS) == 110
    assert key(pygame.K_MINUS) == key(pygame.K_KP_MINUS) == 90
    assert key(pygame.K_0) == 100
    assert key(pygame.K_EQUALS, mod=0) is None   # plain −/+ still zooms the map
    assert key(pygame.K_z) is None


# --------------------------------------------------------------------------- #
# A system split down several lanes
# --------------------------------------------------------------------------- #
def _split_setup():
    """A human system with two lanes in a 50/50 split, open in the popup on the
    first lane, with the panel drawn so its rects are recorded."""
    from starconquest import render

    state, ui = _setup()
    home = max(state.systems.values(), key=lambda s: len(s.neighbors)).id
    state.systems[home].owner_id = 1
    a, b = state.systems[home].neighbors[:2]
    ui.visible = ui.seen = set(state.systems)
    ui.auto_forward[home] = ForwardRule({a: 50, b: 50})
    ui.edit_forward(state, home, a)
    render._FONTS.clear()
    render.draw(pygame.display.get_surface(), state, ui)
    return state, ui, home, a, b


def test_a_split_lanes_own_buttons_retune_that_lane():
    state, ui, home, a, b = _split_setup()
    try:
        rows = {dest: (row, minus, plus) for dest, row, minus, plus in ui.share_rows}
        assert set(rows) == {a, b}
        _click_pos(state, ui, pygame.Rect(*rows[b][2]).center)       # + on b
        step = config.FORWARD_STEP_PCT
        assert ui.auto_forward[home].shares == {a: 50 - step, b: 50 + step}
        assert ui.dest == b, "pressing a lane's buttons makes it the lane in focus"
    finally:
        pygame.quit()


def test_tapping_a_split_row_moves_the_focus():
    state, ui, home, a, b = _split_setup()
    try:
        row = next(r for dest, r, _m, _p in ui.share_rows if dest == b)
        _click_pos(state, ui, (row[0] + 4, row[1] + row[3] // 2))     # the name end
        assert ui.dest == b and ui.auto_forward[home].shares == {a: 50, b: 50}
    finally:
        pygame.quit()


def test_even_split_and_delete_all_buttons():
    state, ui, home, a, b = _split_setup()
    try:
        ui.auto_forward[home].shares = {a: 80, b: 20}
        _click_pos(state, ui, pygame.Rect(*ui.even_split_rect).center)
        assert ui.auto_forward[home].shares == {a: 50, b: 50}
        _click_pos(state, ui, pygame.Rect(*ui.delete_all_rect).center)
        assert home not in ui.auto_forward and ui.mode != CHOOSING
    finally:
        pygame.quit()


def test_clicking_a_splits_other_lane_on_the_map_opens_that_lane():
    state, ui, home, a, b = _split_setup()
    try:
        ui.close_send()
        _click_pos(state, ui, _lane_mid(state, ui, home, b))
        assert ui.mode == CHOOSING and ui.forward_armed
        assert (ui.selected, ui.dest) == (home, b)
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Web canvas: the framebuffer follows the browser viewport
# --------------------------------------------------------------------------- #
def test_the_web_framebuffer_takes_the_viewports_shape():
    """pygbag letterboxes a framebuffer into the page keeping its aspect, so a fixed
    16:9 one never filled any other window; the canvas takes the viewport's shape
    at device resolution instead, held to the WEB_FB pixel budget."""
    budget = config.WEB_FB_W * config.WEB_FB_H
    assert main.web_framebuffer(1920, 1080, 1.0) == (1920, 1080)     # native, unscaled
    assert main.web_framebuffer(844, 390, 3.0) == (2532, 1170)       # landscape phone
    w, h = main.web_framebuffer(390, 844, 3.0)                       # ...rotated
    assert h > w and abs(w / h - 390 / 844) < 0.01
    w, h = main.web_framebuffer(1920, 1080, 2.0)                     # 4K: capped
    assert (w, h) == (config.WEB_FB_W, config.WEB_FB_H)
    w, h = main.web_framebuffer(1600, 1200, 2.0)                     # 4:3, capped
    assert abs(w / h - 4 / 3) < 0.01 and w * h <= budget * 1.01
    assert main.web_framebuffer(640, 480, 0.5) == (640, 480)         # dpr floored at 1


def test_a_browser_resize_reshapes_the_surface(monkeypatch):
    """The surface only changes size when the game resizes it, so a browser resize
    must come through `fit_web_canvas` for the loop's reflow to see anything."""
    pygame.init()
    try:
        pygame.display.set_mode((config.WEB_FB_W, config.WEB_FB_H))
        view = [(1600, 1200, 1.0)]
        monkeypatch.setattr(main.webstore, "viewport", lambda: view[0])

        assert main.fit_web_canvas()
        assert pygame.display.get_surface().get_size() == (1600, 1200)
        assert not main.fit_web_canvas(), "an unchanged viewport must not set_mode again"

        view[0] = (900, 1600, 1.0)                    # rotated to portrait
        assert main.fit_web_canvas()
        assert pygame.display.get_surface().get_size() == (900, 1600)

        view[0] = None                                # DOM unreadable: keep what we have
        assert not main.fit_web_canvas()
        assert pygame.display.get_surface().get_size() == (900, 1600)
    finally:
        pygame.quit()


def test_the_game_owns_the_canvas_box_it_measures(monkeypatch):
    """pygbag's own fit keeps the framebuffer's aspect, so left in charge it
    letterboxes or, racing a resize, stretches the picture. `viewport` takes the
    canvas over, makes it fill the page, and reports the box it is *shown* at."""
    import sys
    import types
    from starconquest import webstore

    style = types.SimpleNamespace()
    canvas = types.SimpleNamespace(
        style=style,
        getBoundingClientRect=lambda: types.SimpleNamespace(width=844.6, height=390.2))
    window = types.SimpleNamespace(
        canvas=canvas, devicePixelRatio=3,
        python=types.SimpleNamespace(config=types.SimpleNamespace(user_canvas_managed=0)))
    monkeypatch.setattr(webstore, "is_web", lambda: True)
    monkeypatch.setitem(sys.modules, "platform", types.SimpleNamespace(window=window))

    assert webstore.viewport() == (844, 390, 3.0)
    assert window.python.config.user_canvas_managed == 1
    assert (style.position, style.width, style.height) == ("fixed", "100%", "100%")

    canvas.getBoundingClientRect = lambda: types.SimpleNamespace(width=0, height=0)
    assert webstore.viewport() is None, "a canvas not laid out yet has no size to fit"


def _stage(ui, next_stage=True):
    ui.stage_label = "Tutorial 2/10 · Supply lines"
    ui.stage_title = "Supply lines"
    ui.briefing = ("Read me.",)
    ui.briefing_open = True
    ui.stage_hint = "A hint."
    ui.has_next_stage = next_stage


def test_any_press_puts_the_briefing_away_and_is_spent_doing_so():
    state, ui = _setup()
    _stage(ui)
    home = next(s.id for s in state.systems.values() if s.owner_id == 1)
    assert _click(state, ui, home) is None
    assert not ui.briefing_open
    assert ui.selected is None, "the press that closed the card selected nothing"

    ui.briefing_open = True
    ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, unicode="\r")
    assert game_input.handle_event(ev, state, ui) is None, "Enter closes the card, not the turn"
    assert not ui.briefing_open

    ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_b, unicode="b")
    assert game_input.handle_event(ev, state, ui) is None
    assert ui.briefing_open, "B brings it back"


def test_the_stage_hint_brings_the_briefing_back():
    state, ui = _setup()
    _stage(ui)
    ui.briefing_open = False
    ui.stage_hint_rect = (10, 10, 100, 40)
    assert _click_pos(state, ui, (50, 30)) is None
    assert ui.briefing_open


def test_the_lore_button_opens_the_selected_systems_star_card():
    state, ui = _setup()
    home = next(s.id for s in state.systems.values() if s.owner_id == 1)
    ui.mode, ui.selected = SELECTED, home
    ui.lore_button_rect = (10, 10, 30, 30)
    assert _click_pos(state, ui, (20, 20)) is None
    assert ui.lore_sid == home
    assert ui.selected == home, "opening the card keeps the selection"


def test_any_press_puts_the_star_card_away_and_is_spent_doing_so():
    state, ui = _setup()
    home = next(s.id for s in state.systems.values() if s.owner_id == 1)
    ui.lore_sid = home
    assert _click(state, ui, home) is None
    assert ui.lore_sid is None
    assert ui.selected is None, "the press that closed the card selected nothing"

    ui.lore_sid = home
    ev = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, unicode="\r")
    assert game_input.handle_event(ev, state, ui) is None, "Enter closes the card, not the turn"
    assert ui.lore_sid is None


def test_i_opens_the_star_card_for_the_selected_or_hovered_system():
    state, ui = _setup()
    i = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_i, unicode="i")
    assert game_input.handle_event(i, state, ui) is None
    assert ui.lore_sid is None, "nothing in focus"

    enemy = next(s.id for s in state.systems.values() if s.owner_id == 2)
    ui.hover = enemy
    game_input.handle_event(i, state, ui)
    assert ui.lore_sid == enemy
    ui.lore_sid = None

    home = next(s.id for s in state.systems.values() if s.owner_id == 1)
    ui.mode, ui.selected = SELECTED, home
    game_input.handle_event(i, state, ui)
    assert ui.lore_sid == home, "the selection wins over a hover"
    ui.lore_sid = None

    state.systems[home].name = "Not In The Catalogue"
    game_input.handle_event(i, state, ui)
    assert ui.lore_sid is None


def test_n_is_next_stage_in_a_stage_and_never_a_new_map():
    state, ui = _setup()
    _stage(ui)
    ui.briefing_open = False
    n = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n, unicode="n")
    assert game_input.handle_event(n, state, ui) is None, "live: a stage's seed is pinned"

    state.winner, ui.hand_turns = 1, 12
    assert game_input.handle_event(n, state, ui) == "next_stage"
    ui.next_stage_rect = (10, 10, 100, 40)
    assert _click_pos(state, ui, (50, 30)) == "next_stage"

    ui.has_next_stage = False          # the last stage: nowhere on to
    assert game_input.handle_event(n, state, ui) is None
    ui.has_next_stage, ui.hand_turns = True, 0   # an autoplayed win cleared nothing
    assert game_input.handle_event(n, state, ui) is None

    ui.stage_label = None              # an ordinary match keeps New map
    assert game_input.handle_event(n, state, ui) == "restart"


def test_start_game_copies_a_stage_onto_the_ui_as_plain_values(tmp_path, monkeypatch):
    from starconquest import gauntlet

    monkeypatch.setattr(main.webstore, "_file_path", lambda: tmp_path / "kv.json")
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    g = gauntlet.builtin()[0]
    first, last = g.stages[0], g.stages[-1]
    cfg = gauntlet.stage_settings(first)
    _state, ui, _log = main.start_game(cfg, cfg.seed, False, (g, first))
    assert ui.stage_label == f"{g.name} 1/{len(g.stages)} · {first.title}"
    assert ui.briefing == first.briefing and ui.briefing_open
    assert ui.stage_hint == first.hint and ui.has_next_stage

    main.stage_ui(ui, (g, last), briefing=False)    # a Retry: no card again
    assert not ui.briefing_open and not ui.has_next_stage

    _state, plain, _log = main.start_game(cfg, cfg.seed, False)
    assert plain.stage_label is None and not plain.in_stage


def test_a_stage_with_no_hint_still_has_a_line_back_to_its_briefing(monkeypatch):
    # A generated stage has none of its own: the panel's line is the only way
    # back to the card on a phone.
    import dataclasses
    from starconquest import gauntlet

    g = gauntlet.builtin()[0]
    bare = dataclasses.replace(g.stages[0], hint="")
    ui = Ui(view=WorldView((0, 0, 100, 100), config.play_rect()))
    monkeypatch.setattr(config, "touch_ui", True)
    main.stage_ui(ui, (g, bare))
    assert ui.stage_hint and "briefing" in ui.stage_hint and "B:" not in ui.stage_hint
    monkeypatch.setattr(config, "touch_ui", False)
    main.stage_ui(ui, (g, bare))
    assert ui.stage_hint.startswith("B:")
    main.stage_ui(ui, (g, dataclasses.replace(bare, briefing=())))
    assert ui.stage_hint is None

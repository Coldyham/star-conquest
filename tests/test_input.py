"""Exercise the human interaction path (input.py) with synthetic pygame events.

This drives the real select -> target -> adjust -> end-turn flow headlessly so
the interactive path is covered without a window, and confirms a queued order
actually resolves through the engine.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

import main  # noqa: E402  (repo-root entry point; pytest adds "." to sys.path)
from starconquest import config, engine, mapgen  # noqa: E402
from starconquest import input as game_input  # noqa: E402
from starconquest.geometry import WorldView  # noqa: E402
from starconquest.viewstate import CHOOSING, IDLE, SELECTED, Ui  # noqa: E402


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
        assert ui.auto_forward.get(home) == (nbr, 0)

        ui.send_tab_rect = (200, 100, 60, 20)
        _click_pos(state, ui, (210, 110))       # -> Send (restores the count)
        assert ui.forward_armed is False and home not in ui.auto_forward
        assert ui.pending and ui.pending[0].ships == 3
    finally:
        pygame.quit()


def test_forward_stepper_adjusts_keep():
    """On the Forward tab the −/+ controls set how many ships to hold back."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        _click(state, ui, home)
        _click(state, ui, nbr)
        ui.forward_tab_rect = (100, 100, 60, 20)
        _click_pos(state, ui, (110, 110))       # arm forwarding, keep 0
        assert ui.keep == 0 and ui.auto_forward[home] == (nbr, 0)

        ui.plus_rect = (100, 130, 20, 20)
        _click_pos(state, ui, (110, 140))       # +1 -> keep 1
        _click_pos(state, ui, (110, 140))       # +1 -> keep 2
        assert ui.keep == 2 and ui.auto_forward[home] == (nbr, 2)

        # keep never drops below 0
        ui.minus_rect = (200, 130, 20, 20)
        for _ in range(5):
            _click_pos(state, ui, (210, 140))
        assert ui.keep == 0 and ui.auto_forward[home] == (nbr, 0)
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
        ui.auto_forward[a] = (b, 0)
        ui.auto_forward[b] = (c, 2)

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
        assert ui.auto_forward.get(home) == (nbr, 0)  # keep 0 == forward all
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
        assert ui.auto_forward.get(home) == (nbr, 0)
        assert ui.pending == []

        # switching to the Send tab on an empty source shows 0, not a phantom
        # 1-ship order
        ui.set_forward_mode(state, False)
        assert ui.forward_armed is False and ui.chosen == 0
        assert ui.pending == [] and home not in ui.auto_forward
    finally:
        pygame.quit()


def test_plain_click_from_empty_system_does_not_send():
    """Without shift, an empty source has nothing to send: a neighbour click
    neither opens the send popup nor queues an order."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = next(n for n in state.systems[home].neighbors
                   if state.systems[n].owner_id != 1)
        state.systems[home].ships = 0

        _click(state, ui, home)
        _click(state, ui, nbr)
        assert ui.mode != CHOOSING
        assert ui.pending == [] and home not in ui.auto_forward
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Selection edge cases and top-level keys
# --------------------------------------------------------------------------- #
def test_cannot_select_foreign_system():
    state, ui = _setup()
    try:
        enemy = next(s.id for s in state.systems.values() if s.owner_id not in (0, 1))
        _click(state, ui, enemy)
        assert ui.mode == IDLE and ui.selected is None
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

        ui.auto_forward[home] = (nbr, 4)   # keep 4, forward the surplus
        orders = main.auto_forward_orders(state, ui)
        assert len(orders) == 1
        o = orders[0]
        assert (o.owner_id, o.source_id, o.dest_id) == (1, home, nbr)
        assert o.ships == state.systems[home].ships - 4

        # keeping the whole garrison forwards nothing
        ui.auto_forward[home] = (nbr, state.systems[home].ships)
        assert main.auto_forward_orders(state, ui) == []
    finally:
        pygame.quit()


def test_x_key_clears_forward_rule():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.selected = home
        ui.auto_forward[home] = (nbr, 2)

        game_input.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui
        )
        assert home not in ui.auto_forward
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Editing / undoing queued orders
# --------------------------------------------------------------------------- #
from starconquest.model import Order  # noqa: E402


def test_click_queued_lane_selects_order():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))

        _click_pos(state, ui, _lane_mid(state, ui, home, nbr))
        assert ui.sel_order == 0
    finally:
        pygame.quit()


def test_wheel_edits_selected_order_in_place():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.sel_order = 0

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


def test_x_key_removes_selected_order():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.sel_order = 0

        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui)
        assert ui.pending == []
        assert ui.sel_order is None
    finally:
        pygame.quit()


def test_lane_click_cycles_orders_on_same_lane():
    """Two opposite-direction orders share one lane; repeat clicks cycle + wrap."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 2))
        ui.pending.append(Order(1, nbr, home, 1))
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
def test_click_rule_row_selects_it():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = (nbr, 2)
        ui.forward_hitboxes = [(home, (100, 100, 50, 20), (140, 100, 10, 20))]

        _click_pos(state, ui, (110, 105))
        assert ui.sel_forward == home
        assert ui.sel_order is None
    finally:
        pygame.quit()


def test_click_rule_lane_selects_it():
    """Clicking a rule's dashed lane selects it for editing, just like a queued
    order's lane — the panel row is no longer the only way in."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = (nbr, 2)

        _click_pos(state, ui, _lane_mid(state, ui, home, nbr))
        assert ui.sel_forward == home
        assert ui.sel_order is None
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
        ui.auto_forward[home] = (nbr, 1)
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


def test_click_rule_delete_button_removes_it():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = (nbr, 2)
        ui.forward_hitboxes = [(home, (100, 100, 50, 20), (140, 100, 10, 20))]

        _click_pos(state, ui, (145, 105))
        assert home not in ui.auto_forward
    finally:
        pygame.quit()


def test_wheel_edits_selected_rule_keep_in_place():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = (nbr, 2)
        ui.sel_forward = home

        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.auto_forward[home] == (nbr, 3)

        # keep is capped at the source's total garrison, and never drops below 0
        for _ in range(50):
            game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.auto_forward[home] == (nbr, state.systems[home].ships)
        for _ in range(50):
            game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1), state, ui)
        assert ui.auto_forward[home] == (nbr, 0)
    finally:
        pygame.quit()


def test_on_map_step_buttons_adjust_selected_rule_keep():
    """The on-map −/+ buttons adjust a selected rule's keep, same as for a queued
    order — a mouse-wheel-free way to edit, for consistency across both."""
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        state.systems[home].ships = 10
        ui.auto_forward[home] = (nbr, 2)
        ui.sel_forward = home
        ui.minus_rect = (100, 100, 20, 20)   # normally recorded by render each frame
        ui.plus_rect = (140, 100, 20, 20)

        _click_pos(state, ui, (150, 110))    # + button
        assert ui.auto_forward[home] == (nbr, 3)
        _click_pos(state, ui, (110, 110))    # − button
        assert ui.auto_forward[home] == (nbr, 2)
    finally:
        pygame.quit()


def test_x_key_removes_selected_rule():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.auto_forward[home] = (nbr, 2)
        ui.sel_forward = home

        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_x), state, ui)
        assert home not in ui.auto_forward
        assert ui.sel_forward is None
    finally:
        pygame.quit()


def test_selecting_order_and_rule_are_mutually_exclusive():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.auto_forward[home] = (nbr, 2)

        ui.select_order(0)
        assert ui.sel_order == 0 and ui.sel_forward is None

        ui.select_forward(home)
        assert ui.sel_forward == home and ui.sel_order is None
    finally:
        pygame.quit()


def test_right_click_deselects_order_then_selecting_system_clears_it():
    state, ui = _setup()
    try:
        home = next(s.id for s in state.systems.values() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.pending.append(Order(1, home, nbr, 3))
        ui.sel_order = 0

        # right-click clears the order-edit highlight
        _click_pos(state, ui, _lane_mid(state, ui, home, nbr), button=3)
        assert ui.sel_order is None

        # re-select, then clicking an owned system to compose also clears it
        ui.sel_order = 0
        _click(state, ui, home)
        assert ui.sel_order is None and ui.selected == home
    finally:
        pygame.quit()

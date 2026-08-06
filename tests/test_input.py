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
from starconquest import config, engine, mapgen, replay  # noqa: E402
from starconquest import input as game_input  # noqa: E402
from starconquest.geometry import WorldView  # noqa: E402
from starconquest.settings import Challenge, Settings  # noqa: E402
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
        assert ui.auto_forward.get(home) == (nbr, 0)
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
        assert ui.auto_forward.get(home) == (nbr, 0)
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
    state, ui = _setup()
    try:
        ui.view.zoom_at((600, 460), 2.0)
        assert ui.view.zoom != 1.0

        ui.reset_view_rect = (100, 100, 120, 24)
        _click_pos(state, ui, (110, 110))
        assert ui.view.zoom == 1.0
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
        ui.auto_forward[home] = (nbr, 2)

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


def test_hand_turns_counts_only_manually_played_turns():
    """A game played by hand and then autoplayed to its end reports the hand
    count, and the Ui's running tally agrees with the log's per-turn flags."""
    state, ui = _setup()
    try:
        log = replay.new_log(Settings(seed=1, nodes=18, players=3), 1)
        log.path = None                       # keep the suite out of games/
        for turn in range(6):
            ui.autoplay = turn >= 4           # decided: let it play out
            main.resolve_turn(state, ui, log)
        assert ui.hand_turns == 4
        assert main.hand_turns(log) == 4
    finally:
        pygame.quit()


def test_restart_only_carries_autoplay_out_of_a_pure_demo():
    """Autoplaying the tail of a hand-played game must not start the next map in
    autoplay too — but a demo that was never touched keeps running."""
    state, ui = _setup()
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


# --------------------------------------------------------------------------- #
# Queued-list scrolling
# --------------------------------------------------------------------------- #
def _queue_many(state, ui, count):
    """Queue ``count`` distinct orders and draw, so the list lays itself out."""
    from starconquest import render
    from starconquest.model import Order

    ids = sorted(state.systems)
    for i in range(count):
        src, dst = ids[i % len(ids)], ids[(i + 1) % len(ids)]
        ui.pending.append(Order(1, src, dst, i + 1))
    screen = pygame.display.get_surface()
    render._FONTS.clear()
    render.draw(screen, state, ui)
    return screen


def test_long_queue_scrolls_instead_of_hiding_entries():
    """A queue too long for its capped block becomes scrollable, and every entry is
    reachable — a bare '+N more' line left the overflow unmanageable."""
    state, ui = _setup()
    try:
        from starconquest import render

        screen = _queue_many(state, ui, 40)
        assert ui.order_scroll_max > 0, "40 orders should overflow the capped block"
        assert ui.order_up_rect[2] > 0 and ui.order_down_rect[2] > 0

        seen = set()
        for _ in range(ui.order_scroll_max + 1):
            seen.update(idx for idx, _row, _del in ui.order_hitboxes)
            _click_pos(state, ui, pygame.Rect(*ui.order_down_rect).center)
            render.draw(screen, state, ui)
        seen.update(idx for idx, _row, _del in ui.order_hitboxes)
        assert seen == set(range(40)), "scrolling must reach every queued order"
    finally:
        pygame.quit()


def test_deleting_a_row_while_scrolled_removes_that_order():
    """The regression guard for the hit-rect index: rows carry their own index into
    `pending`, so the second visible row of a scrolled list is not order #1."""
    state, ui = _setup()
    try:
        from starconquest import render

        screen = _queue_many(state, ui, 40)
        _click_pos(state, ui, pygame.Rect(*ui.order_down_rect).center)
        render.draw(screen, state, ui)
        assert ui.order_scroll > 0

        idx, _row, delete = ui.order_hitboxes[0]
        assert idx == ui.order_scroll, "the first drawn row is the scroll offset"
        doomed = ui.pending[idx]
        before = len(ui.pending)
        _click_pos(state, ui, pygame.Rect(*delete).center)
        assert len(ui.pending) == before - 1
        assert doomed not in ui.pending, "deleted the wrong order"
    finally:
        pygame.quit()


def test_scroll_offset_survives_orders_being_removed():
    """A stale offset (orders resolved out from under it) snaps back into range on
    the next scroll rather than needing one press per vanished row."""
    state, ui = _setup()
    try:
        _queue_many(state, ui, 40)
        ui.order_scroll = ui.order_scroll_max
        del ui.pending[5:]                       # most of the list goes away
        _queue_many(state, ui, 0)                # redraw with the short list
        ui.scroll_orders(1)
        assert ui.order_scroll <= ui.order_scroll_max
    finally:
        pygame.quit()


def test_wheel_over_the_panel_scrolls_the_list_not_the_map():
    """The wheel means 'scroll this list' over the info panel and 'zoom' over the
    map, so a flick while reviewing orders doesn't throw the camera about."""
    state, ui = _setup()
    try:
        _queue_many(state, ui, 40)
        zoom = ui.view.zoom
        panel = (config.SCREEN_W - config.HUD_RIGHT_W // 2, config.SCREEN_H // 2)
        pygame.mouse.set_pos(panel)
        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=-1), state, ui)
        assert ui.order_scroll == 1
        assert ui.view.zoom == zoom, "the wheel must not zoom the map from the panel"

        pygame.mouse.set_pos((config.SCREEN_W // 4, config.SCREEN_H // 2))
        game_input.handle_event(pygame.event.Event(pygame.MOUSEWHEEL, x=0, y=1), state, ui)
        assert ui.view.zoom > zoom, "over the map the wheel still zooms"
        assert ui.order_scroll == 1
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

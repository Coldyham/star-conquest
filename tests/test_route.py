"""Route mode: multi-select a group of systems, aim it at a destination, and
confirm to lay a forwarding chain from each of them to it.

The planning half is pure logic over a hand-built graph (as test_fog.py does),
so most of this needs no display. The gesture half drives real pygame events
through input.py, like test_input.py.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402

import main  # noqa: E402  (repo-root entry point; pytest adds "." to sys.path)
from starconquest import config, mapgen, model, render  # noqa: E402
from starconquest import input as game_input  # noqa: E402
from starconquest.geometry import WorldView  # noqa: E402
from starconquest.model import GameState, Player, System  # noqa: E402
from starconquest.viewstate import IDLE, ROUTING, Ui  # noqa: E402


# --------------------------------------------------------------------------- #
# Hand-built graphs (pure — no display needed)
# --------------------------------------------------------------------------- #
def _graph(edges, owners, n=None, human=1) -> GameState:
    """Graph from ``edges``; ``owners`` maps system id -> owner id (default 0)."""
    n = n if n is not None else 1 + max(max(e) for e in edges)
    s = GameState.new(0)
    for sid in range(n):
        s.systems[sid] = System(id=sid, pos=(sid * 10.0, 0.0),
                                owner_id=owners.get(sid, 0), ships=5)
    s.players[0] = Player(0, "Neutral", (0, 0, 0), is_neutral=True)
    s.players[human] = Player(human, "P", (0, 0, 0), is_human=True)
    for pid in set(owners.values()) - {0, human}:
        s.players[pid] = Player(pid, f"E{pid}", (0, 0, 0))
    for a, b in edges:
        s.add_lane(a, b, 1.0, 1)
    s.rebuild_topology()
    return s


def _ui(state, human=1) -> Ui:
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=human)
    ui.seen = set(state.systems)      # planning tests aren't about fog
    ui.visible = set(state.systems)
    return ui


def _line(n, owned, human=1) -> GameState:
    return _graph([(a, a + 1) for a in range(n - 1)],
                  {sid: human for sid in owned}, n=n, human=human)


# --------------------------------------------------------------------------- #
# model.flow_field
# --------------------------------------------------------------------------- #
def test_flow_field_points_each_node_at_its_next_hop():
    """The map sends every node one step closer to the seed, not straight to it."""
    s = _line(5, owned={0, 1, 2, 3, 4})
    parent = model.flow_field(s, set(s.systems), {4})
    assert parent == {3: 4, 2: 3, 1: 2, 0: 1}


def test_flow_field_never_gives_a_seed_a_parent():
    s = _line(3, owned={0, 1, 2})
    assert 2 not in model.flow_field(s, set(s.systems), {2})


def test_flow_field_only_expands_into_allowed():
    """A node walled off by a disallowed one is simply absent from the map."""
    s = _line(5, owned={0, 1, 2, 3, 4})
    parent = model.flow_field(s, {1, 3, 4}, {4})   # 2 excluded
    assert parent == {3: 4}
    assert 1 not in parent and 0 not in parent


def test_flow_field_seed_need_not_be_allowed():
    """Only expansion is restricted — that is what lets route mode aim a chain
    made of owned hops at a system it does not own."""
    s = _line(3, owned={0, 1})
    parent = model.flow_field(s, {0, 1}, {2})
    assert parent[1] == 2          # our system forwards into the un-owned seed


def test_flow_field_is_deterministic_on_ties():
    """Equidistant seeds must not make a node flip-flop between calls."""
    s = _graph([(0, 1), (0, 2)], {0: 1, 1: 1, 2: 1})
    runs = {tuple(sorted(model.flow_field(s, {0, 1, 2}, {1, 2}).items()))
            for _ in range(10)}
    assert len(runs) == 1


# --------------------------------------------------------------------------- #
# Planning: whole-path spread
# --------------------------------------------------------------------------- #
def test_whole_path_gets_a_rule_and_the_destination_does_not():
    """Selecting one rear system rules every hop of its route, so ships travel
    the full distance instead of pooling at the first unselected system."""
    s = _line(4, owned={0, 1, 2, 3})
    ui = _ui(s)
    ui.route_sel = {0}
    ui.set_route_dest(s, 3)
    assert ui.route_plan == {0: (1, 0), 1: (2, 0), 2: (3, 0)}
    assert 3 not in ui.route_plan


def test_converging_routes_agree_on_the_shared_hop():
    """Two selections that meet mid-path can't demand different next hops."""
    #  0 -\
    #      2 - 3 - 4(dest)
    #  1 -/
    s = _graph([(0, 2), (1, 2), (2, 3), (3, 4)],
               {sid: 1 for sid in range(5)})
    ui = _ui(s)
    ui.route_sel = {0, 1}
    ui.set_route_dest(s, 4)
    assert ui.route_plan == {0: (2, 0), 1: (2, 0), 2: (3, 0), 3: (4, 0)}


def test_selected_system_adjacent_to_the_destination_gets_one_hop():
    s = _line(2, owned={0, 1})
    ui = _ui(s)
    ui.route_sel = {0}
    ui.set_route_dest(s, 1)
    assert ui.route_plan == {0: (1, 0)}


# --------------------------------------------------------------------------- #
# Planning: ownership and reachability
# --------------------------------------------------------------------------- #
def test_path_never_crosses_territory_we_do_not_own():
    """A rule can only exist on a system we hold, so a route through an enemy
    system is unrepresentable — the selection is reported unroutable instead."""
    #  0(us) - 1(enemy) - 2(us, dest)
    s = _graph([(0, 1), (1, 2)], {0: 1, 1: 2, 2: 1})
    ui = _ui(s)
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    assert ui.route_plan == {}
    assert ui.route_unroutable == {0}


def test_an_un_owned_destination_is_legal():
    """The destination is the exception: its incoming rule sits on the last owned
    system of the path, which is what makes an assault funnel expressible."""
    s = _graph([(0, 1), (1, 2)], {0: 1, 1: 1, 2: 2})
    ui = _ui(s)
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    assert ui.route_plan == {0: (1, 0), 1: (2, 0)}


def test_no_destination_yet_means_nothing_is_unroutable():
    """Otherwise a fresh selection would read as entirely unroutable."""
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.route_sel = {0, 1}
    ui.recompute_route(s)
    assert ui.route_plan == {} and ui.route_unroutable == set()


def test_the_destination_is_skipped_not_dropped_from_the_group():
    """A system can't be both a source and the sink — but aiming at one of your own
    picks must not quietly delete it, or re-aiming elsewhere loses it."""
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.route_sel = {0, 2}
    ui.set_route_dest(s, 2)
    assert ui.route_sel == {0, 2}          # still picked
    assert ui.route_sources() == {0}       # but not a source while it is the sink
    assert 2 not in ui.route_plan


def test_re_aiming_hands_the_old_destination_back_as_a_source():
    """The bug this guards: aiming at each pick in turn used to empty the group."""
    s = _line(4, owned={0, 1, 2, 3})
    ui = _ui(s)
    ui.route_sel = {0, 1, 2}
    for dest in (2, 1, 0, 3):              # walk the destination over every pick
        ui.set_route_dest(s, dest)
    assert ui.route_sel == {0, 1, 2}       # nothing was lost on the way
    assert ui.route_sources() == {0, 1, 2}
    assert ui.route_plan == {0: (1, 0), 1: (2, 0), 2: (3, 0)}


def test_a_system_we_no_longer_hold_leaves_the_selection():
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.route_sel = {0, 1}
    s.systems[0].owner_id = 2      # lost it
    ui.set_route_dest(s, 2)
    assert ui.route_sel == {1}


def test_destination_must_have_been_seen():
    """Routing to a never-seen system would disclose whether a path to it exists."""
    s = _line(3, owned={0, 1})
    ui = _ui(s)
    ui.seen = {0, 1}               # 2 never sighted
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    assert ui.route_dest is None and ui.route_plan == {}


# --------------------------------------------------------------------------- #
# Planning: replacing existing rules
# --------------------------------------------------------------------------- #
def test_keep_survives_a_replace_that_does_not_move_the_next_hop():
    """Re-routing over an existing conveyor is idempotent, so a tuned keep isn't
    silently wiped by a plan that agreed with it anyway."""
    s = _line(4, owned={0, 1, 2, 3})
    ui = _ui(s)
    ui.auto_forward[1] = (2, 4)    # already forwards the right way, keeping 4
    ui.route_sel = {0}
    ui.set_route_dest(s, 3)
    assert ui.route_plan[1] == (2, 4)
    assert 1 not in ui.route_replaces


def test_a_rule_pointing_elsewhere_is_replaced_and_reported():
    s = _graph([(0, 1), (1, 2), (1, 3)], {sid: 1 for sid in range(4)})
    ui = _ui(s)
    ui.auto_forward[1] = (3, 2)    # points the wrong way
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    assert ui.route_plan[1] == (2, 0)      # re-aimed, keep reset
    assert ui.route_replaces == {1}


# --------------------------------------------------------------------------- #
# Planning: cycles
# --------------------------------------------------------------------------- #
def test_a_destination_forwarding_back_into_the_plan_is_a_cycle():
    """The destination gets no plan rule, so its own surviving rule can close a
    loop the plan would otherwise never contain."""
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.auto_forward[2] = (1, 0)    # dest forwards back to a planned system
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    assert ui.route_cycles                 # flagged before the player confirms
    assert 1 in ui.route_cycles and 2 in ui.route_cycles


def test_confirm_breaks_the_cycle_rather_than_arming_it():
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.auto_forward[2] = (1, 0)
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    ui.confirm_route(s)
    assert 2 not in ui.auto_forward         # the closing edge is dropped
    assert ui.auto_forward == {0: (1, 0), 1: (2, 0)}


def test_a_rule_feeding_into_the_destination_is_not_a_cycle():
    """Only a rule leading back *out* of the destination loops. One pointing at
    it is another tributary, and must survive untouched."""
    s = _graph([(0, 1), (1, 2), (2, 3)], {sid: 1 for sid in range(4)})
    ui = _ui(s)
    ui.auto_forward[3] = (2, 1)     # 3 also feeds the destination
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    assert ui.route_cycles == set()
    ui.confirm_route(s)
    assert ui.auto_forward[3] == (2, 1)    # untouched, keep and all
    assert ui.auto_forward[0] == (1, 0) and ui.auto_forward[1] == (2, 0)


# --------------------------------------------------------------------------- #
# Confirm / cancel
# --------------------------------------------------------------------------- #
def test_confirm_writes_the_plan_and_leaves_the_mode():
    s = _line(4, owned={0, 1, 2, 3})
    ui = _ui(s)
    ui.begin_route()
    ui.route_sel = {0}
    ui.set_route_dest(s, 3)
    ui.confirm_route(s)
    assert ui.auto_forward == {0: (1, 0), 1: (2, 0), 2: (3, 0)}
    assert ui.mode == IDLE and ui.route_sel == set() and ui.route_dest is None


def test_cancel_writes_nothing():
    s = _line(4, owned={0, 1, 2, 3})
    ui = _ui(s)
    ui.begin_route()
    ui.route_sel = {0}
    ui.set_route_dest(s, 3)
    assert ui.route_plan                    # there *was* a proposal
    ui.reset_route()
    assert ui.auto_forward == {} and ui.mode == IDLE


def test_confirmed_rules_expand_into_orders_at_end_of_turn():
    """The whole point: a confirmed chain is ordinary forwarding, so the existing
    end-of-turn expansion carries it with no new plumbing."""
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.route_sel = {0}
    ui.set_route_dest(s, 2)
    ui.confirm_route(s)
    orders = main.auto_forward_orders(s, ui)
    assert {(o.source_id, o.dest_id, o.ships) for o in orders} == {(0, 1, 5), (1, 2, 5)}


def test_begin_route_drops_an_in_progress_send_and_stops_playback():
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.selected = 0
    ui.playing = True
    ui.begin_route()
    assert ui.mode == ROUTING and ui.selected is None and ui.playing is False


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
def test_route_mode_is_not_offered_when_there_is_nothing_to_plan():
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    assert ui.can_route(s) is True
    ui.autoplay = True
    assert ui.can_route(s) is False          # the AI issues our orders
    ui.autoplay = False
    s.winner = 1
    assert ui.can_route(s) is False          # match decided
    s.winner = None
    s.players[1].alive = False               # what the engine's win check writes
    assert ui.can_route(s) is False          # knocked out


# --------------------------------------------------------------------------- #
# The drag box (needs a display for the view/screen mapping)
# --------------------------------------------------------------------------- #
def _display_setup():
    pygame.init()
    pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    render._FONTS.clear()   # a previous test's pygame.quit() killed the cached ones
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    ui = Ui(view=WorldView(mapgen.map_bounds(state), config.play_rect()), human_id=1)
    ui.seen = set(state.systems)
    ui.visible = set(state.systems)
    return state, ui


def test_box_selects_only_our_own_systems_inside_it():
    state, ui = _display_setup()
    try:
        px, py, pw, ph = config.play_rect()
        ui.add_route_box(state, (px, py, pw, ph))      # the whole viewport
        mine = {sid for sid, s in state.systems.items() if s.owner_id == 1}
        assert ui.route_sel == mine
        assert all(state.systems[sid].owner_id == 1 for sid in ui.route_sel)
    finally:
        pygame.quit()


def test_box_is_additive():
    state, ui = _display_setup()
    try:
        mine = sorted(sid for sid, s in state.systems.items() if s.owner_id == 1)
        for sid in mine:
            x, y = ui.view.to_screen(state.systems[sid].pos)
            ui.add_route_box(state, (x - 2, y - 2, 4, 4))
        assert ui.route_sel == set(mine)
    finally:
        pygame.quit()


def test_box_is_clipped_to_the_map_viewport():
    """A box dragged over the side panel must not pick up systems hidden there —
    to_screen projects every system, but only the drawing is clipped."""
    state, ui = _display_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        # park that system under the side panel, then box the panel column
        x = config.SCREEN_W - config.HUD_RIGHT_W // 2
        y = config.SCREEN_H // 2
        sx, sy = ui.view.to_screen(state.systems[home].pos)
        ui.view.pan(x - sx, y - sy)
        ui.add_route_box(state, (config.SCREEN_W - config.HUD_RIGHT_W, 0,
                                 config.HUD_RIGHT_W, config.SCREEN_H))
        assert home not in ui.route_sel
    finally:
        pygame.quit()


def test_box_follows_the_camera_when_zoomed():
    state, ui = _display_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        ui.view.zoom_at(ui.view.to_screen(state.systems[home].pos), 2.0)
        x, y = ui.view.to_screen(state.systems[home].pos)
        ui.add_route_box(state, (x - 6, y - 6, 12, 12))
        assert home in ui.route_sel
    finally:
        pygame.quit()


def test_a_tap_always_aims():
    """One primary meaning, whatever is under it — a pick, a rival's system, an
    empty neutral. Aiming is never destructive."""
    state, ui = _display_setup()
    try:
        a = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        b = state.systems[a].neighbors[0]
        state.systems[b].owner_id = 1     # a second system, as any mid-game has
        ui.route_sel = {a, b}

        ui.route_tap(state, a)                  # one of our own picks
        assert ui.route_dest == a and ui.route_sel == {a, b}
        theirs = next(sid for sid, s in state.systems.items() if s.owner_id != 1)
        ui.route_tap(state, theirs)              # a rival's system
        assert ui.route_dest == theirs and ui.route_sel == {a, b}
    finally:
        pygame.quit()


def test_a_second_tap_on_the_target_un_aims_and_drops_it():
    """The remove gesture: it has to be a second tap, because the first one is
    already spoken for by aiming."""
    state, ui = _display_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        ui.route_sel = {home}
        ui.route_tap(state, home)
        assert ui.route_dest == home and ui.route_sel == {home}
        ui.route_tap(state, home)
        assert ui.route_dest is None and ui.route_sel == set()
    finally:
        pygame.quit()


def test_un_aiming_something_that_was_never_picked_drops_nothing():
    state, ui = _display_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        theirs = next(sid for sid, s in state.systems.items() if s.owner_id != 1)
        ui.route_sel = {home}
        ui.route_tap(state, theirs)
        ui.route_tap(state, theirs)
        assert ui.route_dest is None and ui.route_sel == {home}
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# The gesture path, through input.handle_event
# --------------------------------------------------------------------------- #
def _ev(kind, **kw):
    return pygame.event.Event(kind, **kw)


def _press(state, ui, pos, button=1):
    return game_input.handle_event(_ev(pygame.MOUSEBUTTONDOWN, pos=pos, button=button), state, ui)


def _release(state, ui, pos, button=1):
    return game_input.handle_event(_ev(pygame.MOUSEBUTTONUP, pos=pos, button=button), state, ui)


def _motion(state, ui, pos, held=True):
    return game_input.handle_event(
        _ev(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(1 if held else 0, 0, 0)), state, ui)


def _key(state, ui, key):
    return game_input.handle_event(_ev(pygame.KEYDOWN, key=key, mod=0, unicode=""), state, ui)


def _tap(state, ui, sid):
    pos = ui.view.to_screen(state.systems[sid].pos)
    _press(state, ui, pos)
    return _release(state, ui, pos)


def _empty_pos(state, ui):
    """A point in the map viewport with no system under it."""
    px, py, pw, ph = config.play_rect()
    for x in range(px + 5, px + pw - 5, 7):
        for y in range(py + 5, py + ph - 5, 7):
            if game_input.pick_node(state, ui, (x, y)) is None:
                return (x, y)
    raise AssertionError("no empty point in the viewport")


def _routed_setup():
    """A live map already in route mode."""
    state, ui = _display_setup()
    game_input.handle_event(_ev(pygame.KEYDOWN, key=pygame.K_g, mod=0, unicode="g"), state, ui)
    ui.begin_route()
    return state, ui


def test_g_toggles_the_mode():
    state, ui = _display_setup()
    try:
        assert _key(state, ui, pygame.K_g) == "toggle_route"
        ui.begin_route()
        # inside the mode G is handled locally, and leaves it
        assert _key(state, ui, pygame.K_g) is None
        assert ui.mode == IDLE
    finally:
        pygame.quit()


def test_a_tap_through_input_aims_and_then_un_aims():
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        _tap(state, ui, home)
        assert ui.route_dest == home
        _tap(state, ui, home)
        assert ui.route_dest is None
    finally:
        pygame.quit()


def test_walking_the_target_over_every_pick_keeps_the_group():
    """The reported bug, at the gesture level: aiming at each pick in turn used to
    empty the group one system at a time."""
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        for n in state.systems[home].neighbors[:2]:
            state.systems[n].owner_id = 1
        picks = {home} | set(state.systems[home].neighbors[:2])
        ui.route_sel = set(picks)
        for sid in sorted(picks):
            _tap(state, ui, sid)
            assert ui.route_sel == picks, f"aiming at {sid} disturbed the group"
            assert ui.route_sources() == picks - {sid}
    finally:
        pygame.quit()


def test_a_drag_from_empty_space_boxes_a_selection():
    state, ui = _routed_setup()
    try:
        px, py, pw, ph = config.play_rect()
        start = _empty_pos(state, ui)
        _press(state, ui, start)
        assert ui.route_press is True and ui.route_box is False
        _motion(state, ui, (px + pw - 3, py + ph - 3))
        assert ui.route_box is True          # promoted past the threshold
        _release(state, ui, (px + pw - 3, py + ph - 3))
        assert ui.route_sel                  # picked up whatever was in the box
        assert ui.route_box is False and ui.route_press is False
    finally:
        pygame.quit()


def test_a_tap_on_empty_space_selects_nothing_and_clears_nothing():
    """This mode cancels explicitly, so a stray tap must not undo the group."""
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        ui.route_sel = {home}
        pos = _empty_pos(state, ui)
        _press(state, ui, pos)
        _release(state, ui, pos)             # never moved: no box
        assert ui.route_sel == {home}
    finally:
        pygame.quit()


def test_box_then_tap_is_the_whole_flow():
    """The one gesture pair the mode is built around: drag a group, tap a target."""
    state, ui = _routed_setup()
    try:
        px, py, pw, ph = config.play_rect()
        start = _empty_pos(state, ui)
        _press(state, ui, start)
        _motion(state, ui, (px + pw - 3, py + ph - 3))
        _release(state, ui, (px + pw - 3, py + ph - 3))
        assert ui.route_sel                       # a group, in one drag
        home = next(iter(ui.route_sel))
        nbr = state.systems[home].neighbors[0]
        _tap(state, ui, nbr)
        assert ui.route_dest == nbr and ui.route_plan
    finally:
        pygame.quit()


def test_cancel_button_drops_the_plan_without_writing_it():
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.route_sel = {home}
        _tap(state, ui, nbr)
        ui.route_cancel_rect = (300, 100, 60, 20)
        _press(state, ui, (310, 110))
        assert ui.mode == IDLE and ui.auto_forward == {}
    finally:
        pygame.quit()


def test_confirm_button_writes_the_plan():
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.route_sel = {home}
        _tap(state, ui, nbr)
        ui.route_confirm_rect = (400, 100, 60, 20)
        _press(state, ui, (410, 110))
        assert ui.auto_forward == {home: (nbr, 0)}
        assert ui.mode == IDLE
    finally:
        pygame.quit()


def test_esc_cancels_the_route_rather_than_quitting():
    state, ui = _routed_setup()
    try:
        assert _key(state, ui, pygame.K_ESCAPE) is None
        assert ui.mode == IDLE
    finally:
        pygame.quit()


def test_enter_confirms_instead_of_ending_the_turn():
    """The turn can't be ended from route mode at all, so the most obvious key
    keeps pointing at the most obvious action."""
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        nbr = state.systems[home].neighbors[0]
        ui.route_sel = {home}
        ui.route_dest = nbr
        ui.recompute_route(state)
        assert _key(state, ui, pygame.K_RETURN) is None    # not "end_turn"
        assert ui.auto_forward == {home: (nbr, 0)}
    finally:
        pygame.quit()


def test_x_clears_the_group_but_stays_in_the_mode():
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        ui.route_sel = {home}
        ui.set_route_dest(state, state.systems[home].neighbors[0])
        _key(state, ui, pygame.K_x)
        assert ui.route_sel == set() and ui.route_dest is None
        assert ui.mode == ROUTING
    finally:
        pygame.quit()


def test_render_zeroes_end_turn_so_it_cannot_be_hit_while_routing():
    state, ui = _routed_setup()
    try:
        screen = pygame.display.get_surface()
        render.draw(screen, state, ui)
        assert ui.end_turn_rect == (0, 0, 0, 0)
        # and the live-play strip is out of service too
        assert ui.play_pause_rect == (0, 0, 0, 0)
        assert ui.autoplay_button_rect == (0, 0, 0, 0)
        assert ui.history_button_rect == (0, 0, 0, 0)
    finally:
        pygame.quit()


def test_render_suppresses_the_panel_lists_while_routing():
    """Their × buttons would mutate auto_forward underneath the preview."""
    state, ui = _display_setup()
    try:
        screen = pygame.display.get_surface()
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        ui.auto_forward[home] = (state.systems[home].neighbors[0], 0)
        render.draw(screen, state, ui)
        assert ui.forward_hitboxes                 # normally listed
        ui.begin_route()
        render.draw(screen, state, ui)
        assert ui.forward_hitboxes == [] and ui.order_hitboxes == []
        assert ui.clear_forward_rect == (0, 0, 0, 0)
    finally:
        pygame.quit()


def test_resolving_a_turn_drops_any_open_plan():
    """A plan is only valid for the ownership it was computed against."""
    state, ui = _routed_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        ui.route_sel = {home}
        main.resolve_turn(state, ui)
        assert ui.mode == IDLE and ui.route_sel == set()
    finally:
        pygame.quit()


def test_history_mode_retires_the_route_button():
    """Every rect render didn't draw is zeroed, or a stale one keeps answering
    clicks — history mode draws no footer at all."""
    state, ui = _display_setup()
    try:
        render.draw(pygame.display.get_surface(), state, ui)
        assert ui.route_button_rect[2] > 0
        ui.history = True
        ui.history_max = ui.history_turn = 0
        render.draw(pygame.display.get_surface(), state, ui)
        assert ui.route_button_rect == (0, 0, 0, 0)
        assert ui.route_cancel_rect == (0, 0, 0, 0)
    finally:
        pygame.quit()


# --------------------------------------------------------------------------- #
# Rally sub-mode: planning
# --------------------------------------------------------------------------- #
def _rally(state, sel, human=1) -> Ui:
    """A Ui in rally mode with ``sel`` already picked as the rally points."""
    ui = _ui(state, human=human)
    ui.route_rally = True
    ui.route_sel = set(sel)
    ui.recompute_route(state)
    return ui


def test_rally_flows_every_system_to_the_nearest_point():
    """Two rally points at either end of a line split it down the middle."""
    s = _line(7, owned=range(7))
    ui = _rally(s, {0, 6})
    assert ui.route_plan == {1: (0, 0), 2: (1, 0), 3: (2, 0), 4: (5, 0), 5: (6, 0)}


def test_a_rally_point_gets_no_rule_of_its_own():
    """It is the sink: a rule there would forward the gathered ships straight out."""
    s = _line(4, owned=range(4))
    ui = _rally(s, {0})
    assert 0 not in ui.route_plan
    assert ui.route_plan == {1: (0, 0), 2: (1, 0), 3: (2, 0)}


def test_rally_covers_systems_the_player_never_picked():
    """The whole point of the sub-mode: one tap rules the entire empire."""
    s = _line(5, owned=range(5))
    ui = _rally(s, {4})
    assert set(ui.route_plan) == {0, 1, 2, 3}


def test_rally_ties_are_deterministic():
    """A system equidistant from two rally points must not flip-flop between
    recomputes, or the preview shimmers and the confirmed rule is a coin toss."""
    s = _line(5, owned=range(5))
    ui = _rally(s, {0, 4})
    picks = set()
    for _ in range(10):
        ui.recompute_route(s)
        picks.add(ui.route_plan[2])
    assert len(picks) == 1


def test_an_un_owned_rally_point_is_legal():
    """The assault funnel: rally the empire on an enemy system. Its incoming rule
    sits on the last system we own, exactly as a chain destination's does."""
    s = _line(4, owned={0, 1, 2})
    s.systems[3].owner_id = 2
    ui = _rally(s, {3})
    assert ui.route_plan == {0: (1, 0), 1: (2, 0), 2: (3, 0)}
    assert ui.route_unroutable == set()


def test_a_pocket_no_rally_point_reaches_is_unroutable():
    s = _graph([(0, 1), (1, 2), (3, 4)], {sid: 1 for sid in range(5)})
    ui = _rally(s, {0})
    assert set(ui.route_plan) == {1, 2}
    assert ui.route_unroutable == {3, 4}


def test_rally_never_routes_through_territory_we_do_not_own():
    s = _line(4, owned={0, 3})
    s.systems[1].owner_id = s.systems[2].owner_id = 2
    ui = _rally(s, {0})
    assert ui.route_plan == {}
    assert ui.route_unroutable == {3}


def test_no_rally_points_yet_means_nothing_is_unroutable():
    """Otherwise the whole empire reads as unreachable before the first tap."""
    s = _line(4, owned=range(4))
    ui = _rally(s, set())
    assert ui.route_plan == {} and ui.route_unroutable == set()


def test_rally_keeps_a_rule_that_already_points_the_right_way():
    s = _line(4, owned=range(4))
    ui = _ui(s)
    ui.route_rally = True
    ui.auto_forward[2] = (1, 4)     # already flowing inward, keeping 4
    ui.route_sel = {0}
    ui.recompute_route(s)
    assert ui.route_plan[2] == (1, 4)
    assert 2 not in ui.route_replaces


def test_a_rally_plan_reports_the_rules_it_replaces():
    """Rally overwrites the whole rear, so the count is the only warning the
    player gets before confirming."""
    s = _graph([(0, 1), (1, 2), (2, 3), (1, 3)], {sid: 1 for sid in range(4)})
    ui = _ui(s)
    ui.route_rally = True
    ui.auto_forward[3] = (2, 4)     # points away from the rally point
    ui.route_sel = {0}
    ui.recompute_route(s)
    assert ui.route_plan[3] == (1, 0)   # re-aimed, keep reset
    assert ui.route_replaces == {3}


def test_a_rally_point_forwarding_back_into_the_field_is_a_cycle():
    """The only shape a rally loop can take: the plan rules every system it
    reaches, so the sink's own leftover rule is the one edge that can close one."""
    s = _line(3, owned=range(3))
    ui = _ui(s)
    ui.route_rally = True
    ui.auto_forward[0] = (1, 0)     # the rally point pushes straight back out
    ui.route_sel = {0}
    ui.recompute_route(s)
    assert ui.route_cycles == {0, 1}


def test_rally_confirm_breaks_the_cycle_rather_than_arming_it():
    s = _line(3, owned=range(3))
    ui = _ui(s)
    ui.route_rally = True
    ui.auto_forward[0] = (1, 0)
    ui.route_sel = {0}
    ui.recompute_route(s)
    ui.confirm_route(s)
    assert ui.auto_forward == {1: (0, 0), 2: (1, 0)}   # the sink's rule is gone


def test_rally_confirm_writes_the_field_and_leaves_the_mode():
    s = _line(4, owned=range(4))
    ui = _rally(s, {0})
    ui.mode = ROUTING
    ui.confirm_route(s)
    assert ui.auto_forward == {1: (0, 0), 2: (1, 0), 3: (2, 0)}
    assert ui.mode == IDLE and ui.route_sel == set()


# --------------------------------------------------------------------------- #
# Rally sub-mode: switching, pruning and taps
# --------------------------------------------------------------------------- #
def test_switching_sub_mode_drops_the_proposal():
    """A chain group and a set of rally points share `route_sel` but mean opposite
    things, so carrying one over would reinterpret sources as sinks."""
    s = _line(4, owned=range(4))
    ui = _ui(s)
    ui.route_sel = {0, 1}
    ui.set_route_dest(s, 3)
    ui.set_route_rally(s, True)
    assert ui.route_rally is True
    assert ui.route_sel == set() and ui.route_dest is None and ui.route_plan == {}


def test_switching_to_the_sub_mode_already_live_changes_nothing():
    s = _line(4, owned=range(4))
    ui = _rally(s, {0})
    ui.set_route_rally(s, True)
    assert ui.route_sel == {0} and ui.route_plan


def test_the_sub_mode_survives_reset_route():
    """It is a preference, not proposal state — and `reset_route` runs every turn."""
    s = _line(4, owned=range(4))
    ui = _rally(s, {0})
    ui.mode = ROUTING
    ui.reset_route()
    assert ui.route_rally is True
    assert ui.route_sel == set() and ui.route_plan == {}


def test_an_un_owned_rally_point_survives_the_prune():
    """A chain source has to be a system we hold; a sink does not."""
    s = _line(4, owned={0, 1, 2})
    s.systems[3].owner_id = 2
    ui = _rally(s, {3})
    assert ui.route_sel == {3}
    ui.set_route_rally(s, False)
    ui.route_sel = {3}
    ui.recompute_route(s)
    assert ui.route_sel == set()


def test_a_rally_point_must_have_been_seen():
    s = _line(3, owned={0, 1})
    ui = _ui(s)
    ui.route_rally = True
    ui.seen = {0, 1}               # 2 never sighted
    ui.route_tap(s, 2)
    assert ui.route_sel == set()


def test_a_rally_tap_toggles():
    """One meaning, whatever it lands on — unlike a chain tap, which aims."""
    s = _line(3, owned=range(3))
    ui = _rally(s, set())
    ui.route_tap(s, 0)
    assert ui.route_sel == {0} and ui.route_dest is None
    ui.route_tap(s, 2)
    assert ui.route_sel == {0, 2}
    ui.route_tap(s, 0)
    assert ui.route_sel == {2}


# --------------------------------------------------------------------------- #
# Rally sub-mode: gestures and render
# --------------------------------------------------------------------------- #
def _rally_setup():
    """A live map already in route mode, switched to rally."""
    state, ui = _routed_setup()
    ui.set_route_rally(state, True)
    return state, ui


def test_tab_switches_sub_mode():
    state, ui = _routed_setup()
    try:
        assert ui.route_rally is False
        _key(state, ui, pygame.K_TAB)
        assert ui.route_rally is True and ui.mode == ROUTING
        _key(state, ui, pygame.K_TAB)
        assert ui.route_rally is False
    finally:
        pygame.quit()


def test_the_footer_buttons_switch_sub_mode():
    state, ui = _routed_setup()
    try:
        render.draw(pygame.display.get_surface(), state, ui)
        assert ui.route_chain_rect[2] > 0 and ui.route_rally_rect[2] > 0
        rx, ry, rw, rh = ui.route_rally_rect
        _press(state, ui, (rx + rw // 2, ry + rh // 2))
        assert ui.route_rally is True
        render.draw(pygame.display.get_surface(), state, ui)
        cx, cy, cw, ch = ui.route_chain_rect
        _press(state, ui, (cx + cw // 2, cy + ch // 2))
        assert ui.route_rally is False
    finally:
        pygame.quit()


def test_a_tap_through_input_toggles_a_rally_point():
    state, ui = _rally_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        _tap(state, ui, home)
        assert ui.route_sel == {home} and ui.route_dest is None
        _tap(state, ui, home)
        assert ui.route_sel == set()
    finally:
        pygame.quit()


def test_a_drag_from_empty_space_pans_instead_of_boxing_in_rally():
    """Rally picks are all taps, so drag goes back to panning — the one gesture
    chain mode has to give up for its selection box."""
    state, ui = _rally_setup()
    try:
        px, py, pw, ph = config.play_rect()
        # zoom about the middle, so the pan clamp has slack in every direction
        ui.view.zoom_at((px + pw // 2, py + ph // 2), 2.0)
        start = _empty_pos(state, ui)
        before = ui.view.to_screen(state.systems[0].pos)
        _press(state, ui, start)
        assert ui.pan_active is True and ui.route_press is False
        _motion(state, ui, (start[0] + 40, start[1] + 40))
        _release(state, ui, (start[0] + 40, start[1] + 40))
        assert ui.view.to_screen(state.systems[0].pos) != before
        assert ui.route_sel == set() and ui.route_box is False
    finally:
        pygame.quit()


def test_render_draws_a_rally_plan_without_a_destination():
    """Rally leaves `route_dest` None, so every preview layer — the sink rings
    especially, which chain mode reaches through `route_dest` — has to cope."""
    state, ui = _rally_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        for nbr in state.systems[home].neighbors:   # something for the field to rule
            state.systems[nbr].owner_id = 1
        ui.route_tap(state, home)
        assert ui.route_plan and ui.route_dest is None
        render.draw(pygame.display.get_surface(), state, ui)
        assert ui.route_confirm_rect[2] > 0       # the confirm takes the End Turn slot
        assert ui.end_turn_rect == (0, 0, 0, 0)
    finally:
        pygame.quit()

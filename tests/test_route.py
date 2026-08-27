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
from starconquest import config, mapgen, model  # noqa: E402
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


def test_the_destination_is_dropped_from_the_selection():
    """A system can't be both a source and the sink."""
    s = _line(3, owned={0, 1, 2})
    ui = _ui(s)
    ui.route_sel = {0, 2}
    ui.set_route_dest(s, 2)
    assert ui.route_sel == {0}
    assert 2 not in ui.route_plan


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


def test_tapping_a_selected_system_removes_it():
    state, ui = _display_setup()
    try:
        home = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
        ui.toggle_route_system(state, home)
        assert home in ui.route_sel
        ui.toggle_route_system(state, home)
        assert home not in ui.route_sel
    finally:
        pygame.quit()


def test_a_system_we_do_not_own_cannot_be_selected():
    state, ui = _display_setup()
    try:
        theirs = next(sid for sid, s in state.systems.items() if s.owner_id != 1)
        ui.toggle_route_system(state, theirs)
        assert ui.route_sel == set()
    finally:
        pygame.quit()

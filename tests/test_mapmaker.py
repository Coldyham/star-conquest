"""Drive the map creator headlessly with synthetic pygame events.

Mirrors ``test_menu``'s harness: SDL dummy drivers, no real window, draw first so
the widgets record their rects, then click a rect's centre.

The editor is the one scene laid out on the *real* surface at ``config.ui_scale``
rather than on a fixed canvas, so it can be checked for layout containment at more
than one scale — a check the menu structurally cannot make.
"""

from __future__ import annotations

import os
import random

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame  # noqa: E402
import pytest  # noqa: E402

from starconquest import config, custommap, mapgen, mapmaker, widgets  # noqa: E402
from starconquest.custommap import CustomMap, MapNode  # noqa: E402
from starconquest.geometry import dist  # noqa: E402
from starconquest.settings import Settings  # noqa: E402


@pytest.fixture
def scene():
    """A live editor over a small hand map, plus its surface and settings."""
    pygame.init()
    # Fonts are cached module-globally and must belong to the live pygame session;
    # the per-test init/quit cycle would otherwise render with freed handles.
    widgets._FONTS.clear()
    scale = config.ui_scale
    config.apply_ui_scale(1.0)
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    settings = Settings.defaults()
    ed = mapmaker.Editor(recipe=_square(), view=mapmaker._build_view())
    try:
        yield screen, ed, settings
    finally:
        config.apply_ui_scale(scale)
        widgets._FONTS.clear()
        pygame.quit()


def _square() -> CustomMap:
    return CustomMap(
        nodes=[
            MapNode(200, 200, 3, 12, 1),
            MapNode(800, 200, 4, 6, 0),
            MapNode(800, 800, 3, 12, 2),
            MapNode(200, 800, 5, 4, 0),
        ],
        lanes=[(0, 1), (1, 2), (2, 3), (0, 3)],
    ).normalised()


def _press(screen, ed, settings, pos, button=1):
    """Draw (to lay out rects), then press at ``pos``."""
    mapmaker.draw(screen, ed, settings)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button)
    return mapmaker.handle_event(ev, ed, settings)


def _click_key(screen, ed, settings, key, button=1):
    mapmaker.draw(screen, ed, settings)
    ev = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=ed.rects[key].center, button=button)
    return mapmaker.handle_event(ev, ed, settings)


def _release(ed, settings, pos, button=1):
    ev = pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos, button=button)
    return mapmaker.handle_event(ev, ed, settings)


def _drag(screen, ed, settings, index, to_world):
    """Press on node ``index`` and drag it to a world position, then release."""
    start = ed.view.to_screen(ed.recipe.nodes[index].pos)
    _press(screen, ed, settings, start)
    target = ed.view.to_screen(to_world)
    for pos in (start, target):
        mapmaker.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=pos, buttons=(1, 0, 0), rel=(0, 0)),
            ed, settings)
    _release(ed, settings, target)


def _empty_spot(ed) -> tuple[int, int]:
    """A viewport point well clear of every system — the middle of the square."""
    return ed.view.to_screen((500.0, 500.0))


# --------------------------------------------------------------------------- #
# Placing
# --------------------------------------------------------------------------- #
def test_a_placed_system_lands_where_it_was_clicked(scene):
    """The editor's one novel use of the transform: `to_world` on the way in must
    invert back through `to_screen` to the pixel that was pressed."""
    screen, ed, settings = scene
    pos = _empty_spot(ed)
    _press(screen, ed, settings, pos)
    assert len(ed.recipe.nodes) == 5
    back = ed.view.to_screen(ed.recipe.nodes[-1].pos)
    assert abs(back[0] - pos[0]) <= 2 and abs(back[1] - pos[1]) <= 2


def test_placing_selects_the_new_system(scene):
    screen, ed, settings = scene
    _press(screen, ed, settings, _empty_spot(ed))
    assert ed.sel_node == len(ed.recipe.nodes) - 1


def test_a_placement_too_close_to_another_system_is_refused(scene):
    screen, ed, settings = scene
    # Framed in close first: at the resting zoom the separation rule (45 world
    # units) is barely wider than a system's tap reach, so an unzoomed press that
    # near node 0 would select it rather than try to place beside it.
    ed.view.fit_to([(150.0, 150.0), (350.0, 350.0)])
    sep = config.CUSTOM_MIN_NODE_SEP_FRAC * config.WORLD_SIZE
    pos = ed.view.to_screen((200.0 + sep * 0.8, 200.0))
    assert dist(pos, ed.view.to_screen((200.0, 200.0))) > config.NODE_TAP_MIN

    _press(screen, ed, settings, pos)
    assert len(ed.recipe.nodes) == 4
    assert not ed.status_ok and ed.status


def test_a_placement_on_a_lane_is_refused(scene):
    """mapgen's graze rule: a system sitting on a lane renders as if the lane ran
    underneath it."""
    screen, ed, settings = scene
    on_lane = ed.view.to_screen((500.0, 200.0))     # midway along the 0-1 lane
    _press(screen, ed, settings, on_lane)
    assert len(ed.recipe.nodes) == 4


def test_placement_stops_at_the_node_cap(scene):
    screen, ed, settings = scene
    ed.recipe = CustomMap(
        nodes=[MapNode(0, 0, 3, 1, 1)] * config.MAX_NODES, lanes=[])
    _press(screen, ed, settings, _empty_spot(ed))
    assert len(ed.recipe.nodes) == config.MAX_NODES
    assert str(config.MAX_NODES) in ed.status


def test_a_palette_pick_sets_the_next_placement(scene):
    screen, ed, settings = scene
    _click_key(screen, ed, settings, "pal_2")
    ed.sel_node = None                              # nothing to retype
    _press(screen, ed, settings, _empty_spot(ed))
    assert ed.recipe.nodes[-1].production == 2


def test_a_palette_pick_retypes_the_selected_system(scene):
    """Choosing "5/ship" while a system is selected must visibly do something —
    otherwise the swatch looks inert."""
    screen, ed, settings = scene
    ed.sel_node = 0
    _click_key(screen, ed, settings, "pal_5")
    assert ed.recipe.nodes[0].production == 5


def test_a_random_placement_only_ever_rolls_the_weighted_values(scene):
    screen, ed, settings = scene
    ed.pick = mapmaker.PALETTE_RANDOM
    rolled = {mapmaker._rolled_production(ed) for _ in range(200)}
    assert rolled <= set(config.PRODUCTION_WEIGHTS)


# --------------------------------------------------------------------------- #
# Dragging
# --------------------------------------------------------------------------- #
def test_a_legal_drag_moves_the_system(scene):
    """Checked in pixels, not world units: a drag round-trips through integer
    screen coordinates, so at the resting zoom the landing world position is only
    ever accurate to the pixel the cursor was on."""
    screen, ed, settings = scene
    _drag(screen, ed, settings, 1, (700.0, 350.0))
    landed = ed.view.to_screen(ed.recipe.nodes[1].pos)
    target = ed.view.to_screen((700.0, 350.0))
    assert abs(landed[0] - target[0]) <= 1 and abs(landed[1] - target[1]) <= 1


def test_an_illegal_drag_snaps_back_to_where_it_started(scene):
    """Not clamped to "the nearest legal point": with several rules live at once
    that point is ill-defined, and it silently puts the system somewhere nobody
    asked for."""
    screen, ed, settings = scene
    before = (ed.recipe.nodes[1].x, ed.recipe.nodes[1].y)
    _drag(screen, ed, settings, 1, (215.0, 210.0))   # right on top of node 0
    assert (ed.recipe.nodes[1].x, ed.recipe.nodes[1].y) == before
    assert not ed.status_ok


def test_an_illegal_drag_rings_the_offenders_while_it_is_held(scene):
    screen, ed, settings = scene
    start = ed.view.to_screen(ed.recipe.nodes[1].pos)
    _press(screen, ed, settings, start)
    mapmaker.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=ed.view.to_screen((215.0, 210.0)),
                           buttons=(1, 0, 0), rel=(0, 0)), ed, settings)
    assert {0, 1} <= ed.bad_nodes     # both ends of the violation are named
    _release(ed, settings, ed.view.to_screen((215.0, 210.0)))
    assert ed.bad_nodes == frozenset()               # cleared once the drag is over


def test_moving_a_system_keeps_its_lane_times_honest(scene):
    """The test that would have caught treating the recipe as if lane lengths were
    stored: positions plus index pairs means the lanes follow their nodes, and the
    number the sidebar quotes must be the number the built map gives."""
    screen, ed, settings = scene
    _drag(screen, ed, settings, 1, (400.0, 250.0))
    shown = mapmaker._lane_turns(ed.recipe.nodes[0], ed.recipe.nodes[1])

    state = mapgen.generate_custom(1, ed.recipe.normalised())
    assert state.travel_turns(0, 1) == shown


def test_a_tap_that_does_not_move_is_not_an_edit(scene):
    """Selecting a system must not push an undo snapshot — doing so would clear
    the redo stack every time you looked at one."""
    screen, ed, settings = scene
    pos = ed.view.to_screen(ed.recipe.nodes[0].pos)
    _press(screen, ed, settings, pos)
    _release(ed, settings, pos)
    assert ed.sel_node == 0
    assert ed.undo_stack == []


def test_place_then_position_is_one_undo(scene):
    screen, ed, settings = scene
    pos = _empty_spot(ed)
    _press(screen, ed, settings, pos)
    for step in (pos, ed.view.to_screen((520.0, 540.0))):
        mapmaker.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=step, buttons=(1, 0, 0), rel=(0, 0)),
            ed, settings)
    _release(ed, settings, ed.view.to_screen((520.0, 540.0)))
    assert len(ed.recipe.nodes) == 5
    mapmaker._undo(ed)
    assert len(ed.recipe.nodes) == 4


# --------------------------------------------------------------------------- #
# The sidebar
# --------------------------------------------------------------------------- #
def test_steppers_edit_the_selected_system(scene):
    screen, ed, settings = scene
    ed.sel_node = 1
    _click_key(screen, ed, settings, "prod_plus")
    _click_key(screen, ed, settings, "ships_minus")
    assert ed.recipe.nodes[1].production == 5
    assert ed.recipe.nodes[1].ships == 5


def test_the_owner_row_sets_a_seat_in_one_press(scene):
    """A seat is a colour, so it is pointed at rather than stepped to — and any
    seat in the row is one press away, not four."""
    screen, ed, settings = scene
    ed.sel_node = 1
    _click_key(screen, ed, settings, "own_2")
    assert ed.recipe.nodes[1].owner == 2
    mapmaker._undo(ed)
    assert ed.recipe.nodes[1].owner == 0


def test_the_owner_row_offers_exactly_what_the_seat_palette_does(scene):
    """One list, two readers: a seat the sidebar offers but the Owners palette
    calls a gap (or the other way about) is a contradiction, not a choice."""
    screen, ed, settings = scene
    ed.sel_node = 1
    mapmaker.draw(screen, ed, settings)
    sidebar = {int(k[4:]) for k in ed.rects if k.startswith("own_")}
    ed.tool = mapmaker.OWNERS
    mapmaker.draw(screen, ed, settings)
    palette = {int(k[5:]) for k in ed.rects if k.startswith("seat_")}
    assert sidebar == palette == set(mapmaker._seat_entries(ed.recipe))


def test_the_owner_row_does_not_toggle_back_to_neutral(scene):
    """Neutral has its own swatch here, so pressing the seat a system already
    holds must not mean something else — that is the map tap's job, where there is
    nothing else to press."""
    screen, ed, settings = scene
    ed.sel_node = 0                      # already seat 1
    _click_key(screen, ed, settings, "own_1")
    assert ed.recipe.nodes[0].owner == 1
    assert not ed.undo_stack             # nothing happened, so nothing to undo


def test_steppers_stop_at_their_ceilings(scene):
    screen, ed, settings = scene
    ed.sel_node = 1
    ed.recipe.nodes[1].ships = config.CUSTOM_MAX_SHIPS
    _click_key(screen, ed, settings, "ships_plus")
    assert ed.recipe.nodes[1].ships == config.CUSTOM_MAX_SHIPS
    ed.recipe.nodes[1].ships = 0
    _click_key(screen, ed, settings, "ships_minus")
    assert ed.recipe.nodes[1].ships == 0


def test_delete_remaps_every_surviving_lane_and_drops_the_selection(scene):
    screen, ed, settings = scene
    ed.sel_node = 1
    _click_key(screen, ed, settings, "delete")
    assert len(ed.recipe.nodes) == 3
    assert ed.sel_node is None
    # The square minus node 1: (0,1)/(1,2) went with it, (2,3) and (0,3) shift down.
    assert ed.recipe.lanes == [(0, 2), (1, 2)]
    assert all(a < len(ed.recipe.nodes) and b < len(ed.recipe.nodes)
               for a, b in ed.recipe.lanes)


def test_an_econ_slider_writes_settings(scene):
    screen, ed, settings = scene
    mapmaker.draw(screen, ed, settings)
    rect = ed.rects["adv_garr_base"]
    mapmaker.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(rect.right - 1, rect.centery), button=1),
        ed, settings)
    assert settings.garrison_base == 20     # the spec's top end


# --------------------------------------------------------------------------- #
# Lanes, undo and the library
# --------------------------------------------------------------------------- #
def test_auto_lanes_reproduces_mapgens_own_edge_builder(scene):
    screen, ed, settings = scene
    ed.tool = mapmaker.LANES
    ed.recipe.lanes = []
    _click_key(screen, ed, settings, "auto_lanes")
    assert ed.confirm == "auto_lanes"       # destructive, so it asks first
    mapmaker.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y, mod=0), ed, settings)

    expected = sorted((min(a, b), max(a, b))
                      for a, b in mapgen._planar_edges([n.pos for n in ed.recipe.nodes]))
    assert ed.recipe.lanes == expected
    assert ed.recipe.problems() == []       # planar and graze-free by construction


def test_auto_lanes_can_be_declined(scene):
    screen, ed, settings = scene
    ed.tool = mapmaker.LANES
    ed.recipe.lanes = []
    _click_key(screen, ed, settings, "auto_lanes")
    mapmaker.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0), ed, settings)
    assert ed.recipe.lanes == [] and ed.confirm is None


def test_undo_and_redo_walk_the_same_path(scene):
    screen, ed, settings = scene
    before = ed.recipe.to_dict()
    _press(screen, ed, settings, _empty_spot(ed))
    _release(ed, settings, _empty_spot(ed))
    after = ed.recipe.to_dict()

    mapmaker._undo(ed)
    assert ed.recipe.to_dict() == before
    mapmaker._redo(ed)
    assert ed.recipe.to_dict() == after


def test_undo_is_bounded(scene):
    screen, ed, settings = scene
    for _ in range(config.EDIT_UNDO_DEPTH + 20):
        mapmaker._push_undo(ed)
    assert len(ed.undo_stack) == config.EDIT_UNDO_DEPTH


def test_save_and_open_round_trip(scene, tmp_path, monkeypatch):
    screen, ed, settings = scene
    monkeypatch.setattr(mapmaker.paths, "maps_dir", lambda: tmp_path)
    ed.filename = "ring"
    _click_key(screen, ed, settings, "save")
    assert (tmp_path / "ring.json").exists() and ed.status_ok

    saved = ed.recipe.to_dict()
    ed.recipe = CustomMap()
    _click_key(screen, ed, settings, "open")
    assert ed.recipe.to_dict() == saved


def test_opening_a_file_that_is_not_a_map_says_so(scene, tmp_path, monkeypatch):
    screen, ed, settings = scene
    monkeypatch.setattr(mapmaker.paths, "maps_dir", lambda: tmp_path)
    (tmp_path / "junk.json").write_text('{"n": []}')
    ed.filename = "junk"
    before = ed.recipe.to_dict()
    _click_key(screen, ed, settings, "open")
    assert not ed.status_ok and ed.recipe.to_dict() == before


# --------------------------------------------------------------------------- #
# The Play gate, committing, and the camera
# --------------------------------------------------------------------------- #
def test_play_commits_and_starts_when_the_map_is_playable(scene):
    screen, ed, settings = scene
    assert _click_key(screen, ed, settings, "play") == "play"
    assert settings.custom_map == ed.recipe.normalised()
    assert settings.players == 2 and settings.nodes == 4


def test_play_is_refused_while_a_blocker_stands(scene):
    screen, ed, settings = scene
    ed.recipe.lanes = [(0, 1)]                      # nodes 2 and 3 stranded
    assert _click_key(screen, ed, settings, "play") is None
    assert settings.custom_map is None
    assert ed.status == ed.recipe.blockers()[0].text


def test_the_play_gate_is_exactly_the_validator(scene):
    screen, ed, settings = scene
    for mutate in (lambda: None,
                   lambda: ed.recipe.lanes.clear(),
                   lambda: setattr(ed.recipe.nodes[0], "owner", 0)):
        mutate()
        assert ed.can_play() == (ed.recipe.problems() == [])


def test_leaving_to_the_menu_commits_even_a_half_built_map(scene):
    """A half-built map must survive a trip to the menu to change a setting; only
    *starting a game* is gated."""
    screen, ed, settings = scene
    ed.recipe.lanes = [(0, 1)]
    assert _click_key(screen, ed, settings, "back") == "menu"
    assert settings.custom_map is not None
    assert settings.custom_map.lanes == [(0, 1)]


def test_an_empty_canvas_commits_as_no_hand_map_at_all(scene):
    """Not a half-built map: it carries nothing to preserve, and writing it would
    pin the menu into "Edit map" with a setup that cannot start — which is what
    opening the creator and leaving straight away would do, now that blank is
    where it opens."""
    screen, ed, settings = scene
    settings.players, settings.nodes = 4, 20
    mapmaker._adopt(ed, CustomMap())
    assert _click_key(screen, ed, settings, "back") == "menu"
    assert settings.custom_map is None
    assert (settings.players, settings.nodes) == (4, 20)   # the menu's own, untouched


def test_commit_keeps_players_and_nodes_in_step_with_the_map(scene):
    screen, ed, settings = scene
    ed.recipe.nodes[3].owner = 3
    mapmaker.commit(ed, settings)
    assert settings.players == 3 and settings.nodes == 4


def test_the_zoom_cluster_wins_over_placing(scene):
    """The cluster sits *inside* the viewport, so testing the map first would
    place a system under the + button."""
    screen, ed, settings = scene
    before = len(ed.recipe.nodes)
    zoom = ed.view.zoom
    _click_key(screen, ed, settings, "zoom_in")
    assert len(ed.recipe.nodes) == before
    assert ed.view.zoom > zoom


def test_right_drag_pans_and_left_drag_does_not(scene):
    screen, ed, settings = scene
    ed.view.zoom_at(_empty_spot(ed), 2.0)
    off = ed.view.off_x
    pos = _empty_spot(ed)
    _press(screen, ed, settings, pos, button=3)
    mapmaker.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=(pos[0] - 40, pos[1]),
                           buttons=(0, 0, 1), rel=(0, 0)), ed, settings)
    assert ed.view.off_x != off
    assert len(ed.recipe.nodes) == 4        # a right press never places


def test_r_resets_the_camera(scene):
    screen, ed, settings = scene
    ed.view.zoom_at(_empty_spot(ed), 3.0)
    mapmaker.draw(screen, ed, settings)
    mapmaker.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r, mod=0), ed, settings)
    assert ed.view.zoom == 1.0


def test_a_problem_row_selects_and_frames_its_offender(scene):
    """A warning you cannot find is a warning you ignore."""
    screen, ed, settings = scene
    ed.recipe.lanes = [(0, 1)]
    mapmaker.draw(screen, ed, settings)
    index, rect = ed.problem_rows[0]
    mapmaker.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=rect.center, button=1), ed, settings)
    assert ed.sel_node in ed.recipe.problems()[index].nodes


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
@pytest.mark.parametrize("tool", [mapmaker.SYSTEMS, mapmaker.LANES, mapmaker.OWNERS])
def test_every_control_stays_on_screen_at_any_ui_scale(scene, scale, tool):
    """The editor's analogue of `test_tab_content_stays_inside_the_panel`, and a
    check the fixed-canvas menu structurally cannot make. Every tool, because each
    draws its own palette band and its own sidebar."""
    screen, ed, settings = scene
    config.apply_ui_scale(scale)
    widgets._FONTS.clear()
    ed.view = mapmaker._build_view()
    ed.tool, ed.sel_node = tool, 0
    ed.sel_lane = 0
    ed.recipe.nodes[2].owner = 4        # so the seat-gap fix is drawn too
    mapmaker.draw(screen, ed, settings)

    window = pygame.Rect(0, 0, config.SCREEN_W, config.SCREEN_H)
    for key, rect in ed.rects.items():
        assert window.contains(rect), f"{tool} at {scale}: {key} {tuple(rect)} is off screen"


@pytest.mark.parametrize("scale", [1.0, 2.0])
def test_the_viewport_never_overlaps_the_chrome(scene, scale):
    screen, ed, settings = scene
    config.apply_ui_scale(scale)
    view = pygame.Rect(mapmaker._view_rect())
    assert view.w > 0 and view.h > 0
    assert not view.colliderect(mapmaker._side_rect())
    assert not view.colliderect(mapmaker._palette_rect())
    assert not view.colliderect(mapmaker._footer_rect())


def test_the_viewport_does_not_move_when_the_tool_does(scene):
    """A band that resized as tools switched would shift the map under the cursor
    and invalidate the camera with it."""
    screen, ed, settings = scene
    before = mapmaker._view_rect()
    for tool in (mapmaker.LANES, mapmaker.OWNERS, mapmaker.SYSTEMS):
        ed.tool = tool
        assert mapmaker._view_rect() == before


def test_an_unfinished_tool_records_no_rect(scene, monkeypatch):
    """Inert by construction rather than by a disabled flag some later branch
    forgets to check. Every tool is finished now, so the mechanism is pinned by
    taking one away rather than by waiting for the next unfinished one."""
    screen, ed, settings = scene
    mapmaker.draw(screen, ed, settings)
    assert {"tool_systems", "tool_lanes", "tool_owners"} <= set(ed.rects)

    monkeypatch.setattr(mapmaker, "_READY_TOOLS", (mapmaker.SYSTEMS,))
    mapmaker.draw(screen, ed, settings)
    assert "tool_systems" in ed.rects
    assert "tool_lanes" not in ed.rects and "tool_owners" not in ed.rects


def test_a_control_not_drawn_this_frame_cannot_be_clicked(scene):
    """`draw` clears the rect table every frame, so there is never a stale or
    zeroed rect left answering clicks."""
    screen, ed, settings = scene
    ed.sel_node = 0
    mapmaker.draw(screen, ed, settings)
    delete = ed.rects["delete"]
    ed.sel_node = None
    mapmaker.draw(screen, ed, settings)

    assert "delete" not in ed.rects and "reroll" not in ed.rects
    assert mapmaker._hit(ed, delete.center) != "delete"
    before = ed.recipe.to_dict()
    mapmaker.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=delete.center, button=1), ed, settings)
    assert ed.recipe.to_dict() == before


# --------------------------------------------------------------------------- #
# Opening the editor
# --------------------------------------------------------------------------- #
def test_opening_without_a_map_starts_from_a_blank_canvas(scene):
    """*Create map* means create. A generated board to pick apart is a different
    task, and it is one press away on the footer's *Generate*."""
    screen, ed, settings = scene
    fresh = mapmaker.open_editor(settings)
    assert fresh.recipe == CustomMap()


def test_generate_rolls_the_map_the_seed_describes(scene):
    """Same layout as the seed's own board, only translated — `_centred` shifts it
    into the middle of the creator's wider canvas and changes nothing else."""
    screen, ed, settings = scene
    fresh = mapmaker.open_editor(settings, seed=11)
    _click_key(screen, fresh, settings, "generate")
    expected = mapmaker._centred(custommap.from_state(
        mapgen.generate(11, settings.mode, settings.nodes, settings.players)))
    assert fresh.recipe == expected
    assert fresh.can_play()


def test_opening_with_a_map_edits_a_copy_of_it(scene):
    screen, ed, settings = scene
    settings.custom_map = _square()
    fresh = mapmaker.open_editor(settings)
    fresh.recipe.nodes[0].x = 123
    assert settings.custom_map.nodes[0].x == 200


def test_new_clears_to_a_blank_canvas(scene):
    screen, ed, settings = scene
    _click_key(screen, ed, settings, "new")
    mapmaker.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y, mod=0), ed, settings)
    assert ed.recipe.nodes == [] and ed.sel_node is None
    mapmaker._undo(ed)
    assert len(ed.recipe.nodes) == 4


def test_a_resize_keeps_the_zoom_and_reflows_the_viewport(scene):
    screen, ed, settings = scene
    ed.view.zoom_at(_empty_spot(ed), 2.5)
    zoomed = ed.view.zoom
    try:
        config.SCREEN_W, config.SCREEN_H = 1100, 800
        mapmaker.reflow(ed)
        assert ed.view.zoom == pytest.approx(zoomed)
        view = pygame.Rect(mapmaker._view_rect())
        assert view.right <= config.SCREEN_W and view.bottom <= config.SCREEN_H
    finally:
        config.SCREEN_W, config.SCREEN_H = 1440, 960


# --------------------------------------------------------------------------- #
# The Lanes tool
# --------------------------------------------------------------------------- #
def _lanes(scene):
    screen, ed, settings = scene
    ed.tool = mapmaker.LANES
    return screen, ed, settings


def _at(ed, index):
    return ed.view.to_screen(ed.recipe.nodes[index].pos)


def test_tapping_two_systems_lays_a_lane(scene):
    screen, ed, settings = _lanes(scene)
    ed.recipe.lanes = []
    _press(screen, ed, settings, _at(ed, 0))
    assert ed.lane_src == 0 and ed.recipe.lanes == []   # armed, not yet committed
    _release(ed, settings, _at(ed, 0))
    _press(screen, ed, settings, _at(ed, 2))
    assert ed.recipe.lanes == [(0, 2)]
    assert ed.lane_src is None


def test_dragging_between_systems_lays_the_same_lane(scene):
    """Two gestures, one `_add_lane` — so they must produce byte-identical work."""
    screen, ed, settings = _lanes(scene)
    ed.recipe.lanes = []
    _press(screen, ed, settings, _at(ed, 0))
    for pos in (_at(ed, 0), _at(ed, 2)):
        mapmaker.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=pos, buttons=(1, 0, 0), rel=(0, 0)),
            ed, settings)
    assert ed.lane_drag
    _release(ed, settings, _at(ed, 2))
    assert ed.recipe.lanes == [(0, 2)]
    assert ed.lane_src is None and not ed.lane_drag


def test_a_drag_that_lands_on_nothing_cancels_the_arming(scene):
    screen, ed, settings = _lanes(scene)
    ed.recipe.lanes = []
    _press(screen, ed, settings, _at(ed, 0))
    for pos in (_at(ed, 0), _empty_spot(ed)):
        mapmaker.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=pos, buttons=(1, 0, 0), rel=(0, 0)),
            ed, settings)
    _release(ed, settings, _empty_spot(ed))
    assert ed.recipe.lanes == [] and ed.lane_src is None


def test_tapping_the_armed_system_again_disarms(scene):
    screen, ed, settings = _lanes(scene)
    _press(screen, ed, settings, _at(ed, 0))
    _release(ed, settings, _at(ed, 0))
    _press(screen, ed, settings, _at(ed, 0))
    assert ed.lane_src is None


def test_a_duplicate_lane_is_refused(scene):
    screen, ed, settings = _lanes(scene)
    before = list(ed.recipe.lanes)
    _press(screen, ed, settings, _at(ed, 0))
    _release(ed, settings, _at(ed, 0))
    _press(screen, ed, settings, _at(ed, 1))          # (0, 1) already exists
    assert ed.recipe.lanes == before and not ed.status_ok


def test_a_lane_that_would_run_under_a_system_is_refused(scene):
    screen, ed, settings = _lanes(scene)
    ed.recipe = CustomMap(
        nodes=[MapNode(100, 500, 3, 5, 1), MapNode(500, 500, 3, 5, 0), MapNode(900, 500, 3, 5, 2)],
        lanes=[(0, 1), (1, 2)],
    ).normalised()
    _press(screen, ed, settings, _at(ed, 0))
    _release(ed, settings, _at(ed, 0))
    _press(screen, ed, settings, _at(ed, 2))          # 0-2 would pass through 1
    assert ed.recipe.lanes == [(0, 1), (1, 2)] and not ed.status_ok


def _crossing_setup(ed):
    """A square with both sides drawn; the two diagonals would cross."""
    ed.recipe = CustomMap(
        nodes=[MapNode(200, 200, 3, 5, 1), MapNode(800, 200, 3, 5, 0),
               MapNode(800, 800, 3, 5, 2), MapNode(200, 800, 3, 5, 0)],
        lanes=[(0, 1), (1, 2), (2, 3), (0, 3), (0, 2)],
    ).normalised()


def test_planar_on_refuses_a_crossing_lane(scene):
    screen, ed, settings = _lanes(scene)
    _crossing_setup(ed)
    assert ed.planar
    _press(screen, ed, settings, _at(ed, 1))
    _release(ed, settings, _at(ed, 1))
    _press(screen, ed, settings, _at(ed, 3))          # 1-3 crosses 0-2
    assert (1, 3) not in ed.recipe.lanes and not ed.status_ok


def test_planar_off_allows_it_and_the_map_still_plays(scene):
    """Which is the whole point of the toggle: a crossing is a warning, not a
    blocker, so turning it off must leave a map you can actually play."""
    screen, ed, settings = _lanes(scene)
    _crossing_setup(ed)
    _click_key(screen, ed, settings, "planar")
    assert not ed.planar

    _press(screen, ed, settings, _at(ed, 1))
    _release(ed, settings, _at(ed, 1))
    _press(screen, ed, settings, _at(ed, 3))
    assert (1, 3) in ed.recipe.lanes
    assert ed.can_play()
    assert any(p.code == "crossing" and not p.blocks for p in ed.problems())


def test_a_lane_can_be_picked_and_deleted(scene):
    screen, ed, settings = _lanes(scene)
    a, b = _at(ed, 0), _at(ed, 1)
    midpoint = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
    _press(screen, ed, settings, midpoint)
    assert ed.recipe.lanes[ed.sel_lane] == (0, 1)

    _click_key(screen, ed, settings, "delete_lane")
    assert (0, 1) not in ed.recipe.lanes and ed.sel_lane is None


def test_a_repeat_press_cycles_between_overlapping_lanes(scene):
    """Two lanes can sit on top of each other, so the nearest one must not be the
    only one you can ever reach — the shape `input._pick_lane` uses."""
    screen, ed, settings = _lanes(scene)
    _crossing_setup(ed)
    ed.planar = False
    ed.recipe.lanes = sorted(ed.recipe.lanes + [(1, 3)])
    centre = ed.view.to_screen((500.0, 500.0))        # where the diagonals cross

    picks = set()
    for _ in range(4):
        _press(screen, ed, settings, centre)
        picks.add(ed.sel_lane)
    assert len(picks) == 2, picks                     # both diagonals reachable


def test_a_system_is_tested_before_a_lane(scene):
    """A lane's endpoint sits inside its system's tap reach, and "start a lane
    here" has to win there."""
    screen, ed, settings = _lanes(scene)
    _press(screen, ed, settings, _at(ed, 0))
    assert ed.lane_src == 0 and ed.sel_lane is None


def test_clear_lanes_asks_first_and_keeps_the_systems(scene):
    screen, ed, settings = _lanes(scene)
    _click_key(screen, ed, settings, "clear_lanes")
    assert ed.confirm == "clear_lanes" and ed.recipe.lanes
    mapmaker.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_y, mod=0), ed, settings)
    assert ed.recipe.lanes == [] and len(ed.recipe.nodes) == 4
    mapmaker._undo(ed)
    assert len(ed.recipe.lanes) == 4


def test_left_drag_pans_in_the_lanes_tool(scene):
    """Unlike the Systems tool, where a press on empty space always means
    "place", so there is no free left gesture."""
    screen, ed, settings = _lanes(scene)
    ed.view.zoom_at(_empty_spot(ed), 2.0)
    off = ed.view.off_x
    pos = _empty_spot(ed)
    _press(screen, ed, settings, pos)
    mapmaker.handle_event(
        pygame.event.Event(pygame.MOUSEMOTION, pos=(pos[0] - 40, pos[1]),
                           buttons=(1, 0, 0), rel=(0, 0)), ed, settings)
    assert ed.view.off_x != off


def test_the_lane_sidebar_quotes_the_travel_time_the_board_gives(scene):
    screen, ed, settings = _lanes(scene)
    ed.sel_lane = ed.recipe.lanes.index((0, 1))
    mapmaker.draw(screen, ed, settings)
    a, b = ed.recipe.nodes[0], ed.recipe.nodes[1]
    state = mapgen.generate_custom(1, ed.recipe.normalised())
    assert mapmaker._lane_turns(a, b) == state.travel_turns(0, 1)


def test_a_lane_slider_writes_settings(scene):
    screen, ed, settings = _lanes(scene)
    mapmaker.draw(screen, ed, settings)
    rect = ed.rects["adv_extra_edges"]
    mapmaker.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(rect.x, rect.centery), button=1),
        ed, settings)
    assert settings.extra_edge_fraction == 0.0        # the spec's bottom end


def test_switching_tools_never_changes_the_map(scene):
    screen, ed, settings = scene
    before = ed.recipe.to_dict()
    for _ in range(5):
        for tool in (mapmaker.SYSTEMS, mapmaker.LANES):
            ed.tool = tool
            mapmaker.draw(screen, ed, settings)
    assert ed.recipe.to_dict() == before


# --------------------------------------------------------------------------- #
# The Owners tool
# --------------------------------------------------------------------------- #
def _owners(scene):
    screen, ed, settings = scene
    ed.tool = mapmaker.OWNERS
    return screen, ed, settings


def test_tapping_a_system_paints_the_picked_seat(scene):
    screen, ed, settings = _owners(scene)
    ed.owner_pick = 2
    _press(screen, ed, settings, _at(ed, 1))
    assert ed.recipe.nodes[1].owner == 2


def test_tapping_a_system_that_already_holds_the_pick_clears_it(scene):
    """One meaning per tap: never "select here, paint there"."""
    screen, ed, settings = _owners(scene)
    ed.owner_pick = 1
    _press(screen, ed, settings, _at(ed, 0))      # node 0 is already seat 1
    assert ed.recipe.nodes[0].owner == 0


def test_the_seat_palette_offers_one_more_seat_than_is_held(scene):
    """Which is what makes the common path gap-free by construction: the next
    seat is always reachable and the one past it never is."""
    screen, ed, settings = _owners(scene)
    mapmaker.draw(screen, ed, settings)
    seats = {int(k[5:]) for k in ed.rects if k.startswith("seat_")}
    assert seats == {0, 1, 2, 3}                  # neutral, the two held, one more


def test_the_seat_palette_always_offers_at_least_two_seats(scene):
    screen, ed, settings = _owners(scene)
    for node in ed.recipe.nodes:
        node.owner = 0
    mapmaker.draw(screen, ed, settings)
    seats = {int(k[5:]) for k in ed.rects if k.startswith("seat_")}
    assert seats == {0, 1, config.MIN_PLAYERS}


def test_the_seat_palette_stops_at_max_players(scene):
    screen, ed, settings = _owners(scene)
    ed.recipe = CustomMap(
        nodes=[MapNode(100 + i * 120, 500, 3, 5, i + 1) for i in range(config.MAX_PLAYERS)],
        lanes=[(i, i + 1) for i in range(config.MAX_PLAYERS - 1)],
    ).normalised()
    mapmaker.draw(screen, ed, settings)
    seats = {int(k[5:]) for k in ed.rects if k.startswith("seat_")}
    assert max(seats) == config.MAX_PLAYERS


def test_a_palette_swatch_picks_that_seat(scene):
    screen, ed, settings = _owners(scene)
    _click_key(screen, ed, settings, "seat_2")
    assert ed.owner_pick == 2
    _click_key(screen, ed, settings, "seat_0")
    assert ed.owner_pick == 0


def test_a_drag_box_paints_every_system_inside_it(scene):
    screen, ed, settings = _owners(scene)
    ed.owner_pick = 2
    lo = ed.view.to_screen((150.0, 150.0))
    hi = ed.view.to_screen((850.0, 850.0))
    _press(screen, ed, settings, lo)
    for pos in (lo, hi):
        mapmaker.handle_event(
            pygame.event.Event(pygame.MOUSEMOTION, pos=pos, buttons=(1, 0, 0), rel=(0, 0)),
            ed, settings)
    assert ed.box_active
    _release(ed, settings, hi)
    assert [n.owner for n in ed.recipe.nodes] == [2, 2, 2, 2]


def test_a_tap_on_empty_space_paints_nothing(scene):
    """route mode's two-flag arming: `box_press` on the press, `box_active` only
    past the threshold."""
    screen, ed, settings = _owners(scene)
    before = [n.owner for n in ed.recipe.nodes]
    pos = _empty_spot(ed)
    _press(screen, ed, settings, pos)
    assert ed.box_press and not ed.box_active
    _release(ed, settings, pos)
    assert [n.owner for n in ed.recipe.nodes] == before


def test_a_box_is_clipped_to_the_viewport_before_it_picks_anything(scene):
    """`to_screen` projects every system, including ones panned out under the
    sidebar — only the *drawing* is clipped."""
    screen, ed, settings = _owners(scene)
    side = mapmaker._side_rect()
    box = (side.x + 10, side.y + 10, side.w - 20, side.h - 20)
    assert mapmaker._nodes_in_box(ed, box) == []


def test_a_box_paints_as_one_group_rather_than_half_toggling(scene):
    screen, ed, settings = _owners(scene)
    ed.owner_pick = 1                             # node 0 already holds seat 1
    mapmaker._paint(ed, [0, 1, 2, 3])
    assert [n.owner for n in ed.recipe.nodes] == [1, 1, 1, 1]
    mapmaker._paint(ed, [0, 1, 2, 3])             # now all of them hold it: clear
    assert [n.owner for n in ed.recipe.nodes] == [0, 0, 0, 0]


def test_make_homeworld_stamps_both_numbers_in_one_press(scene):
    screen, ed, settings = _owners(scene)
    ed.sel_node = 1
    _click_key(screen, ed, settings, "make_home")
    assert ed.recipe.nodes[1].production == config.HOME_PRODUCTION
    assert ed.recipe.nodes[1].ships == config.HOME_START_SHIPS


def test_painting_a_seat_never_rewrites_the_numbers(scene):
    """Explicit, never implicit — Make homeworld is the deliberate version."""
    screen, ed, settings = _owners(scene)
    ed.owner_pick = 2
    before = (ed.recipe.nodes[1].production, ed.recipe.nodes[1].ships)
    _press(screen, ed, settings, _at(ed, 1))
    assert (ed.recipe.nodes[1].production, ed.recipe.nodes[1].ships) == before


def test_a_seat_gap_blocks_and_offers_a_one_press_fix(scene):
    screen, ed, settings = _owners(scene)
    ed.recipe.nodes[2].owner = 3                  # seats 1 and 3, no 2
    assert any(p.code == "seat_gap" and p.blocks for p in ed.problems())
    assert not ed.can_play()

    _click_key(screen, ed, settings, "renumber")
    assert [n.owner for n in ed.recipe.nodes] == [1, 0, 2, 0]
    assert ed.can_play()


def test_renumbering_is_undoable(scene):
    screen, ed, settings = _owners(scene)
    ed.recipe.nodes[2].owner = 5
    mapmaker._renumber_seats(ed)
    assert ed.recipe.nodes[2].owner == 2
    mapmaker._undo(ed)
    assert ed.recipe.nodes[2].owner == 5


def test_the_renumber_button_only_appears_when_there_is_a_gap(scene):
    screen, ed, settings = _owners(scene)
    mapmaker.draw(screen, ed, settings)
    assert "renumber" not in ed.rects
    ed.recipe.nodes[2].owner = 4
    mapmaker.draw(screen, ed, settings)
    assert "renumber" in ed.rects


def test_switching_between_all_three_tools_never_changes_the_map(scene):
    screen, ed, settings = scene
    before = ed.recipe.to_dict()
    for _ in range(4):
        for tool in (mapmaker.SYSTEMS, mapmaker.LANES, mapmaker.OWNERS):
            ed.tool = tool
            mapmaker.draw(screen, ed, settings)
    assert ed.recipe.to_dict() == before


# --------------------------------------------------------------------------- #
# Auto homeworlds
# --------------------------------------------------------------------------- #
def _confirm(ed, settings, yes=True):
    key = pygame.K_y if yes else pygame.K_ESCAPE
    mapmaker.handle_event(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0), ed, settings)


def test_auto_homeworlds_gives_every_seat_exactly_one_start(scene):
    screen, ed, settings = _owners(scene)
    ed.auto_seats = 3
    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    owners = [n.owner for n in ed.recipe.nodes]
    assert sorted(o for o in owners if o) == [1, 2, 3]
    assert ed.recipe.seats() == 3


def test_auto_homeworlds_is_mapgens_own_placement(scene):
    """The same function the generator uses, not a second copy of the maths — so a
    hand-drawn board is started from no differently than one rolled from a seed."""
    screen, ed, settings = _owners(scene)
    ed.rng = random.Random(7)
    want = ed.seat_target()
    expected = mapgen.peripheral_starts(
        {i: n.pos for i, n in enumerate(ed.recipe.nodes)}, want, random.Random(7))

    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    placed = [i for i, n in enumerate(ed.recipe.nodes) if n.owner > 0]
    assert sorted(placed) == sorted(expected)
    # ...and seat order follows the sweep, not the node order.
    assert [ed.recipe.nodes[i].owner for i in expected] == list(range(1, want + 1))


def test_auto_homeworlds_stamps_the_homeworld_numbers(scene):
    screen, ed, settings = _owners(scene)
    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    for node in ed.recipe.nodes:
        if node.owner > 0:
            assert (node.production, node.ships) == (config.HOME_PRODUCTION,
                                                     config.HOME_START_SHIPS)


def test_auto_homeworlds_demotes_the_starts_it_replaces(scene):
    """A start that loses its seat is rolled back down to an ordinary system —
    otherwise the map's largest prize is a leftover, sitting where a homeworld
    used to be."""
    screen, ed, settings = _owners(scene)
    for node in ed.recipe.nodes:           # every system a homeworld to begin with
        node.owner = 0
        node.production, node.ships = config.HOME_PRODUCTION, config.HOME_START_SHIPS
    ed.auto_seats = 2
    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    for node in ed.recipe.nodes:
        if node.owner == 0:
            assert node.ships <= config.garrison_for(node.production, config.GARRISON_JITTER)


def test_auto_homeworlds_leaves_hand_set_numbers_alone(scene):
    """Only an exact homeworld stamp is demoted — the line `_make_homeworld` draws
    between what an author chose and what the tool put there."""
    screen, ed, settings = _owners(scene)
    ed.recipe.nodes[1].production, ed.recipe.nodes[1].ships = 4, 31
    ed.auto_seats = 2
    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    if ed.recipe.nodes[1].owner == 0:
        assert (ed.recipe.nodes[1].production, ed.recipe.nodes[1].ships) == (4, 31)


def test_auto_homeworlds_asks_first(scene):
    screen, ed, settings = _owners(scene)
    before = ed.recipe.to_dict()
    _click_key(screen, ed, settings, "auto_owners")
    assert ed.confirm == "auto_owners"
    _confirm(ed, settings, yes=False)
    assert ed.recipe.to_dict() == before and ed.confirm is None


def test_auto_homeworlds_is_one_undo(scene):
    screen, ed, settings = _owners(scene)
    before = ed.recipe.to_dict()
    ed.auto_seats = 3
    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    assert ed.recipe.to_dict() != before
    mapmaker._undo(ed)
    assert ed.recipe.to_dict() == before


def test_auto_homeworlds_refuses_more_seats_than_systems(scene):
    screen, ed, settings = _owners(scene)
    ed.recipe = CustomMap(
        nodes=[MapNode(200, 200, 3, 5, 0), MapNode(800, 800, 3, 5, 0)],
        lanes=[(0, 1)],
    ).normalised()
    ed.auto_seats = 4
    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    assert [n.owner for n in ed.recipe.nodes] == [0, 0]
    assert not ed.status_ok


def test_the_seat_count_follows_the_map_until_it_is_set(scene):
    """`auto_seats` is None until the stepper is touched, so the readout is the
    truth about the map rather than a number that can go stale behind it."""
    screen, ed, settings = _owners(scene)
    assert ed.seat_target() == ed.recipe.seats() == 2
    ed.recipe.nodes[1].owner = 3
    assert ed.seat_target() == 3
    _click_key(screen, ed, settings, "seats_plus")
    assert (ed.auto_seats, ed.seat_target()) == (4, 4)


def test_the_seats_stepper_stops_at_the_player_limits(scene):
    screen, ed, settings = _owners(scene)
    for _ in range(config.MAX_PLAYERS + 3):
        _click_key(screen, ed, settings, "seats_plus")
    assert ed.seat_target() == config.MAX_PLAYERS
    for _ in range(config.MAX_PLAYERS + 3):
        _click_key(screen, ed, settings, "seats_minus")
    assert ed.seat_target() == config.MIN_PLAYERS


def test_a_whole_recipe_swap_hands_the_seat_count_back_to_the_map(scene):
    """A count belongs to the map it was chosen for — the same argument `_adopt`
    already makes about a selection index."""
    screen, ed, settings = _owners(scene)
    ed.auto_seats = 6
    mapmaker._adopt(ed, _square())
    assert ed.auto_seats is None and ed.seat_target() == 2


def test_the_map_the_auto_button_draws_is_playable(scene):
    screen, ed, settings = _owners(scene)
    for node in ed.recipe.nodes:
        node.owner = 0
    assert not ed.can_play()               # too few seats
    _click_key(screen, ed, settings, "auto_owners")
    _confirm(ed, settings)
    assert ed.can_play()


# --------------------------------------------------------------------------- #
# The footer's default action
# --------------------------------------------------------------------------- #
def test_the_menu_button_sits_in_the_tool_strip(scene):
    """Going back to the seats, strategies, fog and seed is the stage after the
    three tools, not a sibling of Undo — so it rides in the strip, not the
    footer."""
    screen, ed, settings = scene
    mapmaker.draw(screen, ed, settings)
    assert ed.rects["back"].bottom <= config.EDIT_TOP_H
    assert "back" not in {spec[0] for spec in mapmaker._footer_specs(ed)}


def test_the_footer_reads_left_to_right_in_the_order_it_is_specified(scene):
    screen, ed, settings = scene
    mapmaker.draw(screen, ed, settings)
    order = ["generate", "new", "undo", "redo", "filename", "open", "save", "play"]
    lefts = [ed.rects[key].x for key in order]
    assert lefts == sorted(lefts)


def test_an_unplayable_map_still_greys_the_play_button(scene):
    screen, ed, settings = scene
    ed.recipe.lanes = []                   # disconnected: a blocker
    palettes = {spec[0]: (spec[2], spec[3]) for spec in mapmaker._footer_specs(ed)}
    assert palettes["play"] == mapmaker._DEAD_PALETTE

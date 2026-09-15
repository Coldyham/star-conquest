"""Drive the map creator headlessly with synthetic pygame events.

Mirrors ``test_menu``'s harness: SDL dummy drivers, no real window, draw first so
the widgets record their rects, then click a rect's centre.

The editor is the one scene laid out on the *real* surface at ``config.ui_scale``
rather than on a fixed canvas, so it can be checked for layout containment at more
than one scale — a check the menu structurally cannot make.
"""

from __future__ import annotations

import os

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
    screen, ed, settings = scene
    _drag(screen, ed, settings, 1, (700.0, 350.0))
    assert (ed.recipe.nodes[1].x, ed.recipe.nodes[1].y) == (700, 350)


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
    _click_key(screen, ed, settings, "owner_plus")
    assert ed.recipe.nodes[1].production == 5
    assert ed.recipe.nodes[1].ships == 5
    assert ed.recipe.nodes[1].owner == 1


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
def test_every_control_stays_on_screen_at_any_ui_scale(scene, scale):
    """The editor's analogue of `test_tab_content_stays_inside_the_panel`, and a
    check the fixed-canvas menu structurally cannot make."""
    screen, ed, settings = scene
    config.apply_ui_scale(scale)
    widgets._FONTS.clear()
    ed.view = mapmaker._build_view()
    ed.sel_node = 0
    mapmaker.draw(screen, ed, settings)

    window = pygame.Rect(0, 0, config.SCREEN_W, config.SCREEN_H)
    for key, rect in ed.rects.items():
        assert window.contains(rect), f"scale {scale}: {key} {tuple(rect)} is off screen"


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


def test_an_unfinished_tool_records_no_rect(scene):
    """Inert by construction rather than by a disabled flag some later branch
    forgets to check."""
    screen, ed, settings = scene
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
def test_opening_without_a_map_starts_from_a_generated_one(scene):
    screen, ed, settings = scene
    fresh = mapmaker.open_editor(settings, seed=11)
    assert fresh.recipe == custommap.from_state(
        mapgen.generate(11, settings.mode, settings.nodes, settings.players))
    assert fresh.can_play()          # never a blank canvas you cannot play


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

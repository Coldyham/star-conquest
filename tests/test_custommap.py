"""The hand-authored map recipe: its tolerant parser, its validator, and the
canonical form the setup digest rests on.

Two properties carry most of the weight here. ``from_dict`` must be *total* —
never raising, whatever it is handed — because it is the one point where an
untrusted share link becomes a map. And ``normalised`` must be idempotent, or a
sender's own link reads as edited the moment the recipient opens it.
"""

from __future__ import annotations

import base64
import json
import zlib

import pytest

from starconquest import config, custommap, mapgen
from starconquest.custommap import CustomMap, MapNode


def _playable() -> CustomMap:
    """Four systems in a square, two seats, a connected ring of lanes."""
    return CustomMap(
        nodes=[
            MapNode(200, 200, 3, 12, 1),
            MapNode(800, 200, 4, 6, 0),
            MapNode(800, 800, 3, 12, 2),
            MapNode(200, 800, 5, 4, 0),
        ],
        lanes=[(0, 1), (1, 2), (2, 3), (0, 3)],
    ).normalised()


# --------------------------------------------------------------------------- #
# The canonical form
# --------------------------------------------------------------------------- #
def test_normalised_is_idempotent():
    """What `challenge_key` rests on: the editor normalises on commit and
    `from_dict` normalises again on the way in, and the two must agree or a
    sender's own link reads as edited the moment it is opened."""
    messy = CustomMap(
        nodes=[MapNode(-50, 2000, 99, 500, 9), MapNode(400, 400, 2, 5, 1)],
        lanes=[(1, 0), (0, 1), (0, 0), (0, 7)],
    )
    once = messy.normalised()
    assert once.normalised() == once


def test_normalised_canonicalises_lanes():
    m = CustomMap(nodes=[MapNode(), MapNode(), MapNode()],
                  lanes=[(2, 1), (1, 2), (0, 2), (1, 1), (0, 9)]).normalised()
    assert m.lanes == [(0, 2), (1, 2)]   # ordered, deduped, self- and junk-lanes gone


def test_clamping_keeps_every_value_in_range():
    node = MapNode(-999, 99999, 400, 9999, 42).clamped()
    assert (node.x, node.y) == (0, int(config.WORLD_SIZE))
    assert node.production == config.CUSTOM_MAX_PRODUCTION
    assert node.ships == config.CUSTOM_MAX_SHIPS
    assert node.owner == 0   # an out-of-range seat goes neutral, never clamped *up*


def test_a_hand_map_is_wider_than_it_is_tall():
    """The creator's box is not the square `mapgen` rolls into: a recipe stores
    concrete coordinates and never goes through `_play_bounds`, so it can be drawn
    to the shape a window actually is without re-rolling a single seed."""
    assert config.CUSTOM_WORLD_W > config.WORLD_SIZE
    far = MapNode(99999, 99999).clamped()
    assert (far.x, far.y) == (int(config.CUSTOM_WORLD_W), int(config.WORLD_SIZE))
    # ...and a point the square box would have rejected survives untouched.
    wide = MapNode(int(config.WORLD_SIZE) + 200, 500).clamped()
    assert wide.x == int(config.WORLD_SIZE) + 200


def test_an_out_of_range_owner_never_invents_a_seat():
    """Clamping a typo'd owner up to MAX_PLAYERS would add a player to the game
    nobody asked for; neutral is the only safe reading."""
    assert MapNode(0, 0, 3, 1, config.MAX_PLAYERS + 5).clamped().owner == 0
    assert MapNode(0, 0, 3, 1, -3).clamped().owner == 0


# --------------------------------------------------------------------------- #
# The tolerance table, one assertion per row — and nothing ever raises
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("junk", [
    None, 42, "nope", [], {}, {"n": None}, {"n": []}, {"n": "abc"},
    {"n": [[0, 0]]},                                   # one node: too few seats
    {"n": [["x", "y"]]},                               # non-numeric coords
    {"n": [[1]]},                                      # a row with no y
    {"n": [[True, False]]},                            # booleans are not coordinates
    {"n": [[0, 0, 1, 1, 1]] * (config.CUSTOM_MAX_NODES + 1)},  # over the node cap
])
def test_junk_never_raises_and_lands_at_none(junk):
    assert CustomMap.from_dict(junk) is None


def test_a_short_row_is_a_complete_node():
    """The payoff of positional rows over keyed objects: a truncated row is a free
    tolerance rather than a parse error."""
    data = {"n": [[200, 200, 3, 9, 1], [800, 800], [500, 100, 3, 5, 2]],
            "l": [[0, 1], [1, 2]]}
    m = CustomMap.from_dict(data)
    assert m is not None
    assert (m.nodes[1].x, m.nodes[1].y) == (800, 800)
    assert m.nodes[1].production == config.HOME_PRODUCTION
    assert m.nodes[1].owner == 0


def test_a_junk_lane_drops_and_the_map_survives():
    """Dropping one bad index is bounded; it is also what keeps
    `rebuild_topology` from KeyError-ing on a system that doesn't exist."""
    good = _playable().to_dict()
    good["l"] = good["l"] + [[0, 99], ["a", "b"], [2, 2], [1], "junk"]
    m = CustomMap.from_dict(good)
    assert m is not None and m.lanes == _playable().lanes


def test_a_disconnected_map_is_rejected_not_repaired():
    """Auto-linking is cheap and deterministic, and that is the problem: it would
    present structure the author never drew as theirs."""
    m = _playable()
    data = m.to_dict()
    data["l"] = [[0, 1]]        # nodes 2 and 3 stranded
    assert CustomMap.from_dict(data) is None


def test_a_seat_gap_is_rejected():
    m = _playable()
    m.nodes[2].owner = 3        # seats 1 and 3, no 2
    assert CustomMap.from_dict(m.to_dict()) is None


def test_too_few_seats_is_rejected():
    m = _playable()
    m.nodes[2].owner = 1        # only seat 1 holds anything
    assert CustomMap.from_dict(m.to_dict()) is None


# --------------------------------------------------------------------------- #
# The validator
# --------------------------------------------------------------------------- #
def test_a_playable_map_reports_nothing():
    assert _playable().problems() == []
    assert _playable().is_playable()


def test_systems_too_close_block_and_name_both():
    m = _playable()
    m.nodes[1].x, m.nodes[1].y = 210, 205      # right on top of node 0
    codes = {p.code for p in m.problems()}
    assert "too_close" in codes
    offender = next(p for p in m.problems() if p.code == "too_close")
    assert set(offender.nodes) == {0, 1} and offender.blocks


def test_a_lane_running_under_a_third_system_blocks():
    """mapgen's own `_grazes_other_node` rule, applied to a hand-drawn graph: a
    near-collinear third system would render as if the lane ran beneath it."""
    m = CustomMap(
        nodes=[MapNode(100, 500, 3, 5, 1), MapNode(500, 500, 3, 5, 0),
               MapNode(900, 500, 3, 5, 2)],
        lanes=[(0, 1), (1, 2), (0, 2)],        # the direct 0-2 lane passes through 1
    ).normalised()
    assert any(p.code == "graze" for p in m.problems())


def _crossed() -> CustomMap:
    """Two lanes crossing in open space: the diagonals of a square."""
    return CustomMap(
        nodes=[MapNode(100, 100, 3, 5, 1), MapNode(900, 900, 3, 5, 2),
               MapNode(900, 100, 3, 5, 0), MapNode(100, 900, 3, 5, 0)],
        lanes=[(0, 1), (2, 3), (0, 2), (1, 3)],
    ).normalised()


def test_crossing_lanes_warn_rather_than_block():
    """Two lanes crossing in open space only looks busier — the engine, the AI
    and every bot are indifferent to planarity. A lane hidden *under* a system is
    the one that misrepresents the graph, and that still blocks."""
    crossing = next(p for p in _crossed().problems() if p.code == "crossing")
    assert not crossing.blocks
    assert set(crossing.nodes) == {0, 1, 2, 3}


def test_a_crossing_map_is_still_playable_and_still_parses():
    """Which is what makes the creator's Planar toggle coherent: turning it off
    has to leave you with a map you can actually play and share."""
    m = _crossed()
    assert m.is_playable()
    assert CustomMap.from_dict(m.to_dict()) == m


def test_blockers_are_listed_before_warnings():
    """The Play gate quotes `blockers()[0]`, so a warning at the top of the list
    would bury the thing actually stopping the game."""
    m = _crossed()
    m.lanes = [(0, 1), (2, 3)]                  # ...and now nothing is connected
    severities = [p.blocks for p in m.problems()]
    assert severities == sorted(severities, reverse=True)
    assert m.problems()[0].blocks


def test_lanes_sharing_a_system_are_incident_not_crossing():
    assert not any(p.code == "crossing" for p in _playable().problems())


def test_a_problem_carries_the_offenders_so_it_can_be_found():
    m = _playable()
    stranded = CustomMap(nodes=m.nodes, lanes=[(0, 1), (1, 2)])   # node 3 stranded
    problem = next(p for p in stranded.problems() if p.code == "disconnected")
    assert 3 in problem.nodes


def test_seats_is_the_highest_seat_not_the_count():
    """A gap is a blocker, and reporting 2 for seats {1, 3} would quietly describe
    a different game than the one drawn."""
    m = _playable()
    m.nodes[2].owner = 3
    assert m.seats() == 3


# --------------------------------------------------------------------------- #
# Deletion — the one consequence of positional node identity
# --------------------------------------------------------------------------- #
def test_deleting_a_node_remaps_every_later_lane():
    m = CustomMap(nodes=[MapNode(i * 100, 100) for i in range(5)],
                  lanes=[(0, 1), (1, 2), (2, 3), (3, 4)])
    out = m.without_node(1)
    assert len(out.nodes) == 4
    # (0,1) and (1,2) both touched node 1 and go; (2,3) and (3,4) shift down one.
    assert out.lanes == [(1, 2), (2, 3)]


def test_deleting_leaves_the_original_untouched():
    m = _playable()
    before = m.to_dict()
    m.without_node(0)
    assert m.to_dict() == before


def test_deleting_an_index_that_does_not_exist_is_a_copy():
    m = _playable()
    assert m.without_node(99).to_dict() == m.to_dict()
    assert m.without_node(-1).to_dict() == m.to_dict()


# --------------------------------------------------------------------------- #
# Round trips, and the wire form's size
# --------------------------------------------------------------------------- #
def test_to_dict_from_dict_round_trip():
    m = _playable()
    assert CustomMap.from_dict(m.to_dict()) == m


def test_from_state_inverts_generate_custom():
    m = custommap.from_state(mapgen.generate(19, "random", 20, 3))
    assert custommap.from_state(mapgen.generate_custom(7, m)) == m


def test_a_generated_map_round_trips_through_the_recipe():
    """Every generated map must be describable as a recipe — it is how "start from
    a generated map" works, and it is what the separation rule is measured
    against."""
    for mode in ("random", "symmetric"):
        for nodes in (12, 24):
            state = mapgen.generate(5, mode, nodes, 3)
            recipe = custommap.from_state(state)
            assert recipe.problems() == [], f"{mode}/{nodes}: {recipe.problems()}"


def test_a_symmetric_map_bigger_than_max_nodes_still_imports():
    """`generate_symmetric` rounds the node count up per sector and adds the
    shared centre, so it returns 41 systems at 40 nodes — a board the game
    generates and plays today. The parser's ceiling has to clear that, or "start
    from a generated map" would fail on a setup anyone can pick."""
    state = mapgen.generate_symmetric(0, 40, 2)
    assert len(state.systems) > config.MAX_NODES
    recipe = custommap.from_state(state)
    assert recipe.problems() == []
    assert CustomMap.from_dict(recipe.to_dict()) == recipe


def test_the_wire_form_stays_short():
    """A guard against someone reintroducing a verbose encoding: this rides in
    every shared URL, so a keyed-object-per-node form would roughly treble it."""
    state = mapgen.generate(3, "random", config.MAX_NODES, 4)
    raw = json.dumps(custommap.from_state(state).to_dict(), separators=(",", ":"))
    token = base64.urlsafe_b64encode(zlib.compress(raw.encode(), 9)).rstrip(b"=")
    assert len(token) < 1200, len(token)


def test_copy_is_deep():
    m = _playable()
    other = m.copy()
    other.nodes[0].x = 999
    other.lanes.append((0, 2))
    assert m.nodes[0].x == 200 and len(m.lanes) == 4

"""Forwarding rules: a system's hold, and a share of what is left per lane.

The share arithmetic is pure (`model`); the editing flow is `Ui` state, also
pygame-free; `main.auto_forward_orders` is where the two become this turn's orders.
"""

from __future__ import annotations

from starconquest import config, main, model
from starconquest.geometry import WorldView
from starconquest.model import ForwardRule, GameState, Player, System
from starconquest.viewstate import CHOOSING, Ui


# --------------------------------------------------------------------------- #
# The share arithmetic
# --------------------------------------------------------------------------- #
def test_adding_lanes_splits_evenly():
    shares: dict[int, int] = {}
    for dest, want in ((7, {7: 100}), (8, {7: 50, 8: 50}), (9, {7: 34, 8: 33, 9: 33})):
        shares = model.shares_with(shares, dest)
        assert shares == want


def test_adding_a_lane_keeps_what_stays_home_and_the_others_proportions():
    assert model.shares_with({7: 50}, 8) == {7: 25, 8: 25}
    assert model.shares_with({7: 70, 8: 30}, 9) == {7: 47, 8: 20, 9: 33}


def test_a_rule_that_sends_nothing_splits_everything_when_a_lane_joins():
    assert model.shares_with({7: 0}, 8) == {7: 50, 8: 50}


def test_removing_a_lane_undoes_adding_it():
    assert model.shares_without({7: 50, 8: 50}, 8) == {7: 100}
    assert model.shares_without({7: 34, 8: 33, 9: 33}, 7) == {8: 50, 9: 50}
    assert model.shares_without({7: 47, 8: 20, 9: 33}, 9) == {7: 70, 8: 30}
    assert model.shares_without({7: 100}, 7) == {}


def test_raising_a_share_takes_from_home_first_then_the_other_lanes():
    assert model.shares_set({7: 50}, 7, 80) == {7: 80}
    assert model.shares_set({7: 50, 8: 50}, 7, 60) == {7: 60, 8: 40}
    assert model.shares_set({7: 50, 8: 25}, 7, 80) == {7: 80, 8: 20}
    assert model.shares_set({7: 40, 8: 30, 9: 30}, 7, 100) == {7: 100, 8: 0, 9: 0}


def test_lowering_a_share_gives_it_back_to_home():
    assert model.shares_set({7: 50, 8: 50}, 7, 40) == {7: 40, 8: 50}
    assert model.shares_set({7: 10}, 7, -30) == {7: 0}


def test_even_split_keeps_the_total():
    assert model.shares_even({7: 60, 8: 40}) == {7: 50, 8: 50}
    assert model.shares_even({7: 60, 8: 15}) == {7: 38, 8: 37}


def test_split_sends_shares_of_the_surplus_and_rounds_to_the_total():
    assert model.forward_split(9, {7: 50, 8: 50}, 0) == {7: 5, 8: 4}
    assert model.forward_split(12, {7: 34, 8: 33, 9: 33}, 0) == {7: 4, 8: 4, 9: 4}
    assert model.forward_split(12, {7: 50}, 0) == {7: 6}
    assert model.forward_split(-3, {7: 100}, 0) == {7: 0}


def test_an_odd_ship_alternates_rather_than_always_going_first():
    """"Keep half" now means half, every turn: a 50/50 tie goes round with the turn."""
    firsts = [model.forward_split(5, {7: 50, 8: 50}, t)[7] for t in range(4)]
    assert firsts == [3, 2, 3, 2]
    lone = [model.forward_split(1, {7: 50}, t)[7] for t in range(4)]
    assert lone == [1, 0, 1, 0]


def test_cycle_nodes_finds_loops_reachable_from_the_starts():
    assert model.cycle_nodes({1: [2], 2: [3], 3: [2]}, {1}) == {2, 3}
    assert model.cycle_nodes({1: [2, 3], 2: [], 3: [1]}, {1}) == {1, 3}
    assert model.cycle_nodes({1: [2], 2: [1]}, {3}) == set()
    assert model.cycle_nodes({1: [1]}, {1}) == {1}


# --------------------------------------------------------------------------- #
# Editing, through Ui
# --------------------------------------------------------------------------- #
def _hub() -> GameState:
    """System 0 (12 ships) with owned neighbours 1-3 and an enemy neighbour 4."""
    s = GameState.new(0)
    s.players[0] = Player(0, "Neutral", (0, 0, 0), is_neutral=True)
    s.players[1] = Player(1, "P", (0, 0, 0), is_human=True)
    s.players[2] = Player(2, "E", (0, 0, 0))
    owners = {0: 1, 1: 1, 2: 1, 3: 1, 4: 2}
    for sid, owner in owners.items():
        s.systems[sid] = System(id=sid, pos=(sid * 10.0, 0.0), owner_id=owner, ships=12 if sid == 0 else 5)
    for nbr in (1, 2, 3, 4):
        s.add_lane(0, nbr, 1.0, 1)
    s.rebuild_topology()
    return s


def _ui() -> Ui:
    return Ui(view=WorldView((0.0, 0.0, 100.0, 100.0), (0.0, 0.0, 800.0, 600.0)), human_id=1)


def _forward(state, ui, src, dest) -> None:
    """Pick ``src``, aim at ``dest`` and switch the popup to the Forward tab."""
    ui.reset_selection()
    ui.selected = src
    ui.begin_send(state, dest)
    ui.set_forward_mode(state, True)


def test_a_second_lane_makes_the_rule_a_split_and_cancel_undoes_it():
    state, ui = _hub(), _ui()
    _forward(state, ui, 0, 1)
    assert ui.auto_forward[0].shares == {1: 100} and ui.pending == []
    ui.close_send()
    _forward(state, ui, 0, 2)
    assert ui.auto_forward[0].shares == {1: 50, 2: 50}
    ui.cancel_send()
    assert ui.auto_forward[0].shares == {1: 100}


def test_trying_the_forward_tab_and_going_back_restores_the_other_lanes():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 70}, 3)
    _forward(state, ui, 0, 2)
    assert ui.auto_forward[0].shares == {1: 35, 2: 35}
    ui.set_forward_mode(state, False)
    assert ui.auto_forward[0] == ForwardRule({1: 70}, 3)
    assert [(o.dest_id, o.ships) for o in ui.pending] == [(2, 12)]


def test_a_third_lane_splits_three_ways():
    state, ui = _hub(), _ui()
    for dest in (1, 2, 3):
        _forward(state, ui, 0, dest)
        ui.close_send()
    assert ui.auto_forward[0].shares == {1: 34, 2: 33, 3: 33}


def test_deleting_one_lane_of_a_reopened_split_keeps_the_rest():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 34, 2: 33, 3: 33}, 2)
    ui.edit_forward(state, 0, 2)
    assert ui.dest == 2 and ui.editing_existing
    ui.cancel_send()
    assert ui.auto_forward[0] == ForwardRule({1: 50, 3: 50}, 2)
    assert ui.mode != CHOOSING


def test_focus_moves_between_lanes_without_closing_the_popup():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 50, 2: 50})
    ui.edit_forward(state, 0, 1)
    ui.popup_pos = (300, 300)
    ui.edit_forward(state, 0, 2)            # a click on the rule's other lane
    assert ui.dest == 2 and ui.popup_pos == (300, 300)
    ui.focus_lane(1)
    ui.step_share(1)
    assert ui.auto_forward[0].shares == {1: 50 + config.FORWARD_STEP_PCT, 2: 50 - config.FORWARD_STEP_PCT}
    ui.focus_lane(4)                        # not one of its lanes: ignored
    assert ui.dest == 1


def test_even_split_and_delete_all():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 80, 2: 20}, 1)
    ui.edit_forward(state, 0, 1)
    ui.even_split()
    assert ui.auto_forward[0] == ForwardRule({1: 50, 2: 50}, 1)
    ui.delete_all_forward()
    assert 0 not in ui.auto_forward and ui.mode != CHOOSING


def test_hold_is_the_systems_and_stays_in_range():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 50, 2: 50})
    ui.edit_forward(state, 0, 2)
    ui.step_hold(3)
    assert ui.auto_forward[0].hold == 3
    ui.step_hold(-10)
    assert ui.auto_forward[0].hold == 0
    ui.step_hold(config.FORWARD_HOLD_MAX + 5)
    assert ui.auto_forward[0].hold == config.FORWARD_HOLD_MAX


def test_clearing_dangerous_lanes_leaves_the_safe_ones():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 50, 4: 50})
    ui.auto_forward[2] = ForwardRule({0: 100})
    assert ui.rule_is_hostile(state, 0) and not ui.rule_is_hostile(state, 2)
    ui.clear_dangerous_forward(state)
    assert ui.auto_forward == {0: ForwardRule({1: 100}), 2: ForwardRule({0: 100})}
    assert ui.forward_lanes() == 2


# --------------------------------------------------------------------------- #
# What goes at the end of the turn
# --------------------------------------------------------------------------- #
def test_orders_send_each_lane_its_share_after_the_hold():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 50, 2: 50}, 3)
    orders = main.auto_forward_orders(state, ui)
    assert sorted((o.dest_id, o.ships) for o in orders) == [(1, 5), (2, 4)]
    assert ui.forward_this_turn(state, 0) == {1: 5, 2: 4}   # what the popup shows


def test_a_half_rule_sends_half_of_whatever_is_there():
    """The old Keep half stamped garrison // 2 when pressed; a share follows the
    garrison as it grows."""
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 50})
    assert [o.ships for o in main.auto_forward_orders(state, ui)] == [6]
    state.systems[0].ships = 30
    assert [o.ships for o in main.auto_forward_orders(state, ui)] == [15]


def test_manual_orders_come_out_of_the_surplus_first():
    from starconquest.model import Order

    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 100}, 2)
    ui.pending.append(Order(1, 0, 3, 4))
    assert [(o.dest_id, o.ships) for o in main.auto_forward_orders(state, ui)] == [(1, 6)]


def test_a_zero_share_lane_sends_nothing():
    state, ui = _hub(), _ui()
    ui.auto_forward[0] = ForwardRule({1: 0, 2: 100})
    assert [(o.dest_id, o.ships) for o in main.auto_forward_orders(state, ui)] == [(2, 12)]

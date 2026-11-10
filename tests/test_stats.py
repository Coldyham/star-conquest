"""The end-of-game graphs: `stats.chart` read off the history boards, and the
overlay's controls in history review."""

import math



from starconquest import stats


def test_an_empty_history_charts_nothing():
    chart = stats.chart([])
    assert chart.turns == 0 and chart.seats == ()
    assert chart.peak("ships", chart.seats) == 0.0


def test_a_turns_losses_need_it_and_the_turn_before_known():
    """Losses are cumulative, so a turn after a gap would carry the whole gap's
    losses as its own: it's left a gap too. The running total is still known."""
    c = stats.Chart(3, (2,), {"losses": {2: (0.0, 4.0, 9.0, 10.0)},
                              "lost_turn": {2: (0.0, 4.0, 5.0, 1.0)}})
    m = stats.masked(c, {2: (True, False, True, True)})
    assert [math.isnan(v) for v in m.series("losses", 2)] == [False, True, False, False]
    assert [math.isnan(v) for v in m.series("lost_turn", 2)] == [False, True, True, False]
    assert m.peak("losses", (2,)) == 10.0 and m.stack_peak("lost_turn", (2,)) == 1.0
    assert m.sighted(2) is False   # no "systems" series at all: nothing to offer


def test_a_knocked_out_rival_stays_known_once_sighted():
    from starconquest import mapgen
    board = mapgen.generate_random(1, num_nodes=18, num_players=3)
    rival_home = next(sid for sid, sys in board.systems.items() if sys.owner_id == 2)
    import copy
    gone = copy.deepcopy(board)
    gone.players[2].alive = False
    seen = stats.sightings([board, gone], [{rival_home}, set()], 1)
    assert seen[2] == (True, True) and seen[3] == (False, False)
    assert stats.sightings([gone], [set()], 1)[2] == (False,)   # never sighted: still unknown



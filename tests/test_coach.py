"""The post-game coach: `coach.review` on hand-built games, and its tab in
history review."""

from __future__ import annotations

import copy
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from starconquest import app, coach, combat, config, engine, render, replay
from starconquest import input as game_input
from starconquest.model import Order
from starconquest.settings import Settings
from tests.test_engine import make_state, no_jitter


def _play(state, turns: int, mine: dict[int, list[Order]] | None = None,
          theirs: dict[int, list[Order]] | None = None):
    """Play ``turns`` turns: seat 1's orders from ``mine``, every other seat's
    from ``theirs``, both keyed by turn. Returns (boards, orders) as history
    review holds them."""
    mine, theirs = mine or {}, theirs or {}
    boards, orders = [copy.deepcopy(state)], []

    def decide(s, pid):
        return [o for o in theirs.get(s.turn, []) if o.owner_id == pid]

    for _ in range(turns):
        t = state.turn
        record = engine.end_turn(state, human_orders=mine.get(t, []), decide=decide)
        orders.append(list(record.orders))
        boards.append(copy.deepcopy(state))
    return boards, orders


def test_an_attack_below_the_sure_count_is_a_gamble():
    with no_jitter():
        state = make_state([(0, 1, 6, 100), (1, 2, 6, 100), (2, 2, 9, 100)], [(0, 1, 1), (1, 2, 1)], human=1)
        boards, orders = _play(state, 2, mine={0: [Order(1, 0, 1, 6)]})
    notes = coach.review(boards, orders, 1).notes
    gamble = next(n for n in notes if n.kind == coach.GAMBLE)
    assert gamble.turn == 0 and gamble.systems == (0, 1)
    sure = combat.sure_count(6, config.COMBAT_JITTER, config.DEFENDER_ADVANTAGE)
    assert f"{sure} would have been sure" in gamble.text


def test_a_capture_is_counted_not_noted():
    state = make_state([(0, 1, 20, 100), (1, 2, 3, 100), (2, 2, 9, 100)], [(0, 1, 1), (1, 2, 1)], human=1)
    boards, orders = _play(state, 2, mine={0: [Order(1, 0, 1, 20)]})
    review = coach.review(boards, orders, 1)
    assert review.captures == 1 and review.failed == 0
    assert not [n for n in review.notes if n.kind in (coach.GAMBLE, coach.SURPRISE)]


def test_a_system_sent_out_and_then_lost_is_noted():
    state = make_state([(0, 1, 10, 100), (2, 1, 1, 100), (1, 2, 20, 100)],
                       [(0, 1, 1), (0, 2, 1)], human=1)
    with no_jitter():
        boards, orders = _play(state, 3, mine={0: [Order(1, 0, 2, 9)]},
                               theirs={1: [Order(2, 1, 0, 8)]})
        review = coach.review(boards, orders, 1)
    emptied = next(n for n in review.notes if n.kind == coach.EMPTIED)
    assert emptied.turn == 0 and emptied.systems == (0,)
    assert "sent 9 of 10" in emptied.text and "P2 took it on turn 2" in emptied.text
    assert "10 would likely have held" in emptied.text and "against P2's 8" in emptied.text
    assert review.lost == 1


def test_an_emptying_over_several_turns_names_one_launch():
    # 6 of 10 out, then the 5 left (4 and one built): the note is the bigger
    # launch against its own board, never 11 of 10.
    state = make_state([(0, 1, 10, 1), (2, 1, 1, 100), (1, 2, 20, 100)],
                       [(0, 1, 1), (0, 2, 1)], human=1)
    with no_jitter():
        boards, orders = _play(state, 3, mine={0: [Order(1, 0, 2, 6)], 1: [Order(1, 0, 2, 5)]},
                               theirs={1: [Order(2, 1, 0, 8)]})
        review = coach.review(boards, orders, 1)
    emptied = next(n for n in review.notes if n.kind == coach.EMPTIED)
    assert emptied.turn == 0 and "sent 6 of 10" in emptied.text


def test_an_emptying_that_took_a_system_names_the_trade():
    state = make_state([(0, 1, 10, 100), (3, 0, 2, 100), (1, 2, 20, 100)],
                       [(0, 1, 1), (0, 3, 1)], human=1)
    with no_jitter():
        boards, orders = _play(state, 3, mine={0: [Order(1, 0, 3, 9)]},
                               theirs={1: [Order(2, 1, 0, 8)]})
        review = coach.review(boards, orders, 1)
    emptied = next(n for n in review.notes if n.kind == coach.EMPTIED)
    took, name = boards[0].systems[3].short, boards[0].systems[0].short
    assert f"and took {took} with them" in emptied.text
    assert f"P2 took {name} on turn 2" in emptied.text


def test_pulling_out_of_a_system_that_would_fall_anyway_is_an_evacuation():
    state = make_state([(0, 1, 10, 100), (2, 1, 1, 100), (1, 2, 20, 100)],
                       [(0, 1, 1), (0, 2, 1)], human=1)
    with no_jitter():
        boards, orders = _play(state, 3, mine={1: [Order(1, 0, 2, 10)]},
                               theirs={1: [Order(2, 1, 0, 15)]})
        review = coach.review(boards, orders, 1)
    assert not [n for n in review.notes if n.kind == coach.EMPTIED]
    evac = next(n for n in review.notes if n.kind == coach.EVACUATED)
    assert evac.turn == 1 and evac.systems == (0,)
    assert "pulled 10 of 10" in evac.text and "P2's 15" in evac.text
    assert review.lost == 1


def test_ships_idle_beside_a_sure_capture_are_noted_once():
    state = make_state([(0, 1, 20, 100), (1, 0, 3, 2), (2, 2, 5, 100)], [(0, 1, 1), (1, 2, 4)], human=1)
    boards, orders = _play(state, coach.IDLE_TURNS + 2)
    idle = [n for n in coach.review(boards, orders, 1).notes if n.kind == coach.IDLE]
    assert len(idle) == 1 and idle[0].turn == 0 and idle[0].systems == (0, 1)


def test_holding_beside_a_rival_is_not_idleness():
    state = make_state([(0, 1, 20, 100), (1, 0, 3, 2), (2, 2, 5, 100)], [(0, 1, 1), (0, 2, 4)], human=1)
    boards, orders = _play(state, coach.IDLE_TURNS + 2)
    assert not [n for n in coach.review(boards, orders, 1).notes if n.kind == coach.IDLE]


def test_the_turning_point_of_a_loss():
    """Ahead, then behind for good: the note is on the first board behind."""
    state = make_state([(0, 1, 9, 3), (3, 1, 9, 3), (1, 2, 4, 3), (2, 0, 0, 1), (4, 0, 0, 1)],
                       [(0, 1, 9), (1, 2, 1), (1, 4, 1), (0, 3, 9)], human=1)
    boards, orders = _play(state, 3, theirs={1: [Order(2, 1, 2, 1), Order(2, 1, 4, 1)]})
    for b in boards:
        b.winner = 2
    turn = next(n for n in coach.review(boards, orders, 1).notes if n.kind == coach.TURN)
    assert turn.turn == 2 and "P2's income passed yours" in turn.text


def test_the_costliest_notes_are_kept_and_shown_in_turn_order():
    state = make_state([(0, 1, 6, 100), (1, 2, 9, 100)], [(0, 1, 1)], human=1)
    attacks = {t: [Order(1, 0, 1, 1)] for t in range(6)}
    boards, orders = _play(state, 6, mine=attacks)
    notes = coach.review(boards, orders, 1, limit=3).notes
    assert len(notes) == 3
    assert [n.turn for n in notes] == sorted(n.turn for n in notes)


# -- in history review ------------------------------------------------------- #
def _decided_review(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    settings = Settings(seed=1, nodes=18, players=3)
    log = replay.new_log(settings, 1)
    log.path = tmp_path / "game.json"
    state, ui = app.resume_game(log, settings)
    ui.autoplay = True
    while state.winner is None and state.turn < 300:
        app.resolve_turn(state, ui, log, settings)
    assert state.winner is not None
    states, _f, _e, _l = app.open_history(state, ui, log)
    return screen, states, ui


def _key(key, mod=0):
    return pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod, unicode="")


def test_the_coach_tab_steps_through_its_notes(tmp_path, monkeypatch):
    try:
        screen, states, ui = _decided_review(tmp_path, monkeypatch)
        review = ui.coach_review
        assert review is not None and review.notes
        assert ui.coach_open == any(n.kind != coach.TURN for n in review.notes)
        ui.coach_open = True
        render.draw(screen, states[ui.history_turn], ui)
        assert ui.coach_tab_rect[2] and ui.coach_rows and not ui.graph_trace_rects
        # Down selects the first note, and History jumps to its board
        game_input.handle_event(_key(pygame.K_DOWN), states[ui.history_turn], ui)
        assert ui.coach_sel == 0 and ui.history_turn == review.notes[0].turn
        # a click on a row selects it
        render.draw(screen, states[ui.history_turn], ui)
        i, rect = ui.coach_rows[-1]
        click = pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(rect[0] + 4, rect[1] + 4))
        game_input.handle_event(click, states[ui.history_turn], ui)
        assert ui.coach_sel == i and ui.history_turn == review.notes[i].turn
        # Tab leaves for the first chart and comes round to the coach again
        game_input.handle_event(_key(pygame.K_TAB), states[ui.history_turn], ui)
        assert not ui.coach_open and ui.graph_metric == 0
        game_input.handle_event(_key(pygame.K_TAB, pygame.KMOD_SHIFT), states[ui.history_turn], ui)
        assert ui.coach_open
        render.draw(screen, states[ui.history_turn], ui)
    finally:
        pygame.quit()


def test_no_coach_mid_game(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    settings = Settings(seed=1, nodes=14, players=2)
    log = replay.new_log(settings, 1)
    log.path = tmp_path / "game.json"
    state, ui = app.resume_game(log, settings)
    ui.autoplay = True
    for _ in range(4):
        app.resolve_turn(state, ui, log, settings)
    app.open_history(state, ui, log)
    assert ui.coach_review is None and not ui.coach_open

"""The end-of-game graphs: `stats.chart` read off the history boards, and the
overlay's controls in history review."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from starconquest import config, fog, main, render, replay, stats
from starconquest import input as game_input
from starconquest.settings import Settings


def _finished_review(tmp_path, monkeypatch, turns=8):
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    pygame.init()
    render._FONTS.clear()   # a font from an earlier test's pygame session is dead
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    settings = Settings(seed=1, nodes=18, players=3)
    log = replay.new_log(settings, 1)
    log.path = tmp_path / "game.json"
    state, ui = main.resume_game(log, settings)
    ui.autoplay = True
    for _ in range(turns):
        main.resolve_turn(state, ui, log, settings)
    return screen, state, ui, log


def test_a_chart_reads_every_board_it_is_given(tmp_path, monkeypatch):
    """One value per board, turn 0 included, and each one exactly what the
    scoreboard says about that board — the graph never disagrees with the map."""
    try:
        _screen, state, ui, log = _finished_review(tmp_path, monkeypatch)
        states, _fog, _events = main.build_history(ui, log)
        chart = stats.chart(states)
        assert chart.turns == log.turn_count
        assert chart.seats == tuple(sorted(p.id for p in state.non_neutral_players()))
        for t, board in enumerate(states):
            for pid in chart.seats:
                systems, ships, income = fog.player_totals(board, pid)
                assert chart.series("systems", pid)[t] == systems
                assert chart.series("ships", pid)[t] == ships
                assert chart.series("income", pid)[t] == income
                assert chart.series("losses", pid)[t] == board.players[pid].ships_lost
        # losses are cumulative, so never fall
        for pid in chart.seats:
            losses = chart.series("losses", pid)
            assert all(a <= b for a, b in zip(losses, losses[1:]))
    finally:
        pygame.quit()


def test_an_empty_history_charts_nothing():
    chart = stats.chart([])
    assert chart.turns == 0 and chart.seats == ()
    assert chart.peak("ships", chart.seats) == 0.0


def test_graphs_show_rivals_only_once_review_is_revealed(tmp_path, monkeypatch):
    """Mid-game, a rival's curve would show what the fog hides on the map."""
    try:
        _screen, state, ui, log = _finished_review(tmp_path, monkeypatch)
        main.open_history(state, ui, log)
        ui.history_reveal = False
        assert ui.graph_seats() == (ui.human_id,)
        ui.history_reveal = True
        assert ui.graph_seats() == ui.history_chart.seats
    finally:
        pygame.quit()


def test_g_toggles_the_graphs_and_tab_cycles_the_metric(tmp_path, monkeypatch):
    try:
        _screen, state, ui, log = _finished_review(tmp_path, monkeypatch)
        main.open_history(state, ui, log)
        shown = ui.history_graph
        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_g, mod=0), state, ui)
        assert ui.history_graph is not shown
        ui.history_graph = True
        for want in (1, 2, 3, 0):
            game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_TAB, mod=0), state, ui)
            assert ui.graph_metric == want
    finally:
        pygame.quit()


def test_clicking_the_plot_jumps_to_that_turn(tmp_path, monkeypatch):
    """The plot spans turn 0 to the last, as the scrubber does, so a click on its
    right edge lands on the final turn and one on its left on the opening."""
    try:
        screen, state, ui, log = _finished_review(tmp_path, monkeypatch)
        states, _f, _e, _l = main.open_history(state, ui, log)
        ui.history_graph = True
        ui.history_turn = ui.history_max // 2
        render.draw(screen, states[ui.history_turn], ui)
        x, y, w, h = ui.graph_plot_rect
        assert w > 0 and len(ui.graph_tab_rects) == len(stats.METRICS)
        down = pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(x + w, y + h // 2), button=1)
        game_input.handle_event(down, state, ui)
        assert ui.history_turn == ui.history_max and ui.dragging_graph
        drag = pygame.event.Event(pygame.MOUSEMOTION, pos=(x, y + h // 2), rel=(0, 0), buttons=(1, 0, 0))
        game_input.handle_event(drag, state, ui)
        assert ui.history_turn == 0
        game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=(x, y), button=1), state, ui)
        assert not ui.dragging_graph
        tx, ty, tw, th = ui.graph_tab_rects[2]
        game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=(tx + 1, ty + 1), button=1), state, ui)
        assert ui.graph_metric == 2
    finally:
        pygame.quit()


def test_closed_graphs_leave_no_hit_rects(tmp_path, monkeypatch):
    try:
        screen, state, ui, log = _finished_review(tmp_path, monkeypatch)
        states, _f, _e, _l = main.open_history(state, ui, log)
        ui.history_graph = False
        render.draw(screen, states[-1], ui)
        assert ui.graph_plot_rect == (0, 0, 0, 0) and ui.graph_tab_rects == []
        assert ui.graph_button_rect[2] > 0
    finally:
        pygame.quit()

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
        # losses are cumulative, so never fall, and per turn they sum back up
        for pid in chart.seats:
            losses = chart.series("losses", pid)
            assert all(a <= b for a, b in zip(losses, losses[1:]))
            per_turn = chart.series("lost_turn", pid)
            assert per_turn[0] == 0 and len(per_turn) == len(losses)
            assert sum(per_turn) == losses[-1]
        # every board's shares, unclaimed included, make up the whole map
        for t, board in enumerate(states):
            held = sum(chart.series("share", pid)[t] for pid in chart.seats)
            assert abs(held + chart.series("share", stats.NEUTRAL)[t] - 100.0) < 1e-9
            for pid in chart.seats:
                owned = sum(1 for sys in board.systems.values() if sys.owner_id == pid)
                assert abs(chart.series("share", pid)[t] - 100.0 * owned / len(board.systems)) < 1e-9
        tallest = max(sum(chart.series("lost_turn", pid)[t] for pid in chart.seats)
                      for t in range(chart.turns + 1))
        assert chart.stack_peak("lost_turn", chart.seats) == tallest
        assert chart.stack_peak("lost_turn", ()) == 0.0
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
        for want in [*range(1, len(stats.METRICS)), 0]:
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


def _open_graphs(tmp_path, monkeypatch):
    screen, state, ui, log = _finished_review(tmp_path, monkeypatch)
    states, _f, _e, _l = main.open_history(state, ui, log)
    ui.history_reveal = True          # every seat offered, as on a finished game
    ui.history_graph = True
    return screen, states[-1], ui


def _press(state, ui, pos):
    game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1), state, ui)


def test_the_caret_switches_between_docked_and_full_screen(tmp_path, monkeypatch):
    """Docked, the chart leaves the map's upper part showing; full, it covers
    everything between the bars. The scrubber is drawn either way."""
    try:
        screen, board, ui = _open_graphs(tmp_path, monkeypatch)
        render.draw(screen, board, ui)
        docked = ui.graph_plot_rect
        x, y, w, h = ui.graph_full_rect
        assert w > 0 and y > config.play_rect()[1] + config.play_rect()[3] // 2
        _press(board, ui, (x + w // 2, y + h // 2))
        assert ui.graph_full
        render.draw(screen, board, ui)
        assert ui.graph_plot_rect[3] > docked[3] and ui.graph_plot_rect[2] > docked[2]
        assert ui.graph_full_rect[1] < config.HUD_TOP_H + config.s(40)
        assert ui.scrubber_rect[2] > 0
        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_f, mod=0), board, ui)
        assert not ui.graph_full
    finally:
        pygame.quit()


def test_the_legend_shows_and_hides_each_trace(tmp_path, monkeypatch):
    """A click on a seat's row hides its line, another brings it back, and Show
    all appears only while something is hidden. The 1-9 keys do the same."""
    try:
        screen, board, ui = _open_graphs(tmp_path, monkeypatch)
        render.draw(screen, board, ui)
        assert [pid for pid, _r in ui.graph_trace_rects] == list(ui.graph_seats())
        assert ui.graph_all_rect == (0, 0, 0, 0)
        pid, (x, y, w, h) = ui.graph_trace_rects[0]
        _press(board, ui, (x + 2, y + h // 2))
        assert pid not in ui.graph_traces() and pid in ui.graph_seats()
        render.draw(screen, board, ui)
        ax, ay, aw, ah = ui.graph_all_rect
        assert aw > 0
        _press(board, ui, (ax + 2, ay + 2))
        assert ui.graph_traces() == ui.graph_seats()
        game_input.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_2, mod=0), board, ui)
        assert ui.graph_seats()[1] not in ui.graph_traces()
    finally:
        pygame.quit()


def test_a_rival_cannot_be_toggled_mid_game(tmp_path, monkeypatch):
    """Mid-game only our seat is offered; a rival can't be toggled into view."""
    try:
        _screen, _board, ui = _open_graphs(tmp_path, monkeypatch)
        ui.history_reveal = False
        rival = next(pid for pid in ui.history_chart.seats if pid != ui.human_id)
        ui.toggle_trace(rival)
        assert ui.graph_hidden == set() and ui.graph_traces() == (ui.human_id,)
    finally:
        pygame.quit()


def test_the_stacked_tabs_draw_in_both_sizes(tmp_path, monkeypatch):
    """Lost/turn draws bars and Map share bands rather than lines, docked and
    full screen alike, revealed or not, and with a seat hidden."""
    try:
        screen, board, ui = _open_graphs(tmp_path, monkeypatch)
        keys = [k for k, _t, _u in stats.METRICS]
        for metric in ("lost_turn", "share"):
            ui.graph_metric = keys.index(metric)
            for full in (False, True):
                for reveal in (True, False):
                    ui.graph_full, ui.history_reveal = full, reveal
                    ui.graph_hidden = {ui.graph_seats()[0]}
                    render.draw(screen, board, ui)
                    assert ui.graph_plot_rect[2] > 0
    finally:
        pygame.quit()

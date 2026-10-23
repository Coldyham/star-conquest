"""The end-of-game graphs: `stats.chart` read off the history boards, and the
overlay's controls in history review."""

import math
import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import pytest

from starconquest import config, fog, main, render, replay, stats
from starconquest import input as game_input
from starconquest.settings import Settings


def _finished_review(tmp_path, monkeypatch, turns=8, **setup):
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    pygame.init()
    render._FONTS.clear()   # a font from an earlier test's pygame session is dead
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    settings = Settings(seed=1, nodes=18, players=3, **setup)
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


def _mid_game_review(tmp_path, monkeypatch, turns=12, **setup):
    _screen, state, ui, log = _finished_review(tmp_path, monkeypatch, turns, **setup)
    assert state.winner is None
    states, fogs, _e, _l = main.open_history(state, ui, log)
    assert not ui.history_reveal
    known = stats.sightings(states, [f[0] for f in fogs], ui.human_id)
    return ui, known


def test_mid_game_graphs_chart_a_rival_only_while_in_sight(tmp_path, monkeypatch):
    """As the top bar does: a rival's figures are known on the turns it holds
    a system in sight, and only those; the rest are gaps, never zeros."""
    try:
        ui, known = _mid_game_review(tmp_path, monkeypatch, fog_sight=config.FOG_ON_SIGHT,
                                     fog_scout=config.FOG_ON_SCOUT)
        chart = ui.history_chart
        assert all(known[ui.human_id])
        assert any(not all(k) for k in known.values()), "fog should hide someone, some turn"
        for pid, seen in known.items():
            for key in ("systems", "ships", "income", "losses", "share"):
                assert [not math.isnan(v) for v in chart.series(key, pid)] == list(seen)
        assert ui.graph_seats() == tuple(pid for pid in chart.seats if any(known[pid]))
    finally:
        pygame.quit()


def test_with_fog_off_mid_game_graphs_chart_every_seat(tmp_path, monkeypatch):
    try:
        ui, known = _mid_game_review(tmp_path, monkeypatch)   # fog is off by default
        assert all(all(k) for k in known.values())
        assert ui.graph_seats() == ui.history_chart.seats
    finally:
        pygame.quit()


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
    """Mid-game a rival never in sight isn't offered, so can't be toggled into view."""
    try:
        _screen, _board, ui = _open_graphs(tmp_path, monkeypatch)
        ui.history_reveal = False
        chart = ui.history_chart
        rival = next(pid for pid in chart.seats if pid != ui.human_id)
        ui.history_chart = stats.masked(chart, {pid: (pid != rival,) * (chart.turns + 1) for pid in chart.seats})
        assert rival not in ui.graph_seats()
        ui.toggle_trace(rival)
        assert ui.graph_hidden == set() and rival not in ui.graph_traces()
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


def test_a_review_has_the_camera_cluster_above_the_docked_graphs(tmp_path, monkeypatch):
    """Reset and −/+ sit on the map in history as in live play, lifted clear of
    the docked graphs; full-screen graphs leave no map, so no cluster."""
    try:
        screen, board, ui = _open_graphs(tmp_path, monkeypatch)
        render.draw(screen, board, ui)
        x, y, w, h = ui.zoom_plus_rect
        assert w > 0 and y + h <= ui.graph_tab_rects[0][1]
        zoom = ui.view.zoom
        _press(board, ui, (x + w // 2, y + h // 2))
        assert ui.view.zoom > zoom
        rx, ry, rw, rh = ui.reset_view_rect
        _press(board, ui, (rx + rw // 2, ry + rh // 2))
        assert ui.view.zoom == zoom
        mx, my, mw, mh = ui.zoom_minus_rect
        for _ in range(12):          # review's camera zooms out past the fit...
            _press(board, ui, (mx + mw // 2, my + mh // 2))
        assert ui.view.zoom == pytest.approx(1 / (1 + 2 * config.REVIEW_ROOM))
        _press(board, ui, (rx + rw // 2, ry + rh // 2))
        assert ui.view.zoom == zoom  # ...and Reset brings it back
        ui.history_graph = False
        render.draw(screen, board, ui)
        assert ui.zoom_plus_rect[1] > y        # back down to the map's foot
        ui.history_graph = ui.graph_full = True
        render.draw(screen, board, ui)
        assert ui.zoom_plus_rect == ui.reset_view_rect == (0, 0, 0, 0)
    finally:
        pygame.quit()


def test_a_drag_on_the_reviewed_map_pans_it(tmp_path, monkeypatch):
    try:
        screen, board, ui = _open_graphs(tmp_path, monkeypatch)
        render.draw(screen, board, ui)
        x, y, w, h = ui.zoom_plus_rect
        _press(board, ui, (x + w // 2, y + h // 2))
        _press(board, ui, (x + w // 2, y + h // 2))
        before = ui.view.to_screen(board.systems[next(iter(board.systems))].pos)
        start = (config.play_rect()[2] // 2, config.play_rect()[1] + config.s(80))
        _press(board, ui, start)
        assert ui.pan_active and not ui.dragging_graph
        game_input.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(start[0] + 30, start[1] + 20),
                                                   rel=(30, 20), buttons=(1, 0, 0)), board, ui)
        assert ui.view.to_screen(board.systems[next(iter(board.systems))].pos) != before
        game_input.handle_event(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=start, button=1), board, ui)
        assert not ui.pan_active
    finally:
        pygame.quit()


def test_a_portrait_legend_runs_under_a_full_width_plot(tmp_path, monkeypatch):
    """A column beside the plot would halve it on a tall screen and still clip
    the bot names; under the plot, the rows have the panel's width for them."""
    try:
        monkeypatch.setattr(config, "SCREEN_W", 900)
        monkeypatch.setattr(config, "SCREEN_H", 1600)
        screen, board, ui = _open_graphs(tmp_path, monkeypatch)
        screen = pygame.display.set_mode((900, 1600))
        monkeypatch.setattr(render, "_seat_who", lambda s, u, pid: "thinker claudebot (random)")
        drawn = []
        real = render._text
        monkeypatch.setattr(render, "_text", lambda surf, font, s, *a, **k: drawn.append(s) or real(surf, font, s, *a, **k))
        for full in (False, True):
            ui.graph_full = full
            drawn.clear()
            render.draw(screen, board, ui)
            px, py, pw, ph = ui.graph_plot_rect
            box_w = config.SCREEN_W if full else config.play_rect()[2]
            assert pw > box_w * 0.75
            assert all(r[1] > py + ph for _pid, r in ui.graph_trace_rects)
            assert drawn.count(" · thinker claudebot (random)") == len(ui.graph_seats())
            tab_bottom = max(t[1] + t[3] for t in ui.graph_tab_rects)
            assert tab_bottom < py and all(t[0] + t[2] < ui.graph_full_rect[0] for t in ui.graph_tab_rects)
    finally:
        pygame.quit()


def test_every_tab_draws_a_chart_with_gaps(tmp_path, monkeypatch):
    """A rival in and out of sight: lines break and dot across the gaps, bars
    and bands skip the unknown turns, and the legend reads a dash, never 'nan'."""
    try:
        screen, board, ui = _open_graphs(tmp_path, monkeypatch)
        ui.history_reveal = False
        chart = ui.history_chart
        rival = next(pid for pid in chart.seats if pid != ui.human_id)
        flicker = tuple(t % 3 != 1 for t in range(chart.turns + 1))
        ui.history_chart = stats.masked(chart, {rival: flicker})
        ui.history_turn = 1                # a turn the rival is out of sight
        drawn = []
        real = render._text
        monkeypatch.setattr(render, "_text", lambda surf, font, s, *a, **k: drawn.append(s) or real(surf, font, s, *a, **k))
        for metric in range(len(stats.METRICS)):
            ui.graph_metric = metric
            render.draw(screen, board, ui)
            assert ui.graph_plot_rect[2] > 0
        assert "–" in drawn and not any("nan" in s for s in drawn)
    finally:
        pygame.quit()

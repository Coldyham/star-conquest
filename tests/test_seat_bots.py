"""Which bot plays each seat, as the HUD names it: the top bar's hover tooltip
and the graph legend, with a random seat kept secret until the game is decided."""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from starconquest import ai, config, main, render, replay
from starconquest import input as game_input
from starconquest import settings as settings_mod
from starconquest.settings import RANDOM_STRATEGY, Settings


def _game(tmp_path, monkeypatch):
    """A 3-seat game: us at 1, a random seat at 2, knower at 3."""
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    # pin every knob `build_state` writes, so teardown puts each one back
    for _, const in settings_mod._GLOBAL_KNOBS:
        monkeypatch.setattr(config, const, getattr(config, const))
    monkeypatch.setattr(ai, "STRATEGIES", dict(ai.STRATEGIES))
    ai.load_models()
    pygame.init()
    render._FONTS.clear()
    screen = pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))
    cfg = Settings(seed=5, nodes=18, players=3)
    cfg.ai_strategy[1] = RANDOM_STRATEGY
    cfg.ai_strategy[2] = "knower"
    state, ui, log = main.start_game(cfg, 5, False)
    return screen, cfg, state, ui, log


def test_each_seat_is_labelled_with_the_bot_it_plays(tmp_path, monkeypatch):
    try:
        _screen, _cfg, state, ui, _log = _game(tmp_path, monkeypatch)
        assert ui.random_seats == frozenset({2})
        assert ui.seat_bots[3].startswith("Knower")
        dealt = state.players[2].ai_strategy
        assert ui.seat_bots[2].lower().startswith(ai.family(dealt).lower())
        assert ui.bot_label(state, 1) is None          # our own seat is a person's
        assert render._seat_who(state, ui, 1) == "you"
        assert ui.bot_label(state, 0) is None          # neutral plays nothing
    finally:
        pygame.quit()


def test_a_random_seat_is_revealed_only_once_the_game_is_decided(tmp_path, monkeypatch):
    """Mid-game it reads "Random", even reviewing history, and even knocked out;
    a decided game names the bot it was dealt, on the board and in review."""
    try:
        _screen, _cfg, state, ui, _log = _game(tmp_path, monkeypatch)
        assert ui.bot_label(state, 2) == "Random"
        state.players[1].alive = False                 # a spectator still waits
        assert ui.bot_label(state, 2) == "Random"
        ui.history, ui.history_reveal = True, False    # a mid-game review
        assert ui.bot_label(state, 2) == "Random"
        ui.history_reveal = True                       # reviewing a finished game
        assert ui.bot_label(state, 2) == f"{ui.seat_bots[2]} (random)"
        ui.history = False
        state.winner = 3
        assert ui.bot_label(state, 2) == f"{ui.seat_bots[2]} (random)"
        assert ui.bot_label(state, 3) == ui.seat_bots[3]   # a chosen bot is no secret
    finally:
        pygame.quit()


def test_hovering_a_scoreboard_entry_names_its_bot(tmp_path, monkeypatch):
    """The entry's rect is recorded by the frame, the pointer finds it, and the
    next frame draws the tooltip — saying Random, never the dealt bot."""
    try:
        screen, _cfg, state, ui, _log = _game(tmp_path, monkeypatch)
        ui.visible = set(state.systems)                # every rival in sight
        render.draw(screen, state, ui)
        rect = dict(ui.score_rects)[2]
        pos = (rect[0] + 2, rect[1] + rect[3] // 2)
        game_input.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=(0, 0, 0)),
                                state, ui)
        assert ui.score_hover == 2
        drawn = []
        real = render._text
        monkeypatch.setattr(render, "_text", lambda surf, font, s, *a, **k: drawn.append(s) or real(surf, font, s, *a, **k))
        render.draw(screen, state, ui)
        name = state.players[2].name
        assert f"{name} · Random" in drawn
        assert not any(ui.seat_bots[2] in s for s in drawn)
        game_input.handle_event(pygame.event.Event(pygame.MOUSEMOTION, pos=(5, config.SCREEN_H // 2),
                                                   rel=(0, 0), buttons=(0, 0, 0)), state, ui)
        assert ui.score_hover is None
    finally:
        pygame.quit()


def test_the_graph_legend_names_each_seats_bot(tmp_path, monkeypatch):
    try:
        screen, cfg, state, ui, log = _game(tmp_path, monkeypatch)
        ui.autoplay = True
        for _ in range(4):
            main.resolve_turn(state, ui, log, cfg)
        states, *_ = main.open_history(state, ui, log)
        ui.history_reveal = True
        ui.history_graph = ui.graph_full = True
        drawn = []
        real = render._text
        monkeypatch.setattr(render, "_text", lambda surf, font, s, *a, **k: drawn.append(s) or real(surf, font, s, *a, **k))
        render.draw(screen, states[-1], ui)
        assert f" · {ui.seat_bots[3]}" in drawn
        assert f" · {ui.seat_bots[2]} (random)" in drawn
    finally:
        pygame.quit()


def test_a_stop_that_repeats_its_family_is_left_off():
    """"Thinker · Thinker" says nothing a plain "Thinker" doesn't."""
    from starconquest import mapgen
    ai.load_models()
    state = mapgen.generate_random(1, num_nodes=18, num_players=3)
    names = ai.aux_names("thinker") or ()
    assert "Thinker" in names
    state.players[2].ai_strategy = "thinker"
    state.players[2].ai_params.aux = float(ai.aux_spec("thinker")[1] + names.index("Thinker"))
    assert main.seat_bot_labels(state)[2] == "Thinker"

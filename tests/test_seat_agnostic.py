"""The shell works for a seat that is not pid 1.

Every single-player match seats the person at pid 1 — `mapgen._make_players`
stamps it and nothing in the setup moves it — so until play-by-post nothing had
ever run the shell from anywhere else. `Ui.human_id` was always a field and the
shell always read it, but "always read it" was an untested claim: these tests are
what make it a checked one.

Pure/headless: `main` imports pygame, but nothing exercised here draws, so the
SDL dummy driver is enough.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

import main as app
from starconquest import ai, config, fog, mapgen, replay
from starconquest.model import Order

pygame.init()
pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))


def _seated(seat: int, seed: int = 5, nodes: int = 16, players: int = 3):
    """A live match with the person at ``seat`` instead of pid 1."""
    state = mapgen.generate(seed, "random", nodes, players)
    for player in state.players.values():
        player.is_human = False
    state.players[seat].is_human = True
    ui = app.new_ui(state, autoplay=False, seat=seat)
    return state, ui


def test_new_ui_seats_the_view_where_it_is_told():
    _, ui = _seated(2)
    assert ui.human_id == 2


def test_fog_is_computed_from_the_seated_players_own_territory():
    """The seat's fog must be its own, not seat 1's. With fog on, two different
    seats see different boards from the same position."""
    state, ui = _seated(2)
    config_sight, config_scout = config.FOG_SIGHT, config.FOG_SCOUT
    config.FOG_SIGHT, config.FOG_SCOUT = 1, 1
    try:
        app.refresh_fog(state, ui)
        theirs, _ = fog.observe(state, 2, 1, 1)
        seat_one, _ = fog.observe(state, 1, 1, 1)
        assert ui.visible == theirs
        assert theirs != seat_one, "the two seats must not see the same board"
    finally:
        config.FOG_SIGHT, config.FOG_SCOUT = config_sight, config_scout


def test_a_whole_game_plays_out_from_seat_two():
    """The end-to-end claim: drive the shell's own turn loop from a seat that is
    not 1, to a decided result, and have nothing special-case it."""
    state, ui = _seated(2)
    ai.load_models()
    for _ in range(400):
        if state.winner is not None:
            break
        ui.pending.extend(ai.decide(state, ui.human_id))   # the seat plays itself
        app.resolve_turn(state, ui)
    assert state.winner is not None, "the match must actually decide"
    assert ui.hand_turns > 0


def test_orders_queued_on_the_ui_belong_to_its_own_seat():
    """`Ui` builds orders with `self.human_id`, so a reseated one must not be
    able to launch seat 1's ships."""
    state, ui = _seated(3)
    mine = [sid for sid, s in state.systems.items() if s.owner_id == 3]
    src = mine[0]
    dest = next(iter(state.systems[src].neighbors))
    ui.pending.append(Order(ui.human_id, src, dest, 1))
    app.resolve_turn(state, ui)
    assert all(f.owner_id == 3 for f in state.fleets if f.source_id == src)


def test_a_seat_two_match_resumes_and_rebuilds_that_seats_fog(tmp_path):
    """Resume rebuilds fog across every replayed turn — for the seat that was
    playing, which used to be hardcoded to 1."""
    state, ui = _seated(2)
    log = replay.new_log(app.Settings(mode="random", nodes=16, players=3), state.seed)
    log.path = tmp_path / "game.json"
    ai.load_models()
    for _ in range(12):
        if state.winner is not None:
            break
        ui.pending.extend(ai.decide(state, ui.human_id))
        app.resolve_turn(state, ui, log)

    resumed, resumed_ui = app.resume_game(log, app.Settings(), seat=2)
    assert resumed_ui.human_id == 2
    assert resumed.turn == state.turn
    assert resumed_ui.visible == ui.visible, "the resumed seat sees what it saw"

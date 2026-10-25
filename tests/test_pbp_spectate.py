"""Watching a play-by-post match from no seat: the room's TV.

A spectator link (`#spectate=<match>`) opens a shared match at neutral's id
(`pbp.SPECTATOR`): the whole board, nobody's orders, and no token, so nothing
it does can reach the endpoint as a write. It is only offered, and only opens,
with fog off (`pbp.Match.watchable`): on a fogged map the shared screen would
show every player what none of them should see.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

from starconquest import main as app
from starconquest import ai, config, engine, pbp, replay, viewstate, webstore
from starconquest import input as game_input
from starconquest.settings import Settings

pygame.init()
pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))

MATCH = "00112233445566ff"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    return tmp_path


def _settings(**over) -> Settings:
    return Settings(mode="random", players=3, nodes=16, seed=7, **over)


def _payload(spectators="lobby", settings=None, **over):
    payload = {
        "match_id": MATCH,
        "settings_json": (settings or _settings()).to_dict(),
        "seed": 7,
        "seats": [1, 2],
        "turn": 0,
        "submitted": [],
        "turns": [],
        "log": "",
        "finished": False,
        "rules_version": engine.RULES_VERSION,
        "deadline_hours": 48,
        "turn_opened_at": "2026-09-22T12:00:00+00:00",
        "spectators": spectators,
    }
    payload.update(over)
    return payload


def _watching(**over):
    match = pbp.match_from_dict(_payload(**over))
    state, ui, log = app.open_match(match, pbp.spectator(MATCH), Settings())
    return match, state, ui, log


# --------------------------------------------------------------------------- #
# The link, and what a match says about spectators
# --------------------------------------------------------------------------- #
def test_a_spectate_link_carries_the_match_and_nothing_else():
    fragment = pbp.spectate_fragment(MATCH)
    assert fragment == f"spectate={MATCH}"
    assert pbp.parse_spectate_link(fragment) == MATCH
    assert pbp.parse_link(fragment) is None, "a spectate link is never a seat link"
    assert pbp.parse_spectate_link(pbp.link_fragment(MATCH, TOKEN)) is None
    assert pbp.parse_spectate_link("spectate=nothex") is None
    # A seat link that lost its token is refused as a seat link, not opened as
    # somebody's spectator view.
    assert pbp.parse_link(f"pbp={MATCH}") is None
    assert pbp.parse_spectate_link(f"pbp={MATCH}") is None


@pytest.mark.parametrize("raw, read", [("lobby", "lobby"), ("link", "link"),
                                       ("none", "none"), ("all", "none"), (None, "none"),
                                       (3, "none")])
def test_who_may_spectate_reads_closed(raw, read):
    """Anything the build does not know is read as no spectators."""
    assert pbp.match_from_dict(_payload(spectators=raw)).spectators == read


def test_a_match_is_watchable_only_with_spectators_on_and_fog_off():
    assert pbp.match_from_dict(_payload("lobby")).watchable
    assert pbp.match_from_dict(_payload("link")).watchable
    assert not pbp.match_from_dict(_payload("none")).watchable
    fogged = _settings(fog_sight=config.FOG_ON_SIGHT, fog_scout=config.FOG_ON_SCOUT)
    assert not pbp.match_from_dict(_payload("lobby", settings=fogged)).watchable
    scouted = _settings(fog_scout=config.FOG_ON_SCOUT)
    assert not pbp.match_from_dict(_payload("lobby", settings=scouted)).watchable


def test_a_match_that_may_not_be_watched_says_why():
    assert app.pbp_unwatchable(pbp.match_from_dict(_payload("lobby"))) == ""
    assert (app.pbp_unwatchable(pbp.match_from_dict(_payload("none")))
            == app.PBP_NO_SPECTATORS_MSG)
    fogged = _settings(fog_sight=config.FOG_ON_SIGHT, fog_scout=config.FOG_ON_SCOUT)
    assert (app.pbp_unwatchable(pbp.match_from_dict(_payload("link", settings=fogged)))
            == app.PBP_FOGGED_MSG)


# --------------------------------------------------------------------------- #
# Creating a match that takes spectators
# --------------------------------------------------------------------------- #
def _sent(monkeypatch) -> dict:
    sent: dict = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    return sent


def test_a_new_match_carries_who_may_spectate(monkeypatch):
    sent = _sent(monkeypatch)
    app.pbp_open(_settings(), seed=1, spectators="lobby")
    assert sent["spectators"] == "lobby"
    app.pbp_open(_settings(), seed=1)
    assert sent["spectators"] == "none", "off unless asked for"


def test_a_fogged_match_is_never_opened_to_spectators(monkeypatch):
    sent = _sent(monkeypatch)
    fogged = _settings(fog_sight=config.FOG_ON_SIGHT, fog_scout=config.FOG_ON_SCOUT)
    app.pbp_open(fogged, seed=1, spectators="link")
    assert sent["spectators"] == "none"
    app.pbp_open(_settings(), seed=1, spectators="everyone")
    assert sent["spectators"] == "none"


def test_the_spectate_link_handed_out_is_the_match_alone():
    link = app.pbp_spectate_link(MATCH)
    assert link.endswith(pbp.spectate_fragment(MATCH))
    assert TOKEN not in link


# --------------------------------------------------------------------------- #
# Watching
# --------------------------------------------------------------------------- #
def test_a_spectator_sits_at_no_seat_and_sees_the_whole_board():
    _, state, ui, _ = _watching()
    assert ui.spectating and ui.in_pbp
    assert ui.human_id == pbp.SPECTATOR
    assert ui.visible == set(state.systems)
    # The roster is still the truth about who the people are.
    assert {pid for pid, p in state.players.items() if p.is_human} == {1, 2}


def test_a_seat_is_never_spectating():
    match = pbp.match_from_dict(_payload())
    _, ui, _ = app.open_match(match, pbp.Seat(MATCH, 1, TOKEN), Settings())
    assert not ui.spectating


def test_a_spectator_can_write_nothing():
    """No token, so every write refuses it before it is sent."""
    seat = pbp.spectator(MATCH)
    assert seat.spectating and not seat.valid()
    _, state, _, log = _watching()
    assert pbp.submit(seat, 0, []) is None
    assert pbp.send_lapse(seat, 0, {2: []}) is None
    assert pbp.send_resolved(seat, 0, log, "", False) is None
    assert not pbp.remember(seat)


def test_a_spectator_has_no_turn_to_end_and_nothing_to_send():
    _, state, ui, _ = _watching()
    enter = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r")
    assert game_input.handle_event(enter, state, ui) is None
    assert not ui.can_route(state)
    # A press on a system selects nothing: nothing on the board is ours.
    sid = next(iter(state.systems))
    pos = ui.view.to_screen(state.systems[sid].pos)
    game_input.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1), state, ui)
    assert ui.selected is None and not ui.pending


def test_a_spectator_steps_to_the_board_the_players_resolved():
    """Stepping applies the resolver's record, exactly as a seat's client does."""
    ai.load_models()
    complete = pbp.match_from_dict(_payload(submitted=[1, 2]))
    _elsewhere, log, digest = pbp.resolve(complete, ai.decide)
    _, state, ui, local = _watching()
    moved = pbp.match_from_dict(_payload(turn=1, log=pbp.shareable(log).encoded()))
    assert app.pbp_verdict(moved, state) == app.PBP_STEP
    app.resolve_turn(state, ui, local, Settings(), script=pbp.settled_turn(moved, 0))
    assert replay.digest_hex(state) == digest
    assert ui.visible == set(state.systems)


def _drawn(monkeypatch, state, ui) -> list[str]:
    from starconquest import render
    pygame.init()
    render._FONTS.clear()        # another file may have quit pygame
    drawn: list[str] = []
    monkeypatch.setattr(render, "_label_pill",
                        lambda surface, font, text, *a: drawn.append(text))
    real_text = render._text
    monkeypatch.setattr(render, "_text", lambda surface, font, text, *a, **k:
                        (drawn.append(text), real_text(surface, font, text, *a, **k))[1])
    render.draw(pygame.Surface((config.SCREEN_W, config.SCREEN_H)), state, ui)
    return drawn


def test_the_spectators_screen_says_who_the_turn_is_waiting_on(monkeypatch):
    match, state, ui, _ = _watching(submitted=[1])
    app.pbp_adopt(match, pbp.spectator(MATCH), ui)
    drawn = _drawn(monkeypatch, state, ui)
    assert any("Waiting for" in line and state.players[2].name in line for line in drawn)
    assert "Spectating" in drawn
    assert not any(line.startswith("You are") for line in drawn)
    assert ui.end_turn_rect[2] == 0, "no End Turn to press"
    assert ui.clear_button_rect[2] == 0, "nothing to clear"
    assert not any("send its garrison" in line for line in drawn), "no how-to-send help"


def test_the_invite_overlay_offers_the_spectate_link(monkeypatch):
    state, ui, _ = app.open_match(pbp.match_from_dict(_payload()),
                                  pbp.Seat(MATCH, 1, TOKEN), Settings())
    ui.pbp_invite = ((1, "seat-1-link"), (viewstate.SPECTATE_ROW, "watch-link"))
    drawn = _drawn(monkeypatch, state, ui)
    assert "Spectators" in drawn
    assert viewstate.SPECTATE_ROW in ui.pbp_invite_rects
    rect = ui.pbp_invite_rects[viewstate.SPECTATE_ROW]
    press = pygame.event.Event(pygame.MOUSEBUTTONDOWN,
                               pos=(rect[0] + rect[2] // 2, rect[1] + rect[3] // 2), button=1)
    assert game_input.handle_event(press, state, ui) == "pbp_copy_seat"
    assert ui.pbp_copy_seat == viewstate.SPECTATE_ROW

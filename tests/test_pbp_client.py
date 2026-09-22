"""Play-by-post: the shell half — opening a match, submitting, and stepping it on.

`test_pbp.py` pins the pure module: links, seats, and the determinism two clients
must agree on. This pins what `main` does with it, and the property that matters
most is at the join between the two: **the turn the shell plays onto a live board
must be the same turn another client rebuilds from scratch.** One is
`main.resolve_turn` with the wire's orders, the other is `pbp.resolve`, and they
are different code paths over the same inputs. If they ever disagree, the match
has silently forked and nothing on either screen would say so.

Pure/headless: `main` imports pygame, but nothing here draws.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

import main as app
from starconquest import config, engine, pbp, replay, webstore
from starconquest.model import Order
from starconquest.settings import Settings

pygame.init()
pygame.display.set_mode((config.SCREEN_W, config.SCREEN_H))

MATCH = "00112233445566ff"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Seat tokens, preferences and auto-saved logs all stay in the sandbox."""
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    return tmp_path


def _payload(turn=0, submitted=(), turns=(), seats=(1, 2), players=2, **over):
    """What `?action=state` hands back, as the endpoint really shapes it."""
    payload = {
        "match_id": MATCH,
        "settings_json": Settings(mode="random", players=players, nodes=16,
                                  seed=7).to_dict(),
        "seed": 7,
        "seats": list(seats),
        "turn": turn,
        "submitted": list(submitted),
        "turns": list(turns),
        "log": "",
        "finished": False,
        "rules_version": engine.RULES_VERSION,
        "deadline_hours": 48,
        "turn_opened_at": "2026-09-22T12:00:00+00:00",
    }
    payload.update(over)
    return payload


def _seat(seat: int = 1) -> pbp.Seat:
    return pbp.Seat(MATCH, seat, TOKEN)


def _opened(seat: int = 1, **over):
    """A match open on our screen, as `main` would have it after the first read."""
    match = pbp.match_from_dict(_payload(**over))
    state, ui, log = app.open_match(match, _seat(seat), Settings())
    return match, state, ui, log


def _a_move(state, seat: int, ships: int = 2):
    """One legal order out of a system ``seat`` holds, as an order row."""
    src, dst = _lane(state, seat)
    return {"turn": state.turn, "seat": seat,
            "orders_json": [{"src": src, "dst": dst, "ships": ships}]}


def _lane(state, seat: int) -> tuple[int, int]:
    """A system ``seat`` holds and somewhere it can send ships. At the opening a
    seat holds exactly its homeworld, which is why these tests reach for a lane
    rather than for a second system."""
    src = next(sid for sid, s in state.systems.items() if s.owner_id == seat)
    return src, next(iter(state.systems[src].neighbors))


# --------------------------------------------------------------------------- #
# Opening a match
# --------------------------------------------------------------------------- #
def test_opening_a_match_sits_us_at_our_own_seat():
    _, state, ui, _ = _opened(seat=2)
    assert ui.human_id == 2
    assert ui.in_pbp and ui.pbp_match == MATCH
    # ...and the fog on screen is that seat's, not pid 1's.
    assert any(state.systems[sid].owner_id == 2 for sid in ui.visible)


def test_every_seat_in_the_roster_is_a_person_and_nobody_else_is():
    """The roster is the whole truth about who the people are.

    It decides which seats `_collect_orders` asks `decide` about, so it has to be
    the same on every client — and `replay.reconstruct`, which `open_match` goes
    through, restores the single-seat claim a solo game records and knows nothing
    of a match seated at 2 and 3.
    """
    _, state, _, _ = _opened(seat=2, seats=(2, 3), players=3)
    assert [pid for pid, p in state.players.items() if p.is_human] == [2, 3]


def test_a_shared_match_is_never_ours_to_post():
    """A leaderboard score measures a person against a map. Beating two friends
    to the same map says nothing about that setup, so it is not offered."""
    _, state, ui, _ = _opened()
    state.winner = ui.human_id
    ui.hand_turns = 5
    assert not ui.can_post(state)
    ui.pbp_match = ""          # ...and the very same result in a game of our own
    assert ui.can_post(state)


def test_a_match_opens_on_the_turn_the_endpoint_says_it_is_on():
    rows = [_a_move(pbp.rebuild(pbp.match_from_dict(_payload()))[0], 1)]
    _, state, _, log = _opened(turn=1, turns=rows)
    assert state.turn == 1
    assert log.turn_count == 1


# --------------------------------------------------------------------------- #
# The join: the shell's turn and another client's must be the same turn
# --------------------------------------------------------------------------- #
def test_the_shell_steps_a_turn_to_the_board_another_client_rebuilds():
    """The property the whole thin-server design rests on, at the one seam this
    branch adds: `main.resolve_turn` advances a board already on screen, while
    `pbp.resolve` rebuilds from the opening. Different code, same inputs, and a
    disagreement is a forked match neither screen would mention."""
    _, state, ui, log = _opened()
    rows = [_a_move(state, 1, 3), _a_move(state, 2, 2)]
    match = pbp.match_from_dict(_payload(turn=0, submitted=[1, 2], turns=rows))

    app.resolve_turn(state, ui, log, Settings(),
                     seat_orders=match.orders_for_turn(0))
    elsewhere, _, digest = pbp.resolve(match)

    assert replay.digest_hex(state) == digest
    assert state.turn == elsewhere.turn == 1


def test_a_stepped_turn_leaves_a_log_that_reconstructs_to_the_same_board():
    """...and the log it uploads is an ordinary one: a play-by-post match resumes,
    reviews and verifies with nothing taught about it."""
    _, state, ui, log = _opened()
    match = pbp.match_from_dict(
        _payload(turn=0, submitted=[1, 2], turns=[_a_move(state, 2)]))
    app.resolve_turn(state, ui, log, Settings(),
                     seat_orders=match.orders_for_turn(0))
    rebuilt, _ = replay.reconstruct(log)
    assert replay.digest_hex(rebuilt) == replay.digest_hex(state)


def test_the_wires_orders_are_the_only_ones_that_reach_the_board():
    """Our own orders were submitted a turn ago and come back with everybody
    else's. Queuing more while waiting must not smuggle them into the turn — the
    board would then disagree with every other client's."""
    _, state, ui, log = _opened()
    row = _a_move(state, 1, 3)
    src, dst = _lane(state, 1)
    ui.pending.append(Order(1, src, dst, 1))   # queued after submitting, never sent

    match = pbp.match_from_dict(_payload(turn=0, submitted=[1, 2], turns=[row]))
    app.resolve_turn(state, ui, log, Settings(),
                     seat_orders=match.orders_for_turn(0))
    ours = [f for f in state.fleets if f.owner_id == 1]
    assert [f.ships for f in ours] == [3], "the wire's order, and only it"
    assert not ui.pending


# --------------------------------------------------------------------------- #
# What a read of the match asks the client to do
# --------------------------------------------------------------------------- #
def test_a_match_a_turn_ahead_is_stepped_onto_the_board():
    _, state, _, _ = _opened()
    assert app.pbp_verdict(pbp.match_from_dict(_payload(turn=1)), state) == app.PBP_STEP


def test_a_match_further_ahead_is_rebuilt():
    _, state, _, _ = _opened()
    assert app.pbp_verdict(pbp.match_from_dict(_payload(turn=4)), state) == app.PBP_REBUILD


def test_a_complete_turn_is_resolved_by_whoever_notices():
    _, state, _, _ = _opened()
    match = pbp.match_from_dict(_payload(turn=0, submitted=[1, 2]))
    assert app.pbp_verdict(match, state) == app.PBP_RESOLVE


def test_an_incomplete_turn_is_waited_on():
    _, state, _, _ = _opened()
    match = pbp.match_from_dict(_payload(turn=0, submitted=[1]))
    assert app.pbp_verdict(match, state) == app.PBP_WAIT


def test_a_match_behind_us_is_simply_our_own_resolve_not_landing_yet():
    _, state, _, _ = _opened(turn=2, turns=[])
    assert app.pbp_verdict(pbp.match_from_dict(_payload(turn=1)), state) == app.PBP_WAIT


def test_a_finished_match_is_never_resolved_again():
    _, state, _, _ = _opened()
    match = pbp.match_from_dict(_payload(turn=0, submitted=[1, 2], finished=True))
    assert app.pbp_verdict(match, state) == app.PBP_WAIT


# --------------------------------------------------------------------------- #
# Submitting
# --------------------------------------------------------------------------- #
def test_submitting_sends_queued_orders_and_standing_rules_together(monkeypatch):
    """Both halves of a turn, exactly as a single-player End Turn collects them —
    a standing rule that fired locally but was never sent would leave our board
    describing a turn nobody else played."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    _, state, ui, _ = _opened()
    src, dst = _lane(state, 1)
    # A seat holds only its homeworld at the opening, so the standing rule needs
    # somewhere of its own to fire from: take the neighbour, as a turn of play
    # would have.
    other = dst
    state.systems[other].owner_id, state.systems[other].ships = 1, 4
    ui.pending.append(Order(1, src, dst, 1))
    ui.auto_forward[other] = (src, 0)

    app.pbp_send(state, ui, _seat())
    assert {o["src"] for o in sent["orders"]} == {src, other}
    assert sent["turn"] == state.turn


def test_the_board_goes_on_hold_the_moment_the_press_lands(monkeypatch):
    """Not when the reply comes back: a round trip is long enough to get another
    order in, and an order queued after a submission would never be sent."""
    monkeypatch.setattr(pbp, "call", lambda *a, **k: object())
    _, state, ui, _ = _opened()
    assert app.pbp_send(state, ui, _seat()) is not None
    assert ui.pbp_submitted and ui.awaiting_others(state)


def test_a_refused_submission_hands_the_turn_back():
    """Otherwise the player sits in front of a veil waiting on a turn they never
    actually entered."""
    _, state, ui, _ = _opened()
    ui.pbp_submitted = True
    app.pbp_heard(ui, pbp.REFUSED, "")
    assert not ui.pbp_submitted
    assert ui.pbp_msg == app.PBP_REFUSED_MSG


def test_a_submission_that_landed_says_nothing():
    _, state, ui, _ = _opened()
    ui.pbp_submitted, ui.pbp_msg = True, "Sending..."
    app.pbp_heard(ui, pbp.OK, '{"seat": 1, "turn": 0, "waiting": [2]}')
    assert ui.pbp_submitted and ui.pbp_msg == ""


def test_the_endpoints_own_words_are_relayed_rather_than_translated():
    """"stale turn", "already submitted", "turn is not ready" each name a real
    state of the match, and the read that follows is about to show it."""
    assert app.pbp_trouble(pbp.ERROR, '{"error": "stale turn", "turn": 4}') == "stale turn"
    assert app.pbp_trouble(pbp.MISSING, "") == app.PBP_MISSING_MSG
    assert app.pbp_trouble(pbp.ERROR, "<!doctype html>") == app.PBP_UNREACHABLE_MSG
    assert app.pbp_trouble(pbp.ERROR, "") == app.PBP_UNREACHABLE_MSG


# --------------------------------------------------------------------------- #
# What the endpoint says about the live turn, and what the overlay makes of it
# --------------------------------------------------------------------------- #
def test_the_overlay_is_built_from_what_the_endpoint_says():
    _, state, ui, _ = _opened()
    app.pbp_adopt(pbp.match_from_dict(_payload(submitted=[1], seats=(1, 2, 3),
                                               players=3)), _seat(), ui)
    assert ui.pbp_submitted
    assert ui.pbp_waiting == (2, 3)


def test_whether_we_submitted_is_the_endpoints_answer_and_never_ours():
    """A submission that failed on the way out must not leave the board held for
    a turn nobody is waiting on."""
    _, state, ui, _ = _opened()
    ui.pbp_submitted = True
    app.pbp_adopt(pbp.match_from_dict(_payload(submitted=[2])), _seat(), ui)
    assert not ui.pbp_submitted


def test_a_resolved_turn_hands_the_board_straight_back():
    _, state, ui, _ = _opened()
    ui.pbp_submitted, ui.pbp_waiting, ui.pbp_msg = True, (2,), "waiting"
    app.pbp_opened(ui)
    assert not ui.awaiting_others(state)
    assert ui.pbp_waiting == () and ui.pbp_msg == ""

"""The play-by-post client machine, driven through the worker host.

`hostio` stands in for the network: each call the client starts is an ``http``
effect, and the test answers it as the endpoint would. The helpers that decide
what a reply means have their own tests (`test_pbp_client`, `test_pbp_spectate`);
these check the machine that sequences them.
"""

from __future__ import annotations

import json

import pytest

from starconquest import ai, engine, hostio, pbp
from starconquest.app import PBP_POLL_MS, PBP_UNREADABLE_MSG
from starconquest.pbpclient import PbpClient
from starconquest.settings import Settings

MATCH = "00112233445566ff"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.fixture(autouse=True)
def worker():
    hostio.boot({"origin": "https://star-conquest.netlify.app", "hostname": "star-conquest.netlify.app",
                 "pathname": "/next/", "hash": ""}, {})
    ai.load_models()
    yield
    hostio.reset()


def _payload(**over):
    payload = {
        "match_id": MATCH,
        "settings_json": Settings(mode="random", players=2, nodes=16, seed=7).to_dict(),
        "seed": 7, "seats": [1, 2], "turn": 0, "submitted": [], "turns": [], "log": "",
        "finished": False, "rules_version": engine.RULES_VERSION, "deadline_hours": 48,
        "turn_opened_at": "2026-09-22T12:00:00+00:00",
    }
    payload.update(over)
    return payload


def _calls() -> list[dict]:
    return [e for e in hostio.drain() if e["t"] == "http"]


def _answer(effect: dict, state: str, body) -> None:
    assert hostio.reply(effect["req"], state, body if isinstance(body, str) else json.dumps(body))


def _opened(client: PbpClient, seat: int = 1):
    """Follow a seat link to an open match: identify, then the first read."""
    assert client.open_link((MATCH, TOKEN), None) == ("Opening match...", True)
    [ident] = _calls()
    assert "action=seat" in ident["url"]
    _answer(ident, pbp.OK, {"seat": seat})
    assert client.step(0, None, None, None, Settings(), filming=False).game is None
    [read] = _calls()
    assert "action=state" in read["url"] and MATCH in read["url"]
    _answer(read, pbp.OK, _payload())
    out = client.step(0, None, None, None, Settings(), filming=False)
    assert out.opened and out.game is not None and out.seed == 7
    return out.game


def test_a_seat_link_identifies_once_then_opens_the_match():
    client = PbpClient()
    state, ui, _log = _opened(client)
    assert ui.in_pbp and ui.human_id == 1 and state.turn == 0
    again = PbpClient()
    assert again.open_link((MATCH, TOKEN), None) == ("Opening match...", True)
    [read] = _calls()
    assert "action=state" in read["url"]   # remembered: no second identify


def test_a_spectate_link_reads_straight_away_and_holds_no_seat():
    client = PbpClient()
    assert client.open_link(None, MATCH) == ("Opening match...", True)
    [read] = _calls()
    _answer(read, pbp.OK, _payload())
    out = client.step(0, None, None, None, Settings(), filming=False)
    assert out.opened and out.game is not None
    assert out.game[1].spectating


def test_a_first_read_that_fails_says_why_and_lets_the_link_go():
    client = PbpClient()
    client.open_link(None, MATCH)
    [read] = _calls()
    _answer(read, pbp.OK, "<html>")
    out = client.step(0, None, None, None, Settings(), filming=False)
    assert out.status == (PBP_UNREADABLE_MSG, False) and out.game is None
    assert client.invite is None and client.seat is None


def test_reads_wait_out_a_film_and_resume_on_the_cadence():
    client = PbpClient()
    state, ui, log = _opened(client)
    assert _calls() == []
    client.step(PBP_POLL_MS - 1, state, ui, log, Settings(), filming=False)
    assert _calls() == []
    client.step(1, state, ui, log, Settings(), filming=False)
    [read] = _calls()
    _answer(read, pbp.OK, _payload())
    client.step(0, state, ui, log, Settings(), filming=True)
    assert client.poll is not None          # the answer waits for the film to land
    client.step(0, state, ui, log, Settings(), filming=False)
    assert client.poll is None


def test_ending_a_turn_sends_our_orders_and_drops_the_read_in_flight():
    client = PbpClient()
    state, ui, log = _opened(client)
    client.step(PBP_POLL_MS, state, ui, log, Settings(), filming=False)
    assert client.poll is not None
    hostio.drain()
    client.send(state, ui)
    [submit] = _calls()
    assert "action=submit" in submit["url"] and client.poll is None and client.write is not None
    client.send(state, ui)
    assert _calls() == []                   # one submission at a time


def test_leaving_the_match_forgets_everything_in_flight():
    client = PbpClient()
    state, ui, log = _opened(client)
    ui.pbp_match = ""
    client.step(0, state, ui, log, Settings(), filming=False)
    assert (client.seat, client.poll, client.write, client.ident) == (None, None, None, None)

"""Play-by-post: the client half — links, seats, rebuilding and resolving.

Pure/headless, and nothing here touches the network: what is worth pinning is
the part that decides something. Above all **determinism** — two clients must
resolve the same turn to the same board, or the whole thin-server design is
wrong, and that is a property this suite can check exactly rather than hope for.
"""

from __future__ import annotations

import pytest

from starconquest import engine, pbp, replay, webstore
from starconquest.model import Order
from starconquest.settings import Settings


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Keep seat tokens out of the repo's own kv.json (mirrors test_webstore)."""
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")
    return tmp_path / "kv.json"


MATCH = "00112233445566ff"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _state_payload(turn=0, submitted=(), turns=(), seats=(1, 2), **over):
    """What `?action=state` hands back, as the endpoint really shapes it."""
    payload = {
        "match_id": MATCH,
        "settings_json": Settings(mode="random", players=2, nodes=16, seed=7).to_dict(),
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


# --------------------------------------------------------------------------- #
# The link
# --------------------------------------------------------------------------- #
def test_a_seat_link_round_trips():
    fragment = pbp.link_fragment(MATCH, TOKEN)
    assert pbp.parse_link(fragment) == (MATCH, TOKEN)


@pytest.mark.parametrize("fragment", [
    "log=00112233445566ff",                 # the other kind of link
    "pbp=notamatchid:" + TOKEN,
    "pbp=" + MATCH + ":short",
    "pbp=" + MATCH,                          # no token at all
    "pbp=",
    "",
])
def test_a_fragment_that_is_not_a_seat_link_is_not_one(fragment):
    assert pbp.parse_link(fragment) is None


def test_the_seat_is_not_in_the_link():
    """The endpoint knows which seat a token belongs to, so putting it in the
    link too would be a second claim to keep in step with the first."""
    assert "seat" not in pbp.link_fragment(MATCH, TOKEN)


# --------------------------------------------------------------------------- #
# Remembering a seat
# --------------------------------------------------------------------------- #
def test_a_seat_is_remembered_and_returned():
    assert pbp.remember(pbp.Seat(MATCH, 2, TOKEN))
    seat = pbp.seat_for(MATCH)
    assert (seat.match_id, seat.seat, seat.token) == (MATCH, 2, TOKEN)


def test_a_seat_in_another_match_is_not_confused_with_ours():
    other = "ffeeddccbbaa9988"
    pbp.remember(pbp.Seat(MATCH, 1, TOKEN))
    pbp.remember(pbp.Seat(other, 2, "b" * 32))
    assert pbp.seat_for(MATCH).seat == 1
    assert pbp.seat_for(other).seat == 2


def test_forgetting_a_match_drops_only_that_one():
    other = "ffeeddccbbaa9988"
    pbp.remember(pbp.Seat(MATCH, 1, TOKEN))
    pbp.remember(pbp.Seat(other, 2, "b" * 32))
    assert pbp.forget(MATCH)
    assert pbp.seat_for(MATCH) is None
    assert pbp.seat_for(other) is not None


def test_a_malformed_seat_is_never_stored():
    assert not pbp.remember(pbp.Seat(MATCH, 2, "nope"))
    assert not pbp.remember(pbp.Seat("bad", 2, TOKEN))
    assert pbp.seat_for(MATCH) is None


def test_a_corrupt_store_reads_as_no_seats():
    """Storage is a nicety, never load-bearing — the rule the rest of the game's
    web bridges follow."""
    webstore.set(pbp.WEB_PBP_SEATS_KEY, "{not json")
    assert pbp.remembered() == {}
    assert pbp.seat_for(MATCH) is None


# --------------------------------------------------------------------------- #
# Parsing what the endpoint says
# --------------------------------------------------------------------------- #
def test_a_match_parses_with_its_roster_and_live_turn():
    match = pbp.match_from_dict(_state_payload(turn=3, submitted=[1]))
    assert match.match_id == MATCH
    assert match.seats == [1, 2]
    assert match.turn == 3
    assert match.submitted == [1]


@pytest.mark.parametrize("payload", [
    None, {}, "nope",
    _state_payload(match_id="bad"),
    _state_payload(settings_json=None),
    _state_payload(seats=[]),
])
def test_something_that_cannot_describe_a_match_is_refused(payload):
    assert pbp.match_from_dict(payload) is None


def test_waiting_is_whoever_has_not_sent_orders():
    match = pbp.match_from_dict(_state_payload(seats=[1, 2, 3], submitted=[2]))
    assert match.waiting == [1, 3]
    assert not match.ready
    assert match.has_submitted(2) and not match.has_submitted(1)


def test_a_turn_is_ready_only_when_every_seat_is_in():
    assert pbp.match_from_dict(_state_payload(submitted=[1, 2])).ready
    assert not pbp.match_from_dict(_state_payload(submitted=[1])).ready


def test_a_finished_match_is_never_ready():
    match = pbp.match_from_dict(_state_payload(submitted=[1, 2], finished=True))
    assert not match.ready


def test_orders_are_stamped_with_the_seat_they_were_filed_under():
    """There is no owner on the wire — the seat is the row's own, which is what
    makes a foreign order inexpressible rather than merely filtered."""
    match = pbp.match_from_dict(_state_payload(turn=1, turns=[
        {"turn": 0, "seat": 2, "orders_json": [{"src": 5, "dst": 6, "ships": 4}]},
    ]))
    orders = match.orders_for_turn(0)
    assert [(o.owner_id, o.source_id, o.dest_id, o.ships) for o in orders[2]] \
        == [(2, 5, 6, 4)]


def test_a_malformed_order_row_is_dropped_not_coerced():
    match = pbp.match_from_dict(_state_payload(turn=1, turns=[
        {"turn": 0, "seat": 1, "orders_json": [
            {"src": 1, "dst": 2, "ships": 3},
            {"src": "x", "dst": 2, "ships": 3},
            {"dst": 2, "ships": 3},
        ]},
    ]))
    assert len(match.orders_for_turn(0)[1]) == 1


# --------------------------------------------------------------------------- #
# Rebuilding and resolving — the determinism the design rests on
# --------------------------------------------------------------------------- #
def test_two_clients_resolve_a_turn_to_the_same_board():
    """The whole thin-server design in one assertion. The server stores no board
    and computes no dice; every client rebuilds from the same inputs, so they
    must land on the same position or the match has silently forked."""
    match = pbp.match_from_dict(_state_payload(submitted=[1, 2]))
    _, _, one = pbp.resolve(match)
    _, _, two = pbp.resolve(match)
    assert one == two


def test_the_digest_notices_a_board_that_differs():
    """...and the tripwire is worth having only if it actually trips."""
    a = pbp.match_from_dict(_state_payload(submitted=[1, 2]))
    b = pbp.match_from_dict(_state_payload(submitted=[1, 2], seed=8))
    _, _, one = pbp.resolve(a)
    _, _, two = pbp.resolve(b)
    assert one != two


def test_a_rebuilt_board_replays_every_resolved_turn():
    match = pbp.match_from_dict(_state_payload(turn=0, submitted=[1, 2]))
    state, _, _ = pbp.resolve(match)
    assert state.turn == 1


def test_resolving_produces_a_log_that_reconstructs_to_the_same_board():
    """A play-by-post match is an ordinary GameLog — which is what lets it
    resume, review and verify with nothing taught about it."""
    match = pbp.match_from_dict(_state_payload(submitted=[1, 2]))
    _state, log, digest = pbp.resolve(match)
    rebuilt, _ = replay.reconstruct(log)
    assert replay.digest_hex(rebuilt) == digest


def test_a_seats_orders_reach_the_board_it_resolves_to():
    """End to end: a real order, submitted by a real seat, moves real ships."""
    base = pbp.match_from_dict(_state_payload())
    state, _ = pbp.rebuild(base)
    mine = [sid for sid, s in state.systems.items() if s.owner_id == 1]
    src = mine[0]
    dest = next(iter(state.systems[src].neighbors))
    match = pbp.match_from_dict(_state_payload(submitted=[1, 2], turns=[
        {"turn": 0, "seat": 1,
         "orders_json": [{"src": src, "dst": dest, "ships": 2}]},
    ]))
    # The live turn's own orders come from `turns` rows filed at `match.turn`.
    resolved, _, _ = pbp.resolve(match)
    assert any(f.owner_id == 1 and f.source_id == src for f in resolved.fleets)


# --------------------------------------------------------------------------- #
# Endpoint plumbing
# --------------------------------------------------------------------------- #
def test_every_endpoint_switches_itself_off_without_an_origin(monkeypatch):
    """Blank origin disables every leaderboard feature — the rule `paths`
    documents, and play-by-post is no exception."""
    monkeypatch.setattr(pbp.webstore, "leaderboard_origin", lambda: "")
    assert pbp.endpoint("state") == ""
    assert not pbp.configured()


def test_an_endpoint_names_its_action(monkeypatch):
    monkeypatch.setattr(pbp.webstore, "leaderboard_origin", lambda: "https://board")
    assert pbp.endpoint("submit").endswith("/api/pbp?action=submit")


# --------------------------------------------------------------------------- #
# The live turn's orders (regression)
# --------------------------------------------------------------------------- #
def test_resolving_applies_the_live_turns_orders():
    """Found in a live match, and the reason the coherence digest exists.

    `?action=state` used to send only *resolved* turns, so a client resolving the
    live one applied an empty order set and played the turn as though nobody had
    moved. Two clients agreed with each other perfectly — both being equally
    wrong — and only comparing a rebuild against the *stored log* showed it.
    """
    base = pbp.match_from_dict(_state_payload())
    state, _ = pbp.rebuild(base)
    src = next(sid for sid, s in state.systems.items() if s.owner_id == 1)
    dest = next(iter(state.systems[src].neighbors))

    match = pbp.match_from_dict(_state_payload(turn=0, submitted=[1, 2], turns=[
        {"turn": 0, "seat": 1, "orders_json": [{"src": src, "dst": dest, "ships": 3}]},
    ]))
    resolved, log, _ = pbp.resolve(match)
    assert any(f.owner_id == 1 and f.source_id == src for f in resolved.fleets), \
        "the live turn's order must actually launch"
    assert len(log.orders_for(0)) == 1, "...and be recorded in the log it uploads"


def test_a_resolved_log_and_a_fresh_rebuild_land_on_the_same_board():
    """The two paths to a position must agree: replaying the uploaded log, and
    rebuilding from the stored order rows. They diverging is exactly the bug
    above, and is what a real match caught."""
    base = pbp.match_from_dict(_state_payload())
    state, _ = pbp.rebuild(base)
    src = next(sid for sid, s in state.systems.items() if s.owner_id == 2)
    dest = next(iter(state.systems[src].neighbors))
    rows = [{"turn": 0, "seat": 2, "orders_json": [{"src": src, "dst": dest, "ships": 2}]}]

    _resolved, log, digest = pbp.resolve(
        pbp.match_from_dict(_state_payload(turn=0, submitted=[1, 2], turns=rows)))
    # ...and the next client, seeing turn 1 with that turn now settled.
    rebuilt, _ = pbp.rebuild(pbp.match_from_dict(
        _state_payload(turn=1, turns=rows, log=pbp.shareable(log).encoded())))
    assert replay.digest_hex(rebuilt) == digest
    assert replay.digest_hex(replay.reconstruct(log)[0]) == digest


# --------------------------------------------------------------------------- #
# The wire (both backends intercepted; nothing here touches a network)
# --------------------------------------------------------------------------- #
@pytest.fixture
def on_a_board(monkeypatch):
    """An origin to talk to, without one being configured for real."""
    monkeypatch.setattr(pbp.webstore, "leaderboard_origin", lambda: "https://board")


@pytest.fixture
def sent(monkeypatch):
    """Capture what the desktop backend would put on the wire."""
    calls = []

    def fake(url, body):
        calls.append((url, body))
        return pbp.Request(web=False)

    monkeypatch.setattr(pbp, "_call_desktop", fake)
    monkeypatch.setattr(pbp, "is_web", lambda: False)
    return calls


def test_a_submission_names_its_match_seat_and_turn(on_a_board, sent):
    seat = pbp.Seat(MATCH, 2, TOKEN)
    pbp.submit(seat, 3, [Order(2, 5, 6, 4)])
    url, body = sent[0]
    assert "action=submit" in url
    payload = __import__("json").loads(body)
    assert payload["match_id"] == MATCH
    assert payload["token"] == TOKEN
    assert payload["turn"] == 3


def test_a_submitted_order_carries_no_owner(on_a_board, sent):
    """The endpoint stamps the seat from the token, so an owner on the wire would
    be a second claim to keep in step — and the thing that makes a foreign order
    inexpressible rather than merely filtered."""
    pbp.submit(pbp.Seat(MATCH, 2, TOKEN), 0, [Order(2, 5, 6, 4)])
    payload = __import__("json").loads(sent[0][1])
    assert payload["orders"] == [{"src": 5, "dst": 6, "ships": 4}]
    assert "owner" not in payload["orders"][0]


def test_nothing_is_sent_for_a_seat_we_do_not_really_hold(on_a_board, sent):
    assert pbp.submit(pbp.Seat(MATCH, 2, "not-a-token"), 0, []) is None
    assert pbp.send_resolved(pbp.Seat("bad", 1, TOKEN), 0,
                             replay.GameLog(seed=1, settings={}), "d", False) is None
    assert sent == []


def test_a_fetch_for_a_malformed_match_id_is_never_attempted(on_a_board, sent):
    assert pbp.fetch_state("not-an-id") is None
    assert sent == []


def test_nothing_is_attempted_with_no_endpoint(monkeypatch, sent):
    monkeypatch.setattr(pbp.webstore, "leaderboard_origin", lambda: "")
    assert pbp.fetch_state(MATCH) is None
    assert pbp.submit(pbp.Seat(MATCH, 1, TOKEN), 0, []) is None
    assert sent == []


def test_a_poll_answers_pending_until_the_thread_lands():
    request = pbp.Request(web=False)
    assert request.poll() == (pbp.PENDING, "")
    request._result.append((pbp.OK, "{}"))
    assert request.poll() == (pbp.OK, "{}")


def test_the_web_mailbox_is_cleared_once_collected(monkeypatch):
    """A stale `ok` from an earlier session would otherwise read as an instant
    success carrying somebody else's answer (the trap `share` documents)."""
    state_key, body_key = pbp._web_keys(3)
    monkeypatch.setattr(pbp.webstore, "get",
                        lambda key: pbp.OK if key == state_key else "body")
    cleared = []
    monkeypatch.setattr(pbp.webstore, "set",
                        lambda key, value: cleared.append((key, value)) or True)
    assert pbp.Request(web=True, slot=3).poll() == (pbp.OK, "body")
    assert (state_key, "") in cleared
    assert (body_key, "") in cleared


def test_two_calls_in_flight_each_collect_their_own_answer(on_a_board, monkeypatch):
    """A poll is routinely still out when End Turn submits. With one shared
    mailbox, whichever reply landed first was handed to whichever request was
    collected first — so the poll read the submission's reply, which names no
    match, and the player was told the match answered with something unreadable."""
    import re

    store: dict[str, str] = {}
    monkeypatch.setattr(pbp.webstore, "get", lambda key: store.get(key, ""))
    monkeypatch.setattr(pbp.webstore, "set",
                        lambda key, value: store.__setitem__(key, value) or True)
    keys = []

    class FakeWindow:
        @staticmethod
        def eval(js):
            keys.append(re.findall(r'localStorage\.setItem\("([^"]+)"', js)[0])

    monkeypatch.setattr(pbp, "is_web", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "platform",
                        type("P", (), {"window": FakeWindow}))
    poll = pbp.fetch_state(MATCH)
    submit = pbp.submit(pbp.Seat(MATCH, 1, TOKEN), 0, [])
    assert poll is not None and submit is not None
    assert keys[0] != keys[1], "two calls in flight must not share a mailbox"

    # The submission lands first, then the read: each fetch parks its reply
    # under the keys its own call named.
    for request, body in ((submit, '{"seat": 1}'), (poll, '{"match_id": "x"}')):
        state_key, body_key = pbp._web_keys(request._slot)
        store[body_key], store[state_key] = body, pbp.OK
    assert submit.poll() == (pbp.OK, '{"seat": 1}')
    assert poll.poll() == (pbp.OK, '{"match_id": "x"}')


def test_the_web_call_is_valid_javascript_with_a_rejection_handler(on_a_board, monkeypatch):
    """Inspected as the JS source string it really is — `test_share` does the
    same. A promise with no `.catch` surfaces in the console as a crash."""
    source = []

    class FakeWindow:
        @staticmethod
        def eval(js):
            source.append(js)

    fake = type("P", (), {"window": FakeWindow})
    monkeypatch.setattr(pbp, "is_web", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "platform", fake)
    pbp.submit(pbp.Seat(MATCH, 1, TOKEN), 0, [Order(1, 2, 3, 4)])
    js = source[0]
    assert ".catch(" in js, "an unhandled rejection reads as a crash"
    assert "'POST'" in js and "application/json" in js
    assert TOKEN in js


# --------------------------------------------------------------------------- #
# What the lobby is told, and the way back from it
# --------------------------------------------------------------------------- #
def test_a_bare_match_link_names_the_match_and_nothing_else():
    assert pbp.bare_match(f"pbp={MATCH}") == MATCH
    for fragment in (f"pbp={MATCH}:{TOKEN}", "pbp=nothex", f"log={MATCH}", ""):
        assert pbp.bare_match(fragment) == ""
    assert pbp.parse_link(f"pbp={MATCH}") is None, "a seat link still needs its token"


def test_the_lobby_hand_off_carries_ids_and_never_a_token():
    assert pbp.lobby_fragment() == ""
    other = "ffeeddccbbaa0099"
    pbp.remember(pbp.Seat(MATCH, 2, TOKEN))
    pbp.remember(pbp.Seat(other, 1, TOKEN))
    fragment = pbp.lobby_fragment()
    assert fragment == f"#mine={MATCH},{other}"
    assert TOKEN not in fragment
    assert pbp.lobby_fragment(limit=1) == f"#mine={other}"


def test_a_match_is_created_with_its_title_and_the_creators_name(monkeypatch):
    sent = {}
    monkeypatch.setattr(pbp, "call", lambda action, payload=None, **kw: sent.update(payload or {}))
    pbp.create(MATCH, Settings(players=2), 7, [1, 2], title="  Friday  ",
               name="x" * 40)
    assert sent["title"] == "Friday"
    assert sent["name"] == "x" * pbp.NAME_MAX


def test_a_match_stores_its_setup_pruned_and_rebuilds_the_same_one(monkeypatch):
    """The lobby lists non-default knobs by reading what is present, so the
    setup goes up in `token_dict` form — which `from_dict` reads back whole."""
    sent = {}
    monkeypatch.setattr(pbp, "call", lambda action, payload=None, **kw: sent.update(payload or {}))
    settings = Settings(players=3, nodes=20, combat_jitter=0.2, seed=5)
    pbp.create(MATCH, settings, 5, [1, 2, 3])
    assert sent["settings_json"] == settings.token_dict()
    assert "garrison_k" not in sent["settings_json"]
    assert Settings.from_dict(sent["settings_json"]) == settings


def test_the_final_resolve_reports_the_winner_and_no_other_does(monkeypatch):
    sent = {}
    monkeypatch.setattr(pbp, "call", lambda action, payload=None, **kw: sent.update(payload or {}))
    log = replay.GameLog(seed=7, settings=Settings().to_dict())
    pbp.send_resolved(pbp.Seat(MATCH, 1, TOKEN), 3, log, "d", False, 2)
    assert sent["winner"] is None
    pbp.send_resolved(pbp.Seat(MATCH, 1, TOKEN), 4, log, "d", True, 2)
    assert sent["winner"] == 2 and sent["finished"] is True


def test_a_matchs_title_and_names_are_read_off_the_wire():
    match = pbp.match_from_dict(_state_payload(title="Friday", names={"1": "Alice", "2": "", "x": "?"}))
    assert match is not None
    assert match.title == "Friday"
    assert match.names == {1: "Alice"}
    bare = pbp.match_from_dict(_state_payload())
    assert bare is not None and bare.title == "" and bare.names == {}

"""Play-by-post: the client half — links, seats, rebuilding and resolving.

Pure/headless, and nothing here touches the network: what is worth pinning is
the part that decides something. Above all **determinism** — two clients must
resolve the same turn to the same board, or the whole thin-server design is
wrong, and that is a property this suite can check exactly rather than hope for.
"""

from __future__ import annotations

import pytest

from starconquest import engine, mapgen, pbp, replay, webstore
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
    state, log, digest = pbp.resolve(match)
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

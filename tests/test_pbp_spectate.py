"""Watching a play-by-post match from no seat: the room's TV.

A spectator link (`#spectate=<match>`) opens a shared match at neutral's id
(`pbp.SPECTATOR`): the whole board, nobody's orders, and no token, so nothing
it does can reach the endpoint as a write. It is only offered, and only opens,
with fog off (`pbp.Match.watchable`): on a fogged map the shared screen would
show every player what none of them should see.
"""

from __future__ import annotations



import pytest

from starconquest import ai, app, config, engine, model, pbp, replay, viewstate, webstore
from starconquest.settings import Settings


MATCH = "00112233445566ff"
TOKEN = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    return tmp_path


def _settings(**over) -> Settings:
    return Settings(mode="random", players=3, nodes=16, seed=7, **over)


def _payload(settings=None, **over):
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
    }
    payload.update(over)
    return payload


def _parsed(payload) -> pbp.Match:
    match = pbp.match_from_dict(payload)
    assert match is not None
    return match


def _resolve(match: pbp.Match, decide=None) -> tuple[model.GameState, replay.GameLog, str]:
    resolved = pbp.resolve(match, decide)
    assert resolved is not None
    return resolved


def _open(match: pbp.Match, seat: pbp.Seat) -> tuple[model.GameState, viewstate.Ui, replay.GameLog]:
    opened = app.open_match(match, seat, Settings())
    assert opened is not None
    return opened


def _watching(**over):
    match = _parsed(_payload(**over))
    state, ui, log = _open(match, pbp.spectator(MATCH))
    return match, state, ui, log


# --------------------------------------------------------------------------- #
# The link, and which matches it opens
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


def test_a_match_is_watchable_only_with_fog_off():
    assert _parsed(_payload()).watchable
    fogged = _settings(fog_sight=config.FOG_ON_SIGHT, fog_scout=config.FOG_ON_SCOUT)
    assert not _parsed(_payload(settings=fogged)).watchable
    scouted = _settings(fog_scout=config.FOG_ON_SCOUT)
    assert not _parsed(_payload(settings=scouted)).watchable


def test_a_match_that_may_not_be_watched_says_why():
    assert app.pbp_unwatchable(_parsed(_payload())) == ""
    fogged = _settings(fog_sight=config.FOG_ON_SIGHT, fog_scout=config.FOG_ON_SCOUT)
    assert (app.pbp_unwatchable(_parsed(_payload(settings=fogged)))
            == app.PBP_FOGGED_MSG)


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
    match = _parsed(_payload())
    _, ui, _ = _open(match, pbp.Seat(MATCH, 1, TOKEN))
    assert not ui.spectating


def test_a_spectator_can_write_nothing():
    """No token, so every write refuses it before it is sent."""
    seat = pbp.spectator(MATCH)
    assert seat.spectating and not seat.valid()
    _, _state, _, log = _watching()
    assert pbp.submit(seat, 0, []) is None
    assert pbp.send_lapse(seat, 0, {2: []}) is None
    assert pbp.send_resolved(seat, 0, log, "", False) is None
    assert not pbp.remember(seat)


def test_a_spectator_steps_to_the_board_the_players_resolved():
    """Stepping applies the resolver's record, exactly as a seat's client does."""
    ai.load_models()
    complete = _parsed(_payload(submitted=[1, 2]))
    _elsewhere, log, digest = _resolve(complete, ai.decide)
    _, state, ui, local = _watching()
    moved = _parsed(_payload(turn=1, log=pbp.shareable(log).encoded()))
    assert app.pbp_verdict(moved, state) == app.PBP_STEP
    app.resolve_turn(state, ui, local, Settings(), script=pbp.settled_turn(moved, 0))
    assert replay.digest_hex(state) == digest
    assert ui.visible == set(state.systems)


def test_a_spectator_never_touches_a_seats_saved_rules():
    """This device may hold a seat in the match too, with standing rules saved
    for it; watching neither loads them nor writes them away."""
    ai.load_models()
    rules = {3: model.ForwardRule(shares={4: 100})}
    pbp.remember_rules(MATCH, rules)
    complete = _parsed(_payload(submitted=[1, 2]))
    _elsewhere, log, _digest = _resolve(complete, ai.decide)
    _, state, ui, local = _watching()
    assert ui.auto_forward == {}
    moved = _parsed(_payload(turn=1, log=pbp.shareable(log).encoded()))
    app.resolve_turn(state, ui, local, Settings(), script=pbp.settled_turn(moved, 0))
    assert pbp.remembered_rules(MATCH) == rules


def test_a_spectator_reads_quickly_while_the_match_moves_and_slowly_once_it_stops():
    """Nobody touches a TV, so its reads key on the match rather than on input:
    the ordinary cadence while a turn resolved or a seat submitted in the last
    half hour, then the idle ceiling, and straight back on a touch."""
    quiet, never_touched = app.PBP_SPECTATOR_QUIET_MS, 10 ** 9
    assert app.pbp_spectator_floor(0, never_touched) == 0
    assert app.pbp_spectator_floor(quiet - 1, never_touched) == 0
    assert app.pbp_spectator_floor(quiet, never_touched) == app.PBP_IDLE_MAX_MS
    assert app.pbp_spectator_floor(10 ** 9, never_touched) == app.PBP_IDLE_MAX_MS
    assert app.pbp_spectator_floor(10 ** 9, 0) == 0, "a touch brings it back"
    # Backed off, a forgotten tab reads at a twelfth of the steady cadence.
    assert app.PBP_IDLE_MAX_MS // app.PBP_POLL_MS == 12

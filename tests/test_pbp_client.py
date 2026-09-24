"""Play-by-post: the shell half — opening a match, submitting, and stepping it on.

`test_pbp.py` pins the pure module: links, seats, and the determinism two clients
must agree on. This pins what `main` does with it, and the property that matters
most is at the join between the two: **the turn the shell plays onto a live board
must be the same turn another client rebuilds from the stored log.** One is
`main.resolve_turn` (resolving with the wire's orders, or stepping with the
resolver's record), the other is `pbp.resolve`/`pbp.rebuild`, and they are
different code paths. If they ever disagree, the match has silently forked and
nothing on either screen would say so.

Pure/headless: `main` imports pygame, but nothing here draws.
"""

from __future__ import annotations

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import pytest

import main as app
from starconquest import ai, config, engine, pbp, replay, webstore
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


def _resolved_log(rows, **over) -> str:
    """The log a client would have uploaded for turn 0 played with ``rows``."""
    _, log, _ = pbp.resolve(pbp.match_from_dict(
        _payload(turn=0, submitted=[1, 2], turns=rows, **over)))
    return pbp.shareable(log).encoded()


def test_a_match_opens_on_the_turn_the_endpoint_says_it_is_on():
    rows = [_a_move(pbp.rebuild(pbp.match_from_dict(_payload()))[0], 1)]
    _, state, _, log = _opened(turn=1, turns=rows, log=_resolved_log(rows))
    assert state.turn == 1
    assert log.turn_count == 1


def test_a_match_with_no_record_of_its_turns_does_not_open():
    """Past turn 0 the log is the only record of what the bots decided, so a
    match without one cannot be rebuilt — and must say so, not guess."""
    rows = [_a_move(pbp.rebuild(pbp.match_from_dict(_payload()))[0], 1)]
    assert app.open_match(pbp.match_from_dict(_payload(turn=1, turns=rows)),
                          _seat(1), Settings()) is None


def test_a_log_that_files_an_order_a_person_never_sent_is_refused():
    """The resolver writes the bots' orders and the dice; a person's orders are
    stored under their own token, and the log may not add to them."""
    state = pbp.rebuild(pbp.match_from_dict(_payload()))[0]
    rows = [_a_move(state, 1, 2)]
    log = replay.GameLog.decode(_resolved_log(rows))
    src, dst = _lane(state, 2)
    log.turns[0]["orders"].append(replay._order_to_dict(Order(2, src, dst, 1)))
    forged = pbp.match_from_dict(_payload(turn=1, turns=rows, log=log.encoded()))
    assert pbp.match_log(forged) is None
    honest = pbp.match_from_dict(_payload(turn=1, turns=rows, log=_resolved_log(rows)))
    assert pbp.match_log(honest) is not None


def test_the_uploaded_log_carries_none_of_our_standing_rules():
    """Every other client opens from it, and a route plan is ours alone."""
    _, state, ui, log = _opened()
    src, dst = _lane(state, 1)
    ui.auto_forward = {src: (dst, 0)}
    match = pbp.match_from_dict(_payload(turn=0, submitted=[1, 2]))
    app.resolve_turn(state, ui, log, Settings(),
                     seat_orders=match.orders_for_turn(0))
    assert log.rules_for(0), "recorded locally, so our own resume keeps them"
    assert not pbp.shareable(log).rules_for(0)


def test_a_stepped_turn_applies_the_record_and_decides_nothing(monkeypatch):
    """The whole fix for a bot on a wall clock: only the resolver asks it."""
    ai.load_models()
    settings = Settings(mode="random", players=3, nodes=16, seed=7)
    match = pbp.match_from_dict(_payload(
        seats=(1, 2), players=3, settings_json=settings.to_dict(),
        submitted=[1, 2]))
    _elsewhere, log, digest = pbp.resolve(match, ai.decide)

    _, state, ui, local = _opened(seats=(1, 2), players=3,
                                  settings_json=settings.to_dict())
    monkeypatch.setattr(app.ai, "decide", lambda *a: pytest.fail("decided again"))
    stepped = pbp.match_from_dict(_payload(
        seats=(1, 2), players=3, settings_json=settings.to_dict(), turn=1,
        log=pbp.shareable(log).encoded()))
    app.resolve_turn(state, ui, local, Settings(),
                     script=pbp.settled_turn(stepped, 0))
    assert replay.digest_hex(state) == digest


def _fought_turn():
    """A board, and an honest record of a turn on it with at least one fight."""
    ai.load_models()
    settings = Settings(mode="random", players=3, nodes=12, seed=4)
    over = {"seats": (1, 2), "players": 3, "settings_json": settings.to_dict(), "seed": 4}
    log_blob = None
    for turn in range(60):
        match = pbp.match_from_dict(_payload(turn=turn, submitted=[1, 2], **over))
        if log_blob is not None:
            match.log = log_blob
        rebuilt = pbp.rebuild(match)
        assert rebuilt is not None
        state, log = rebuilt
        _resolved, log, _ = pbp.resolve(match, ai.decide)
        if log.dice_for(turn):
            return state, log.script_for(turn), 4
        log_blob = pbp.shareable(log).encoded()
    pytest.fail("no fight in sixty turns")


def test_a_turn_rolled_honestly_checks_out():
    state, record, seed = _fought_turn()
    assert pbp.verify_turn(state, record, seed)


def test_a_turn_with_dice_its_orders_do_not_roll_is_caught():
    """The resolver writes the bots' orders, which nothing can check; it cannot
    also pick the dice."""
    state, record, seed = _fought_turn()
    record.dice[0] = 0.0 if record.dice[0] else 0.5
    assert not pbp.verify_turn(state, record, seed)


def test_deciding_the_bots_leaves_the_turns_dice_alone():
    """The bots decide on a scratch copy, so the live rng still stands where the
    turn's seed put it — which is the whole of what makes the dice checkable."""
    ai.load_models()
    settings = Settings(mode="random", players=3, nodes=14, seed=7)
    match = pbp.match_from_dict(_payload(seats=(1,), players=3,
                                         settings_json=settings.to_dict()))
    state, _ = pbp.rebuild(match)
    pbp.reseed(state, match.seed)
    before = state.rng.getstate()
    orders = pbp.turn_orders(state, match, ai.decide)
    assert set(orders) >= {2, 3}, "both bots must really have been asked"
    assert state.rng.getstate() == before


def test_a_refused_resolve_rebuilds_from_the_log_that_won():
    """Our board may hold a turn the match never had: somebody else resolved it
    first, and their bots need not have decided as ours did."""
    _, state, _, _ = _opened()
    match = pbp.match_from_dict(_payload(turn=1))
    state.turn = 1
    assert app.pbp_verdict(match, state) != app.PBP_REBUILD
    assert app.pbp_verdict(match, state, stale=True) == app.PBP_REBUILD


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
    _, state, _, _ = _opened()
    state.turn = 2
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


def test_we_are_never_on_the_list_of_seats_we_are_waiting_for(monkeypatch):
    """Straight after the press, and again once the reply says who is left."""
    monkeypatch.setattr(pbp, "call", lambda *a, **k: object())
    _, state, ui, _ = _opened()
    ui.pbp_waiting = (1, 2)
    app.pbp_send(state, ui, _seat(1))
    assert ui.pbp_waiting == (2,)
    ui.pbp_waiting = (1, 2)       # ...a stale read landed in between
    app.pbp_heard(ui, pbp.OK, '{"seat": 1, "turn": 0, "waiting": [2]}')
    assert ui.pbp_waiting == (2,)


def test_a_refused_submission_hands_the_turn_back():
    """Otherwise the player sits in front of a veil waiting on a turn they never
    actually entered."""
    _, _state, ui, _ = _opened()
    ui.pbp_submitted = True
    app.pbp_heard(ui, pbp.REFUSED, "")
    assert not ui.pbp_submitted
    assert ui.pbp_msg == app.PBP_REFUSED_MSG


def test_a_submission_that_landed_says_nothing():
    _, _state, ui, _ = _opened()
    ui.pbp_submitted, ui.pbp_msg = True, "Sending..."
    app.pbp_heard(ui, pbp.OK, '{"seat": 1, "turn": 0, "waiting": [2]}')
    assert ui.pbp_submitted and ui.pbp_msg == ""


def test_the_endpoints_refusals_read_as_refusals_not_as_a_lost_connection():
    """A known refusal is put in the player's terms, an unknown one is relayed as
    a refusal, and only a call with no answer at all is blamed on the network."""
    assert (app.pbp_trouble(pbp.ERROR, '{"error": "stale turn", "turn": 4}')
            == app.PBP_ENDPOINT_MSGS["stale turn"])
    assert (app.pbp_trouble(pbp.ERROR, '{"error": "bad turn"}')
            == app.PBP_REFUSAL_MSG.format("bad turn"))
    assert app.pbp_trouble(pbp.MISSING, "") == app.PBP_MISSING_MSG
    assert app.pbp_trouble(pbp.ERROR, "<!doctype html>") == app.PBP_UNREACHABLE_MSG
    assert app.pbp_trouble(pbp.ERROR, "") == app.PBP_UNREACHABLE_MSG


def test_only_the_rate_limit_counts_as_being_told_to_slow_down():
    assert app.pbp_throttled('{"error": "slow down"}')
    assert not app.pbp_throttled('{"error": "stale turn"}')
    assert not app.pbp_throttled("")
    assert not app.pbp_throttled("<!doctype html>")


def test_a_refusal_stays_on_screen_once_the_veil_drops(monkeypatch):
    """The refusal hands the board back, which drops the waiting overlay in the
    same frame. Its reason has to be drawn somewhere else, or the press reads as
    having done nothing."""
    from starconquest import render
    pygame.init()
    render._FONTS.clear()        # another file may have quit pygame
    _, state, ui, _ = _opened()
    ui.pbp_submitted = True
    app.pbp_heard(ui, pbp.ERROR, '{"error": "stale turn"}')
    drawn = []
    monkeypatch.setattr(render, "_label_pill",
                        lambda surface, font, text, *a: drawn.append(text))
    render.draw(pygame.Surface((config.SCREEN_W, config.SCREEN_H)), state, ui)
    assert not ui.awaiting_others(state)
    assert app.PBP_ENDPOINT_MSGS["stale turn"] in drawn


# --------------------------------------------------------------------------- #
# What the endpoint says about the live turn, and what the overlay makes of it
# --------------------------------------------------------------------------- #
def test_the_overlay_is_built_from_what_the_endpoint_says():
    _, _state, ui, _ = _opened()
    app.pbp_adopt(pbp.match_from_dict(_payload(submitted=[1], seats=(1, 2, 3),
                                               players=3)), _seat(), ui)
    assert ui.pbp_submitted
    assert ui.pbp_waiting == (2, 3)


def test_whether_we_submitted_is_the_endpoints_answer_and_never_ours():
    """A submission that failed on the way out must not leave the board held for
    a turn nobody is waiting on."""
    _, _state, ui, _ = _opened()
    ui.pbp_submitted = True
    app.pbp_adopt(pbp.match_from_dict(_payload(submitted=[2])), _seat(), ui)
    assert not ui.pbp_submitted


def test_a_resolved_turn_hands_the_board_straight_back():
    _, state, ui, _ = _opened()
    ui.pbp_submitted, ui.pbp_waiting, ui.pbp_msg = True, (2,), "waiting"
    app.pbp_opened(ui)
    assert not ui.awaiting_others(state)
    assert ui.pbp_waiting == () and ui.pbp_msg == ""


# --------------------------------------------------------------------------- #
# Opening a match from the menu
# --------------------------------------------------------------------------- #
def test_a_new_match_seats_every_player(monkeypatch):
    """Every player is a person — the simplest rule that fits in a button, and
    the one someone is already holding in their head when they set the count."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    match_id, _ = app.pbp_open(Settings(players=4), seed=11)
    assert sent["seats"] == [1, 2, 3, 4]
    assert sent["match_id"] == match_id and sent["seed"] == 11
    assert sent["rules_version"] == engine.RULES_VERSION


def test_a_new_match_can_seat_a_roster_smaller_than_the_table(monkeypatch):
    """The menu's roster prompt can leave a seat to its own strategy — seat 1 is
    always in it (the creator ends up seated there), but any other seat can be
    left off, and only the seats actually passed are asked for."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    match_id, _ = app.pbp_open(Settings(players=4), seed=11, seats=[1, 3])
    assert sent["seats"] == [1, 3]
    assert sent["match_id"] == match_id


def test_a_new_match_can_be_opened_with_a_chosen_deadline(monkeypatch):
    """The menu's roster prompt carries a deadline alongside the roster — left
    out, the format's own default (`pbp.DEADLINE_HOURS`) still applies, exactly
    as it did before this was a choice."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    app.pbp_open(Settings(), seed=1, deadline_hours=72)
    assert sent["deadline_hours"] == 72


def test_a_public_match_mints_only_the_creators_seat(monkeypatch):
    """A public match leaves seats 2+ to be claimed on the lobby page, so it asks
    for seat 1's token alone; a private one still asks for every seat's."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    app.pbp_open(Settings(players=3), seed=1, public=True)
    assert sent["public"] is True
    assert sent["claimed"] == [1]
    assert sent["seats"] == [1, 2, 3]
    app.pbp_open(Settings(players=3), seed=1)
    assert sent["public"] is False
    assert sent["claimed"] == [1, 2, 3]


def test_a_new_match_carries_its_title_and_name_and_the_name_is_kept(monkeypatch):
    """The prompt's two optional fields go up with the match; the name is
    remembered for the next prompt, and clearing it is remembered too."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    app.pbp_open(Settings(players=2), seed=1, title="Friday", name="Alice")
    assert sent["title"] == "Friday" and sent["name"] == "Alice"
    assert webstore.pbp_name() == "Alice"
    app.pbp_open(Settings(players=2), seed=1)
    assert sent["title"] == "" and sent["name"] == ""
    assert webstore.pbp_name() == ""


def test_a_new_match_mints_its_own_id_rather_than_being_handed_one(monkeypatch):
    """Sent rather than handed back, so a retry after a lost reply opens a second
    match instead of quietly rewriting the first."""
    monkeypatch.setattr(pbp, "call", lambda *a, **k: object())
    first, _ = app.pbp_open(Settings(), seed=1)
    second, _ = app.pbp_open(Settings(), seed=1)
    assert first != second
    assert replay._MATCH_ID_RE.match(first)


def test_the_seed_is_pinned_before_the_match_is_opened(monkeypatch):
    """"Roll a fresh seed" has to mean one seed for the whole table, not one
    each, so it is resolved and *sent* rather than left in the setup."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    app.pbp_open(Settings(seed=None), seed=4242)
    assert sent["seed"] == 4242


def test_every_seat_gets_a_link_including_our_own(monkeypatch):
    """A match is only a match once the others are in it, so the thing to hand
    over is the whole set — and the one that seats us is how we get back in
    after closing the tab."""
    monkeypatch.setattr(webstore, "link_url", lambda token: f"https://game/#{token}")
    lines = app.pbp_links(MATCH, {1: TOKEN, 2: "b" * 32})
    assert len(lines) == 2
    assert f"https://game/#pbp={MATCH}:{TOKEN}" in lines[0]
    assert config.player_name(2) in lines[1]


def test_off_the_web_a_link_is_the_bare_fragment():
    """There is no page to point at, so what goes out is what `--match` takes and
    what a player can paste onto the web build's address."""
    lines = app.pbp_links(MATCH, {1: TOKEN})
    assert lines[0].endswith(f"pbp={MATCH}:{TOKEN}")


def test_seat_links_are_the_raw_pairs_the_invite_overlay_copies(monkeypatch):
    """`pbp_links` and the invite overlay want different shapes off the same
    tokens — text lines for the desktop fallback, `(seat, link)` pairs for a
    row's own Copy button — so one is built from the other rather than the two
    drifting apart on what a link actually is."""
    monkeypatch.setattr(webstore, "link_url", lambda token: f"https://game/#{token}")
    pairs = app.pbp_seat_links(MATCH, {1: TOKEN, 2: "b" * 32})
    assert pairs == [(1, f"https://game/#pbp={MATCH}:{TOKEN}"),
                     (2, f"https://game/#pbp={MATCH}:{'b' * 32}")]


def test_a_token_that_could_not_seat_anyone_is_never_handed_out():
    """A link that cannot submit is worse than no link: it would be sent to a
    player as though it worked."""
    body = {"match_id": MATCH, "tokens": {"1": TOKEN, "2": "nope", "x": TOKEN}}
    assert pbp.tokens_from(body, MATCH) == {1: TOKEN}
    assert pbp.tokens_from(body, "f" * 16) == {}, "...nor one for another match"


def test_the_links_are_saved_where_they_can_be_got_at(tmp_path, monkeypatch):
    """Clipboard first (the only channel an installed PWA has), then a file (the
    only one a desktop build has). Never the address bar: a seat link left there
    is read back at the next launch and would seat you in a match you had left.
    """
    monkeypatch.setattr(app.paths, "saves_dir", lambda: tmp_path / "saves")
    monkeypatch.setattr(webstore, "set_url_fragment",
                        lambda token: pytest.fail("a seat link must not reach the URL"))
    line = app.pbp_handed_out(MATCH, {1: TOKEN, 2: "b" * 32})
    saved = (tmp_path / "saves" / f"match_{MATCH}.txt").read_text()
    assert TOKEN in saved and "b" * 32 in saved
    assert "match_" in line


# --------------------------------------------------------------------------- #
# Two clients, a whole match, and a server that holds no board
# --------------------------------------------------------------------------- #
class _Endpoint:
    """`pbp.mjs` in miniature: the decisions it makes, none of its plumbing.

    Worth having as well as the JS tests because those check the rules in
    isolation, and what actually broke in a live match was the *interaction* —
    `?action=state` withholding the live turn's orders from the very client about
    to resolve it, so the turn played as though nobody had moved and two clients
    agreed with each other because both were equally wrong. Only running two of
    them against one store for a run of turns shows that.
    """

    def __init__(self, settings: Settings, seed: int, seats: list[int],
                 lapse_now: bool = False):
        self.settings, self.seed, self.seats = settings, seed, sorted(seats)
        self.turn, self.finished = 0, False
        self.rows: list[dict] = []
        self.log = ""                          # the winning resolver's upload
        self.digests: dict[int, str] = {}      # first digest per turn wins
        # `lapse_now` stands in for a clock that has already run out, since these
        # tests have no two days to wait: it is the one thing `deadlinePassed`
        # decides, and what it decides is a yes or a no.
        self.lapse_now = lapse_now

    # -- reads -------------------------------------------------------------- #
    def waiting(self) -> list[int]:
        live = {r["seat"] for r in self.rows if r["turn"] == self.turn}
        return [s for s in self.seats if s not in live]

    def misses(self, seat: int) -> int:
        """`consecutiveMisses`: how many turns running this seat did not play."""
        count = 0
        for turn in range(self.turn - 1, -1, -1):
            row = next((r for r in self.rows
                        if r["seat"] == seat and r["turn"] == turn), None)
            if row is None or row["source"] == "human":
                break
            count += 1
        return count

    def lapsed(self) -> dict[int, str]:
        if not self.lapse_now:
            return {}
        return {seat: ("bot" if self.misses(seat) >= 1 else "hold")
                for seat in self.waiting()}

    def state(self) -> dict:
        # Complete-or-nothing on the live turn, exactly as `visibleOrders` is.
        settled = [r for r in self.rows if r["turn"] < self.turn]
        return {
            "match_id": MATCH, "settings_json": self.settings.to_dict(),
            "seed": self.seed, "seats": list(self.seats), "turn": self.turn,
            "submitted": [r["seat"] for r in self.rows if r["turn"] == self.turn],
            "turns": settled if self.waiting() else list(self.rows),
            "log": self.log, "finished": self.finished,
            "rules_version": engine.RULES_VERSION, "lapsed": self.lapsed(),
            "deadline_hours": 48, "turn_opened_at": "2020-01-01T00:00:00Z",
        }

    # -- writes ------------------------------------------------------------- #
    def submit(self, seat: int, turn: int, orders: list[dict]) -> None:
        assert turn == self.turn, "a stale submission is refused, never applied"
        assert seat not in [r["seat"] for r in self.rows if r["turn"] == turn], \
            "the unique constraint answers a double submission"
        self.rows.append({"turn": turn, "seat": seat, "orders_json": orders,
                          "source": "human"})

    def lapse(self, turn: int, filing: dict[int, list[dict]]) -> None:
        assert turn == self.turn, "a stale lapse is refused, never applied"
        allowed = self.lapsed()
        for seat, orders in filing.items():
            assert seat in allowed, "only a seat the clock has run out on"
            # A held turn is no orders at all, whatever the caller sent.
            self.rows.append({
                "turn": turn, "seat": seat, "source": allowed[seat],
                "orders_json": orders if allowed[seat] == "bot" else []})

    def resolve(self, turn: int, digest: str, finished: bool, log: str) -> bool:
        if turn != self.turn:
            return False                  # somebody else got there first
        assert not self.waiting(), "a turn is resolved only once every seat is in"
        # The coherence tripwire: the first digest for a turn is kept, and every
        # later one is compared against it.
        assert self.digests.setdefault(turn, digest) == digest, \
            f"two clients resolved turn {turn} to different boards"
        self.turn, self.finished, self.log = turn + 1, finished, log
        return True


def _orders_for(state, seat: int) -> list[dict]:
    """What a person at ``seat`` does this turn.

    Deterministic, and drawing nothing from `state.rng` — which is also true of a
    real person's orders, and is why a bot cannot stand in here: `ai.decide` draws,
    so each client would leave its own rng in a different place and the dice would
    diverge for reasons that have nothing to do with what was played.
    """
    out = []
    for sid, system in sorted(state.systems.items()):
        if system.owner_id != seat or system.ships < 4 or not system.neighbors:
            continue
        lanes = sorted(system.neighbors)
        out.append({"src": sid, "dst": lanes[(sid + state.turn) % len(lanes)],
                    "ships": system.ships // 2})
    return out


def _client(server: _Endpoint, seat: int):
    state, ui, log = app.open_match(
        pbp.match_from_dict(server.state()), _seat(seat), Settings())
    return [state, ui, log, _seat(seat)]


def _tick(server: _Endpoint, client) -> None:
    """One client's frame: read the match, then do whatever the read asks for."""
    state, ui, log, seat = client
    match = pbp.match_from_dict(server.state())
    app.pbp_adopt(match, seat, ui)
    verdict = app.pbp_verdict(match, state)
    if verdict == app.PBP_WAIT:
        # Somebody else's lapse first, then our own turn. Not either/or: a lapse
        # naming only *our* seat files nothing (`skip`), and a client that took
        # that as its whole turn would sit there refusing to play.
        filing = (pbp.lapse_orders(state, match, ai.decide, skip=seat.seat)
                  if match.lapsed else {})
        if filing:
            server.lapse(match.turn, filing)
        elif not ui.pbp_submitted and not match.finished:
            server.submit(seat.seat, state.turn, _orders_for(state, seat.seat))
    elif verdict == app.PBP_REBUILD:
        client[0], client[1], client[2] = app.open_match(match, seat, Settings())
    elif verdict == app.PBP_STEP:
        script = pbp.settled_turn(match, state.turn)
        assert script is not None and pbp.verify_turn(state, script, match.seed), \
            "an honest resolver's turn must check out on every other client"
        app.resolve_turn(state, ui, log, Settings(), script=script)
        app.pbp_opened(ui)
    else:
        turn = state.turn
        pbp.reseed(state, match.seed)
        app.resolve_turn(state, ui, log, Settings(),
                         seat_orders=pbp.turn_orders(state, match, ai.decide))
        app.pbp_opened(ui)
        if not server.resolve(turn, replay.digest_hex(state), state.winner is not None,
                              pbp.shareable(log).encoded()):
            # Somebody else's resolution won: theirs is the record.
            client[0], client[1], client[2] = app.open_match(
                pbp.match_from_dict(server.state()), seat, Settings())


def test_two_clients_play_a_match_out_and_never_disagree():
    """The whole design, end to end. Each client rebuilds the position from
    inputs the server merely keeps, and the server's own digest check is what
    would catch them drifting apart."""
    settings = Settings(mode="random", players=2, nodes=14, seed=7)
    server = _Endpoint(settings, 7, [1, 2])
    one, two = _client(server, 1), _client(server, 2)

    for _ in range(400):
        _tick(server, one)
        _tick(server, two)
        if server.finished:
            break

    assert server.turn >= 6, "the match has to actually get somewhere"
    assert one[0].turn == two[0].turn == server.turn
    assert replay.digest_hex(one[0]) == replay.digest_hex(two[0])
    # ...and each client's log is an ordinary one that rebuilds its own board.
    for state, _, log, _ in (one, two):
        assert replay.digest_hex(replay.reconstruct(log)[0]) == replay.digest_hex(state)


def test_a_bot_that_decides_differently_every_time_cannot_fork_the_match(monkeypatch):
    """A bot on a wall-clock budget, at its worst: the same position never gets
    the same orders twice. Two people and one such bot, and the clients still
    agree on every board — because only the resolver ever asks it."""
    asked = [0]

    def fickle(state, pid):
        asked[0] += 1
        out = []
        for sid, system in sorted(state.systems.items()):
            if system.owner_id == pid and system.ships >= 2 and system.neighbors:
                lanes = sorted(system.neighbors)
                out.append(Order(pid, sid, lanes[asked[0] % len(lanes)],
                                 1 + asked[0] % system.ships))
        return out

    monkeypatch.setattr(app.ai, "decide", fickle)
    settings = Settings(mode="random", players=3, nodes=14, seed=5)
    server = _Endpoint(settings, 5, [1, 2])
    one, two = _client(server, 1), _client(server, 2)
    for _ in range(200):
        _tick(server, one)
        _tick(server, two)
        if server.finished or server.turn >= 20:
            break

    assert server.turn >= 10 and asked[0] >= 10, "the bot has to have played"
    assert one[0].turn == two[0].turn == server.turn
    assert replay.digest_hex(one[0]) == replay.digest_hex(two[0])
    late = _client(server, 1)
    assert replay.digest_hex(late[0]) == replay.digest_hex(one[0])


def test_a_client_that_looks_away_for_several_turns_catches_up():
    """The rebuild arm. There is no single turn to animate, so the position is
    rebuilt — and must land exactly where the client that never left is."""
    settings = Settings(mode="random", players=2, nodes=14, seed=3)
    server = _Endpoint(settings, 3, [1, 2])
    one, two = _client(server, 1), _client(server, 2)

    for _ in range(60):
        _tick(server, one)
        if server.turn >= 4:
            break
        # Seat two is not looking: its orders go in, but it never reads back.
        if server.waiting() == [2]:
            server.submit(2, server.turn, _orders_for(two[0], 2))

    assert two[0].turn == 0, "...it really was left behind"
    _tick(server, two)
    assert two[0].turn == one[0].turn
    assert replay.digest_hex(two[0]) == replay.digest_hex(one[0])


# --------------------------------------------------------------------------- #
# When somebody stops answering
# --------------------------------------------------------------------------- #
def test_a_first_miss_files_no_orders_at_all():
    """A held turn is already a legal one — production ticks, garrisons defend —
    so somebody a day late loses a tempo rather than their position."""
    match, state, _, _ = _opened()
    match.lapsed = {2: "hold"}
    assert pbp.lapse_orders(state, match, ai.decide) == {2: []}


def test_a_second_miss_hands_the_seat_to_its_bot():
    """...which a client has to compute, because the endpoint has no engine."""
    match, state, _, _ = _opened()
    state.systems[next(sid for sid, s in state.systems.items()
                       if s.owner_id == 2)].ships = 40   # enough to want to move
    match.lapsed = {2: "bot"}
    filed = pbp.lapse_orders(state, match, ai.decide)[2]
    assert filed, "a bot with ships to spend should have filed something"
    assert all(state.systems[o["src"]].owner_id == 2 for o in filed)


def test_filing_a_bots_turn_leaves_the_live_dice_exactly_where_they_were():
    """The load-bearing line. Every bot draws from `state.rng`, and where the
    live rng stands is part of what makes every client fight the same battles —
    so a client that ran one on its own board would take a draw nobody else took
    and every roll after it would differ.

    Asserted on the rng directly rather than on a board downstream of it: a turn
    with no fight in it draws nothing, so a digest would agree for reasons that
    have nothing to do with the property. `test_a_match_carries_on_when_one_
    player_stops_answering` is what shows the consequence over a run of turns.
    """
    match, state, _, _ = _opened()
    state.systems[next(sid for sid, s in state.systems.items()
                       if s.owner_id == 2)].ships = 40
    match.lapsed = {2: "bot"}

    before = state.rng.getstate()
    filed = pbp.lapse_orders(state, match, ai.decide)[2]
    assert filed, "the bot must really have been asked, or this proves nothing"
    assert state.rng.getstate() == before


def test_an_action_this_build_cannot_carry_out_is_dropped():
    """A client and an endpoint on different deploys disagreeing must leave the
    seat waiting, not file something neither of them means."""
    match = pbp.match_from_dict(_payload(lapsed={"2": "resign", "3": "hold"}))
    assert match.lapsed == {3: "hold"}


def test_nothing_is_filed_for_a_match_whose_clock_is_still_running():
    match, state, _, _ = _opened()
    assert match.lapsed == {}
    assert pbp.send_lapse(_seat(), 0, pbp.lapse_orders(state, match, ai.decide)) is None


def test_a_lapse_names_the_turn_it_is_for(monkeypatch):
    """...so a client that has fallen behind cannot have yesterday's absences
    filed against today's board."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    pbp.send_lapse(_seat(), 4, {2: [], 3: [{"src": 1, "dst": 2, "ships": 3}]})
    assert sent["turn"] == 4 and sent["match_id"] == MATCH
    assert sorted(sent["seats"]) == ["2", "3"]


def test_a_new_match_is_opened_with_a_clock_on_it(monkeypatch):
    """A match with no deadline never lapses, so one opened without a clock is a
    match that stops the first time anybody stops answering."""
    sent = {}
    monkeypatch.setattr(pbp, "call",
                        lambda action, payload=None, **kw: sent.update(payload or {}))
    app.pbp_open(Settings(), seed=1)
    assert sent["deadline_hours"] == pbp.DEADLINE_HOURS


def test_a_match_carries_on_when_one_player_stops_answering():
    """End to end: seat two goes quiet, and the match keeps moving without it —
    holding first, then falling to its bot — with both clients still agreeing on
    every board."""
    settings = Settings(mode="random", players=2, nodes=14, seed=11)
    server = _Endpoint(settings, 11, [1, 2], lapse_now=True)
    one = _client(server, 1)

    for _ in range(40):
        _tick(server, one)
        if server.turn >= 5:
            break

    assert server.turn >= 5, "a quiet seat must not stop the match"
    filed = [r["source"] for r in server.rows if r["seat"] == 2]
    assert filed[0] == "hold", "the first miss holds"
    assert "bot" in filed[1:], "...and a second consecutive miss falls to the bot"
    # ...and a client that was never there rebuilds the same board from the rows.
    late = _client(server, 2)
    assert replay.digest_hex(late[0]) == replay.digest_hex(one[0])


def test_our_own_lapse_is_never_filed_by_us():
    """Somebody who opens the game two days late is *here*. Filing their hold the
    moment they arrive would take the turn away from the one person about to take
    it — so another client may file it on their behalf, but never this one."""
    match, state, _, _ = _opened()
    match.lapsed = {1: "hold", 2: "hold"}
    assert pbp.lapse_orders(state, match, ai.decide, skip=1) == {2: []}
    # ...and with nobody else outstanding there is nothing to send at all.
    match.lapsed = {1: "bot"}
    assert pbp.lapse_orders(state, match, ai.decide, skip=1) == {}
    assert pbp.send_lapse(_seat(1), 0, {}) is None


def test_a_failed_read_is_retried_quickly_then_backs_off():
    """A blip is over by the next try, so the first retry is well inside the
    steady cadence; an endpoint that keeps failing is asked less and less often,
    never more often than the cadence and never less than the cap."""
    delays = [app.pbp_poll_delay(n) for n in range(10)]
    assert delays[0] == app.PBP_POLL_MS
    assert delays[1] == app.PBP_RETRY_MS < app.PBP_POLL_MS
    assert delays[1:] == sorted(delays[1:])
    assert delays[5] > app.PBP_POLL_MS
    assert delays[-1] == app.PBP_RETRY_MAX_MS

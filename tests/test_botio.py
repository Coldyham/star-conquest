"""The wire format (`starconquest.botio`) and one bot ported across it.

`test_wire_bot_matches_in_process_original` is the load-bearing one, and the
reason the port exists: a payload quietly missing a field looks exactly like a
bot that plays slightly worse, so the only thing that catches it is demanding
*identical* orders from the same algorithm on both sides of the wire.
"""

from __future__ import annotations

import json
import random
import subprocess

import pytest

from starconquest import ai, botio, combat, config, engine, mapgen
from starconquest.model import Order
from starconquest.settings import Settings, _GLOBAL_KNOBS

from . import botproc


@pytest.fixture(scope="module")
def rusherplus():
    """The in-process original the wire port is measured against."""
    assert "rusherplus" in ai.load_models(), "models/rusherplus.py failed to import"
    return ai.STRATEGIES["rusherplus"]


@pytest.fixture(autouse=True)
def _preserve_config():
    """Snapshot & restore the menu-managed config knobs.

    `settings.build_state` is their single writer and writes them *globally*
    (that is the whole mechanism — every module reads `config.X` live), so a
    test that builds a state from tuned settings leaks that balance into every
    test after it. Same guard as `test_settings._preserve_config`, autouse here
    because `botio` reads config in almost every test.
    """
    saved = {const: getattr(config, const) for _, const in _GLOBAL_KNOBS}
    yield
    for const, value in saved.items():
        setattr(config, const, value)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Drop any strategy a test registered, so tests can't leak into each other."""
    before = dict(ai.STRATEGIES)
    yield
    ai.STRATEGIES.clear()
    ai.STRATEGIES.update(before)


def _board(seed=5, nodes=18, players=3):
    return mapgen.generate(seed, "random", nodes, players)


def _built(settings: Settings):
    from starconquest.settings import build_state
    return build_state(settings, settings.seed or 1)


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

def test_hello_and_turn_are_json():
    state = _board()
    for message in (botio.hello(state, 1, 150), botio.turn_payload(state, 1, 150)):
        assert json.loads(json.dumps(message)) == message


def test_setup_carries_every_global_knob():
    """A knob added to `Settings` must reach the wire without a second edit —
    hence `setup` walking `_GLOBAL_KNOBS` rather than restating the list."""
    setup = botio.setup(_board())
    for attr, _const in _GLOBAL_KNOBS:
        assert attr in setup, attr
    for identity in ("mode", "seed", "nodes", "players"):
        assert identity in setup


def test_setup_reports_the_knobs_in_force_not_the_defaults():
    settings = Settings(nodes=18, players=2, seed=11, combat_jitter=0.25,
                        defender_advantage=1.4, ship_ly_per_turn=18.0)
    state = _built(settings)
    setup = botio.setup(state)
    assert setup["combat_jitter"] == pytest.approx(0.25)
    assert setup["defender_advantage"] == pytest.approx(1.4)
    assert setup["ship_ly_per_turn"] == pytest.approx(18.0)


def test_rules_match_combat_live(monkeypatch):
    """The wire's break-even multiples are `combat`'s, not a second derivation:
    a bot cannot call `edge_attacking()`, and re-deriving the swing in another
    language is the kind of duplicate that drifts with nothing failing."""
    monkeypatch.setattr(config, "COMBAT_JITTER", 0.2)
    monkeypatch.setattr(config, "DEFENDER_ADVANTAGE", 1.25)
    rules = botio.rules()
    assert rules["edge_attacking"] == pytest.approx(combat.edge_attacking())
    assert rules["edge_defending"] == pytest.approx(combat.edge_defending())
    assert rules["swing"] == pytest.approx(1.2 / 0.8)


def test_opponent_names_are_masked_unless_revealed():
    state = _board(players=3)
    state.players[2].ai_strategy = "marshal"
    masked = {s["id"]: s.get("strategy") for s in botio.hello(state, 1, 150)["seats"]}
    assert masked[2] == "seat2" and "marshal" not in masked.values()
    revealed = {s["id"]: s.get("strategy")
                for s in botio.hello(state, 1, 150, reveal_opponents=True)["seats"]}
    assert revealed[2] == "marshal"
    # Tuning is sent either way: `aux` exists to tell a shallow opponent from a
    # deep one, which is tuning rather than identity.
    assert all("params" in s for s in botio.hello(state, 1, 150)["seats"]
               if not s["is_neutral"])


def test_lane_turns_ride_along_only_with_speed_growth(monkeypatch):
    state = _board()
    assert "lane_turns" not in botio.turn_payload(state, 1, 150)
    monkeypatch.setattr(config, "SHIP_SPEED_GROWTH_PCT", 5.0)
    state.turn = 40
    payload = botio.turn_payload(state, 1, 150)
    lanes = botio.hello(state, 1, 150)["map"]["lanes"]
    assert len(payload["lane_turns"]) == len(lanes)
    # Index-parallel with the handshake's lanes, and re-timed for `turn`.
    for lane, turns in zip(lanes, payload["lane_turns"]):
        assert turns == state.travel_turns(lane["a"], lane["b"])
    assert any(t < lane["base_turns"] for lane, t in zip(lanes, payload["lane_turns"]))


def test_building_a_payload_never_touches_the_game_rng():
    """The seed is derived, not drawn, so an external seat cannot shift the
    engine's dice: every other seat's battles roll as they did without it."""
    state = _board()
    before = state.rng.getstate()
    botio.hello(state, 1, 150)
    botio.turn_payload(state, 1, 150)
    assert state.rng.getstate() == before


def test_decide_seed_is_stable_and_per_seat():
    assert botio.decide_seed(7, 3, 1) == botio.decide_seed(7, 3, 1)
    assert botio.decide_seed(7, 3, 1) != botio.decide_seed(7, 3, 2)
    assert botio.decide_seed(7, 3, 1) != botio.decide_seed(7, 4, 1)
    assert botio.decide_seed(7, 3, 1) != botio.decide_seed(8, 3, 1)
    # Pinned: a bot may cache behaviour against a seed across releases.
    assert botio.decide_seed(1, 0, 1) == random.Random("1:0:1").getrandbits(64)


# --------------------------------------------------------------------------- #
# The reply
# --------------------------------------------------------------------------- #

def test_orders_from_stamps_the_owner_and_drops_malformed():
    reply = {"type": "orders", "orders": [
        {"src": 1, "dst": 2, "ships": 3},
        {"src": 1, "dst": 2, "ships": 0},           # nothing to send
        {"src": 1, "dst": 2, "ships": -4},          # negative
        {"src": 1, "dst": 2, "ships": 2.5},         # fractional ships
        {"src": "1", "dst": 2, "ships": 1},         # id as a string
        {"src": 1, "dst": 2},                       # no count
        {"src": 1, "dst": 2, "ships": True},        # a bool is not a count
        "not an order",
    ]}
    assert botio.orders_from(reply, 7) == [Order(7, 1, 2, 3)]
    # 4.0 is an exact integer and allowed: JSON in some languages has no ints.
    assert botio.orders_from({"type": "orders", "orders": [{"src": 1, "dst": 2, "ships": 4.0}]},
                             7) == [Order(7, 1, 2, 4)]


def test_a_bot_cannot_express_a_foreign_order():
    """The protocol's answer to `engine._own_orders`: there is no owner field, so
    the owner is stamped rather than filtered."""
    reply = {"type": "orders", "orders": [{"src": 1, "dst": 2, "ships": 3, "owner": 2}]}
    assert botio.orders_from(reply, 1) == [Order(1, 1, 2, 3)]


@pytest.mark.parametrize("reply", [
    None, {}, {"type": "ready"}, {"type": "orders"}, {"type": "orders", "orders": "x"},
    {"type": "orders", "orders": 3}, "orders", [1, 2],
])
def test_a_reply_that_is_not_orders_yields_none(reply):
    assert botio.orders_from(reply, 1) == []


def test_illegal_orders_are_left_to_the_engine():
    """Shape only, deliberately: whether a source is held or a destination
    adjacent is `apply_order`'s to decide, and it decides it for every seat
    alike. A second copy here is a second thing to keep in step."""
    state = _board()
    mine = next(s for s in state.systems.values() if s.owner_id == 1)
    far = next(s for s in state.systems.values()
               if s.id != mine.id and s.id not in mine.neighbors)
    orders = botio.orders_from({"type": "orders", "orders": [
        {"src": mine.id, "dst": far.id, "ships": 1},          # not adjacent
        {"src": mine.id, "dst": mine.neighbors[0], "ships": 10 ** 6},  # unaffordable
    ]}, 1)
    assert len(orders) == 2
    garrison = mine.ships
    assert engine.apply_order(state, orders[0]) is None
    launched = engine.apply_order(state, orders[1])
    assert launched is not None and launched.ships == garrison


# --------------------------------------------------------------------------- #
# Transport
# --------------------------------------------------------------------------- #

def test_the_example_manifest_loads():
    names = [m.name for m in botproc.load_manifests()]
    assert "rusherwire" in names
    manifest = next(m for m in botproc.load_manifests() if m.name == "rusherwire")
    assert (manifest.cwd / "main.py").is_file()
    assert manifest.protocol == botio.PROTOCOL


def test_a_broken_manifest_is_skipped_not_fatal(tmp_path):
    (tmp_path / "bad.bot.json").write_text("{ not json")
    (tmp_path / "empty.bot.json").write_text('{"name": "empty", "cmd": []}')
    (tmp_path / "ok.bot.json").write_text('{"name": "ok", "cmd": ["true"]}')
    assert [m.name for m in botproc.load_manifests(tmp_path)] == ["ok"]


def _wire_strategy(reveal=True, budget_ms=15_000):
    manifest = next(m for m in botproc.load_manifests() if m.name == "rusherwire")
    return botproc.Strategy(manifest, budget_ms, reveal_opponents=reveal,
                            stderr_to=subprocess.DEVNULL)


def test_a_bot_that_says_nothing_degrades_to_heuristic_loudly(tmp_path, capsys):
    """The app falls back to `heuristic` silently for an unknown strategy, which
    keeps a game playable. A ladder result containing a fallback seat is not a
    result, so this one is recorded and printed."""
    (tmp_path / "mute.bot.json").write_text(json.dumps(
        {"name": "mute", "cmd": ["python3", "-c", "import time; time.sleep(30)"],
         "budget_ms": 200}))
    manifest = botproc.load_manifests(tmp_path)[0]
    strategy = botproc.Strategy(manifest, 200, stderr_to=subprocess.DEVNULL)
    try:
        state = _board()
        assert strategy(state, 1) == ai.compute_orders(state, 1)   # handshake failed
        assert strategy.degraded and "no handshake" in strategy.degraded[0]
        assert "falls back to heuristic" in capsys.readouterr().err
    finally:
        strategy.close()


def test_a_dead_bot_degrades_rather_than_raising(tmp_path):
    (tmp_path / "dead.bot.json").write_text(json.dumps(
        {"name": "dead", "cmd": ["python3", "-c", "raise SystemExit(1)"], "budget_ms": 2000}))
    manifest = botproc.load_manifests(tmp_path)[0]
    strategy = botproc.Strategy(manifest, 2000, stderr_to=subprocess.DEVNULL)
    try:
        state = _board()
        for _ in range(botproc.FORFEIT_TIMEOUTS + 1):
            strategy(state, 1)          # never raises
        assert strategy.degraded
    finally:
        strategy.close()


# --------------------------------------------------------------------------- #
# Parity: the whole point of the port
# --------------------------------------------------------------------------- #

def test_wire_bot_matches_in_process_original(rusherplus):
    """`bots/rusherwire` must issue *exactly* what `models/rusherplus` issues.

    Both are asked on the same board, on the same turn, with the same seed: the
    reference's `state.rng` is swapped for a `Random(rng_seed)` — the very seed
    the payload carries — so the two draw the same tie-break stream in the same
    order. Anything the payload fails to carry shows up here as a different
    order rather than as a bot that mysteriously plays a little worse.
    """
    strategy = _wire_strategy()
    compared = 0
    try:
        for seed in (3, 11, 29, 41, 57):
            state = mapgen.generate(seed, "random", 18, 3)
            for _ in range(12):
                if state.winner is not None:
                    break
                for pid in (1, 2, 3):
                    seat_seed = botio.decide_seed(state.seed, state.turn, pid)
                    real_rng, state.rng = state.rng, random.Random(seat_seed)
                    try:
                        expected = rusherplus(state, pid)
                    finally:
                        state.rng = real_rng
                    assert strategy(state, pid) == expected, (seed, state.turn, pid)
                    compared += 1
                engine.end_turn(state, decide=ai.decide)
    finally:
        strategy.close()
    assert compared >= 100        # the loop really ran, on live mid-game boards


def test_the_wire_bot_plays_a_whole_game_through_the_ladder():
    """End to end: registered as a strategy, driving a seat for a full game with
    no special-casing anywhere in `engine` or `sim`."""
    from . import sim
    strategy = _wire_strategy(reveal=False)
    ai.register("rusherwire", strategy)
    try:
        result = sim.play(seed=9, players=2, nodes=14,
                          strategies=["rusherwire", "heuristic"])
        assert result.turns > 1 and not strategy.degraded
    finally:
        strategy.close()

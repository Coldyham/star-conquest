"""Replay logs: record a match's inputs, then reconstruct it bit-identically.

Pure/headless (replay.py imports no pygame), so this needs no display. The core
guarantee under test: a log of {settings, seed, per-turn human orders} replayed
through the engine reproduces the exact GameState — for hand play, for autoplay
(where the human seat is AI-driven and draws rng), and for a mix of the two.
"""

from __future__ import annotations

import contextlib
import json

import pytest

from starconquest import ai, config, engine, replay
from starconquest.model import Order
from starconquest.settings import _GLOBAL_KNOBS, Settings, build_state


@contextlib.contextmanager
def _preserve_config():
    """Snapshot & restore the menu-managed config knobs (build_state mutates them)."""
    saved = {const: getattr(config, const) for _, const in _GLOBAL_KNOBS}
    try:
        yield
    finally:
        for const, val in saved.items():
            setattr(config, const, val)


def _snapshot(state):
    """A comparable summary of everything that must match after reconstruction."""
    return {
        "turn": state.turn,
        "winner": state.winner,
        "owners": {sid: s.owner_id for sid, s in state.systems.items()},
        "ships": {sid: s.ships for sid, s in state.systems.items()},
        "prog": {sid: s.prod_progress for sid, s in state.systems.items()},
        "fleets": sorted(
            (f.owner_id, f.source_id, f.dest_id, f.ships, f.turns_remaining)
            for f in state.fleets
        ),
    }


def _manual_order(state, hid):
    """A hand order that draws no rng (mirrors the real non-autoplay path)."""
    owned = [s for s in state.systems.values()
             if s.owner_id == hid and s.ships > 1 and s.neighbors]
    if not owned:
        return []
    s = owned[0]
    return [Order(hid, s.id, sorted(s.neighbors)[0], s.ships // 2)]


def _play(seed, *, mode="random", players=3, nodes=16, max_turns=400,
          policy="pass"):
    """Play a full game while recording, returning (final_state, log).

    ``policy`` chooses how the human seat acts each turn:
      "pass"    – issues no orders (hand play, draws no rng)
      "manual"  – issues one hand order every other turn (draws no rng)
      "autoplay"– AI-driven every turn (draws rng, flagged human_ai=True)
      "mixed"   – flips autoplay on/off every few turns
    """
    settings = Settings(mode=mode, players=players, nodes=nodes, seed=seed,
                        autoplay=(policy == "autoplay"))
    state = build_state(settings, seed)
    log = replay.new_log(settings, seed)
    hid = state.human().id
    turns = 0
    while state.winner is None and turns < max_turns:
        if policy == "autoplay":
            autoplay, orders = True, ai.decide(state, hid)
        elif policy == "mixed":
            autoplay = (turns // 5) % 2 == 0
            orders = ai.decide(state, hid) if autoplay else _manual_order(state, hid)
        elif policy == "manual":
            autoplay, orders = False, (_manual_order(state, hid) if turns % 2 == 0 else [])
        else:  # pass
            autoplay, orders = False, []
        engine.end_turn(state, human_orders=orders, decide=ai.decide)
        log.record_turn(orders, human_ai=autoplay)
        turns += 1
    log.mark_finished(state.winner)
    return state, log


# --------------------------------------------------------------------------- #
# Log data model: recording + JSON round-trip
# --------------------------------------------------------------------------- #
def test_new_log_snapshots_settings_and_seed():
    settings = Settings(mode="symmetric", players=4, nodes=20, seed=7)
    log = replay.new_log(settings, 7)
    assert log.seed == 7
    assert log.settings["players"] == 4
    assert log.settings["mode"] == "symmetric"
    assert log.turns == [] and not log.finished


def test_record_turn_stores_orders_and_ai_flag():
    log = replay.new_log(Settings(seed=1), 1)
    log.record_turn([Order(1, 0, 2, 5)], human_ai=False)
    log.record_turn([], human_ai=True)
    assert log.turn_count == 2
    assert log.turn_is_ai(0) is False and log.turn_is_ai(1) is True
    o = log.orders_for(0)
    assert len(o) == 1 and (o[0].source_id, o[0].dest_id, o[0].ships) == (0, 2, 5)
    assert log.orders_for(1) == []


def test_to_from_dict_round_trip():
    log = replay.new_log(Settings(seed=42, players=3), 42)
    log.record_turn([Order(1, 0, 1, 3)], human_ai=False)
    log.record_turn([Order(1, 1, 2, 2)], human_ai=True)
    log.mark_finished(2)
    clone = replay.GameLog.from_dict(json.loads(json.dumps(log.to_dict())))
    assert clone.seed == log.seed
    assert clone.finished and clone.winner == 2
    assert clone.turn_count == 2 and clone.turn_is_ai(1)
    assert clone.orders_for(0)[0].dest_id == 1


def test_from_dict_tolerates_bare_list_turn_entry():
    """A hand-edited/legacy entry that is a bare order list is read as a manual turn."""
    data = {"seed": 5, "settings": {}, "turns": [[{"owner": 1, "src": 0, "dest": 1, "ships": 4}]]}
    log = replay.GameLog.from_dict(data)
    assert log.turn_is_ai(0) is False
    assert log.orders_for(0)[0].ships == 4


def test_order_from_dict_skips_malformed_entries():
    log = replay.GameLog.from_dict(
        {"seed": 0, "settings": {}, "turns": [{"ai": False, "orders": [
            {"owner": 1, "src": 0, "dest": 1, "ships": 2},
            {"owner": 1, "src": 0},                        # malformed: dropped
        ]}]}
    )
    assert len(log.orders_for(0)) == 1


# --------------------------------------------------------------------------- #
# Reconstruction is bit-identical
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("policy", ["pass", "manual", "autoplay", "mixed"])
@pytest.mark.parametrize("seed", [12345, 777, 54321])
def test_reconstruct_matches_original(policy, seed):
    with _preserve_config():
        original, log = _play(seed, policy=policy)
        before = _snapshot(original)
        # Round-trip through JSON to exercise the serialized form, then rebuild.
        log2 = replay.GameLog.from_dict(json.loads(json.dumps(log.to_dict())))
        rebuilt, _ = replay.reconstruct(log2, ai.decide)
    assert _snapshot(rebuilt) == before


def test_reconstruct_unfinished_stops_at_recorded_turn():
    """A partial (unfinished) log rebuilds to exactly where recording stopped."""
    with _preserve_config():
        state, log = _play(999, policy="autoplay", max_turns=20)
        # cut the log short to simulate a game abandoned mid-match
        log.turns = log.turns[:12]
        log.finished = False
        rebuilt, _ = replay.reconstruct(log, ai.decide)
    assert rebuilt.turn == 12


def test_reconstruct_returns_adopted_settings():
    with _preserve_config():
        _, log = _play(2024, policy="pass", nodes=18, players=3)
        _, settings = replay.reconstruct(log, ai.decide)
    assert settings.nodes == 18 and settings.players == 3


def test_reconstruct_on_turn_fires_each_turn():
    """on_turn is invoked at the opening position and after every replayed turn,
    with the state's turn counter advancing 0,1,2,... — this is the hook the shell
    uses to rebuild fog-of-war memory across the whole game, not just the end."""
    with _preserve_config():
        state, log = _play(321, policy="autoplay", max_turns=15)
        seen_turns: list[int] = []
        replay.reconstruct(log, ai.decide, on_turn=lambda s: seen_turns.append(s.turn))
    assert seen_turns == list(range(state.turn + 1))   # initial + one per turn


# --------------------------------------------------------------------------- #
# On-disk save / load / discovery (GAMES_DIR redirected to a tmp dir)
# --------------------------------------------------------------------------- #
@pytest.fixture
def games_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    return tmp_path


def test_save_creates_file_and_load_round_trips(games_dir):
    log = replay.new_log(Settings(seed=3, players=3), 3)
    log.record_turn([Order(1, 0, 1, 2)], human_ai=False)
    log.save()
    assert log.path is not None and log.path.exists()
    # no leftover temp file from the atomic write
    assert not list(games_dir.glob("*.tmp"))
    loaded = replay.load(log.path)
    assert loaded.seed == 3 and loaded.turn_count == 1


def test_list_and_latest_log_newest_first(games_dir):
    old = replay.new_log(Settings(seed=1), 1); old.record_turn([], human_ai=False); old.save()
    new = replay.new_log(Settings(seed=2), 2); new.record_turn([], human_ai=False); new.save()
    # make ``new`` unambiguously the more recently modified file
    import os, time
    t = time.time()
    os.utime(old.path, (t - 100, t - 100))
    os.utime(new.path, (t, t))
    assert replay.list_logs()[0] == new.path
    assert replay.latest_log().seed == 2


def test_latest_log_none_when_empty(games_dir):
    assert replay.latest_log() is None


def test_latest_log_skips_corrupt_file(games_dir):
    good = replay.new_log(Settings(seed=8), 8); good.record_turn([], human_ai=False); good.save()
    bad = games_dir / "game_99999999_999999_0.json"
    bad.write_text("{ this is not valid json ")
    import os, time
    t = time.time()
    os.utime(good.path, (t - 100, t - 100))
    os.utime(bad, (t, t))          # corrupt file is "newest" but must be skipped
    assert replay.latest_log().seed == 8


def test_save_is_atomic_replace_on_rewrite(games_dir):
    """Re-saving the same log rewrites its file in place (resume appends here)."""
    log = replay.new_log(Settings(seed=4), 4)
    log.record_turn([], human_ai=False); log.save()
    first_path = log.path
    log.record_turn([Order(1, 0, 1, 1)], human_ai=True); log.save()
    assert log.path == first_path
    assert replay.load(first_path).turn_count == 2
    assert len(replay.list_logs()) == 1

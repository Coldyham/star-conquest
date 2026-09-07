"""Replay logs: record a match's inputs, then reconstruct it bit-identically.

Pure/headless (replay.py imports no pygame), so this needs no display. The core
guarantee under test: a log of {settings, seed, per-turn orders + dice} replayed
through the engine reproduces the exact GameState — for hand play, for autoplay
(where the human seat is AI-driven), and for a mix of the two. Crucially it does
so *without consulting a single bot*, which is what makes the record hold for a
strategy that is not reproducible on its own (see `test_reconstruct_survives_an
_unreproducible_bot`).
"""

from __future__ import annotations

import contextlib
import copy
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
          policy="pass", strategies=None):
    """Play a full game while recording, returning (final_state, log).

    ``policy`` chooses how the human seat acts each turn:
      "pass"    – issues no orders (hand play, draws no rng)
      "manual"  – issues one hand order every other turn (draws no rng)
      "autoplay"– AI-driven every turn (draws rng, flagged human_ai=True)
      "mixed"   – flips autoplay on/off every few turns
    """
    settings = Settings(mode=mode, players=players, nodes=nodes, seed=seed,
                        autoplay=(policy == "autoplay"))
    if strategies is not None:
        settings.ai_strategy[:len(strategies)] = strategies
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
        record = engine.end_turn(state, human_orders=orders, decide=ai.decide)
        log.record_turn(record, human_ai=autoplay)
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


def _record(orders, dice=()):
    return engine.TurnRecord(list(orders), list(dice))


def test_record_turn_stores_orders_dice_and_ai_flag():
    log = replay.new_log(Settings(seed=1), 1)
    log.record_turn(_record([Order(1, 0, 2, 5)], [0.05, -0.03]), human_ai=False)
    log.record_turn(_record([]), human_ai=True)
    assert log.turn_count == 2
    assert log.turn_is_ai(0) is False and log.turn_is_ai(1) is True
    o = log.orders_for(0)
    assert len(o) == 1 and (o[0].source_id, o[0].dest_id, o[0].ships) == (0, 2, 5)
    assert log.dice_for(0) == [0.05, -0.03]
    assert log.orders_for(1) == [] and log.dice_for(1) == []


def test_record_turn_stores_every_seat_not_just_the_human():
    """The whole point of format 2: an AI seat's orders are in the record, so the
    replay never has to ask that bot what it did."""
    log = replay.new_log(Settings(seed=1, players=3), 1)
    log.record_turn(_record([Order(1, 0, 2, 5), Order(2, 9, 8, 4), Order(3, 4, 5, 1)]))
    assert sorted(o.owner_id for o in log.orders_for(0)) == [1, 2, 3]


def test_record_turn_stores_standing_rules():
    log = replay.new_log(Settings(seed=1), 1)
    log.record_turn(_record([]), rules={3: (7, 2), 4: (1, 0)})
    assert log.rules_for(0) == {3: (7, 2), 4: (1, 0)}
    # ...and survives the JSON round trip, where the keys become strings
    clone = replay.GameLog.from_dict(json.loads(json.dumps(log.to_dict())))
    assert clone.rules_for(0) == {3: (7, 2), 4: (1, 0)}


def test_rules_for_drops_malformed_entries():
    log = replay.GameLog.from_dict(
        {"seed": 0, "settings": {}, "version": 2,
         "turns": [{"orders": [], "rules": {"3": [7, 2], "4": "nonsense", "x": [1, 1]}}]}
    )
    assert log.rules_for(0) == {3: (7, 2)}


def test_to_from_dict_round_trip():
    log = replay.new_log(Settings(seed=42, players=3), 42)
    log.record_turn(_record([Order(1, 0, 1, 3)], [0.1]), human_ai=False)
    log.record_turn(_record([Order(1, 1, 2, 2)]), human_ai=True)
    log.mark_finished(2)
    clone = replay.GameLog.from_dict(json.loads(json.dumps(log.to_dict())))
    assert clone.seed == log.seed
    assert clone.finished and clone.winner == 2
    assert clone.turn_count == 2 and clone.turn_is_ai(1)
    assert clone.orders_for(0)[0].dest_id == 1
    assert clone.dice_for(0) == [0.1]


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
        rebuilt, _ = replay.reconstruct(log2)
    assert _snapshot(rebuilt) == before


@pytest.fixture
def unreproducible_bot():
    """A registered strategy that decides differently every time it is asked the
    same question — a stand-in for `models/knower.py`, which truncates its search
    on a wall-clock budget and so cannot promise the same plan twice.

    Yields a one-element list holding its call count.
    """
    calls = [0]

    def wobbly(state, pid):
        calls[0] += 1
        owned = sorted((s for s in state.systems.values()
                        if s.owner_id == pid and s.ships > 1 and s.neighbors),
                       key=lambda s: s.id)
        if not owned:
            return []
        s = owned[calls[0] % len(owned)]      # the drift: same board, different move
        return [Order(pid, s.id, sorted(s.neighbors)[0], s.ships // 2)]

    ai.register("wobbly", wobbly)
    try:
        yield calls
    finally:
        ai.STRATEGIES.pop("wobbly", None)


def test_reconstruct_survives_an_unreproducible_bot(unreproducible_bot):
    """The guarantee format 2 exists for. With only the human's orders recorded,
    replaying this game re-ran the bot and got different orders out of it, so the
    rebuilt match diverged from the one that was played — silently, in history
    review and on resume alike. The record now holds what every seat did, so the
    bot is never asked again."""
    with _preserve_config():
        original, log = _play(31337, policy="pass", players=3, max_turns=60,
                              strategies=["wobbly", "wobbly", "wobbly"])
        played = unreproducible_bot[0]
        rebuilt, _ = replay.reconstruct(log)
    assert played > 0 and unreproducible_bot[0] == played   # never consulted again
    assert _snapshot(rebuilt) == _snapshot(original)


def test_reconstruct_unfinished_stops_at_recorded_turn():
    """A partial (unfinished) log rebuilds to exactly where recording stopped."""
    with _preserve_config():
        state, log = _play(999, policy="autoplay", max_turns=20)
        # cut the log short to simulate a game abandoned mid-match
        log.turns = log.turns[:12]
        log.finished = False
        rebuilt, _ = replay.reconstruct(log)
    assert rebuilt.turn == 12


def test_reconstruct_returns_adopted_settings():
    with _preserve_config():
        _, log = _play(2024, policy="pass", nodes=18, players=3)
        _, settings = replay.reconstruct(log)
    assert settings.nodes == 18 and settings.players == 3


def test_reconstruct_on_turn_fires_each_turn():
    """on_turn is invoked at the opening position and after every replayed turn,
    with the state's turn counter advancing 0,1,2,... — this is the hook the shell
    uses to rebuild fog-of-war memory across the whole game, not just the end."""
    with _preserve_config():
        state, log = _play(321, policy="autoplay", max_turns=15)
        seen_turns: list[int] = []
        replay.reconstruct(log, on_turn=lambda s: seen_turns.append(s.turn))
    assert seen_turns == list(range(state.turn + 1))   # initial + one per turn


def test_history_snapshots_cover_every_turn_and_match_final():
    """History mode snapshots one board per turn via the on_turn hook: there is a
    board for the opening position plus each replayed turn, and the last equals a
    plain reconstruct (the live board)."""
    with _preserve_config():
        original, log = _play(456, policy="mixed", max_turns=40)
        states: list = []
        replay.reconstruct(log, on_turn=lambda s: states.append(copy.deepcopy(s)))
        plain, _ = replay.reconstruct(log)
    assert len(states) == original.turn + 1
    assert _snapshot(states[-1]) == _snapshot(plain) == _snapshot(original)


# --------------------------------------------------------------------------- #
# Rewind: truncate (mid-game, same file) and fork (finished, new file)
# --------------------------------------------------------------------------- #
def test_truncate_drops_later_turns_and_reopens():
    """Mid-game rewind: keep turns[:n] and clear the finished/winner outcome so
    the reconstructed board is exactly the one after n turns."""
    with _preserve_config():
        original, log = _play(1234, policy="autoplay", max_turns=30)
        assert log.finished
        full = log.turn_count
        log.truncate(5)
        assert log.turn_count == 5
        assert not log.finished and log.winner is None
        rebuilt, _ = replay.reconstruct(log)
    assert rebuilt.turn == 5 and full > 5


def test_fork_branches_without_mutating_original():
    """Finished-game rewind: fork a fresh log at turn n, leaving the original
    (a completed match one wants to keep) untouched."""
    with _preserve_config():
        _, log = _play(2468, policy="autoplay", max_turns=30)
    original_turns = list(log.turns)
    original_winner, original_finished = log.winner, log.finished
    forked = log.fork(6)
    assert forked is not log
    assert forked.turn_count == 6
    assert not forked.finished and forked.winner is None
    assert forked.seed == log.seed and forked.settings == log.settings
    assert forked.path is not None
    # the original is completely unmodified
    assert log.turns == original_turns
    assert log.winner == original_winner and log.finished == original_finished


def test_forked_log_reconstructs_to_branch_point():
    with _preserve_config():
        _, log = _play(1357, policy="autoplay", max_turns=30)
        forked = log.fork(8)
        rebuilt, _ = replay.reconstruct(forked)
    assert rebuilt.turn == 8


# --------------------------------------------------------------------------- #
# On-disk save / load / discovery (GAMES_DIR redirected to a tmp dir)
# --------------------------------------------------------------------------- #
@pytest.fixture
def games_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "GAMES_DIR", tmp_path)
    return tmp_path


def test_save_creates_file_and_load_round_trips(games_dir):
    log = replay.new_log(Settings(seed=3, players=3), 3)
    log.record_turn(_record([Order(1, 0, 1, 2)]))
    log.save()
    assert log.path is not None and log.path.exists()
    # no leftover temp file from the atomic write
    assert not list(games_dir.glob("*.tmp"))
    loaded = replay.load(log.path)
    assert loaded.seed == 3 and loaded.turn_count == 1


def test_list_and_latest_log_newest_first(games_dir):
    old = replay.new_log(Settings(seed=1), 1); old.record_turn(_record([])); old.save()
    new = replay.new_log(Settings(seed=2), 2); new.record_turn(_record([])); new.save()
    # make ``new`` unambiguously the more recently modified file
    import os, time
    t = time.time()
    os.utime(old.path, (t - 100, t - 100))
    os.utime(new.path, (t, t))
    assert replay.list_logs()[0] == new.path
    assert replay.latest_log().seed == 2


def test_latest_log_skips_older_format(games_dir):
    """A version-1 log recorded no AI orders, so resuming one would rebuild a
    different game than the player left; it is not offered."""
    current = replay.new_log(Settings(seed=8), 8)
    current.record_turn(_record([])); current.save()
    stale = replay.new_log(Settings(seed=9), 9)
    stale.record_turn(_record([])); stale.version = 1; stale.save()
    import os, time
    t = time.time()
    os.utime(current.path, (t - 100, t - 100))
    os.utime(stale.path, (t, t))       # the stale file is "newest" but must be skipped
    assert replay.latest_log().seed == 8


def test_latest_log_none_when_empty(games_dir):
    assert replay.latest_log() is None


def test_latest_log_skips_corrupt_file(games_dir):
    good = replay.new_log(Settings(seed=8), 8); good.record_turn(_record([])); good.save()
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
    log.record_turn(_record([])); log.save()
    first_path = log.path
    log.record_turn(_record([Order(1, 0, 1, 1)]), human_ai=True); log.save()
    assert log.path == first_path
    assert replay.load(first_path).turn_count == 2
    assert len(replay.list_logs()) == 1


# --------------------------------------------------------------------------- #
# Match identity and the wire form (what a posted score points at)
# --------------------------------------------------------------------------- #
def test_new_log_mints_a_match_id():
    a = replay.new_log(Settings(seed=5), 5)
    b = replay.new_log(Settings(seed=5), 5)
    assert replay._MATCH_ID_RE.match(a.match_id)
    # Same settings, same seed, different match: the id must not be derived from
    # the seed, or every player of a shared map would collide on one id.
    assert a.match_id != b.match_id


def test_match_id_survives_save_and_load(games_dir):
    log = replay.new_log(Settings(seed=3), 3)
    log.record_turn(_record([]))
    log.save()
    assert replay.load(log.path).match_id == log.match_id


def test_from_dict_replaces_a_missing_or_malformed_match_id():
    """A log written before the field existed, or edited by hand, gets a fresh id
    rather than carrying junk into an upload."""
    for bad in ({}, {"match_id": ""}, {"match_id": "../../etc"}, {"match_id": 17},
                {"match_id": "ABCDEF0123456789"}):   # uppercase is not the shape
        assert replay._MATCH_ID_RE.match(replay.GameLog.from_dict(bad).match_id)


def test_truncate_keeps_the_match_id_but_fork_mints_one():
    """A rewind continues the same match; a fork starts another one."""
    log = replay.new_log(Settings(seed=9), 9)
    for _ in range(4):
        log.record_turn(_record([]))
    original = log.match_id
    log.truncate(2)
    assert log.match_id == original
    assert log.fork(1).match_id != original


def test_encoded_round_trips_through_decode():
    with _preserve_config():
        _, log = _play(4242, policy="mixed", max_turns=25)
    restored = replay.GameLog.decode(log.encoded())
    assert restored.to_dict() == log.to_dict()


def test_encoded_is_much_smaller_than_the_saved_file():
    """The wire form is deflated; the file stays indented for reading."""
    with _preserve_config():
        _, log = _play(77, policy="autoplay", max_turns=60)
    assert len(log.encoded()) < len(json.dumps(log.to_dict(), indent=2)) / 3


def test_decode_reads_the_uncompressed_form():
    """Same tolerance Settings.from_token has: plain JSON starts '{', which zlib
    output never does."""
    import base64
    log = replay.new_log(Settings(seed=11), 11)
    log.record_turn(_record([Order(1, 0, 1, 2)]))
    raw = json.dumps(log.to_dict()).encode("utf-8")
    plain = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    assert replay.GameLog.decode(plain).to_dict() == log.to_dict()


@pytest.mark.parametrize("blob", ["", "not base64!!", "eyJub3QiOiJhIGxvZyJ9" * 0 + "AAAA"])
def test_decode_rejects_junk(blob):
    with pytest.raises(ValueError):
        replay.GameLog.decode(blob)


def test_setup_key_pins_the_seed_actually_played():
    """`main` resolves "roll a fresh seed" at game start and never writes it back,
    so hashing the log's stored settings alone would file a random-seed game under
    a key describing no particular map."""
    rolled = Settings(nodes=18, players=3)      # seed None: rolled at start
    log = replay.new_log(rolled, 4821)
    pinned = Settings(nodes=18, players=3, seed=4821)
    assert log.setup_key() == pinned.challenge_key()
    assert log.setup_key() != rolled.challenge_key()


def test_hand_turns_counts_the_turns_the_human_drove():
    log = replay.new_log(Settings(seed=2), 2)
    for autoplayed in (False, False, True, False, True):
        log.record_turn(_record([]), human_ai=autoplayed)
    assert log.hand_turns == 3 and log.turn_count == 5

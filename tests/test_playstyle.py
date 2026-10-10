"""The Playstyle readings (`tools/playstyle.py`) and the worker that stores them."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from starconquest import engine, replay
from tests.test_replay import _preserve_config
from tests.test_verify_scores import _won_match
from tools import playstyle, playstyle_worker


@pytest.fixture(scope="module")
def match():
    return _won_match()


def _read(log):
    with _preserve_config():
        return playstyle.readings(log)


def _all_by_hand(log):
    out = copy.deepcopy(log)
    for entry in out.turns:
        entry["ai"] = False
    return out


def test_a_reading_is_one_game_in_the_documented_shape(match):
    _settings, state, log, _hid = match
    r = _read(log)
    assert json.loads(json.dumps(r)) == r
    assert r["shape"] == playstyle.SHAPE and r["games"] == 1
    assert r["turns"] == [state.turn]
    assert r["hand_turns"] == log.hand_turns
    assert len(r["waves"]) == len(playstyle.SHARE_BANDS) + 1
    assert all(len(row) == playstyle.WAVE_BINS for row in r["waves"])
    assert set(r["relief"]) == set(playstyle.COVER)
    for key in playstyle.MEASURE_KEYS:
        assert len(r["reach"][key]) == len(r["never"][key]) == len(playstyle.THRESHOLDS)
        for at, never in zip(r["reach"][key], r["never"][key]):
            assert len(at) + never == 1
            assert all(0.0 <= x <= 1.0 for x in at)


def test_a_reading_repeats(match):
    log = match[2]
    assert _read(log) == _read(log)


def test_turns_on_autoplay_count_for_nothing_chosen(match):
    log = match[2]
    assert 0 < log.hand_turns < log.turn_count
    hand, every = _read(log), _read(_all_by_hand(log))
    waves = lambda r: sum(map(sum, r["waves"]))
    empties = lambda r: sum(n for n, _ in r["relief"].values())
    assert waves(hand) < waves(every)
    assert empties(hand) <= empties(every)
    # Holding the border is not a choice made turn by turn: it counts throughout.
    assert hand["frontier"] == every["frontier"]
    assert hand["reach"] == every["reach"]


def test_records_pool_by_adding(match):
    r = _read(match[2])
    two = playstyle.pool([r, r])
    assert two["games"] == 2 and two["turns"] == r["turns"] * 2
    assert two["waves"] == [[2 * x for x in row] for row in r["waves"]]
    assert two["frontier"] == [2 * x for x in r["frontier"]]
    assert two["reach"]["income"] == [xs * 2 for xs in r["reach"]["income"]]
    assert r["games"] == 1, "pool must not change what it was handed"
    assert playstyle.pool([]) == {}


def test_the_rev_moves_with_either_source(tmp_path, monkeypatch):
    a, b = tmp_path / "a.py", tmp_path / "b.py"
    a.write_text("x = 1\n")
    b.write_text("y = 1\n")
    monkeypatch.setattr(playstyle, "_REV_SOURCES", (a, b))
    before = playstyle.reading_rev()
    b.write_text("y = 2\n")
    assert playstyle.reading_rev() != before
    assert re.fullmatch(r"[0-9a-f]{16}", before)


def test_the_baseline_file_is_a_pooled_record_of_this_shape():
    text = playstyle.BASELINE.read_text()
    record = json.loads(re.search(r"export const BASELINE = (\{.*\});", text).group(1))
    assert record["shape"] == playstyle.SHAPE
    assert record["games"] == len(record["turns"]) > 0
    assert record["hand_turns"] == 0


# --------------------------------------------------------------------------- #
# The worker's bookkeeping
# --------------------------------------------------------------------------- #
def test_only_current_hand_played_logs_are_read(match):
    log = match[2]
    stale = copy.deepcopy(log)
    stale.match_id = "0" * 16
    stale.rules_version = engine.RULES_VERSION - 1
    rows = [
        {"match_id": log.match_id, "log": log.encoded()},
        {"match_id": log.match_id, "log": log.encoded()},
        {"match_id": "1" * 16, "log": "not a log"},
        {"match_id": stale.match_id, "log": stale.encoded()},
        {"match_id": "", "log": log.encoded()},
    ]
    assert list(playstyle_worker.replayable(rows, min_hand=1)) == [log.match_id]
    assert playstyle_worker.replayable(rows, min_hand=log.hand_turns + 1) == {}


def test_pending_reads_missing_and_stale_rows_and_prune_finds_orphans():
    logs = {"a" * 16: None, "b" * 16: None, "c" * 16: None}
    stored = {"a" * 16: "rev1", "b" * 16: "old", "d" * 16: "rev1"}
    assert playstyle_worker.pending(logs, stored, "rev1") == ["b" * 16, "c" * 16]
    public = {"a" * 16, "b" * 16, "c" * 16}
    assert playstyle_worker.orphans(public, stored) == ["d" * 16]


def test_a_reading_is_read_from_public_replays_only():
    text = Path(playstyle_worker.__file__).read_text()
    assert re.findall(r'\.select\(\s*"([a-z_]+)"', text) == ["public_replays", "playstyle_readings"]

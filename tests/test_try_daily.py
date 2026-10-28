"""Trying the daily's weekdays at home (`tools/try_daily.py`, `docs/design/daily.md`)."""

from __future__ import annotations

import argparse
import datetime as dt
import json

import pytest

from starconquest import ai, replay
from starconquest.settings import Settings
from tools import daily, try_daily

MONDAY = dt.date(2026, 10, 26)


@pytest.fixture(scope="module", autouse=True)
def _roster():
    ai.load_models()


def test_a_weekday_is_read_by_name_or_number():
    assert [try_daily.weekday(t) for t in ("mon", "Tuesday", "we", "thu", "FRI", "sat", "7")] == list(range(7))
    for bad in ("t", "s", "0", "8", "funday", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            try_daily.weekday(bad)
    assert try_daily.next_weekday(5, MONDAY) == dt.date(2026, 10, 31)
    assert try_daily.next_weekday(0, MONDAY) == MONDAY


def test_a_trial_deals_the_same_on_any_week_and_never_the_days_own():
    sat, next_sat = MONDAY + dt.timedelta(days=5), MONDAY + dt.timedelta(days=12)
    a = [s.challenge_key() for s in daily.candidates(sat, limit=2, trial="7")]
    assert a == [s.challenge_key() for s in daily.candidates(next_sat, limit=2, trial="7")]
    assert a != [s.challenge_key() for s in daily.candidates(sat, limit=2, trial="8")]
    assert a != [s.challenge_key() for s in daily.candidates(sat, limit=2)]
    s = try_daily.deal(sat, "7")
    lo, hi = daily.systems(5)
    assert lo <= s.system_count() <= hi and daily.PLAYERS[5][0] <= s.players <= daily.PLAYERS[5][1]


def test_a_probed_trial_records_apart_from_the_day(tmp_path, monkeypatch):
    def play_one(key, stage, bot, max_turns):
        return {"key": key, "bot": bot, "won": True, "turns": 30, "lost": 1, "timed_out": False, "secs": 0.0}
    monkeypatch.setattr(daily.gauntletgen, "play_one", play_one)
    row = daily.make(MONDAY, results=tmp_path, limit=1, out=lambda _: None, trial="7")
    assert row is not None
    assert (tmp_path / "try" / "mon-7.jsonl").exists() and not (tmp_path / f"{MONDAY}.jsonl").exists()


def test_the_saved_setup_opens_as_the_dealt_map(tmp_path, monkeypatch):
    monkeypatch.setattr(try_daily, "TRY_DIR", tmp_path)
    s = try_daily.deal(MONDAY, "3")
    path = try_daily.save(s, "mon-3")
    log = replay.GameLog.from_dict(json.loads(path.read_text()))
    back = Settings.from_dict(log.settings)
    back.seed = log.seed
    assert back.challenge_key() == s.challenge_key() and not log.turns


def test_one_of_a_weekday_a_day_or_the_week_is_asked_for():
    assert try_daily.parse_args(["sat"]).weekday == 5
    assert try_daily.parse_args(["--day", "2026-11-01"]).day == dt.date(2026, 11, 1)
    for argv in ([], ["sat", "--week"], ["sat", "--day", "2026-11-01"]):
        with pytest.raises(SystemExit):
            try_daily.parse_args(argv)

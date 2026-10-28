"""The daily challenge's worker (`tools/daily.py`, `docs/design/daily.md`)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from starconquest import ai, settings
from tools import daily

MONDAY = dt.date(2026, 10, 26)


@pytest.fixture(scope="module", autouse=True)
def _roster():
    ai.load_models()


def test_the_board_and_the_cap_grow_through_the_week():
    assert [daily.systems(w) for w in range(7)] == [
        (11, 14), (13, 16), (15, 18), (17, 20), (19, 22), (21, 24), (23, 26)]
    assert [daily.turn_cap(w) for w in range(7)] == [80, 85, 90, 95, 100, 105, 110]
    assert daily.PLAYERS == ((3, 4), (3, 4), (3, 5), (3, 5), (3, 6), (4, 6), (5, 6))


def test_the_spec_file_deals():
    for w in range(7):
        spec = daily.day_spec(MONDAY + dt.timedelta(days=w))
        assert spec.stages == 1 and spec.seed is None
        assert "lane_turns" in spec.rules and "seed" not in daily.base_spec()


@pytest.mark.parametrize("w", [0, 3, 6])
def test_a_days_candidates_keep_its_weekday_and_lanes(w):
    day = MONDAY + dt.timedelta(days=w)
    depth, cands = daily.candidates(day, limit=4)
    assert 0 <= depth <= 1
    lo, hi = daily.systems(w)
    plo, phi = daily.PLAYERS[w]
    for s in cands:
        assert lo <= s.system_count() <= hi and plo <= s.players <= phi
        assert 6 <= s.ship_ly_per_turn <= 12 and s.ship_speed_growth_pct == 0
        assert 2 <= settings.lane_turns(settings.lane_lengths(s), s.ship_ly_per_turn)[1] <= 5
        assert isinstance(s.seed, int)


def test_a_days_candidates_are_a_pure_function_of_the_date():
    a = [s.challenge_key() for s in daily.candidates(MONDAY, limit=3)[1]]
    b = [s.challenge_key() for s in daily.candidates(MONDAY, limit=3)[1]]
    c = [s.challenge_key() for s in daily.candidates(MONDAY + dt.timedelta(days=7), limit=3)[1]]
    assert a == b and a != c


def _fake_play(results: dict):
    """A stand-in for a probe game: ``results[(candidate, bot)]`` turns to win,
    or nothing for a loss."""
    calls = []

    def play_one(key, stage, bot, max_turns):
        calls.append((key, bot))
        turns = results.get((len({k for k, _ in calls}) - 1, bot))
        won = turns is not None and turns <= max_turns
        return {"key": key, "bot": bot, "won": won, "turns": turns or max_turns, "lost": 3,
                "timed_out": not won, "secs": 0.0}
    return play_one, calls


def test_the_first_candidate_a_probe_wins_inside_the_cap_is_the_day(tmp_path, monkeypatch):
    # Candidate 0 is won only past Monday's 80; candidate 1 by the second probe.
    play, calls = _fake_play({(0, "thinker"): 81, (1, "thinker"): 40})
    monkeypatch.setattr(daily.gauntletgen, "play_one", play)
    row = daily.make(MONDAY, results=tmp_path, limit=4, out=lambda _: None)
    assert row is not None and row["spec"]["candidate"] == 1 and row["spec"]["turn_cap"] == 80
    assert [p["bot"] for p in row["probes"]] == ["heuristic+lean", "thinker"]
    assert row["probes"][-1]["won"] and row["day"] == MONDAY.isoformat()
    assert row["settings"] == daily.dealer.stage_dict(daily.candidates(MONDAY, limit=4)[1][1])
    # Probes stop at the first win: five on candidate 0, two on candidate 1.
    assert len(calls) == 7
    lines = (tmp_path / f"{MONDAY.isoformat()}.jsonl").read_text().splitlines()
    assert len(lines) == 7 and json.loads(lines[-1])["candidate"] == 1


def test_a_rerun_resumes_from_the_recorded_games(tmp_path, monkeypatch):
    play, calls = _fake_play({(0, "marshal"): 50})
    monkeypatch.setattr(daily.gauntletgen, "play_one", play)
    first = daily.make(MONDAY, results=tmp_path, limit=2, out=lambda _: None)
    assert len(calls) == 5
    again = daily.make(MONDAY, results=tmp_path, limit=2, out=lambda _: None)
    assert again == first and len(calls) == 5


def test_a_day_nobody_wins_is_left_empty(tmp_path, monkeypatch):
    play, calls = _fake_play({})
    monkeypatch.setattr(daily.gauntletgen, "play_one", play)
    assert daily.make(MONDAY, results=tmp_path, limit=2, out=lambda _: None) is None
    assert len(calls) == 10


def test_today_and_tomorrow_are_made():
    assert daily.days_to_make(MONDAY) == [MONDAY, MONDAY + dt.timedelta(days=1)]


# --------------------------------------------------------------------------- #
# The puzzle
# --------------------------------------------------------------------------- #
def test_a_puzzle_is_a_small_open_duel_against_the_weekdays_opponent():
    for w, name in enumerate(daily.PUZZLE_OPPONENTS):
        day = MONDAY + dt.timedelta(days=w)
        depth, cands = daily.candidates(day, limit=3, kind="puzzle")
        assert depth == 0
        strategy, params = daily.gauntletgen.probe_profile(name)
        for s in cands:
            assert s.players == 2 and s.mode == "random" and 5 <= s.system_count() <= 8
            assert settings.fog_off(s) and 15 <= s.ship_ly_per_turn <= 20
            assert s.seat_strategy(2) == strategy and s.seat_params(2) == params
            # The opponent survives being stored, a tweak's params included.
            stored = Settings.from_dict(daily.dealer.stage_dict(s))
            assert stored.seat_params(2) == params and stored.challenge_key() == s.challenge_key()


def test_a_puzzle_deals_from_its_own_stream():
    board = [s.challenge_key() for s in daily.candidates(MONDAY, limit=2)[1]]
    puzzle = [s.challenge_key() for s in daily.candidates(MONDAY, limit=2, kind="puzzle")[1]]
    again = [s.challenge_key() for s in daily.candidates(MONDAY, limit=2, kind="puzzle")[1]]
    assert puzzle == again and not set(board) & set(puzzle)


def test_a_puzzle_row_names_its_kind_and_opponent(tmp_path, monkeypatch):
    play, calls = _fake_play({(0, "thinker"): 30})
    monkeypatch.setattr(daily.gauntletgen, "play_one", play)
    sunday = MONDAY + dt.timedelta(days=6)
    row = daily.make(sunday, results=tmp_path, limit=2, out=lambda _: None, kind="puzzle")
    assert row is not None and row["kind"] == "puzzle" and row["spec"]["opponent"] == "actuary"
    assert row["spec"]["turn_cap"] == daily.PUZZLE_TURN_CAP
    assert (tmp_path / "puzzle" / f"{sunday.isoformat()}.jsonl").exists()
    assert not (tmp_path / f"{sunday.isoformat()}.jsonl").exists()
    play, _ = _fake_play({(0, "thinker"): 30})
    monkeypatch.setattr(daily.gauntletgen, "play_one", play)
    board = daily.make(MONDAY, results=tmp_path, limit=2, out=lambda _: None)
    assert board is not None and board["kind"] == "board"


# --------------------------------------------------------------------------- #
# The game's side (`starconquest/daily.py`)
# --------------------------------------------------------------------------- #
from starconquest import daily as game_daily, webstore    # noqa: E402
from starconquest.settings import Settings    # noqa: E402


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(webstore, "_file_path", lambda: tmp_path / "kv.json")


def test_a_daily_link_names_its_kind_and_today_or_a_day():
    assert game_daily.link("daily") == ("board", "")
    assert game_daily.link("daily=2026-10-28") == ("board", "2026-10-28")
    assert game_daily.link("puzzle") == ("puzzle", "")
    assert game_daily.link("puzzle=2026-10-28") == ("puzzle", "2026-10-28")
    for other in ("daily=2026-02-30", "daily=soon", "puzzle=x", "g=abc", "", "dailyx", "puzzles"):
        assert game_daily.link(other) is None, other


def test_a_fetch_asks_for_the_puzzle_by_kind(monkeypatch):
    asked = []
    monkeypatch.setattr(game_daily.webstore, "leaderboard_url", lambda path: "https://x" + path)
    monkeypatch.setattr(game_daily.pbp, "request", lambda url: asked.append(url) or url)
    game_daily.fetch()
    game_daily.fetch("2026-10-28")
    game_daily.fetch("", "puzzle")
    game_daily.fetch("2026-10-28", "puzzle")
    assert asked == ["https://x/api/daily", "https://x/api/daily?day=2026-10-28",
                     "https://x/api/daily?kind=puzzle", "https://x/api/daily?day=2026-10-28&kind=puzzle"]
    assert game_daily.fetch("", "chess") is None


def test_a_reply_is_read_as_a_day_and_a_setup():
    stage = daily.dealer.stage_dict(daily.candidates(MONDAY, limit=1)[1][0])
    got = game_daily.from_reply(json.dumps({"day": "2026-10-26", "now": 1, "settings": stage,
                                            "rules_version": 2}))
    assert got is not None and got[:2] == ("2026-10-26", "board")
    assert got[2].challenge_key() == Settings.from_dict(stage).challenge_key()
    puzzle = game_daily.from_reply(json.dumps({"day": "2026-10-26", "kind": "puzzle", "settings": stage}))
    assert puzzle is not None and puzzle[1] == "puzzle"
    for bad in ("", "nope", json.dumps({"day": "x", "settings": stage}),
                json.dumps({"day": "2026-10-26", "settings": {"mode": "random"}}),
                json.dumps({"day": "2026-10-26", "kind": "chess", "settings": stage})):
        assert game_daily.from_reply(bad) is None


def test_a_remembered_daily_is_known_by_its_key(store):
    a, b = (daily.candidates(MONDAY + dt.timedelta(days=i), limit=1)[1][0] for i in (0, 1))
    p = daily.candidates(MONDAY, limit=1, kind="puzzle")[1][0]
    assert game_daily.daily_of(a) == ("", "")
    game_daily.remember("2026-10-26", a)
    game_daily.remember("2026-10-27", b)
    game_daily.remember("2026-10-26", p, "puzzle")
    assert game_daily.daily_of(a) == ("2026-10-26", "board")
    assert game_daily.daily_of(b) == ("2026-10-27", "board")
    assert game_daily.daily_of(p) == ("2026-10-26", "puzzle")
    for i in range(game_daily.REMEMBER_MAX):
        s = Settings.from_dict(dict(daily.dealer.stage_dict(a), seed=1000 + i))
        game_daily.remember((MONDAY + dt.timedelta(days=30 + i)).isoformat(), s)
    assert game_daily.daily_of(a) == ("", "")          # the oldest fell off


def test_a_daily_remembered_before_the_puzzle_is_a_board(store):
    a = daily.candidates(MONDAY, limit=1)[1][0]
    webstore.set(game_daily.WEB_DAILIES_KEY, json.dumps({a.challenge_key(): "2026-10-26", "junk": [1]}))
    assert game_daily.daily_of(a) == ("2026-10-26", "board")


def test_the_result_line_gives_nothing_away():
    s = daily.candidates(MONDAY, limit=1)[1][0]
    text = game_daily.result_line("2026-10-28", s, 23, 7)
    assert text.splitlines()[:2] == [f"Star Conquest daily · Wed 28 Oct · {s.system_count()} systems",
                                     "Won in 23 turns, 7 ships lost"]
    assert str(s.seed) not in text and text.splitlines()[-1].endswith("/game/#daily")


def test_a_puzzle_result_names_its_opponent():
    for w, name in ((1, "heuristic+lean"), (3, "Thinker · Claudebot"), (6, "Actuary · Field")):
        s = daily.candidates(MONDAY + dt.timedelta(days=w), limit=1, kind="puzzle")[1][0]
        text = game_daily.result_line("2026-10-28", s, 9, 2, "puzzle")
        assert text.splitlines()[0] == (f"Star Conquest daily puzzle · Wed 28 Oct · {s.system_count()} systems"
                                        f" vs {name}")
        assert str(s.seed) not in text and text.splitlines()[-1].endswith("/game/#puzzle")
        assert game_daily.label("2026-10-28", s, "puzzle").endswith(f"vs {name}")

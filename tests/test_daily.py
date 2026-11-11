"""The daily challenge's worker (`tools/daily.py`, `docs/design/daily.md`)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

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
    cands = daily.candidates(day, limit=4)
    lo, hi = daily.systems(w)
    plo, phi = daily.PLAYERS[w]
    for s in cands:
        assert lo <= s.system_count() <= hi and plo <= s.players <= phi
        assert 6 <= s.ship_ly_per_turn <= 12 and s.ship_speed_growth_pct == 0
        turns = settings.lane_turns(settings.lane_lengths(s), s.ship_ly_per_turn)
        assert turns is not None and 2 <= turns[1] <= 5
        assert isinstance(s.seed, int)


def test_a_board_can_seat_any_mix_of_the_allowed_bots():
    # Each seat is drawn on its own, so the weakest and strongest can meet.
    allowed = set(daily.base_spec()["opponents"]["in"])
    staged = {e.partition(":")[0] for e in allowed if ":" in e}

    def entry(s, seat):
        name = s.seat_strategy(seat)
        return f"{name}:{s.seat_params(seat).aux:g}" if name in staged else name

    seen, mixed = set(), False
    for i in range(14):
        for s in daily.candidates(MONDAY + dt.timedelta(days=i), limit=3):
            seats = {entry(s, seat) for seat in range(2, s.players + 1)}
            assert seats <= allowed
            seen |= seats
            mixed |= "rusherplus" in seats and any(e.startswith("actuary") for e in seats)
    assert seen == allowed and mixed


def test_a_days_candidates_are_a_pure_function_of_the_date():
    a = [s.challenge_key() for s in daily.candidates(MONDAY, limit=3)]
    b = [s.challenge_key() for s in daily.candidates(MONDAY, limit=3)]
    c = [s.challenge_key() for s in daily.candidates(MONDAY + dt.timedelta(days=7), limit=3)]
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
    assert row["settings"] == daily.dealer.stage_dict(daily.candidates(MONDAY, limit=4)[1])
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
        cands = daily.candidates(day, limit=3, kind="puzzle")
        strategy, params = daily.gauntletgen.probe_profile(name)
        for s in cands:
            assert s.players == 2 and s.mode == "random" and 5 <= s.system_count() <= 8
            assert settings.fog_off(s) and 15 <= s.ship_ly_per_turn <= 20
            assert s.seat_strategy(2) == strategy and s.seat_params(2) == params
            # The opponent survives being stored, a tweak's params included.
            stored = Settings.from_dict(daily.dealer.stage_dict(s))
            assert stored.seat_params(2) == params and stored.challenge_key() == s.challenge_key()


def test_a_puzzle_deals_from_its_own_stream():
    board = [s.challenge_key() for s in daily.candidates(MONDAY, limit=2)]
    puzzle = [s.challenge_key() for s in daily.candidates(MONDAY, limit=2, kind="puzzle")]
    again = [s.challenge_key() for s in daily.candidates(MONDAY, limit=2, kind="puzzle")]
    assert puzzle == again and not set(board) & set(puzzle)


def test_a_puzzle_row_names_its_kind_and_opponent(tmp_path, monkeypatch):
    play, _calls = _fake_play({(0, "thinker"): 30})
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
from starconquest import daily as game_daily, webstore
from starconquest.settings import Settings


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
    stage = daily.dealer.stage_dict(daily.candidates(MONDAY, limit=1)[0])
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
    a, b = (daily.candidates(MONDAY + dt.timedelta(days=i), limit=1)[0] for i in (0, 1))
    p = daily.candidates(MONDAY, limit=1, kind="puzzle")[0]
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
    a = daily.candidates(MONDAY, limit=1)[0]
    webstore.set(game_daily.WEB_DAILIES_KEY, json.dumps({a.challenge_key(): "2026-10-26", "junk": [1]}))
    assert game_daily.daily_of(a) == ("2026-10-26", "board")


def test_a_dailys_setup_carries_its_banner_until_a_knob_moves(store):
    from starconquest import setupmenu
    s = daily.candidates(MONDAY, limit=1)[0]
    ms = setupmenu.Menu()
    plain = setupmenu.view(ms, s)["subtitle"]
    game_daily.remember("2026-11-10", s)
    banner = setupmenu.view(ms, s)["subtitle"]
    assert [line["text"] for line in banner][:1] == ["DAILY MAP #14 · Tue 10 Nov"]
    assert banner[1]["text"] == daily.dealer.describe(s)
    s.seed += 1
    assert setupmenu.view(ms, s)["subtitle"] == plain


def test_the_result_line_gives_nothing_away():
    """Wordle-short: the day's number and the result, then the link. No date,
    map size, opponent or seed."""
    s = daily.candidates(MONDAY, limit=1)[0]
    text = game_daily.result_line("2026-10-28", 23, 7)
    assert text.splitlines()[0] == "Star Conquest daily #1 - 23 turns, 7 ships"
    assert str(s.seed) not in text and text.splitlines()[-1].endswith("/play/#daily")
    puzzle = game_daily.result_line("2026-11-07", 8, 9, "puzzle")
    assert puzzle.splitlines()[0] == "Star Conquest puzzle #11 - 8 turns, 9 ships"
    assert puzzle.splitlines()[-1].endswith("/play/#puzzle")
    assert game_daily.number(game_daily.FIRST_DAY) == 1


def test_a_puzzle_label_names_its_opponent():
    for w, name in ((1, "heuristic+lean"), (3, "Thinker · Claudebot"), (6, "Actuary · Field")):
        s = daily.candidates(MONDAY + dt.timedelta(days=w), limit=1, kind="puzzle")[0]
        assert game_daily.banner("2026-10-28", s, "puzzle")[1].endswith(f"vs {name}")
        assert game_daily.banner("2026-11-07", s, "puzzle")[0] == "DAILY PUZZLE #11 · Sat 7 Nov"


# --------------------------------------------------------------------------- #
# The puzzle's par
# --------------------------------------------------------------------------- #
def test_a_par_check_asks_about_the_result(monkeypatch):
    asked = []
    monkeypatch.setattr(game_daily.webstore, "leaderboard_url", lambda path: "https://x" + path)
    monkeypatch.setattr(game_daily.pbp, "request", lambda url: asked.append(url) or url)
    game_daily.par_check("2026-11-04", 10, 3)
    assert asked == ["https://x/api/daily?day=2026-11-04&kind=puzzle&turns=10&lost=3"]
    assert game_daily.par_check("soon", 10, 3) is None
    assert game_daily.par_check("2026-11-04", 0, 3) is None
    monkeypatch.setattr(game_daily.webstore, "leaderboard_url", lambda path: "")
    assert game_daily.par_check("2026-11-04", 10, 3) is None


def test_a_par_reply_is_read_only_when_it_names_the_par():
    reply = {"day": "2026-11-04", "kind": "puzzle",
             "par": {"turns": 10, "lost": 3, "status": "proved", "turns_only": True, "beaten": False}}
    assert game_daily.par_from_reply(json.dumps(reply)) == game_daily.Par(10, 3, True, True)
    likely = dict(reply, par={"turns": 10, "lost": 3, "status": "likely"})
    assert game_daily.par_from_reply(json.dumps(likely)) == game_daily.Par(10, 3, False, False)
    for bad in ("", "nope", json.dumps({"par": None}), json.dumps({"par": {"turns": "10", "lost": 3}}),
                json.dumps({"par": {"turns": True, "lost": 3}}), json.dumps([1])):
        assert game_daily.par_from_reply(bad) is None


def test_the_congratulation_is_only_for_a_result_that_met_the_par():
    proved, likely = game_daily.Par(10, 3, True, False), game_daily.Par(10, 3, False, False)
    assert game_daily.par_note(proved, 10, 3).startswith("Perfect puzzle!")
    assert "proven the fastest" in game_daily.par_note(game_daily.Par(10, 3, True, True), 10, 3)
    assert game_daily.par_note(likely, 10, 3).startswith("Par!")
    assert game_daily.par_note(likely, 9, 8).startswith("Beyond par! You won faster")
    assert game_daily.par_note(proved, 10, 2).startswith("Beyond par! The par's 10 turns")
    assert game_daily.par_note(likely, 10, 4) == ""
    assert game_daily.par_note(likely, 11, 0) == ""


def test_the_board_says_it_in_the_games_words():
    """`leaderboard/js/daily.mjs`'s `parNote` mirrors `par_note`: every sentence
    the game can say is in the board's file, and the JS test pins the same cases."""
    js = (Path(__file__).resolve().parent.parent / "leaderboard" / "js" / "daily.mjs").read_text()
    cases = [(game_daily.Par(10, 3, True, False), 10, 3), (game_daily.Par(10, 3, True, True), 10, 3),
             (game_daily.Par(10, 3, False, False), 10, 3), (game_daily.Par(10, 3, False, False), 9, 8),
             (game_daily.Par(10, 3, True, False), 10, 2)]
    tests = (Path(__file__).resolve().parent.parent / "leaderboard" / "tests" / "daily.test.mjs").read_text()
    for par, turns, lost in cases:
        text = game_daily.par_note(par, turns, lost)
        assert f'"{text}"' in tests, text
        assert text.split("!")[0] + "!" in js



def test_the_boards_result_line_is_the_games(monkeypatch):
    """`leaderboard/js/daily.mjs`'s `resultLine` (the daily page's "Copy daily
    result") copies what the end screen copies: the JS test pins these strings."""
    tests = (Path(__file__).resolve().parent.parent / "leaderboard" / "tests" / "daily.test.mjs").read_text()
    monkeypatch.setattr(game_daily.webstore, "leaderboard_origin", lambda: "https://example.org/")
    for day, turns, lost, kind in (("2026-11-08", 12, 3, game_daily.BOARD), ("2026-11-08", 7, 0, game_daily.PUZZLE)):
        text = game_daily.result_line(day, turns, lost, kind)
        assert '"' + text.replace("\n", "\\n") + '"' in tests, text
    monkeypatch.setattr(game_daily.webstore, "leaderboard_origin", lambda: "")
    assert f'"{game_daily.result_line("2026-10-28", 9, 1)}"' in tests

"""tools/bot_rerun.py: which rows a changed bot reaches, and the before/after comparison.

No network: planning is a pure function of the rows and maps it is handed.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from starconquest import ai
from starconquest.settings import Settings
from tools import bot_rerun

CUTOFF = datetime(2026, 11, 11, 16, 51, tzinfo=UTC)
OLD, NEW = "2026-11-01T00:00:00+00:00", "2026-11-12T00:00:00.123456+00:00"


def _game(key: str, **seats: tuple[str, float]) -> dict:
    cfg = Settings(mode="random", players=3, nodes=16, seed=12)
    for seat, (name, aux) in seats.items():
        index = int(seat.removeprefix("seat")) - 1
        cfg.ai_strategy[index] = name
        cfg.ai[index].aux = aux
    return {"game_key": key, "seed": 12, "settings_json": cfg.to_dict()}


def test_a_seat_uses_what_it_is_deals_or_borrows():
    ai.load_models()
    assert bot_rerun.uses("rusherplus", 1.0, "rusherplus")
    assert not bot_rerun.uses("marshal", 1.0, "rusherplus")
    assert bot_rerun.uses("chaotic", 1.0, "rusherplus")
    assert not bot_rerun.uses("chaotic", 1.0, "knower")
    assert not bot_rerun.uses("knower", 1.0, "rusherplus")     # Predict borrows nothing
    assert bot_rerun.uses("knower", 2.0, "rusherplus")         # Search borrows openings
    assert not bot_rerun.uses("knower", 2.0, "actuary")
    assert bot_rerun.uses("knower", 3.0, "actuary")            # ...and Council more
    assert bot_rerun.uses("knower", 9.0, "rusherplus")         # past the top reads as Search
    assert not bot_rerun.uses("knower", 9.0, "actuary")


def test_a_rivals_map_reruns_whole_and_elsewhere_only_the_bots_that_use_it():
    ai.load_models()
    games = [_game("rival", seat2=("rusherplus", 1.0), seat3=("marshal", 1.0)),
             _game("clean", seat2=("marshal", 1.0), seat3=("thinker", 1.0)),
             _game("search", seat2=("knower", 2.0), seat3=("marshal", 1.0))]
    bots = ("chaotic", "knower", "marshal", "rusherplus")
    rows = [{"game_key": g["game_key"], "bot": b, "computed_at": OLD} for g in games for b in bots]
    rows.append({"game_key": "rival", "bot": "actuary", "computed_at": NEW})

    plan = bot_rerun.plan_changed(games, rows, "rusherplus", CUTOFF, ai.available_strategies())

    assert plan["rival"]["bots"] == ["chaotic", "knower", "marshal", "rusherplus"]
    assert plan["rival"]["why"] == "rival seat 2 rusherplus@1"
    assert plan["clean"]["bots"] == ["chaotic", "knower", "rusherplus"]
    assert plan["clean"]["why"] == "replayed bot runs it"
    assert plan["search"]["bots"] == ["chaotic", "knower", "marshal", "rusherplus"]
    assert plan["search"]["why"] == "rival seat 2 knower@2"

    assert bot_rerun.plan_changed(games, rows, "rusherplus", CUTOFF, ["marshal"]) == {
        "rival": {"bots": ["marshal"], "why": "rival seat 2 rusherplus@1"},
        "search": {"bots": ["marshal"], "why": "rival seat 2 knower@2"}}


def test_listed_maps_rerun_their_existing_rows():
    rows = [{"game_key": "a", "bot": "marshal", "computed_at": OLD},
            {"game_key": "a", "bot": "knower", "computed_at": NEW},
            {"game_key": "b", "bot": "marshal", "computed_at": OLD},
            {"game_key": "j:c", "bot": "thinker", "computed_at": OLD}]
    assert bot_rerun.plan_keys(["a", "j:c"], rows, ["marshal", "knower", "thinker"], None) == {
        "a": {"bots": ["knower", "marshal"], "why": "listed"},
        "j:c": {"bots": ["thinker"], "why": "listed"}}
    assert bot_rerun.plan_keys(["a"], rows, ["marshal", "knower"], CUTOFF) == {
        "a": {"bots": ["marshal"], "why": "listed"}}


def test_compare_counts_what_moved():
    def row(won, turns, lost):
        return {"won": won, "turns": turns, "lost": lost}
    before = {("a", "marshal"): row(True, 40, 10), ("b", "marshal"): row(True, 50, 5),
              ("c", "marshal"): row(False, 90, 30), ("a", "knower"): row(True, 30, 2),
              ("gone", "knower"): row(True, 30, 2)}
    after = {("a", "marshal"): row(True, 36, 14), ("b", "marshal"): row(False, 70, 20),
             ("c", "marshal"): row(False, 90, 30), ("a", "knower"): row(True, 30, 2)}

    changes, stats = bot_rerun.compare(before, after)

    assert [(c["game_key"], c["bot"]) for c in changes] == [("a", "marshal"), ("b", "marshal")]
    assert stats["marshal"] == {"rows": 3, "moved": 2, "gained": 0, "dropped": 1,
                                "both": 1, "dturns": -4, "dlost": 4}
    assert stats["knower"] == {"rows": 1, "moved": 0, "gained": 0, "dropped": 0,
                               "both": 1, "dturns": 0, "dlost": 0}


def test_load_env_reads_a_dotenv_file(tmp_path, monkeypatch):
    for name in ("SC_RERUN_A", "SC_RERUN_B", "SC_RERUN_C"):
        monkeypatch.setenv(name, "unset")
    path = tmp_path / ".env"
    path.write_text("# comment\n\nSC_RERUN_A=plain\nexport SC_RERUN_B='quoted value'\n"
                    'SC_RERUN_C = "x=y"\nnot a setting\n')
    bot_rerun.load_env(path)
    assert (os.environ["SC_RERUN_A"], os.environ["SC_RERUN_B"], os.environ["SC_RERUN_C"]) == (
        "plain", "quoted value", "x=y")

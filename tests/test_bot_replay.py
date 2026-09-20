"""The leaderboard bot column's own new behaviour: a win stores a real replay.

No network here either (see ``test_verify_scores.py``'s note on the same
point) — ``_row_for`` is the pure decision the worker makes once
``sim.play_settings`` has already run, and that is what is worth pinning: a
row must carry exactly what a Watch link needs on a win, and exactly nothing
that could be mistaken for one on a loss.
"""

from __future__ import annotations

from starconquest import ai, replay
from starconquest.settings import Settings
from tests import sim
from tools import bot_replay


def _job(**kw) -> bot_replay.Job:
    cfg = Settings(mode="random", players=3, nodes=16, seed=11)
    fields = {"game_key": "abc123", "cfg": cfg, "seed": 11, "bot": "marshal", "aux": 1.0, **kw}
    return bot_replay.Job(**fields)


def _play(job: bot_replay.Job, bot: str | None = None):
    ai.load_models()
    log = replay.new_log(job.cfg, job.seed)
    result = sim.play_settings(job.cfg, job.seed, bot or job.bot, log=log)
    return result, log


def test_a_win_stores_the_exact_replay_a_watch_link_would_play():
    job = _job()
    result, log = _play(job)
    assert result.won   # this map's seed 11 is a real marshal win (see test_sim.py)

    row = bot_replay._row_for(job, result, log, rev="rev1")
    assert row["match_id"] == log.match_id
    assert row["rules_version"] == log.rules_version
    assert row["log"] == log.encoded()

    # The row is self-contained: decoding *only* what was stored reconstructs
    # the same result the row's own won/turns/lost report, with nothing else
    # consulted — the whole point of storing a log rather than a set of
    # ingredients to re-decide from.
    decoded = replay.GameLog.decode(row["log"])
    state, _ = replay.reconstruct(decoded)
    seat = state.human()
    assert (state.winner == seat.id) == row["won"] == result.won
    assert state.turn == row["turns"] == result.turns
    assert seat.ships_lost == row["lost"] == result.lost


def test_a_loss_stores_no_replay():
    """No Watch link is offered for a loss (`botOrder` doesn't even rank one),
    so nothing is worth keeping — and the row must actively blank these
    columns, not merely omit them, so a bot retuned into losing a map it used
    to win doesn't leave last time's replay pointing at a result this row no
    longer reports."""
    job = _job(bot="heuristic")
    # max_turns=1 guarantees a non-win regardless of this map's own balance.
    log = replay.new_log(job.cfg, job.seed)
    result = sim.play_settings(job.cfg, job.seed, "heuristic", max_turns=1, log=log)
    assert not result.won

    row = bot_replay._row_for(job, result, log, rev="rev1")
    assert row["match_id"] == ""
    assert row["log"] == ""
    assert row["rules_version"] == 0


def test_engine_rev_moves_if_the_replay_harness_changes():
    """`sim.py` decides how a replay is played (seat handover, per-turn
    stepping — and now, what gets recorded), so a change there must mark every
    cached row stale exactly as a change to `engine.py` would. Pinned as a
    property of `_OUTCOME_HARNESS` rather than a specific digest, so it survives
    the file's own contents changing."""
    assert bot_replay._OUTCOME_HARNESS == ("tests", "sim.py")
    from pathlib import Path

    sim_path = bot_replay.ROOT.joinpath(*bot_replay._OUTCOME_HARNESS)
    assert isinstance(sim_path, Path) and sim_path.is_file()

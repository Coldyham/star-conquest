"""The replay-parity corpus reproduces exactly on this build.

`tests/fixtures/parity_replays.jsonl.gz` (from `tools/parity_replays.py`) holds
each case's board digest after every turn. The Pyodide build must reproduce the
same digests; these tests hold the native build to them, so a mismatch there
means the browser runtime, not a drift in the fixture.
"""

from __future__ import annotations

import math

import pytest

from starconquest import ai
from tools import parity_replays as parity

CASES = parity.load_fixture()


@pytest.fixture(scope="module", autouse=True)
def _bots():
    ai.load_models()
    ai.set_budget_scale(math.inf)
    yield
    ai.set_budget_scale(1.0)


@pytest.mark.parametrize("row", CASES, ids=[r["name"] for r in CASES])
def test_log_reconstructs_to_the_recorded_boards(row):
    got = parity.reconstruct_digests(row["log"])
    assert parity.first_mismatch(row["digests"], got) is None


LIVE = [r for r in CASES if r["kind"] == "live"]


@pytest.mark.parametrize("row", LIVE, ids=[r["name"] for r in LIVE])
def test_bots_play_the_recorded_game_again(row):
    got = parity.replay_live(row)
    assert parity.first_mismatch(row["digests"], got) is None


def test_the_corpus_covers_every_ladder_bot_and_a_finished_game():
    played = {name for r in CASES for name in (r["log"].get("strategies") or {}).values()}
    assert set(ai.LADDER_ORDER) <= played
    assert any(r["log"].get("winner") is not None for r in CASES)

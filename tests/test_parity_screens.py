"""The committed screen recipes match the screen list `tools/parity_shots.py` draws.

The HTML build replays `tests/fixtures/parity_screens.json` to shoot its side
of the comparison, so a screen added to the tool must reach the fixture too
(rerun the tool).
"""

from __future__ import annotations

import json
from pathlib import Path

from tools import parity_shots

FIXTURE = Path(__file__).parent / "fixtures" / "parity_screens.json"


def test_every_screen_is_in_the_fixture_with_its_sizes():
    data = json.loads(FIXTURE.read_text())
    stored = {r["name"]: r for r in data["screens"]}
    for recipe in parity_shots.SCREENS:
        assert recipe["name"] in stored, f"rerun tools/parity_shots.py for {recipe['name']}"
        got = stored[recipe["name"]]
        assert (got["surface"], got["sizes"]) == (recipe["surface"], recipe["sizes"])
        assert [s[0] for s in got["steps"]] == [s[0] for s in recipe["steps"]]
    assert set(stored) == {r["name"] for r in parity_shots.SCREENS}


def test_resolved_steps_name_no_symbols():
    data = json.loads(FIXTURE.read_text())
    for recipe in data["screens"]:
        for step in recipe["steps"]:
            if step[0] in ("select", "ui"):
                assert not any(isinstance(a, str) and a.startswith("@") for a in step[1:]), recipe["name"]

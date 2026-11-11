"""The committed screen recipes, which `tools/parity_next.py` replays to shoot
the HTML build's screens. The pygame shell that first drew them is gone, so the
fixture is now the list itself: add a screen by editing it.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "parity_screens.json"


def test_resolved_steps_name_no_symbols():
    data = json.loads(FIXTURE.read_text())
    for recipe in data["screens"]:
        for step in recipe["steps"]:
            if step[0] in ("select", "ui"):
                assert not any(isinstance(a, str) and a.startswith("@") for a in step[1:]), recipe["name"]

"""Values the leaderboard's JS keeps in step with the Python engine by hand.

There is no shared build step between `leaderboard/` (a separate Netlify site,
plain JS) and the game's Python, so a hand-maintained constant is the only way
some values can cross that boundary at all — and the only thing that can catch
them drifting apart is a test that reads both sides as text, the same trick
`test_schema_grants.py` uses for schema.sql.
"""

from __future__ import annotations

import re
from pathlib import Path

from starconquest import engine

ROOT = Path(__file__).resolve().parent.parent
CONFIG_MJS = ROOT / "leaderboard" / "js" / "config.mjs"


def _js_constant(name: str) -> int:
    match = re.search(rf"^export const {name} = (\d+);", CONFIG_MJS.read_text(), re.MULTILINE)
    assert match is not None, f"{name} not found in {CONFIG_MJS}"
    return int(match.group(1))


def test_current_rules_version_matches_the_engine():
    """`game.mjs`'s Watch link is decided by comparing a replay's stamped
    `rules_version` against this constant (`CURRENT_RULES_VERSION`) — if it falls
    behind `engine.RULES_VERSION`, every replay recorded since the last bump reads
    as outdated and loses its Watch link even though it is perfectly current."""
    assert _js_constant("CURRENT_RULES_VERSION") == engine.RULES_VERSION

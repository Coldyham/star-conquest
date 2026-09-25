"""Values the leaderboard's JS keeps in step with the Python engine by hand.

There is no shared build step between `leaderboard/` (plain JS, copied as-is
into the game's site under /board/) and the game's Python, so a hand-maintained constant is the only way
some values can cross that boundary at all — and the only thing that can catch
them drifting apart is a test that reads both sides as text, the same trick
`test_schema_grants.py` uses for schema.sql.
"""

from __future__ import annotations

import re
from pathlib import Path

from starconquest import config, engine, matchnames, paths

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


def _js_string_list(path: Path, name: str) -> list[str]:
    match = re.search(rf"^export const {name} = \[(.*?)\];", path.read_text(), re.MULTILINE | re.DOTALL)
    assert match is not None, f"{name} not found in {path}"
    return re.findall(r'"([^"]*)"', match.group(1))


def test_match_names_use_the_same_words_on_both_sides():
    """A match's pass-phrase is derived, never stored, so the lobby and the game
    only agree on what a match is called while their word lists match exactly."""
    js = ROOT / "leaderboard" / "js" / "matchnames.mjs"
    assert _js_string_list(js, "ADJECTIVES") == list(matchnames.ADJECTIVES)
    assert _js_string_list(js, "NOUNS") == list(matchnames.NOUNS)
    assert len(set(matchnames.ADJECTIVES)) == len(matchnames.ADJECTIVES) == 256
    assert len(set(matchnames.NOUNS)) == len(matchnames.NOUNS) == 256


def test_seat_names_and_colours_match_the_game():
    """The lobby names and colours each seat as the game draws it."""
    assert _js_string_list(CONFIG_MJS, "PLAYER_NAMES") == config.PLAYER_NAMES
    colours = ["#%02x%02x%02x" % rgb for rgb in config.PLAYER_COLORS]
    assert _js_string_list(CONFIG_MJS, "PLAYER_COLORS") == colours


def _js_string_constant(path: Path, name: str) -> str:
    match = re.search(rf'^export const {name} = "([^"]*)";', path.read_text(), re.MULTILINE)
    assert match is not None, f"{name} not found in {path}"
    return match.group(1)


def test_the_board_reads_the_same_storage_keys_the_game_writes():
    """The board is served from the game's origin, so the two share one
    localStorage: the lobby's "yours" is the game's seat store, and the posting
    name is the game's pbp name. A renamed key on one side would silently empty
    the other."""
    js = ROOT / "leaderboard" / "js"
    assert _js_string_constant(js / "pbp.mjs", "SEATS_KEY") == paths.WEB_PBP_SEATS_KEY
    assert _js_string_constant(js / "me.mjs", "NAME_KEY") == paths.WEB_PBP_NAME_KEY

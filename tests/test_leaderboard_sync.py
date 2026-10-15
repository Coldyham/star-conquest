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

from starconquest import ai, config, engine, matchnames, paths

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


def test_the_board_ranks_bots_in_the_games_ladder_order():
    """`standings.botOrder` lists a map's losing bot replays in ladder order and
    sorts any name it doesn't know alphabetically after the rest, so a bot added
    to the game's `LADDER_ORDER` but not the board's sinks to the bottom there."""
    js = ROOT / "leaderboard" / "js" / "standings.mjs"
    assert _js_string_list(js, "LADDER_ORDER") == ai.LADDER_ORDER


def test_seat_names_and_colours_match_the_game():
    """The lobby names and colours each seat as the game draws it."""
    assert _js_string_list(CONFIG_MJS, "PLAYER_NAMES") == config.PLAYER_NAMES
    colours = ["#%02x%02x%02x" % rgb for rgb in config.PLAYER_COLORS]
    assert _js_string_list(CONFIG_MJS, "PLAYER_COLORS") == colours


def test_fog_preset_matches_the_basic_tab():
    """The board names a setup plain "Fog" when its ranges are the Basic tab's
    checkbox preset, and spells out the ranges otherwise."""
    assert _js_constant("FOG_ON_SIGHT") == config.FOG_ON_SIGHT
    assert _js_constant("FOG_ON_SCOUT") == config.FOG_ON_SCOUT


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


def test_the_board_paces_setups_with_the_games_defaults():
    """families.mjs reads a pruned settings_json against these: a speed or a
    knob missing from it is the game's default, and past a standard board the
    box grows (``config.world_side``), so pace holds at its figure."""
    assert _js_constant("SHIP_LY_PER_TURN") == config.SHIP_LY_PER_TURN
    assert _js_constant("STANDARD_MAX_NODES") == config.STANDARD_MAX_NODES


def _js_object(name: str) -> dict[str, object]:
    match = re.search(rf"^export const {name} = \{{(.*?)\}};", CONFIG_MJS.read_text(), re.MULTILINE | re.DOTALL)
    assert match is not None, f"{name} not found in {CONFIG_MJS}"
    out: dict[str, object] = {}
    for key, raw in re.findall(r"^\s*(\w+): ([^,]+),", match.group(1), re.MULTILINE):
        if raw in ("true", "false"):
            out[key] = raw == "true"
        else:
            out[key] = raw.strip('"') if raw.startswith('"') else float(raw)
    return out


def test_the_board_compares_setups_against_the_settings_defaults():
    """settings_json is pruned to non-defaults, so families.mjs reads a missing
    key as ``SETUP_DEFAULTS``. A default moved on one side only would make every
    pruned setup look edited, or hide a real edit."""
    from starconquest.settings import Settings

    defaults = _js_object("SETUP_DEFAULTS")
    assert defaults, "SETUP_DEFAULTS is empty"
    settings = Settings()
    for key, value in defaults.items():
        assert getattr(settings, key) == value, key


def test_the_boards_pace_estimate_tracks_real_maps():
    """The board can't run mapgen, so it estimates a setup's median lane in
    turns as ceil(PACE_LY / sqrt(min(systems, 40)) / speed). Checked here
    against real generated maps across the node counts and speeds people play
    (docs/design/setup-families.md, "Pace"): never more than a turn out, and
    usually exact."""
    import math
    import statistics

    from starconquest.settings import Settings, build_state

    pace_ly = _js_constant("PACE_LY")
    exact = total = 0
    for nodes in (8, 12, 18, 24, 31, 40):
        for speed in (3.0, 6.0, 8.5, 19.5):
            for seed in (1, 2, 3):
                state = build_state(Settings(nodes=nodes, players=3, ship_ly_per_turn=speed), seed)
                turns = [state.travel_turns(lane.a, lane.b) for lane in state.lanes.values()]
                low, high = statistics.median_low(turns), statistics.median_high(turns)
                estimate = max(1, math.ceil(pace_ly / math.sqrt(min(len(state.systems), 40)) / speed))
                assert low - 1 <= estimate <= high + 1, (nodes, speed, seed, estimate, turns)
                exact += low <= estimate <= high
                total += 1
    # Exact on 59 of these 72 maps when written; the misses are the ceil
    # landing either side of a lane length on the turn boundary.
    assert exact >= 0.75 * total, f"exact on {exact} of {total}"

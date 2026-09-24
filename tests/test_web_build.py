"""The merged site's deploy wiring, checked as text — no build is run.

The game's site also serves the leaderboard (pages at /board/, functions at
/api/), and each of these rules is one a broken deploy would only reveal in
production: a service worker answering a play-by-post poll from cache, function
source published as static files, a functions directory that isn't there.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from starconquest import paths

ROOT = Path(__file__).resolve().parent.parent
SW = (ROOT / "tools" / "pwa" / "sw.js").read_text()
BUILD = (ROOT / "tools" / "build_web.sh").read_text()


def _bash_array(name: str) -> list[str]:
    match = re.search(rf"^{name}=\((.*?)\)$", BUILD, re.MULTILINE)
    assert match is not None, f"{name} not found in build_web.sh"
    return match.group(1).split()


def test_the_service_worker_never_answers_the_api():
    """pbp polls `?action=state|list` with GETs; a cached answer is last turn's."""
    bypass = SW.index('url.pathname.startsWith("/api/")')
    assert "return;" in SW[bypass:bypass + 60]
    assert bypass < SW.index("event.respondWith("), "the bypass must come before any respondWith"


def test_the_service_worker_fetches_board_pages_network_first():
    board = SW.index('url.pathname.startsWith("/board/")')
    branch = SW[board:SW.index("return;", board)]
    assert branch.index("fetch(req)") < branch.index("cache.match(req)")


def test_the_board_copy_is_an_allow_list_of_servable_files():
    files = _bash_array("BOARD_FILES")
    dirs = _bash_array("BOARD_DIRS")
    for name in files + dirs:
        assert (ROOT / "leaderboard" / name).exists(), f"build_web.sh copies missing {name}"
    staged = set(files) | set(dirs)
    for never in ("netlify", "tests", "README.md", "netlify.toml", "schema.sql", "fold-game-key.sql"):
        assert never not in staged
    pages = {p.name for p in (ROOT / "leaderboard").glob("*.html")}
    assert pages <= set(files), f"a board page is not staged: {pages - set(files)}"


def test_the_root_site_bundles_the_boards_functions():
    toml = tomllib.loads((ROOT / "netlify.toml").read_text())
    functions = ROOT / toml["functions"]["directory"]
    assert functions.is_dir()
    for name in ("log.mjs", "replay.mjs", "pbp.mjs"):
        assert (functions / name).exists()
    assert toml["build"]["environment"]["SECRETS_SCAN_OMIT_KEYS"] == "SUPABASE_URL"


def test_the_build_drops_the_secret_before_running_anything():
    """The free plan can't scope the key to Functions, so it reaches the build
    shell too; the command has to unset it before the first third-party step."""
    command = tomllib.loads((ROOT / "netlify.toml").read_text())["build"]["command"]
    first, _, rest = command.partition("&&")
    assert first.split()[0] == "unset"
    assert {"SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_KEY"} <= set(first.split()[1:])
    assert "curl" in rest and "build_web.sh" in rest


def test_the_functions_answer_where_the_game_calls_them():
    functions = ROOT / "leaderboard" / "netlify" / "functions"
    for path, name in ((paths.LEADERBOARD_LOG_PATH, "log.mjs"),
                       (paths.LEADERBOARD_REPLAY_PATH, "replay.mjs"),
                       (paths.LEADERBOARD_PBP_PATH, "pbp.mjs")):
        assert f'export const config = {{ path: "{path}" }};' in (functions / name).read_text()


def test_the_games_board_links_point_under_board():
    for path in (paths.LEADERBOARD_SUBMIT_PATH, paths.LEADERBOARD_CONFIGS_PATH,
                 paths.LEADERBOARD_LOBBY_PATH):
        page = path.split("?")[0]
        assert page.startswith("/board/")
        assert (ROOT / "leaderboard" / page.removeprefix("/board/")).exists()


def test_the_old_host_proxies_the_api_rather_than_redirecting_it():
    """Old desktop builds POST there, and urllib won't follow a redirect on POST."""
    toml = tomllib.loads((ROOT / "legacy-board" / "netlify.toml").read_text())
    rules = toml["redirects"]
    api = next(r for r in rules if r["from"] == "/api/*")
    assert api["status"] == 200
    assert api["to"] == f"{paths.LEADERBOARD_ORIGIN}/api/:splat"
    assert rules.index(api) < next(i for i, r in enumerate(rules) if r["from"] == "/*")

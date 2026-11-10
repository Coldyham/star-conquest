"""The merged site's deploy wiring, checked as text — no build is run.

The game's site also serves the leaderboard (pages at /board/, functions at
/api/; the game itself at /play/, behind a router page at the root), and each of these rules is one a broken deploy would only reveal in
production: a service worker answering a play-by-post poll from cache, function
source published as static files, a functions directory that isn't there.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from starconquest import paths
from tools import render_board

ROOT = Path(__file__).resolve().parent.parent
SW = (ROOT / "tools" / "pwa" / "sw.js").read_text()
BUILD = (ROOT / "tools" / "build_web.sh").read_text()
ROUTER = (ROOT / "tools" / "pwa" / "root.html").read_text()


def test_the_service_worker_never_answers_the_api():
    """pbp polls `?action=state|list` with GETs; a cached answer is last turn's."""
    bypass = SW.index('url.pathname.startsWith("/api/")')
    assert "return;" in SW[bypass:bypass + 60]
    assert bypass < SW.index("event.respondWith("), "the bypass must come before any respondWith"


def test_the_service_worker_caches_the_runtime_pygbag_loads():
    """The runtime is cross-origin; only its CDN prefix joins the cache, and only
    as CORS responses (an opaque one can't be checked and pads the quota)."""
    import importlib.util

    found = re.search(r'const RUNTIME = "([^"]+)";', SW)
    assert found is not None
    runtime = found.group(1)
    spec = importlib.util.find_spec("pygbag")
    assert spec is not None and spec.origin is not None
    app = (Path(spec.origin).parent / "app.py").read_text()
    assert f'DEFAULT_CDN = f"{runtime}{{cdn_version}}/"' in app
    assert 'res.type === "cors"' in SW
    assert "!url.href.startsWith(RUNTIME)) return;" in SW


def _sw_function(name: str) -> str:
    start = SW.index(f"async function {name}(req)")
    return SW[start:SW.index("\n}\n", start)]


def test_the_service_worker_fetches_board_pages_network_first():
    board = SW.index('url.pathname.startsWith("/board/")')
    branch = SW[board:SW.index("return;", board)]
    assert "networkFirst(req)" in branch
    first = _sw_function("networkFirst")
    assert first.index("fetch(req)") < first.index("cache.match(req)")


def test_the_html_build_is_network_first_but_for_what_its_name_fixes():
    """/next/'s page and scripts are asked for first, so a deploy never boots an
    old page against a new zip; the hashed zip, Pyodide's versioned files and
    the fonts never change under their name, so the cache answers them."""
    nxt = SW.index('url.pathname.startsWith("/next/")')
    branch = SW[nxt:SW.index("return;", nxt)]
    assert "FIXED.test(url.pathname) ? cacheFirst(req) : networkFirst(req)" in branch
    assert nxt < SW.rindex("event.respondWith(")
    cached = _sw_function("cacheFirst")
    assert cached.index("cache.match(req)") < cached.index("fetch(req)")
    fixed = re.search(r"const FIXED = /(.+)/;", SW).group(1).replace("\\/", "/")
    pattern = re.compile(fixed)
    for path in ("/next/py/starconquest-0123456789ab.zip", "/next/pyodide/0.27.7/pyodide.asm.wasm",
                 "/next/fonts/DejaVuSansMono.ttf"):
        assert pattern.search(path), path
    for path in ("/next/", "/next/index.html", "/next/js/build.mjs", "/next/js/play.mjs", "/next/worker.mjs",
                 "/next/css/play.css", "/next/py/starconquest-0123456789ab.zip.tmp"):
        assert not pattern.search(path), path


def test_the_board_copy_is_an_allow_list_of_servable_files():
    assert 'tools/render_board.py" --out "$ROOT/web/board"' in BUILD
    staged = set(render_board.PAGES) | set(render_board.STATIC_FILES) | set(render_board.STATIC_DIRS)
    for name in staged:
        assert (ROOT / "leaderboard" / name).exists(), f"render_board stages missing {name}"
    for never in ("netlify", "tests", "templates", "README.md", "netlify.toml", "schema.sql",
                  "fold-game-key.sql"):
        assert never not in staged
    pages = {p.name for p in (ROOT / "leaderboard").glob("*.html")}
    assert pages <= set(render_board.PAGES), f"a board page is not staged: {pages - set(render_board.PAGES)}"


def test_every_board_page_renders_with_the_shared_chrome(tmp_path):
    render_board.build(tmp_path)
    header = re.compile(r'<header class="bar">.*?</header>', re.DOTALL)
    headers = set()
    for page in render_board.PAGES:
        html = (tmp_path / page).read_text()
        assert html.startswith("<!doctype html>"), page
        assert "{%" not in html and "{{" not in html and "{#" not in html, page
        found = header.findall(html)
        assert len(found) == 1, f"{page} has {len(found)} headers"
        headers |= set(found)
        assert html.count('href="privacy.html">Privacy</a>') == 1, f"{page}'s footer"
    assert len(headers) == 1
    for name in render_board.STATIC_FILES + render_board.STATIC_DIRS:
        assert (tmp_path / name).exists()


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


def test_the_build_skips_the_tap_to_start_gate_but_keeps_the_leave_warning():
    """The game plays no sound, so pygbag's wait-for-a-tap (`--ume_block`) is
    only a delay. Its "leave site?" prompt (`--can_close`) is kept: it is what
    warns before a single-player match in progress is navigated away from."""
    call = next(line for line in BUILD.splitlines() if "pygbag --build" in line)
    assert "--ume_block 0" in call
    assert "--can_close" not in call


def test_the_game_page_gets_our_favicon_not_pygbags():
    """Without `--icon`, pygbag downloads its own default into /play/favicon.png,
    and the game page links that one rather than the site root's."""
    call = BUILD[BUILD.index("pygbag --build"):].split(")", 1)[0]
    assert '--icon "$ROOT/tools/pwa/favicon.png"' in call
    assert (ROOT / "tools" / "pwa" / "favicon.png").exists()


def test_the_functions_answer_where_the_game_calls_them():
    functions = ROOT / "leaderboard" / "netlify" / "functions"
    for path, name in ((paths.LEADERBOARD_LOG_PATH, "log.mjs"),
                       (paths.LEADERBOARD_REPLAY_PATH, "replay.mjs"),
                       (paths.LEADERBOARD_PBP_PATH, "pbp.mjs"),
                       (paths.LEADERBOARD_CAMPAIGN_PATH, "campaign.mjs")):
        assert f'export const config = {{ path: "{path}" }};' in (functions / name).read_text()


def test_the_games_board_links_point_under_board():
    for path in (paths.LEADERBOARD_SUBMIT_PATH, paths.LEADERBOARD_RECENT_PATH,
                 paths.LEADERBOARD_LOBBY_PATH, paths.LEADERBOARD_GAME_PATH,
                 paths.LEADERBOARD_POST_MODULE_PATH):
        page = path.split("?")[0]
        assert page.startswith("/board/")
        assert (ROOT / "leaderboard" / page.removeprefix("/board/")).exists()


def test_quick_post_calls_what_the_board_module_exports():
    """`share.quick_post` imports the module and calls this by name."""
    module = ROOT / "leaderboard" / paths.LEADERBOARD_POST_MODULE_PATH.removeprefix("/board/")
    assert "export async function quickPost(token, name)" in module.read_text()


def test_the_old_host_proxies_the_api_rather_than_redirecting_it():
    """Old desktop builds POST there, and urllib won't follow a redirect on POST."""
    toml = tomllib.loads((ROOT / "legacy-board" / "netlify.toml").read_text())
    rules = toml["redirects"]
    api = next(r for r in rules if r["from"] == "/api/*")
    assert api["status"] == 200
    assert api["to"] == f"{paths.LEADERBOARD_ORIGIN}/api/:splat"
    assert rules.index(api) < next(i for i, r in enumerate(rules) if r["from"] == "/*")


def _z_index(css: str, selector: str) -> int:
    match = re.search(rf"^{re.escape(selector)} \{{(.*?)^\}}", css, re.MULTILINE | re.DOTALL)
    assert match is not None, f"{selector} rule not found"
    z = re.search(r"z-index:\s*(\d+)", match.group(1))
    assert z is not None, f"{selector} sets no z-index"
    return int(z.group(1))


def test_the_board_header_paints_over_the_page_so_its_menu_can_drop_down():
    """The menu's dropdown lives inside `.bar`, so it can only ever be as high as
    the header itself. `main` and the footer come later in the page, so at an
    equal z-index they painted over the open menu and swallowed its clicks."""
    css = (ROOT / "leaderboard" / "css" / "style.css").read_text()
    header = _z_index(css, ".bar")
    for later in ("main", ".attract"):
        assert header > _z_index(css, later), f".bar must stack above {later}"


def test_the_root_is_the_router_and_the_game_is_under_play():
    assert 'cp -r "$STAGE/build/web/." "$ROOT/web/play/"' in BUILD
    assert 'cp "$ROOT/tools/pwa/root.html" "$ROOT/web/index.html"' in BUILD
    assert 'inject.py" "$ROOT/web/play/index.html"' in BUILD
    # The service worker stays at the root, keeping the scope old installs hold.
    assert 'cp "$ROOT/tools/pwa/sw.js" "$ROOT/web/"' in BUILD
    inject = (ROOT / "tools" / "pwa" / "inject.py").read_text()
    assert 'register("/sw.js")' in inject and 'href="/manifest.webmanifest"' in inject


def test_the_router_sends_shared_links_to_the_game_and_visitors_to_the_board():
    """Every link the game ever shared is the root URL plus a fragment, which the
    server never sees; the router is what keeps them opening the game."""
    assert "location.hash.length > 1" in ROUTER
    assert '"/play/"' in ROUTER and '"/board/"' in ROUTER
    assert "location.hash)" in ROUTER, "the fragment must be forwarded intact"
    assert "display-mode: standalone" in ROUTER, "an old install launches /index.html"


def test_the_manifest_keeps_its_id_and_launches_the_router():
    import json
    manifest = json.loads((ROOT / "tools" / "pwa" / "manifest.webmanifest").read_text())
    # `id` resolves against start_url, so it must be absolute to stay the
    # pre-move default (the old start_url) and let existing installs update.
    assert manifest["id"] == "/index.html"
    # The router, not /play/, so a launch can reopen the board.
    assert manifest["start_url"] == "./index.html"
    assert manifest["scope"] == "./"


def test_the_manifest_shortcuts_open_the_dailies_and_the_campaign():
    """A long press on the installed icon offers today's two dailies, puzzle first, (in the
    game, by the fragment `daily.link` reads) and the campaign (on the board)."""
    import json
    from starconquest import daily
    manifest = json.loads((ROOT / "tools" / "pwa" / "manifest.webmanifest").read_text())
    urls = [s["url"] for s in manifest["shortcuts"]]
    assert urls == ["/play/#puzzle", "/play/#daily", "/board/campaign.html"]
    assert daily.link("daily") == (daily.BOARD, "")
    assert daily.link("puzzle") == (daily.PUZZLE, "")
    assert "campaign.html" in render_board.PAGES
    for shortcut in manifest["shortcuts"]:
        for icon in shortcut.get("icons", []):
            assert (ROOT / "tools" / "pwa" / icon["src"]).is_file()


def test_the_board_links_to_the_game_under_play():
    config = (ROOT / "leaderboard" / "js" / "config.mjs").read_text()
    assert 'GAME_URL_FALLBACK = "https://star-conquest.netlify.app/play/"' in config
    assert "`https://${host}/play/`" in config
    for page in render_board.PAGES:
        html = render_board.render(page)
        assert 'href="../play/">' in html, f"{page}'s Play button"
        assert 'href="../">' not in html
    from tools import admin
    assert admin.GAME_URL == "https://star-conquest.netlify.app/play/"


def test_the_launcher_imports_pygame_for_pygbag():
    """pygbag installs the pygame-ce wheel only for imports it finds in the
    top-level main.py; the shim importing only the package boots to black."""
    launcher = (ROOT / "main.py").read_text()
    assert re.search(r"^import pygame\b", launcher, re.MULTILINE)


def test_every_board_page_is_installable():
    """A browser offers the install only on a page that links the manifest and
    is controlled by the worker, so the board needs both, not just /play/."""
    for page in render_board.PAGES:
        assert '<link rel="manifest" href="/manifest.webmanifest">' in render_board.render(page), page
    nav = (ROOT / "leaderboard" / "js" / "nav.mjs").read_text()
    assert 'register("/sw.js")' in nav
    assert "mountApp();" in nav[nav.index("export function mountNav"):]


def test_an_app_launch_reopens_the_last_half():
    """The router reads the half each side records inside the installed app."""
    assert 'localStorage.getItem("sc_app_last")' in ROUTER
    assert 'last !== "board"' in ROUTER, "a never-recorded launch must still open the game"
    inject = (ROOT / "tools" / "pwa" / "inject.py").read_text()
    nav = (ROOT / "leaderboard" / "js" / "nav.mjs").read_text()
    assert 'setItem("sc_app_last", "game")' in inject
    assert 'setItem("sc_app_last", "board")' in nav


def test_the_web_build_ships_the_gauntlets():
    """The tutorial is a file beside the package code (`gauntlet._ASSETS`), so it
    ships only because the build copies the package whole, fonts and all."""
    from starconquest import gauntlet

    assert 'cp -r "$ROOT/starconquest" "$STAGE/"' in BUILD
    assert gauntlet._ASSETS.is_relative_to(ROOT / "starconquest")
    assert (gauntlet._ASSETS / "tutorial.json").exists()


def test_the_runtime_is_mirrored_after_the_page_is_patched():
    """The mirror rewrites the page inject.py has finished with, under the build's
    own Python, and after the wheel it reuses has been fetched."""
    mirror = BUILD.index('uv run --project "$ROOT" python "$ROOT/tools/mirror_runtime.py" fetch "$ROOT/web"')
    assert BUILD.index('inject.py" "$ROOT/web/play/index.html"') < mirror
    assert BUILD.index('curl -fsSL -o "$ROOT/web/cdn/cp312/$WHEEL"') < mirror


def test_a_page_that_now_redirects_leaves_the_cache():
    """/game/ redirects to /play/: a navigation's opaque redirect is never ok, so
    without this the cached old page (loading the blocked CDN) would win forever."""
    swr = SW.rindex("event.respondWith(")
    assert 'res.type === "opaqueredirect" || res.redirected' in SW[swr:]
    assert SW.index("cache.delete(req)", swr) < SW.index("cache.put(req", swr)
    assert 'const CACHE = "starconquest-v4";' in SW

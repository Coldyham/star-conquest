#!/usr/bin/env bash
# Build the browser (WebAssembly) version of Star Conquest with pygbag into ./web/
#
# We stage just main.py + the starconquest package + models/ into a clean dir
# first, so pygbag doesn't pack .venv / games / web into the bundle. Bundling
# models/ ships committed drop-in AI strategies to the browser build too, the
# same way starconquest/assets/*.ttf already ships the bundled font. The stage
# dir is named `starconquest` so the output bundle is starconquest.apk
# (pygbag's bundle name).
#
#   ./tools/build_web.sh          # build into ./web/
#   uv run pygbag <stage>/main.py # (what the script runs under the hood to serve)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
STAGE="$TMP/starconquest"
mkdir -p "$STAGE"
cp "$ROOT/main.py" "$STAGE/"
cp -r "$ROOT/starconquest" "$STAGE/"
cp -r "$ROOT/models" "$STAGE/"
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --width/--height set the canvas framebuffer size: render at a high native
# resolution so text/edges stay crisp instead of the browser upscaling pygbag's
# 1280x720 default. Must match config.WEB_FB_W/WEB_FB_H (set_mode uses those).
# --ume_block 0 drops pygbag's "Ready to start! Please click/touch page" gate. It
# waits for a tap so the browser will allow sound, and the game plays none; with
# the board on the same site, it was one more stop on every trip back to the game.
# (--can_close stays at its default on purpose: its "leave site?" prompt is what
# warns before a single-player match in progress is navigated away from.)
# --icon hands pygbag our favicon: it looks for one in the stage dir, finds none
# and downloads its own, which the game page then links. The copy to the site
# root below used to cover that, until the game moved to /game/.
( cd "$STAGE" && uv run --project "$ROOT" pygbag --build --ume_block 0 --width 2560 --height 1440 \
    --icon "$ROOT/tools/pwa/favicon.png" main.py )

# The game is served at /game/; the site root is a small router page
# (tools/pwa/root.html) that sends visitors to the leaderboard at /board/ and
# forwards the links the game has always shared — a fragment on the root URL —
# on to /game/. pygbag loads its bundle relative to the page, so it runs from a
# subdirectory unchanged.
rm -rf "$ROOT/web"
mkdir -p "$ROOT/web/game"
cp -r "$STAGE/build/web/." "$ROOT/web/game/"
rm -rf "$TMP"
cp "$ROOT/tools/pwa/root.html" "$ROOT/web/index.html"

# Turn the pygbag output into an installable PWA: copy the committed manifest,
# service worker and home-screen icons (tools/pwa/) over the top, then patch the
# generated index.html (PWA head tags, SW registration, dark loading screen).
# The icons ship pre-rendered so this stays portable — no image toolchain needed
# on the build host (Netlify). Regenerate them with tools/pwa/make_icons.py.
# These stay at the site root rather than beside the game: the service worker
# must, to keep the scope ("/") that installs from before the move registered,
# and the manifest keeps its URL and id so those installs update in place.
cp "$ROOT/tools/pwa/manifest.webmanifest" "$ROOT/web/"
cp "$ROOT/tools/pwa/sw.js" "$ROOT/web/"
cp "$ROOT/tools/pwa/favicon.png" "$ROOT/web/"
cp "$ROOT/tools/pwa/icon-192.png" "$ROOT/tools/pwa/icon-512.png" \
   "$ROOT/tools/pwa/icon-maskable-512.png" "$ROOT/tools/pwa/apple-touch-icon.png" "$ROOT/web/"
cp "$ROOT/tools/pwa/screenshot-wide.png" "$ROOT/tools/pwa/screenshot-mobile.png" "$ROOT/web/"
uv run --project "$ROOT" python "$ROOT/tools/pwa/inject.py" "$ROOT/web/game/index.html"

# The leaderboard's pages, served at /board/ on this same origin (its functions
# are bundled separately, from netlify.toml's [functions] directory). The pages
# are Jinja2 templates sharing one head, menu and footer; render_board.py renders
# them and copies the static files, from an explicit allow-list of what is
# servable, so function source, tests, SQL and the README can never end up
# published by accident.
uv run --project "$ROOT" python "$ROOT/tools/render_board.py" --out "$ROOT/web/board"

# pygbag fetches the pygame-ce WASM wheel from <origin>/cdn/ at runtime. Mirror it
# into the build so the deployment is fully self-contained — works on any static
# host and on a phone over LAN, with no dependency on the pygame-web CDN at run
# time. If pygbag's bundled runtime changes, the browser console will show the new
# wheel name to drop in here.
WHEEL="pygame_ce-2.5.7-cp312-cp312-wasm32_bi_emscripten.whl"
mkdir -p "$ROOT/web/cdn/cp312"
if ! curl -fsSL -o "$ROOT/web/cdn/cp312/$WHEEL" "https://pygame-web.github.io/cdn/cp312/$WHEEL"; then
    echo "WARNING: could not mirror $WHEEL — the app will try the remote CDN at runtime." >&2
fi
echo
echo "Web build ready in: $ROOT/web"
echo "Test it:   cd '$ROOT/web' && python3 -m http.server 8000   # then open http://localhost:8000 (game: /game/, board: /board/)"
echo "Deploy it: copy the contents of $ROOT/web to any static host."

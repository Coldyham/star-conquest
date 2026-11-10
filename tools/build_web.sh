#!/usr/bin/env bash
# Build the site into ./web/: the game at /play/, the leaderboard at /board/, and
# the router, manifest, service worker and icons at the root.
#
# The game is the HTML build (docs/design/html-frontend.md): the page from
# webapp/, the pinned Pyodide (downloaded and checksum-checked) and the game's
# Python as one hashed zip, staged by tools/build_next.py.
#
#   ./tools/build_web.sh          # build into ./web/
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# The site root is a small router page (tools/pwa/root.html) that sends visitors
# to the leaderboard at /board/ and forwards the links the game has always
# shared — a fragment on the root URL — on to /play/.
rm -rf "$ROOT/web"
mkdir -p "$ROOT/web"
uv run --project "$ROOT" python "$ROOT/tools/build_next.py" --out "$ROOT/web/play"
cp "$ROOT/tools/pwa/root.html" "$ROOT/web/index.html"

# The installable-app files: the committed manifest, service worker and
# home-screen icons (tools/pwa/). They stay at the site root rather than beside
# the game: the service worker must, to keep the scope ("/") that installs from
# before the move registered, and the manifest keeps its URL and id so those
# installs update in place. The game's page links them by absolute path.
# Regenerate the icons with tools/pwa/make_icons.py.
cp "$ROOT/tools/pwa/manifest.webmanifest" "$ROOT/web/"
cp "$ROOT/tools/pwa/sw.js" "$ROOT/web/"
cp "$ROOT/tools/pwa/favicon.png" "$ROOT/web/"
cp "$ROOT/tools/pwa/icon-192.png" "$ROOT/tools/pwa/icon-512.png" \
   "$ROOT/tools/pwa/icon-maskable-512.png" "$ROOT/tools/pwa/apple-touch-icon.png" "$ROOT/web/"
cp "$ROOT/tools/pwa/screenshot-wide.png" "$ROOT/tools/pwa/screenshot-mobile.png" "$ROOT/web/"

# The leaderboard's pages, served at /board/ on this same origin (its functions
# are bundled separately, from netlify.toml's [functions] directory). The pages
# are Jinja2 templates sharing one head, menu and footer; render_board.py renders
# them and copies the static files, from an explicit allow-list of what is
# servable, so function source, tests, SQL and the README can never end up
# published by accident.
uv run --project "$ROOT" python "$ROOT/tools/render_board.py" --out "$ROOT/web/board"

echo
echo "Web build ready in: $ROOT/web"
echo "Test it:   cd '$ROOT/web' && python3 -m http.server 8000   # then open http://localhost:8000 (game: /play/, board: /board/)"
echo "Deploy it: copy the contents of $ROOT/web to any static host."

#!/usr/bin/env bash
# Build the browser (WebAssembly) version of Star Conquest with pygbag into ./web/
#
# We stage just main.py + the starconquest package into a clean dir first, so
# pygbag doesn't pack .venv / games / web into the bundle. The stage dir is named
# `starconquest` so the output bundle is starconquest.apk (pygbag's bundle name).
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
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --width/--height set the canvas framebuffer size: render at a high native
# resolution so text/edges stay crisp instead of the browser upscaling pygbag's
# 1280x720 default. Must match config.WEB_FB_W/WEB_FB_H (set_mode uses those).
( cd "$STAGE" && uv run --project "$ROOT" pygbag --build --width 2560 --height 1440 main.py )

rm -rf "$ROOT/web"
mkdir -p "$ROOT/web"
cp -r "$STAGE/build/web/." "$ROOT/web/"
rm -rf "$TMP"

# Turn the pygbag output into an installable PWA: copy the committed manifest,
# service worker and home-screen icons (tools/pwa/) over the top, then patch the
# generated index.html (PWA head tags, SW registration, dark loading screen).
# The icons ship pre-rendered so this stays portable — no image toolchain needed
# on the build host (Netlify). Regenerate them with tools/pwa/make_icons.py.
cp "$ROOT/tools/pwa/manifest.webmanifest" "$ROOT/web/"
cp "$ROOT/tools/pwa/sw.js" "$ROOT/web/"
cp "$ROOT/tools/pwa/favicon.png" "$ROOT/web/"
cp "$ROOT/tools/pwa/icon-192.png" "$ROOT/tools/pwa/icon-512.png" \
   "$ROOT/tools/pwa/icon-maskable-512.png" "$ROOT/tools/pwa/apple-touch-icon.png" "$ROOT/web/"
cp "$ROOT/tools/pwa/screenshot-wide.png" "$ROOT/tools/pwa/screenshot-mobile.png" "$ROOT/web/"
uv run --project "$ROOT" python "$ROOT/tools/pwa/inject.py" "$ROOT/web/index.html"

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
echo "Test it:   cd '$ROOT/web' && python3 -m http.server 8000   # then open http://localhost:8000"
echo "Deploy it: copy the contents of $ROOT/web to any static host."

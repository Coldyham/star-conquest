#!/usr/bin/env bash
# Build the browser (WebAssembly) version of Star Conquest with pygbag into ./web/
#
# We stage just main.py + the starconquest package into a clean dir first, so
# pygbag doesn't pack .venv / bin / .buildozer / games into the bundle. The stage
# dir is named `starconquest` so the output bundle is starconquest.apk.
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

( cd "$STAGE" && uv run --project "$ROOT" pygbag --build main.py )

rm -rf "$ROOT/web"
mkdir -p "$ROOT/web"
cp -r "$STAGE/build/web/." "$ROOT/web/"
rm -rf "$TMP"

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

#!/usr/bin/env python3
"""Post-process pygbag's generated web/index.html into an installable PWA.

pygbag emits a generic index.html (a build artifact — regenerated on every build,
including on Netlify), so the PWA wiring can't be committed there. This script is
run by tools/build_web.sh right after the pygbag build and patches that file in
place: it adds the manifest/apple-touch/theme-color head tags, registers the
service worker, and restyles pygbag's default (green-on-blue) loading screen to
match the game's dark palette.

    uv run python tools/pwa/inject.py web/index.html

Anchors it relies on (the <head>/<title> boilerplate pygbag stamps out) are
asserted so the build fails loudly if a future pygbag version changes them, rather
than silently shipping a non-installable page.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Injected just before </head>. Note pygbag already emits <meta ...web-app-capable>
# for both iOS and Android, so we only add what's missing. The <style> overrides
# use !important so they win over pygbag's base rules and the inline body colour
# JS sets during boot; the SW registration is what tips the page over the PWA
# installability bar (and enables offline play).
HEAD_BLOCK = """
    <!-- PWA wiring — injected by tools/pwa/inject.py -->
    <link rel="manifest" href="manifest.webmanifest">
    <meta name="theme-color" content="#0a0c14">
    <meta name="description" content="A minimalist turn-based strategy game: take every star system to win.">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <meta name="apple-mobile-web-app-title" content="Star Conquest">
    <link rel="apple-touch-icon" href="apple-touch-icon.png">
    <link rel="icon" type="image/png" href="icon-192.png" sizes="192x192">
    <link rel="icon" type="image/png" href="icon-512.png" sizes="512x512">
    <style>
        html, body { background: #0a0c14 !important; }
        #status {
            color: #c8cdd7 !important;
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif !important;
            font-weight: 600;
        }
        progress#progress {
            accent-color: #56aaff;
            width: 260px; height: 8px;
        }
        #infobox {
            background: rgba(18, 22, 36, 0.96) !important;
            color: #e1e6f0 !important;
            border: 1px solid #343950;
            border-radius: 14px;
            padding: 20px 30px !important;
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif !important;
            font-weight: 600;
            letter-spacing: 0.2px;
            box-shadow: 0 12px 48px rgba(0, 0, 0, 0.55);
            text-align: center;
        }
    </style>
    <script>
        if ("serviceWorker" in navigator) {
            window.addEventListener("load", function () {
                navigator.serviceWorker.register("./sw.js").catch(function (e) {
                    console.warn("Star Conquest SW registration failed", e);
                });
            });
        }
    </script>
"""


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "web/index.html")
    html = path.read_text(encoding="utf-8")

    if "</head>" not in html:
        sys.exit(f"inject.py: no </head> in {path} — pygbag template changed?")
    html = html.replace("</head>", HEAD_BLOCK + "</head>", 1)

    # Nicer browser-tab / window title (best-effort; page still works without it).
    if "<title>starconquest</title>" in html:
        html = html.replace("<title>starconquest</title>", "<title>Star Conquest</title>", 1)
    else:
        print("inject.py: WARNING — <title>starconquest</title> not found; leaving title as-is")

    # pygbag paints the body mid-grey during boot; make it the game's dark bg so
    # there's no grey flash before the canvas appears (best-effort).
    if '"#7f7f7f"' in html:
        html = html.replace('"#7f7f7f"', '"#0a0c14"', 1)
    else:
        print("inject.py: WARNING — boot body colour '#7f7f7f' not found; skipping")

    path.write_text(html, encoding="utf-8")
    print(f"inject.py: patched {path} for PWA + dark loading screen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

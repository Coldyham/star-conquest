#!/usr/bin/env python3
"""Serve pygbag's runtime from our own origin instead of pygame-web.github.io.

Some networks (a school filter, say) block pygame-web.github.io, and the game
then sits on "Loading" forever while the board, on our own domain, loads fine.

Two steps, both here:

  record  Run once, on a machine with open internet, after ./tools/build_web.sh:
            uv run python tools/mirror_runtime.py record
          It plays the built game in headless Chromium, forwards every request
          for the runtime to the real CDN, and writes the paths it saw to
          tools/pwa/runtime_files.txt (commit that file). Rerun it when pygbag
          is upgraded.

  fetch   Run by tools/build_web.sh on every build:
            python tools/mirror_runtime.py fetch web
          It downloads each listed file into web/cdn/ and, only if every one
          arrived, points web/play/index.html at /cdn/ in place of the remote
          CDN. If the list is missing or a download fails it changes nothing,
          so the game keeps loading from the remote CDN as before.
"""

from __future__ import annotations

import argparse
import sys
import threading
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "tools" / "pwa" / "runtime_files.txt"
REMOTE = "https://pygame-web.github.io/cdn/"
LOCAL = "/cdn/"


def fetch(web: Path) -> int:
    if not LIST.exists():
        print(f"mirror_runtime: no {LIST.name}; the game keeps the remote CDN.")
        return 0
    paths = [p.strip() for p in LIST.read_text().splitlines() if p.strip() and not p.startswith("#")]
    for rel in paths:
        dest = web / "cdn" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(REMOTE + rel, timeout=120) as r:
                dest.write_bytes(r.read())
        except OSError as e:  # any network failure: leave the page on the remote CDN
            print(f"mirror_runtime: WARNING could not mirror {rel} ({e}); keeping the remote CDN.", file=sys.stderr)
            return 0
    page = web / "game" / "index.html"
    html = page.read_text(encoding="utf-8")
    if REMOTE not in html:
        print("mirror_runtime: WARNING index.html does not name the remote CDN; nothing rewritten.", file=sys.stderr)
        return 0
    page.write_text(html.replace(REMOTE, LOCAL), encoding="utf-8")
    print(f"mirror_runtime: mirrored {len(paths)} runtime files into web/cdn/ and rewrote the game page.")
    return 0


def record(web: Path, wait: int) -> int:
    from playwright.sync_api import sync_playwright

    seen: set[str] = set()

    def route(r):
        url = r.request.url
        rel = None
        for prefix in (REMOTE, "http://localhost:8000" + LOCAL):
            if url.startswith(prefix):
                rel = url[len(prefix):].split("?")[0].split("#")[0]
        if not rel:
            return r.continue_()
        resp = r.fetch(url=REMOTE + rel)
        if resp.ok:
            seen.add(rel)
        r.fulfill(response=resp)

    handler = partial(SimpleHTTPRequestHandler, directory=str(web))
    srv = ThreadingHTTPServer(("127.0.0.1", 8000), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            ctx = b.new_context(service_workers="block")
            ctx.route("**/*", route)
            page = ctx.new_page()
            page.goto("http://localhost:8000/play/")
            print(f"recording for {wait}s (let the game reach its menu)...")
            page.wait_for_timeout(wait * 1000)
            b.close()
    finally:
        srv.shutdown()
    if not seen:
        print("recorded nothing: is web/ built, and is the CDN reachable?", file=sys.stderr)
        return 1
    LIST.write_text("# Written by tools/mirror_runtime.py record; one path under the pygbag CDN per line.\n"
                    + "\n".join(sorted(seen)) + "\n")
    print(f"wrote {len(seen)} paths to {LIST}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("web", type=Path)
    r = sub.add_parser("record")
    r.add_argument("web", type=Path, nargs="?", default=ROOT / "web")
    r.add_argument("--wait", type=int, default=90, help="seconds to let the game load (default 90)")
    a = ap.parse_args()
    return fetch(a.web) if a.cmd == "fetch" else record(a.web, a.wait)


if __name__ == "__main__":
    sys.exit(main())

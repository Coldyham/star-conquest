#!/usr/bin/env python3
"""Serve pygbag's runtime from our own origin instead of pygame-web.github.io.

Some networks (a school filter, say) block pygame-web.github.io, and the game
then sits on "Loading" forever while the board, on our own domain, loads fine.

The build step:

  The list of files, tools/pwa/runtime_files.txt, is made by
  `node tools/record_runtime.mjs` (after ./tools/build_web.sh, on a machine with
  open internet); rerun it when pygbag is upgraded.

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
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIST = ROOT / "tools" / "pwa" / "runtime_files.txt"
REMOTE = "https://pygame-web.github.io/cdn/"
LOCAL = "/cdn/"


# The runtime also names the CDN in two places the page's script URL doesn't
# reach: Python reads the package index from $PYGPI (default pygame-web.github.io;
# the runtime's pep0723 module, found by reading it, not documented), and the index lists wheels under
# its "-CDN-" key. Both go to this origin, or the game still waits on the CDN.
RC_ANCHOR = "    home = f\"/home/{os.environ.get('USER','web_user')}\"\n"
RC_LINE = ('    import platform as _p; os.environ.setdefault("PYGPI", str(_p.window.location.origin) + "/cdn/")'
           "  # tools/mirror_runtime.py\n")


def _point_runtime_at_us(web: Path) -> bool:
    rc = next((web / "cdn").glob("*/cpythonrc.py"), None)
    idx = next((web / "cdn").glob("index-*.json"), None)
    if rc is None or idx is None or RC_ANCHOR not in rc.read_text(encoding="utf-8"):
        print("mirror_runtime: WARNING cpythonrc.py or the package index not as expected; keeping the remote CDN.",
              file=sys.stderr)
        return False
    rc.write_text(rc.read_text(encoding="utf-8").replace(RC_ANCHOR, RC_LINE + RC_ANCHOR, 1), encoding="utf-8")
    idx.write_text(idx.read_text(encoding="utf-8").replace(REMOTE, LOCAL), encoding="utf-8")
    return True


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
    if not _point_runtime_at_us(web):
        return 0
    page = web / "play" / "index.html"
    html = page.read_text(encoding="utf-8")
    if REMOTE not in html:
        print("mirror_runtime: WARNING index.html does not name the remote CDN; nothing rewritten.", file=sys.stderr)
        return 0
    page.write_text(html.replace(REMOTE, LOCAL), encoding="utf-8")
    print(f"mirror_runtime: mirrored {len(paths)} runtime files into web/cdn/ and rewrote the game page.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch").add_argument("web", type=Path)
    return fetch(ap.parse_args().web)


if __name__ == "__main__":
    sys.exit(main())

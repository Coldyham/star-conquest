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
          It downloads every listed file and checks, before writing anything,
          that the list is whole, that it is for the pygbag version the built
          page names, and that the page, cpythonrc.py and the package index
          look as expected. Only then does it write the files into web/cdn/ and
          point web/play/index.html, cpythonrc.py and the index at /cdn/. If any
          of that fails it writes nothing, so the game keeps loading from the
          remote CDN as before. It never fails the build.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
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

_PAGE_VERSION = re.compile(re.escape(REMOTE) + r"(\d+(?:\.\d+)+)/")
_LIST_VERSION = re.compile(r"^(?:(\d+(?:\.\d+)+)/|index-(\d+(?:\.\d+)+)-)")


def _warn(msg: str) -> None:
    print(f"mirror_runtime: WARNING {msg}; keeping the remote CDN.", file=sys.stderr)


def read_list(path: Path | None = None) -> list[str]:
    text = (path or LIST).read_text()
    return [p.strip() for p in text.splitlines() if p.strip() and not p.startswith("#")]


def list_problem(paths: list[str], page_html: str) -> str | None:
    """Why this list can't serve this page, or None. A recording cut short, or a
    list left behind by a pygbag upgrade, would point the page at files that
    aren't there: a 404 from our own origin, for every visitor."""
    versions = {m.group(1) or m.group(2) for p in paths if (m := _LIST_VERSION.match(p))}
    if len(versions) != 1:
        return f"the list names pygbag versions {sorted(versions) or 'none'}, not one"
    (ver,) = versions
    page = set(_PAGE_VERSION.findall(page_html))
    if page != {ver}:
        return f"the list is for pygbag {ver} but the page loads {sorted(page) or 'no version'} (rerun record_runtime.mjs)"
    needed = (f"{ver}/pythons.js", f"{ver}/cpythonrc.py")
    missing = [n for n in needed if n not in paths]
    if not any(p.startswith(f"{ver}/") and p.endswith("/main.wasm") for p in paths):
        missing.append("main.wasm")
    if not any(p.startswith(f"index-{ver}-") and p.endswith(".json") for p in paths):
        missing.append("the package index")
    if not any(p.endswith(".whl") for p in paths):
        missing.append("the pygame-ce wheel")
    return f"the list lacks {', '.join(missing)} (a recording cut short?)" if missing else None


def _download(rel: str) -> bytes:
    with urllib.request.urlopen(REMOTE + rel, timeout=120) as r:
        return r.read()


def point_runtime_at_us(files: dict[str, bytes]) -> bool:
    """Patch cpythonrc.py and the package index in `files`, in memory; False (and
    `files` untouched) if either isn't as expected."""
    rc = next((p for p in files if p.endswith("/cpythonrc.py")), None)
    idx = next((p for p in files if p.startswith("index-") and p.endswith(".json")), None)
    if rc is None or idx is None:
        return False
    rc_text = files[rc].decode("utf-8")
    idx_text = files[idx].decode("utf-8")
    if RC_ANCHOR not in rc_text or REMOTE not in idx_text:
        return False
    files[rc] = rc_text.replace(RC_ANCHOR, RC_LINE + RC_ANCHOR, 1).encode("utf-8")
    files[idx] = idx_text.replace(REMOTE, LOCAL).encode("utf-8")
    return True


def fetch(web: Path, download=_download) -> int:
    if not LIST.exists():
        print(f"mirror_runtime: no {LIST.name}; the game keeps the remote CDN.")
        return 0
    paths = read_list()
    page = web / "play" / "index.html"
    html = page.read_text(encoding="utf-8")
    if REMOTE not in html:
        _warn("index.html does not name the remote CDN")
        return 0
    if problem := list_problem(paths, html):
        _warn(problem)
        return 0

    # Everything is downloaded and checked before a byte is written, so a
    # failure anywhere leaves the build exactly as pygbag made it. A file the
    # build already mirrored (the wheel, by its own curl) isn't fetched again.
    def get(rel: str) -> bytes:
        have = web / "cdn" / rel
        return have.read_bytes() if have.is_file() and have.stat().st_size else download(rel)

    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            files = dict(zip(paths, pool.map(get, paths)))
    except Exception as e:  # noqa: BLE001 - any failure (a dropped connection included) keeps the remote CDN
        _warn(f"a download failed ({type(e).__name__}: {e})")
        return 0
    if not point_runtime_at_us(files):
        _warn("cpythonrc.py or the package index not as expected")
        return 0

    for rel, data in files.items():
        dest = web / "cdn" / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
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

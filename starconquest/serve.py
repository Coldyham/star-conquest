"""Serve the HTML build on this machine: the page, Pyodide, and the game's Python.

    uv run python -m starconquest.serve            # http://127.0.0.1:8000/next/
    uv run python -m starconquest.serve --no-open

Straight from the sources, so an edit shows on reload: `webapp/` at ``/next/``,
the pinned Pyodide from `.cache/` (fetched on first run), the game zip built on
request whenever a source has changed, and the board's modules at
``/board/js/``. Port 8000 because the leaderboard's functions allow
``http://127.0.0.1:8000`` and ``http://localhost:8000`` as origins.

``GET /__bench`` answers `results/next-spike/plan.json`, and ``POST /__bench``
appends its JSON body as one line to `results/next-spike/browser.jsonl`
(`tools/spike_bench.py`): how a headless browser is told what to measure and
hands its measurements back. Local only: the server binds
127.0.0.1.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000
BENCH_LEDGER = ROOT / "results" / "next-spike" / "browser.jsonl"
BENCH_PLAN = ROOT / "results" / "next-spike" / "plan.json"

TYPES = {".html": "text/html; charset=utf-8", ".mjs": "text/javascript", ".js": "text/javascript",
         ".css": "text/css", ".json": "application/json", ".wasm": "application/wasm",
         ".zip": "application/zip", ".png": "image/png", ".svg": "image/svg+xml",
         ".woff2": "font/woff2", ".ttf": "font/ttf", ".webmanifest": "application/manifest+json"}


def _tools():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools import build_next, fetch_pyodide
    return build_next, fetch_pyodide


class _Zip:
    """The game zip, rebuilt when any source it holds has changed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stamp: tuple | None = None
        self.data = b""
        self.name = ""

    def current(self) -> tuple[str, bytes]:
        build_next, _ = _tools()
        sources = sorted([*(ROOT / "starconquest").rglob("*.py"), *(ROOT / "starconquest").rglob("*.json"),
                          *(ROOT / "models").glob("*.py")])
        stamp = tuple((str(p), p.stat().st_mtime_ns) for p in sources)
        with self._lock:
            if stamp != self._stamp:
                self.data = build_next.game_zip()
                self.name = build_next.zip_name(self.data)
                self._stamp = stamp
            return self.name, self.data


ZIP = _Zip()


class Handler(BaseHTTPRequestHandler):
    server_version = "starconquest"

    def log_message(self, format, *args) -> None:
        if "--verbose" in sys.argv:
            super().log_message(format, *args)

    def _send(self, body: bytes, ctype: str, cache: str = "no-store", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _file(self, path: Path, cache: str = "no-store") -> None:
        if not path.is_file():
            self._send(b"not found", "text/plain", status=404)
            return
        self._send(path.read_bytes(), TYPES.get(path.suffix, "application/octet-stream"), cache)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        build_next, fetch_pyodide = _tools()
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path in ("/", "/next"):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/next/")
            self.end_headers()
            return
        if path == "/__bench":
            self._file(BENCH_PLAN)
            return
        if path.startswith("/board/js/"):
            self._file(_inside(ROOT / "leaderboard" / "js", path[len("/board/js/"):]))
            return
        if not path.startswith("/next/"):
            self._send(b"not found", "text/plain", status=404)
            return
        rest = path[len("/next/"):] or "index.html"
        immutable = "public, max-age=31536000, immutable"
        if rest == "js/build.mjs":
            name, _ = ZIP.current()
            self._send(build_next.build_info(f"py/{name}").encode(), TYPES[".mjs"])
        elif rest.startswith("py/"):
            name, data = ZIP.current()
            if rest != f"py/{name}":
                self._send(b"a stale build: reload", "text/plain", status=404)
            else:
                self._send(data, TYPES[".zip"], immutable)
        elif rest.startswith("fonts/"):
            self._file(build_next.font_path(rest[len("fonts/"):]))
        elif rest.startswith(f"pyodide/{fetch_pyodide.VERSION}/"):
            self._file(_inside(fetch_pyodide.fetch(), rest.split("/", 2)[2]), immutable)
        else:
            self._file(_inside(build_next.WEBAPP, rest))

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/__bench":
            self._send(b"not found", "text/plain", status=404)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            row = json.loads(body)
        except ValueError:
            self._send(b"not JSON", "text/plain", status=400)
            return
        BENCH_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(BENCH_LEDGER, "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
            fh.flush()
        print(f"bench: {json.dumps(row)[:160]}", flush=True)
        self._send(b"{}", TYPES[".json"])


def _inside(base: Path, rel: str) -> Path:
    """``base / rel``, or a path that does not exist when ``rel`` climbs out."""
    target = (base / rel).resolve()
    return target if target.is_relative_to(base.resolve()) else base / "\0"


def start(port: int = PORT) -> ThreadingHTTPServer:
    _tools()[1].fetch()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True, name="serve").start()
    return server


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    ap.add_argument("--verbose", action="store_true", help="log every request")
    args = ap.parse_args()
    server = start(args.port)
    url = f"http://127.0.0.1:{args.port}/next/"
    print(f"serving {url}  (Ctrl+C to stop)", flush=True)
    if args.port != PORT:
        print("note: the leaderboard only answers pages on port 8000", flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()

"""The pygbag side of the Pyodide spike's gate: the same work in today's build.

Builds a throwaway pygbag app whose main runs `bench.decide_times` on the
spike's cases (`tools/spike_bench.CASES`) and writes its results into the page's
localStorage, mirrors pygbag's runtime onto the same local server
(`tools/mirror_runtime.py`, as the real build does), and drives it in headless
Chrome: once cold with every case, once warm for startup alone. Rows go to the
spike's ledgers as runtime ``pygbag-chrome`` (`turns.jsonl`) and to
`results/next-spike/pygbag.jsonl` (startup); a run already recorded is skipped.

    uv run python tools/spike_pygbag.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import mirror_runtime, spike_bench

OUT = spike_bench.OUT / "pygbag"
LEDGER = spike_bench.OUT / "pygbag.jsonl"
PORT = 8001

MAIN = '''import asyncio
import json
import math

import platform
import pygame  # noqa: F401  (pygbag picks the wheels to ship from this file's imports)

from starconquest import ai, bench

CASES = json.loads(%r)


async def main():
    win = platform.window
    store = win.localStorage
    store.setItem("scbench_main", str(win.performance.now()))
    ai.load_models()
    ai.set_budget_scale(math.inf)
    store.setItem("scbench_ready", str(win.performance.now()))
    if "warm" not in str(win.location.search):
        rows = []
        for case in CASES:
            r = bench.decide_times(**case)
            rows.append({**case, "ms": r["ms"], "digest": r["digest"], "played": r["turns"]})
            store.setItem("scbench", json.dumps(rows))
            await asyncio.sleep(0)
    store.setItem("scbench_done", "done:" + str(win.location.search))
    while True:
        await asyncio.sleep(1)


asyncio.run(main())
'''


def build() -> None:
    stage_root = spike_bench.OUT / "pygbag-stage"
    shutil.rmtree(stage_root, ignore_errors=True)
    stage = stage_root / "starconquest"
    stage.mkdir(parents=True)
    shutil.copytree(ROOT / "starconquest", stage / "starconquest",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "models", stage / "models", ignore=shutil.ignore_patterns("__pycache__"))
    (stage / "main.py").write_text(MAIN % json.dumps(spike_bench.CASES))
    subprocess.run(["uv", "run", "--project", str(ROOT), "pygbag", "--build", "--ume_block", "0", "main.py"],
                   cwd=stage, check=True)
    shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "play").parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(stage / "build" / "web", OUT / "play")
    mirror_runtime.fetch(OUT)
    if mirror_runtime.REMOTE in (OUT / "play" / "index.html").read_text():
        print("warning: the runtime was not mirrored; pygbag loads from its CDN", flush=True)


def serve() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), partial(_Quiet, directory=str(OUT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:
        pass


def measure() -> None:
    done = {json.loads(line)["run"] for line in LEDGER.read_text().splitlines()} if LEDGER.exists() else set()
    profile = spike_bench.OUT / "pygbag-profile"
    if "cold" not in done:
        shutil.rmtree(profile, ignore_errors=True)
    for run, query in (("cold", ""), ("warm", "?warm=1")):
        if run in done:
            print(f"pygbag {run}: already recorded")
            continue
        proc = subprocess.run(
            ["node", str(ROOT / "tools" / "chrome.mjs"), f"http://127.0.0.1:{PORT}/play/{query}",
             "--until", "localStorage.getItem('scbench_done') === 'done:' + location.search",
             "--timeout", "3000", "--profile", str(profile),
             "--eval", "({main: localStorage.getItem('scbench_main'), ready: localStorage.getItem('scbench_ready'),"
                       " rows: JSON.parse(localStorage.getItem('scbench') || '[]')})"],
            capture_output=True, text=True, check=True)
        got = json.loads(proc.stdout.strip().splitlines()[-1] if not proc.stdout.strip().endswith(".png")
                         else proc.stdout.strip().splitlines()[-2])
        for row in got["rows"] if run == "cold" else []:
            spike_bench._append(spike_bench.TURNS, {"runtime": "pygbag-chrome", **row})
        spike_bench._append(LEDGER, {"run": run, "main_ms": float(got["main"]), "ready_ms": float(got["ready"]),
                                     "cases": len(got["rows"])})
        print(f"pygbag {run}: Python main at {float(got['main']) / 1000:.2f} s, bots loaded at "
              f"{float(got['ready']) / 1000:.2f} s, {len(got['rows'])} cases", flush=True)


def main() -> None:
    if not (OUT / "play" / "index.html").exists():
        build()
    server = serve()
    try:
        measure()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

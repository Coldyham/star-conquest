"""Measure the Pyodide spike against its gate (`docs/design/html-frontend.md`).

Every step appends to a ledger under `results/next-spike/` as each case
finishes and skips what is already there, so a killed run resumes:

    uv run python tools/spike_bench.py native     # bot decide times, CPython
    uv run python tools/spike_bench.py node       # ...the same in Pyodide under Node, then
                                                  # the replay-parity corpus there
    uv run python tools/spike_bench.py chrome     # startup cold and warm, and the decide
                                                  # times, in headless Chrome
    uv run python tools/spike_bench.py sizes      # bytes to first play: pygbag vs Pyodide
    uv run python tools/spike_bench.py report     # the gate, from the ledgers

Decide times are `bench.decide_times` with every wall-clock guard lifted, so
the same case makes the same game in every runtime: its final digest must
match, and the times compare like with like.
"""

from __future__ import annotations

import gzip
import json
import math
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "results" / "next-spike"
TURNS = OUT / "turns.jsonl"
DIGESTS = OUT / "digests.jsonl"
STARTUP = OUT / "startup.jsonl"
BROWSER = OUT / "browser.jsonl"
SIZES = OUT / "sizes.json"
PLAN = OUT / "plan.json"
ZIP = OUT / "game.zip"
PYGBAG_CDN = "https://pygame-web.github.io/cdn/"
PYGBAG_ARCHIVE_KB = 427   # the slimmed game archive, measured (shell.md, "The web build's download")

STOPS = [("knower", 0), ("knower", 2), ("knower", 3), ("actuary", 0), ("actuary", 3), ("marshal", 0),
         ("evolved", 0), ("evolved", 1), ("thinker", 0), ("thinker", 1), ("heuristic", 0), ("rusherplus", 0)]
CASES = [{"bot": bot, "aux": aux, "nodes": nodes, "seed": 1, "turns": 40}
         for nodes in (12, 24, 40) for bot, aux in STOPS]


def _key(row: dict) -> tuple:
    return (row.get("runtime"), row["bot"], row["aux"], row["nodes"], row["seed"], row["turns"])


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        fh.flush()


def _write_plan(run: str, cases: list[dict]) -> None:
    from tools import fetch_pyodide
    OUT.mkdir(parents=True, exist_ok=True)
    PLAN.write_text(json.dumps({"pyodide": fetch_pyodide.VERSION, "run": run, "cases": cases}))


def run_turns(runtime: str) -> None:
    """Every case not yet in the ledger for ``runtime``, in this interpreter."""
    from starconquest import ai, bench
    ai.load_models()
    ai.set_budget_scale(math.inf)
    done = {_key(r) for r in _rows(TURNS)}
    for case in CASES:
        if _key({"runtime": runtime, **case}) in done:
            continue
        t0 = time.perf_counter()
        r = bench.decide_times(**case)
        _append(TURNS, {"runtime": runtime, **case, "ms": r["ms"], "digest": r["digest"], "played": r["turns"]})
        print(f"  {runtime:13} {case['bot']:10} @{case['aux']} n{case['nodes']:<3} "
              f"mean {statistics.fmean(r['ms']):8.1f} ms  ({time.perf_counter() - t0:5.1f}s)", flush=True)


def run_digests(runtime: str) -> None:
    """The replay-parity corpus: every log reconstructs, every bot game replays."""
    from starconquest import ai
    from tools import parity_replays as parity
    ai.load_models()
    ai.set_budget_scale(math.inf)
    done = {(r["runtime"], r["name"]) for r in _rows(DIGESTS)}
    for row in parity.load_fixture():
        if (runtime, row["name"]) in done:
            continue
        t0 = time.perf_counter()
        rebuilt = parity.first_mismatch(row["digests"], parity.reconstruct_digests(row["log"]))
        replayed = (parity.first_mismatch(row["digests"], parity.replay_live(row))
                    if row["kind"] == "live" else None)
        _append(DIGESTS, {"runtime": runtime, "name": row["name"], "kind": row["kind"],
                          "boards": len(row["digests"]), "reconstruct": rebuilt or "ok",
                          "replay": replayed or ("ok" if row["kind"] == "live" else "-")})
        print(f"  {row['name']:36} reconstruct {rebuilt or 'ok'}; replay {replayed or 'ok'} "
              f"({time.perf_counter() - t0:.1f}s)", flush=True)


def in_pyodide(mode: str, boot_s: float, import_s: float) -> None:
    """`tools/spike_node.mjs`'s entry point, inside Pyodide."""
    import platform
    _append(STARTUP, {"runtime": "pyodide-node", "boot_s": round(boot_s, 3), "import_s": round(import_s, 3),
                      "python": platform.python_version(), "at": time.time()})
    if mode == "turns":
        run_turns("pyodide-node")
    elif mode == "digests":
        run_digests("pyodide-node")


def node() -> None:
    from tools import build_next
    _write_plan("node", CASES)
    ZIP.write_bytes(build_next.game_zip())
    for mode in ("digests", "turns"):
        print(f"pyodide under node: {mode}", flush=True)
        subprocess.run(["node", str(ROOT / "tools" / "spike_node.mjs"), str(ROOT), str(ZIP), mode], check=True)


def chrome() -> None:
    """Headless Chrome on the local server: a cold load that also runs every
    decide case, then a warm reload of the same profile (startup only)."""
    from starconquest import serve
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{serve.PORT}/next/", timeout=2)
    except OSError:
        serve.start()
    profile = OUT / "chrome-profile"
    shutil.rmtree(profile, ignore_errors=True)
    done_runs = {r.get("run") for r in _rows(BROWSER) if r.get("kind") == "done"}
    for run, cases in (("cold", CASES), ("warm", [])):
        if run in done_runs:
            print(f"chrome {run}: already recorded")
            continue
        _write_plan(run, cases)
        print(f"chrome {run}: {len(cases)} cases", flush=True)
        subprocess.run(["node", str(ROOT / "tools" / "chrome.mjs"),
                        f"http://127.0.0.1:{serve.PORT}/next/?bench=1",
                        "--until", "document.getElementById('status').textContent.endsWith('done')",
                        "--timeout", "3000", "--profile", str(profile)], check=True)


def _gz(data: bytes) -> int:
    return len(gzip.compress(data, 9))


def sizes() -> None:
    """Bytes a first visit fetches before it can play, raw and gzipped, each way."""
    from tools import build_next, fetch_pyodide
    cache = ROOT / ".cache" / "pygbag-runtime"
    cache.mkdir(parents=True, exist_ok=True)
    pygbag = {}
    for line in (ROOT / "tools" / "pwa" / "runtime_files.txt").read_text().splitlines():
        rel = line.strip()
        if not rel or rel.startswith("#"):
            continue
        local = cache / rel.replace("/", "__")
        if not local.exists():
            with urllib.request.urlopen(PYGBAG_CDN + rel, timeout=120) as resp:
                local.write_bytes(resp.read())
        data = local.read_bytes()
        pygbag[rel] = [len(data), _gz(data)]
    pyodide_dir = fetch_pyodide.fetch()
    ours = {name: [len(d := (pyodide_dir / name).read_bytes()), _gz(d)] for name in fetch_pyodide.FILES}
    data = build_next.game_zip()
    ours["game zip"] = [len(data), _gz(data)]
    SIZES.write_text(json.dumps({"pygbag": pygbag, "pygbag_archive_gz_kb": PYGBAG_ARCHIVE_KB,
                                 "pyodide": ours}, indent=1))
    print(f"wrote {SIZES.relative_to(ROOT)}")


def report() -> None:
    lines = ["# Pyodide spike: the gate", ""]
    # -- digests
    dig = [r for r in _rows(DIGESTS) if r["runtime"] == "pyodide-node"]
    bad = [r for r in dig if r["reconstruct"] != "ok" or r["replay"] not in ("ok", "-")]
    lines += ["## Replays (Pyodide under Node)", "",
              f"{len(dig)} of 24 corpus cases checked, {sum(r['boards'] for r in dig)} boards; "
              f"{len(bad)} mismatched." + ("" if not bad else " " + "; ".join(
                  f"{r['name']}: {r['reconstruct']} / {r['replay']}" for r in bad)), ""]
    # -- decide times
    rows = _rows(TURNS) + [r for r in _rows(BROWSER) if r.get("kind") == "turns"]
    by = {}
    for r in rows:
        by.setdefault((r["bot"], r["aux"], r["nodes"]), {})[r["runtime"]] = r
    lines += ["## Decide time per turn, seat 1 (mean ms over 40 turns, guards lifted)", "",
              "| bot @stop | systems | native | Pyodide/Node | Pyodide/Chrome | pygbag/Chrome "
              "| Pyodide ÷ pygbag | same game |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | :---: |"]
    vs_pygbag, vs_native = [], []
    for (bot, aux, nodes), got in sorted(by.items(), key=lambda kv: (kv[0][2], STOPS.index(kv[0][:2]))):
        runs = {k: got.get(k) for k in ("native", "pyodide-node", "chrome", "pygbag-chrome")}
        m = {k: statistics.fmean(v["ms"]) if v and v["ms"] else None for k, v in runs.items()}
        digests = {v["digest"] for v in runs.values() if v}
        big = (m["native"] or 0) > 0.5   # below that a mean is timer noise
        ratio = m["chrome"] / m["pygbag-chrome"] if big and m["chrome"] and m["pygbag-chrome"] else None
        if ratio:
            vs_pygbag.append(ratio)
        if big and m["chrome"]:
            vs_native.append(m["chrome"] / m["native"])
        fmt = lambda x: "-" if x is None else f"{x:.1f}"
        lines.append(f"| {bot} @{aux} | {nodes} | {fmt(m['native'])} | {fmt(m['pyodide-node'])} | "
                     f"{fmt(m['chrome'])} | {fmt(m['pygbag-chrome'])} | "
                     f"{'-' if ratio is None else f'{ratio:.2f}×'} | {'yes' if len(digests) == 1 else 'NO'} |")
    lines.append("")
    if vs_pygbag:
        lines.append(f"Pyodide ÷ pygbag, both in Chrome, over cases above 0.5 ms natively: median "
                     f"{statistics.median(vs_pygbag):.2f}×, range {min(vs_pygbag):.2f}–{max(vs_pygbag):.2f}×.")
    if vs_native:
        lines.append(f"Pyodide in Chrome ÷ native: median {statistics.median(vs_native):.2f}×.")
    # -- startup
    lines += ["", "## Startup", ""]
    for r in _rows(BROWSER):
        if r.get("kind") == "startup":
            lines.append(f"- Chrome {r['run']}: page to Python ready {r['readyMs'] / 1000:.2f} s "
                         f"(Pyodide boot {r['pyodide'] / 1000:.2f} s, game zip {r['zip'] / 1000:.2f} s, "
                         f"import {r['import'] / 1000:.2f} s)")
    for r in _rows(OUT / "pygbag.jsonl"):
        lines.append(f"- pygbag {r['run']}: page to the bench's main {r['main_ms'] / 1000:.2f} s, bots loaded "
                     f"{r['ready_ms'] / 1000:.2f} s (before the real game would import pygame's shell)")
    for r in _rows(STARTUP)[-1:]:
        lines.append(f"- Node: Pyodide boot {r['boot_s']:.2f} s, import {r['import_s']:.2f} s "
                     f"(Python {r['python']})")
    # -- sizes
    if SIZES.exists():
        s = json.loads(SIZES.read_text())
        pg_raw = sum(v[0] for v in s["pygbag"].values())
        pg_gz = sum(v[1] for v in s["pygbag"].values()) + s["pygbag_archive_gz_kb"] * 1024
        py_raw = sum(v[0] for v in s["pyodide"].values())
        py_gz = sum(v[1] for v in s["pyodide"].values())
        lines += ["", "## First-visit download", "",
                  f"- pygbag: runtime {pg_raw / 1e6:.1f} MB raw; with the {s['pygbag_archive_gz_kb']} KB game "
                  f"archive, {pg_gz / 1e6:.1f} MB gzipped",
                  f"- Pyodide: {py_raw / 1e6:.1f} MB raw, {py_gz / 1e6:.1f} MB gzipped "
                  f"(game zip {s['pyodide']['game zip'][0] // 1024} KB)"]
    text = "\n".join(lines) + "\n"
    (OUT / "report.md").write_text(text)
    print(text)


def main() -> None:
    steps = {"native": lambda: run_turns("native"), "node": node, "chrome": chrome, "sizes": sizes,
             "report": report}
    for step in sys.argv[1:] or ["native", "node", "chrome", "sizes", "report"]:
        steps[step]()


if __name__ == "__main__":
    main()

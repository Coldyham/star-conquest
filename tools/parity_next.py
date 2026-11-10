"""Shoot the parity screens in the HTML build, beside pygame's baseline.

Serves `/next/` locally, and for every recipe in
`tests/fixtures/parity_screens.json` the HTML build can draw yet (the setup
menu and its prompts, and the game surface: play, history, the modals), loads
it into the page through the session (`Session.op_parity_menu` and
`op_parity_load`, the same `starconquest.recipe` build the pygame side draws
from) and screenshots it in headless Chrome to
`results/parity/next/<size>/<name>.png`. `tools/parity_sheet.py` then sets
each beside its baseline. A shot already taken is kept unless ``--force``.

    uv run python tools/parity_next.py                 # every screen it draws, every size
    uv run python tools/parity_next.py --only game-hint --force
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import serve

RECIPES = ROOT / "tests" / "fixtures" / "parity_screens.json"
OUT = ROOT / "results" / "parity" / "next"
PORT = 8011
# The sizes, as CSS pixels (`parity_shots.SIZES`), and whether each is a phone.
SIZES = {"desktop": ("1440x960", False), "phone": ("412x870", True), "phone-land": ("870x412", True)}
# The surfaces the HTML build draws: every recipe on them shoots.
SURFACES = ("game", "menu", "resume", "maker")

SCRIPT = """(async () => {
  try {
    await window.scNext.loadRecipe(%s);
    await new Promise((r) => setTimeout(r, 700));
    return document.getElementById("status").textContent;
  } catch (e) { return "error: " + String(e.stack || e); }
})()"""


def shoot(recipe: dict, size: str) -> str:
    path = OUT / size / f"{recipe['name']}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["node", str(ROOT / "tools" / "chrome.mjs"),
         f"http://127.0.0.1:{PORT}/next/?input={'touch' if SIZES[size][1] else 'mouse'}",
         "--until", "window.scNext && window.scNext.game.ready", "--timeout", "60",
         "--size", SIZES[size][0], *(["--mobile"] if SIZES[size][1] else []),
         "--shot", str(path), "--eval", SCRIPT % json.dumps(recipe)],
        capture_output=True, text=True, check=False)
    lines = [ln for ln in proc.stdout.splitlines() if ln and not ln.startswith(("reached", "screenshot"))]
    said = json.loads(lines[-1]) if lines else ""
    if proc.returncode != 0 or (isinstance(said, str) and said.startswith("error")):
        path.unlink(missing_ok=True)
        return f"failed: {said or proc.stderr.strip()[-300:]}"
    return "ok"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", help="these recipes only")
    ap.add_argument("--force", action="store_true", help="shoot again over an existing shot")
    ap.add_argument("--size", nargs="*", choices=list(SIZES), help="these sizes only (default: all)")
    args = ap.parse_args()
    recipes = [r for r in json.loads(RECIPES.read_text())["screens"]
               if r["surface"] in SURFACES
               and (not args.only or r["name"] in args.only)]
    server = serve.start(PORT)
    try:
        for r in recipes:
            for size in args.size or SIZES:
                if size not in r["sizes"]:
                    continue
                if (OUT / size / f"{r['name']}.png").exists() and not args.force:
                    print(f"  {size:8} {r['name']}: kept", flush=True)
                    continue
                print(f"  {size:8} {r['name']}: {shoot(r, size)}", flush=True)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()

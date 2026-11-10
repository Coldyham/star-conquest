"""Contact sheets of the parity screens, for looking over by eye.

For each size, every screen in `tests/fixtures/parity_screens.json` is laid out
as a labelled thumbnail from `results/parity/baseline/`, the pygame shell's
screens (redraw them with `tools/parity_shots.py` at any commit before the
cutover, such as a1a17ad). Where the HTML build's
shot of the same screen exists (`results/parity/next/<size>/<name>.png`), it
sits to the right of the baseline, and a screen either side is missing is
flagged in red.

    uv run python tools/parity_sheet.py              # every size (pygame-ce: a dev dependency)
    uv run python tools/parity_sheet.py --size phone --width 300

Writes `results/parity/sheet-<size>.png`, a few screens per row.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

ROOT = Path(__file__).resolve().parent.parent
PARITY = ROOT / "results" / "parity"
RECIPES = ROOT / "tests" / "fixtures" / "parity_screens.json"

BG = (16, 18, 24)
TEXT = (220, 224, 232)
MISSING = (220, 70, 70)
GAP = 16
LABEL_H = 22


def _thumb(path: Path, width: int) -> pygame.Surface | None:
    if not path.exists():
        return None
    img = pygame.image.load(str(path))
    h = round(img.get_height() * width / img.get_width())
    return pygame.transform.smoothscale(img, (width, h))


def sheet(size: str, names: list[str], width: int, per_row: int) -> tuple[Path, list[str]]:
    font = pygame.font.Font(None, 22)
    cells = []
    problems = []
    paired = (PARITY / "next" / size).is_dir()
    for name in names:
        base = _thumb(PARITY / "baseline" / size / f"{name}.png", width)
        nxt = _thumb(PARITY / "next" / size / f"{name}.png", width) if paired else None
        if base is None:
            problems.append(f"{size}/{name}: no baseline")
        if paired and nxt is None:
            problems.append(f"{size}/{name}: no next shot")
        cells.append((name, base, nxt))
    cell_w = width * (2 if paired else 1) + (GAP if paired else 0)
    heights = [max((s.get_height() for s in (b, n) if s is not None), default=width // 2)
               for _, b, n in cells]
    rows = [list(range(i, min(i + per_row, len(cells)))) for i in range(0, len(cells), per_row)]
    row_h = [max(heights[i] for i in row) + LABEL_H + GAP for row in rows]
    out = pygame.Surface((GAP + per_row * (cell_w + GAP), GAP + sum(row_h)))
    out.fill(BG)
    y = GAP
    for row, rh in zip(rows, row_h):
        x = GAP
        for i in row:
            name, base, nxt = cells[i]
            bad = base is None or (paired and nxt is None)
            out.blit(font.render(name, True, MISSING if bad else TEXT), (x, y))
            for j, img in enumerate((base, nxt) if paired else (base,)):
                at = (x + j * (width + GAP), y + LABEL_H)
                if img is None:
                    pygame.draw.rect(out, MISSING, (*at, width, width // 2), 2)
                else:
                    out.blit(img, at)
            x += cell_w + GAP
        y += rh
    path = PARITY / f"sheet-{size}.png"
    pygame.image.save(out, str(path))
    return path, problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--size", default="", help="one size (default: every size)")
    ap.add_argument("--width", type=int, default=0, help="thumbnail width in px")
    ap.add_argument("--per-row", type=int, default=0)
    args = ap.parse_args()
    pygame.init()
    recipes = json.loads(RECIPES.read_text())
    sizes = [args.size] if args.size else list(recipes["sizes"])
    problems: list[str] = []
    for size in sizes:
        names = [r["name"] for r in recipes["screens"] if size in r["sizes"]]
        phone = recipes["sizes"][size][3] and recipes["sizes"][size][1] > recipes["sizes"][size][0]
        width = args.width or (220 if phone else 420)
        per_row = args.per_row or (6 if phone else 3)
        path, found = sheet(size, names, width, per_row)
        problems += found
        print(f"{path.relative_to(ROOT)}: {len(names)} screens")
    if problems:
        sys.exit("\n".join(problems))


if __name__ == "__main__":
    main()

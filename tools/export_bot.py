"""Write a visual rule program out as a standalone ``models/*.py`` bot.

    uv run python tools/export_bot.py --list
    uv run python tools/export_bot.py blockheuristic          # -> models/blockheuristic.py
    uv run python tools/export_bot.py blockrush -o /tmp --name myrusher

The graduation path from rules to real code, usable before any editor exists.
The emitted file is an ordinary drop-in model — it imports nothing private and
never calls back into ``botlang`` — so once it lands in ``models/`` it is yours
to edit, and `ai.load_models` picks it up like any other bot.

Its name is the strategy name, so exporting a starter under its own name would
collide with the built-in registration; ``--name`` is there for that.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest import botlang  # noqa: E402 — needs the path above


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program", nargs="?", help="a name from botlang.STARTERS")
    ap.add_argument("-o", "--out-dir", default=str(ROOT / "models"),
                    help="where to write it (default: models/)")
    ap.add_argument("--name", help="file stem to write as (default: the program's name)")
    ap.add_argument("--list", action="store_true", help="list the available programs")
    ap.add_argument("--stdout", action="store_true", help="print the source instead of writing")
    args = ap.parse_args()

    if args.list or not args.program:
        for name, program in sorted(botlang.STARTERS.items()):
            print(f"{name}")
            for rule in program.rules:
                print(f"    {botlang.describe(rule)}")
        if not args.program:
            return
    program = botlang.STARTERS.get(args.program)
    if program is None:
        ap.error(f"unknown program: {args.program}. available: {', '.join(sorted(botlang.STARTERS))}")

    source = botlang.export(program)
    if args.stdout:
        print(source, end="")
        return
    out = Path(args.out_dir) / f"{args.name or program.name}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(source)
    print(f"wrote {out} ({len(source.splitlines())} lines)")


if __name__ == "__main__":
    main()

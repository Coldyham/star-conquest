"""Regenerate ``starconquest/starnames.py`` from the IAU star-name catalogue.

    uv run python tools/gen_starnames.py [tools/iau-star-names.csv]

Reads the "proper names" column of an IAU Working Group on Star Names CSV export
(https://www.iau.org/public/themes/naming_stars/), dedupes it, sorts it, and
writes the list out as a plain Python tuple so the game needs no CSV at runtime
(the web build ships only the package dir).
"""

from __future__ import annotations

import csv
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "tools" / "iau-star-names.csv"
OUT = ROOT / "starconquest" / "starnames.py"

HEADER = '''"""Star names for map flavour: the IAU's catalogue of officially named stars.

Pure core, no I/O: generated from ``tools/iau-star-names.csv`` by
``tools/gen_starnames.py`` — edit the CSV and regenerate rather than hand-editing
this file. Names are cosmetic; systems stay keyed by integer id everywhere.
"""

from __future__ import annotations

import random

'''

BODY = '''

def pick(rng: random.Random, count: int) -> list[str]:
    """``count`` distinct star names drawn from ``rng``.

    Deterministic given the RNG state, which is what keeps a seed reproducing a
    whole map — names included. If a map ever wants more systems than the
    catalogue has names (it cannot today: ``config.MAX_NODES`` is far smaller),
    the surplus is numbered rather than left blank.
    """
    if count <= len(NAMES):
        return rng.sample(NAMES, count)
    names = rng.sample(NAMES, len(NAMES))
    while len(names) < count:
        names.append(f"{NAMES[len(names) % len(NAMES)]} {len(names) // len(NAMES) + 1}")
    return names[:count]
'''


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    with src.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    key = next(k for k in rows[0] if k.strip().lower().startswith("proper name"))
    names = sorted({unicodedata.normalize("NFC", r[key].strip()) for r in rows if r[key].strip()})
    lines = "".join(f"    {name!r},\n" for name in names)
    OUT.write_text(f"{HEADER}NAMES: tuple[str, ...] = (\n{lines})\n{BODY}", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(names)} names from {src.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Regenerate ``starconquest/starnames.py`` and ``starconquest/starlore.py`` from
the IAU star-name catalogue.

    uv run python tools/gen_starnames.py [tools/iau-star-names.csv]

Reads the "proper names" column of an IAU Working Group on Star Names CSV export
(https://www.iau.org/public/themes/naming_stars/), dedupes it, sorts it, and
writes the list out as a plain Python tuple so the game needs no CSV at runtime
(the web build ships only the package dir). Each name's "Language", "Origin"
and "Reference" go to ``starlore.py`` for the star's info card, with the
scripts the bundled font cannot draw taken out (`_drawable`).
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "tools" / "iau-star-names.csv"
OUT = ROOT / "starconquest" / "starnames.py"
LORE_OUT = ROOT / "starconquest" / "starlore.py"

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
    catalogue has names (it cannot today: ``config.MAX_NODES`` is well short of it),
    the surplus is numbered rather than left blank.
    """
    if count <= len(NAMES):
        return rng.sample(NAMES, count)
    names = rng.sample(NAMES, len(NAMES))
    while len(names) < count:
        names.append(f"{NAMES[len(names) % len(NAMES)]} {len(names) // len(NAMES) + 1}")
    return names[:count]
'''


LORE_HEADER = '''"""What the IAU catalogue says about each star name: its language, origin and
reference, for the star's info card.

Pure core, no I/O: generated from ``tools/iau-star-names.csv`` by
``tools/gen_starnames.py`` — edit the CSV and regenerate rather than hand-editing
this file. Keyed by the names in ``starnames.NAMES``; a field the catalogue
leaves blank is ``""``.
"""

from __future__ import annotations

from typing import NamedTuple


class Lore(NamedTuple):
    language: str
    origin: str
    reference: str


'''

# Scripts DejaVu Sans Mono has no glyphs for (CJK, Devanagari, Thai, cuneiform,
# Cherokee, Mongolian, Hangul), or has glyphs for but pygame cannot lay out
# (Arabic: right to left, joined). The catalogue gives a romanisation beside them.
_UNDRAWABLE = ("ARABIC", "CJK", "DEVANAGARI", "THAI", "CUNEIFORM", "CHEROKEE", "MONGOLIAN", "HANGUL")

# Values of the "Language" column that name no language.
_NOT_A_LANGUAGE = {"NEWC"}


def _drawable(text: str) -> str:
    """``text`` with every run of an undrawable script removed, and the brackets
    and spaces each run leaves behind tidied up."""
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ")
    text = "".join("\0" if unicodedata.name(ch, "").startswith(_UNDRAWABLE) else ch for ch in text)
    text = re.sub(r"\0[\0\s]*", "", text)
    text = re.sub(r"\([^()]*:\s*\)", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r":\s*,", ":", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\( ", "(", text)
    text = re.sub(r" ([,.;:)])", r"\1", text)
    return text.strip()


def write_lore(rows: list[dict], key: str) -> int:
    """Write ``starlore.py``: one ``Lore`` per name. Returns how many."""
    lore = {}
    for r in rows:
        name = unicodedata.normalize("NFC", r[key].strip())
        if not name:
            continue
        language = _drawable(r.get("Language") or "")
        lore[name] = (
            "" if language in _NOT_A_LANGUAGE else language,
            _drawable(r.get("Origin") or ""),
            _drawable(r.get("Reference") or ""),
        )
    lines = "".join(f"    {name!r}: Lore({', '.join(repr(v) for v in lore[name])}),\n" for name in sorted(lore))
    LORE_OUT.write_text(f"{LORE_HEADER}LORE: dict[str, Lore] = {{\n{lines}}}\n", encoding="utf-8")
    return len(lore)


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    with src.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    key = next(k for k in rows[0] if k.strip().lower().startswith("proper name"))
    names = sorted({unicodedata.normalize("NFC", r[key].strip()) for r in rows if r[key].strip()})
    lines = "".join(f"    {name!r},\n" for name in names)
    OUT.write_text(f"{HEADER}NAMES: tuple[str, ...] = (\n{lines})\n{BODY}", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(names)} names from {src.relative_to(ROOT)}")
    print(f"wrote {LORE_OUT.relative_to(ROOT)}: {write_lore(rows, key)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

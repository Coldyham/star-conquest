"""Regenerate ``tools/craters.py`` from the IAU's named lunar craters.

    uv run python tools/gen_craternames.py [tools/iau-lunar-craters.csv]

The weekly campaign (``tools/campaign.py``) names each node after a crater on
the Moon, the way a game names its systems after IAU-named stars. The source is
the Gazetteer of Planetary Nomenclature's list of the Moon's craters
(https://planetarynames.wr.usgs.gov, Advanced Search: target Moon, feature type
Crater; tick every column and press CSV), saved as
``tools/iau-lunar-craters.csv``. The site builds that CSV in the browser from
the results table, so the table in the results page's HTML is the same data.

Kept: approved, unlettered craters (never a satellite like "Copernicus A")
named after a person (the Origin line gives life dates, so the small
first-name craters such as "Ina" and the Apollo landing-site names are out), at
most ``MAX_KM`` across, so the famous big ones (Copernicus, Tycho, Plato,
Kepler) stay out and the names read as unfamiliar scientists. Left out too: a
name that is also one of the game's star names, and one the board would
struggle to head a card with (``MAX_LEN``, ``_SHOWABLE``), and the few
household names small enough to pass the size cut (``FAMOUS``). Each name's origin
line (``_origin``), diameter and approval year go with it, for the node's
info card.

Only the worker reads the output: it is not part of the game's zip.
"""

from __future__ import annotations

import csv
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from starconquest.starnames import NAMES as STAR_NAMES

DEFAULT_CSV = ROOT / "tools" / "iau-lunar-craters.csv"
OUT = ROOT / "tools" / "craters.py"

MAX_KM = 30.0          # the largest kept: Kepler is 31, Aristarchus 40, Tycho 85
MAX_LEN = 16           # characters, so a name fits a node's card heading
# Household names small enough to pass MAX_KM. A judgement call: edit freely.
FAMOUS = frozenset({"Aldrin", "Armstrong", "Euclides", "Euler", "Fahrenheit", "Freud",
                    "Galilaei", "Kepler", "Marco Polo", "Rutherford"})

# The Gazetteer's column names, after `_column` folds case, spaces and
# underscores; the first of each that a header carries is read.
_COLUMNS = {
    "name": ("featurename", "name", "cleanfeaturename"),   # keeps "Mädler"
    "diameter": ("diameter", "diameterkm"),
    "origin": ("origin",),
    "approved": ("approvaldate", "approved"),
    "status": ("approvalstatus", "status"),
    "kind": ("featuretype", "type"),
}

_SATELLITE = re.compile(r"\s[A-Z]{1,2}$")       # "Copernicus A", "Mösting AA"
_LIFE_DATES = re.compile(r"\([^()]*\d{3,4}[^()]*\)")   # "(1794-1874)", "(c. 1585-1646)"
_GIVEN = re.compile(r"(^|; )([^;\d]+?); ")             # "Johann Heinrich; " before a description
_SHOWABLE = re.compile(r"^[^\W\d_][\w' .-]*$")

HEADER = '''"""Lunar crater names for the weekly campaign's nodes, with each one's origin.

Generated from ``tools/iau-lunar-craters.csv`` (the IAU Gazetteer of Planetary
Nomenclature) by ``tools/gen_craternames.py``: edit the CSV or the generator's
filters and regenerate rather than hand-editing this file. Read only by
``tools/campaign.py``, which stamps a name and its card onto each node.
"""

from __future__ import annotations

from typing import NamedTuple


class Crater(NamedTuple):
    origin: str          # the Gazetteer's line on who or what it is named after
    diameter_km: float
    approved: str        # the year the IAU approved the name, or ""


'''


def _column(header: str) -> str:
    return re.sub(r"[^a-z]", "", header.lower())


def _columns(fieldnames: list[str]) -> dict[str, str]:
    """Each wanted field's header in this export. Name, diameter and origin
    are required; the rest only narrow the list when present."""
    folded = {_column(h): h for h in fieldnames}
    found = {}
    for want, options in _COLUMNS.items():
        hit = next((folded[o] for o in options if o in folded), None)
        if hit is not None:
            found[want] = hit
    missing = {"name", "diameter", "origin"} - found.keys()
    if missing:
        raise SystemExit(f"the CSV has no {', '.join(sorted(missing))} column; "
                         f"its header is: {', '.join(fieldnames)}")
    return found


def _tidy(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def keep(name: str, diameter: float | None, origin: str, star_names: set[str]) -> bool:
    """Whether a crater makes the list (see the module docstring)."""
    return (bool(name) and bool(origin)
            and not _SATELLITE.search(name)
            and diameter is not None and 0 < diameter <= MAX_KM
            and bool(_LIFE_DATES.search(origin))
            and len(name) <= MAX_LEN and bool(_SHOWABLE.match(name))
            and name not in FAMOUS
            and name.casefold() not in star_names)


def _origin(name: str, origin: str) -> str:
    """The Gazetteer's line with the surname put back: "Johann Heinrich;
    German astronomer (1794-1874)." reads "Johann Heinrich Mädler, German
    astronomer (1794-1874).". A part that already holds the name ("Heike
    Kamerlingh Onnes", "Wilhelm IV, Landgrave of Hesse") or names a person
    whole ("Philip III of Macedonia") keeps its own words; "Of Marseilles"
    follows the name; and a Latinised crater's "Hove, Martin van den" reads
    "Martin van den Hove (Hortensius)"."""
    def whole(match: re.Match) -> str:
        given = match.group(2)
        surname, comma, rest = given.partition(", ")
        if name.casefold() in given.casefold() or " of " in given:
            person = given
        elif given.startswith("Of "):
            person = f"{name} of {given[3:]}"
        elif comma and " " not in surname:
            person = f"{rest} {surname} ({name})"
        else:
            person = f"{given} {name}"
        return f"{match.group(1)}{person}, "
    return re.sub(r";\s*\(", " (", _GIVEN.sub(whole, origin))


def read(rows: list[dict], cols: dict[str, str]) -> dict[str, tuple[str, float, str]]:
    star_names = {n.casefold() for n in STAR_NAMES}
    out = {}
    for row in rows:
        if "kind" in cols and "crater" not in (row.get(cols["kind"]) or "").lower():
            continue
        status = (row.get(cols["status"]) or "approved").lower()
        if "status" in cols and (status.startswith("not") or not status.startswith(("approved", "adopted"))):
            continue
        name = _tidy(row.get(cols["name"]) or "")
        origin = _tidy(row.get(cols["origin"]) or "")
        try:
            diameter = float(row.get(cols["diameter"]) or "")
        except ValueError:
            diameter = None
        if not keep(name, diameter, origin, star_names):
            continue
        year = re.search(r"\b(1[6-9]|20)\d\d\b", row.get(cols.get("approved", ""), "") or "")
        out[name] = (_origin(name, origin), round(diameter, 1), year.group(0) if year else "")
    return out


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    with src.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        cols = _columns(list(reader.fieldnames or []))
    craters = read(rows, cols)
    lines = "".join(f"    {name!r}: Crater({origin!r}, {km!r}, {year!r}),\n"
                    for name, (origin, km, year) in sorted(craters.items()))
    OUT.write_text(f"{HEADER}CRATERS: dict[str, Crater] = {{\n{lines}}}\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(craters)} of {len(rows)} rows "
          f"(approved, at most {MAX_KM:g} km, unlettered, named after a person)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Lunar crater names for the weekly campaign's nodes, with each one's origin.

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


CRATERS: dict[str, Crater] = {
}

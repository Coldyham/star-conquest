"""The crater-name generator (tools/gen_craternames.py) on a few Gazetteer rows."""

from __future__ import annotations

import csv
import io

from tools import gen_craternames as gen

HEADER = ["Feature_ID", "Feature_Name", "Clean_Feature_Name", "Target", "Diameter",
          "Feature_Type", "Approval_Status", "Approval_Date", "Origin"]
ROWS = [
    ["1", "Mädler", "Madler", "Moon", "27.58", "Crater, craters", "Adopted by IAU", "1935", "Johann Heinrich von Mädler, German astronomer (1794-1874)."],
    ["2", "Copernicus", "Copernicus", "Moon", "96.07", "Crater, craters", "Adopted by IAU", "1935", "Nicolaus Copernicus, Polish astronomer (1473-1543)."],
    ["3", "Mädler A", "Madler A", "Moon", "8.1", "Crater, craters", "Adopted by IAU", "2006", "Named from nearby crater."],
    ["4", "Ina", "Ina", "Moon", "2.9", "Crater, craters", "Adopted by IAU", "1979", "Female first name."],
    ["5", "Achernar", "Achernar", "Moon", "5", "Crater, craters", "Adopted by IAU", "1979", "A star's name, which the game already uses."],
    ["6", "Lacus Mortis", "Lacus Mortis", "Moon", "150", "Lacus, lacūs", "Adopted by IAU", "1935", "Lake of Death."],
    ["7", "Keldysh", "Keldysh", "Moon", "", "Crater, craters", "Adopted by IAU", "1982", "Mstislav Keldysh, Soviet mathematician."],
    ["8", "Fra Mauro", "Fra Mauro", "Moon", "26.8", "Crater, craters", "Adopted by IAU", "1935", "Fra Mauro, Italian cartographer (?-1459)."],
    ["9", "Dropped", "Dropped", "Moon", "10", "Crater, craters", "Dropped, disallowed", "", "Never adopted."],
]


def _read(rows=ROWS):
    text = io.StringIO()
    csv.writer(text).writerows([HEADER, *rows])
    reader = csv.DictReader(io.StringIO(text.getvalue()))
    rows = list(reader)
    return gen.read(rows, gen._columns(list(reader.fieldnames)))


def test_keeps_small_unlettered_craters_named_after_people():
    craters = _read()
    assert sorted(craters) == ["Fra Mauro", "Mädler"]
    assert craters["Fra Mauro"] == ("Fra Mauro, Italian cartographer (?-1459).", 26.8, "1935")


def test_the_clean_name_is_read_when_there_is_no_feature_name():
    rows = [[r[0], r[2], r[3], r[4], r[5], r[6], r[7], r[8]] for r in ROWS]
    header = [h for h in HEADER if h != "Feature_Name"]
    text = io.StringIO()
    csv.writer(text).writerows([header, *rows])
    reader = csv.DictReader(io.StringIO(text.getvalue()))
    got = gen.read(list(reader), gen._columns(list(reader.fieldnames)))
    assert "Madler" in got


def test_a_missing_column_names_the_header_it_found():
    try:
        gen._columns(["Feature_Name", "Origin"])
    except SystemExit as err:
        assert "diameter" in str(err) and "Feature_Name" in str(err)
    else:
        raise AssertionError("no error")

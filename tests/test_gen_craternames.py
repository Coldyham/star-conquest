"""The crater-name generator (tools/gen_craternames.py) on a few Gazetteer rows,
in the shape of tools/iau-lunar-craters.csv."""

from __future__ import annotations

import csv
import io

from tools import gen_craternames as gen

HEADER = ["Feature ID", "Feature Name", "Clean Feature Name", "Target", "Diameter",
          "Feature Type", "Approval Status", "Approval Date", "Origin"]
CRATER = "Crater, craters"
ROWS = [
    ["1", "Mädler", "Madler", "Moon", "27.58", CRATER, "Approved", "1935", "Johann Heinrich; German astronomer (1794-1874)."],
    ["2", "Copernicus", "Copernicus", "Moon", "96.07", CRATER, "Approved", "1935", "Nicolaus; Polish astronomer (1473-1543)."],
    ["3", "Mädler A", "Madler A", "Moon", "8.1", CRATER, "Approved", "2006", "Named from nearby crater (1935)."],
    ["4", "Ina", "Ina", "Moon", "2.9", CRATER, "Approved", "1979", "Female first name."],
    ["5", "Achernar", "Achernar", "Moon", "5", CRATER, "Approved", "1979", "Somebody; astronomer (1900-1950)."],
    ["6", "Kepler", "Kepler", "Moon", "29.5", CRATER, "Approved", "1935", "Johannes; German astronomer (1571-1630)."],
    ["7", "Keldysh", "Keldysh", "Moon", "", CRATER, "Approved", "1982", "Mstislav; Soviet mathematician (1911-1978)."],
    ["8", "Hortensius", "Hortensius", "Moon", "14.6", CRATER, "Approved", "1935", "Hove, Martin van den; Dutch astronomer (1605-1639)."],
    ["9", "Dropped", "Dropped", "Moon", "10", CRATER, "Not Approved", "", "Never; adopted (1900-1950)."],
    ["10", "Lacus Mortis", "Lacus Mortis", "Moon", "15", "Lacus, lacūs", "Approved", "1935", "Lake of Death (1935)."],
]


def _read(rows=ROWS, header=HEADER):
    text = io.StringIO()
    csv.writer(text).writerows([header, *rows])
    reader = csv.DictReader(io.StringIO(text.getvalue()))
    return gen.read(list(reader), gen._columns(list(reader.fieldnames)))


def test_keeps_small_approved_unlettered_craters_named_after_people():
    craters = _read()
    assert sorted(craters) == ["Hortensius", "Mädler"]
    assert craters["Mädler"] == ("Johann Heinrich Mädler, German astronomer (1794-1874).", 27.6, "1935")


def test_the_origin_line_puts_the_surname_back():
    assert gen._origin("Mädler", "Johann Heinrich; German astronomer (1794-1874).") == \
        "Johann Heinrich Mädler, German astronomer (1794-1874)."
    assert gen._origin("Hortensius", "Hove, Martin van den; Dutch astronomer (1605-1639).") == \
        "Martin van den Hove (Hortensius), Dutch astronomer (1605-1639)."
    assert gen._origin("Pytheas", "Of Marseilles; Greek navigator (c. 308 B.C.).") == \
        "Pytheas of Marseilles, Greek navigator (c. 308 B.C.)."
    assert gen._origin("Kamerlingh Onnes", "Heike Kamerlingh Onnes; Dutch physicist (1853-1926).") == \
        "Heike Kamerlingh Onnes, Dutch physicist (1853-1926)."
    assert gen._origin("Ross", "James Clark; British explorer (1800-1862); Frank Elmore; American astronomer "
                       "(1874-1966).") == ("James Clark Ross, British explorer (1800-1862); Frank Elmore Ross, "
                                           "American astronomer (1874-1966).")


def test_the_clean_name_is_read_when_there_is_no_feature_name():
    rows = [[r[0], r[2], *r[3:]] for r in ROWS]
    assert "Madler" in _read(rows, [h for h in HEADER if h != "Feature Name"])


def test_a_missing_column_names_the_header_it_found():
    try:
        gen._columns(["Feature Name", "Origin"])
    except SystemExit as err:
        assert "diameter" in str(err) and "Feature Name" in str(err)
    else:
        raise AssertionError("no error")

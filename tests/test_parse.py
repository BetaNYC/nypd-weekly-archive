"""Parsers against real NYPD files saved 2026-11-21 (tests/fixtures/), and
against copies deliberately broken to prove a layout change fails loudly."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from nypd_weekly_archive.parse import (
    LayoutError,
    parse_compstat,
    parse_fatalities,
    parse_qteam,
    parse_trafficstat,
    week_end,
)


def pick(rows, **kw):
    hits = [r for r in rows if all(getattr(r, k) == v for k, v in kw.items())]
    assert len(hits) == 1, (kw, hits)
    return hits[0].value


# ------------------------------------------------------------ real files


def test_trafficstat_precinct(fixtures: Path) -> None:
    rows = parse_trafficstat(fixtures / "ts-en-us-034.xlsx")
    assert len(rows) == 414  # 69 measures x 3 periods x 2 years
    r0 = rows[0]
    assert (r0.geography, r0.precinct, r0.week_start, r0.week_end) == ("034", 34, date(2026, 11, 9), date(2026, 11, 15))
    # Values read by eye from the sheet.
    assert pick(rows, sheet="Page1", section="Parking Summonses", measure="Bicycle Lane", period="ytd", year=2026) == 10
    assert pick(rows, sheet="Page1", section="Parking Summonses", measure="Bicycle Lane", period="ytd", year=2025) == 37
    assert pick(rows, sheet="Page2", section="Moving Summonses", measure="E-bike", period="ytd", year=2026) == 210
    assert pick(rows, sheet="Page2", section="Fatalities", measure="E-bike", period="ytd", year=2026) == 3


def test_trafficstat_citywide(fixtures: Path) -> None:
    rows = parse_trafficstat(fixtures / "ts-en-us-Citywide.xlsx")
    assert rows[0].geography == "Citywide" and rows[0].precinct is None
    assert pick(rows, sheet="Page1", section="Collisions", measure="Collisions", period="wtd", year=2026) == 1459


def test_compstat(fixtures: Path) -> None:
    rows = parse_compstat(fixtures / "cs-en-us-034pct.xlsx")
    assert rows[0].geography == "34th Precinct" and rows[0].week_end == date(2026, 11, 15)
    assert pick(rows, section="Crime Complaints", measure="Murder", period="ytd", year=2026) == 4
    assert pick(rows, section="Traffic Statistics", measure="Traffic Fatalities", period="ytd", year=2025) == 2
    assert pick(rows, section="Crime Complaints", measure="Murder", period="ytd_pct_change_vs_1993") == -92
    assert pick(rows, section="Historical Perspective", measure="Robbery", period="calendar_year", year=1990) == 1919


def test_qteam(fixtures: Path) -> None:
    rows = parse_qteam(fixtures / "qs-en-us-034pct.xlsx")
    assert rows[0].week_start == date(2026, 10, 19) and rows[0].week_end == date(2026, 10, 25)
    assert pick(rows, section="Calls For Service", measure="Total 911 Calls", period="ytd", year=2026) == 47721
    assert pick(rows, section="Vehicle Related Calls", measure="Dangerous Driving*", period="ytd", year=2026) == 178


def test_fatalities(fixtures: Path) -> None:
    rows = parse_fatalities(fixtures / "traffic-fatalities-pbbn.xlsx")
    assert {r.week_end for r in rows} == {date(2026, 11, 15)}
    assert pick(rows, geography="73", period="ytd", year=2026) == 2
    assert pick(rows, geography="PBBN", period="ytd", year=2025) == 27
    assert pick(rows, geography="Citywide", period="ytd", year=2026) == 181


def test_week_end(fixtures: Path) -> None:
    assert week_end("qteam", fixtures / "qs-en-us-034pct.xlsx") == date(2026, 10, 25)


# ------------------------------------------------------- layout changes


def _edit(src: Path, tmp_path: Path, sheet: str, coord: str, value) -> Path:
    dst = tmp_path / src.name
    shutil.copy(src, dst)
    # data_only: keep cached formula results (the fatalities files are formulas
    # linked to another workbook; saving without this would drop the values).
    wb = openpyxl.load_workbook(dst, data_only=True)
    wb[sheet][coord] = value
    wb.save(dst)
    return dst


@pytest.mark.parametrize(
    ("fixture", "parser", "sheet", "coord", "value", "message"),
    [
        # A new measure NYPD might add.
        ("ts-en-us-034.xlsx", parse_trafficstat, "Page1", "A32", "Bike Lane Blocked", "unknown measure"),
        # A shifted column: the change column no longer matches.
        ("ts-en-us-034.xlsx", parse_trafficstat, "Page1", "D28", 999, "!= change"),
        # A renamed section.
        ("ts-en-us-034.xlsx", parse_trafficstat, "Page2", "A37", "Moving Violations", "unrecognized row"),
        # A different date line.
        ("ts-en-us-034.xlsx", parse_trafficstat, "Page1", "G4", "Week of Nov 9", "date line"),
        ("cs-en-us-034pct.xlsx", parse_compstat, "CompStat", "A21", "Total Majors", "unknown measure"),
        ("cs-en-us-034pct.xlsx", parse_compstat, "CompStat", "A49", "Figures are final.", "unknown historical measure"),
        ("qs-en-us-034pct.xlsx", parse_qteam, "QOLStat_PUBLIC_Report", "D44", "Abandoned Cars", "unknown measure"),
        ("qs-en-us-034pct.xlsx", parse_qteam, "QOLStat_PUBLIC_Report", "J22", "lots", "expected a count"),
        ("traffic-fatalities-pbbn.xlsx", parse_fatalities, "Sheet1", "A14", "Brooklyn North", "unknown geography"),
        ("traffic-fatalities-pbbn.xlsx", parse_fatalities, "Sheet1", "F1", "30 Day", "header"),
    ],
)
def test_layout_change_fails_loud(fixtures, tmp_path, fixture, parser, sheet, coord, value, message) -> None:
    broken = _edit(fixtures / fixture, tmp_path, sheet, coord, value)
    with pytest.raises(LayoutError, match=message):
        parser(broken)


def test_compstat_blank_history_cells_are_kept_empty(fixtures: Path, tmp_path: Path) -> None:
    # Precincts created after 1990 (116th, 121st) have blank history cells;
    # a blank must stay in its own column, not shift the rest left.
    broken = _edit(fixtures / "cs-en-us-034pct.xlsx", tmp_path, "CompStat", "C39", None)
    rows = parse_compstat(broken)
    assert pick(rows, section="Historical Perspective", measure="Murder", period="calendar_year", year=1990) is None
    assert pick(rows, section="Historical Perspective", measure="Murder", period="calendar_year", year=1993) == 50


def test_value_outside_header_columns_fails(fixtures: Path, tmp_path: Path) -> None:
    broken = _edit(fixtures / "ts-en-us-034.xlsx", tmp_path, "Page1", "N28", 5)
    with pytest.raises(LayoutError, match="outside the header"):
        parse_trafficstat(broken)


def test_extra_sheet_fails_loud(fixtures: Path, tmp_path: Path) -> None:
    dst = tmp_path / "ts.xlsx"
    shutil.copy(fixtures / "ts-en-us-034.xlsx", dst)
    wb = openpyxl.load_workbook(dst)
    wb.create_sheet("Page3")
    wb.save(dst)
    with pytest.raises(LayoutError, match="sheets"):
        parse_trafficstat(dst)


def test_not_an_xlsx(tmp_path: Path) -> None:
    bad = tmp_path / "x.xlsx"
    bad.write_bytes(b"<html>Access Denied</html>")
    with pytest.raises(LayoutError, match="not a readable xlsx"):
        parse_trafficstat(bad)


def test_one_change_mismatch_is_an_anomaly_two_fail(fixtures: Path, tmp_path: Path) -> None:
    from nypd_weekly_archive.parse import parse_file

    one = _edit(fixtures / "traffic-fatalities-pbbn.xlsx", tmp_path, "Sheet1", "D3", -2)
    rows, notes = parse_file("fatalities", one)
    assert len(notes) == 1 and "!= change -2" in notes[0]
    assert pick(rows, geography="73", period="7d", year=2026) == 0  # counts kept as published
    with pytest.raises(LayoutError):
        parse_fatalities(one)  # direct calls stay strict
    (tmp_path / "x").mkdir()
    two = _edit(one, tmp_path / "x", "Sheet1", "D4", 7)
    with pytest.raises(LayoutError, match="2 Change mismatches"):
        parse_file("fatalities", two)


def test_fatalities_variants(fixtures: Path, tmp_path: Path) -> None:
    # Table shifted to column B, as in pbqn/pbqs/pbsi.
    src = tmp_path / "b.xlsx"
    wb = openpyxl.load_workbook(fixtures / "traffic-fatalities-pbbn.xlsx", data_only=True)
    wb["Sheet1"].insert_cols(1)
    wb["Sheet1"].title = "Table 1"
    stale = wb.create_sheet("PBMS")  # an undated leftover tab, as in papd
    stale["A2"] = "PCT"
    wb.save(src)
    rows = parse_fatalities(src)
    assert pick(rows, geography="73", period="ytd", year=2026) == 2
    assert {r.sheet for r in rows} == {"Table 1"}


def test_citywide_and_correction_sheets_read_as_compstat(fixtures: Path) -> None:
    """Saved 2026-11-22 from citywide-crime-stats.page."""
    city = parse_compstat(fixtures / "cs-en-us-city.xlsx")
    correction = parse_compstat(fixtures / "cs-en-us-Correction.xlsx")
    assert len(city) == len(correction) == 211
    assert {r.geography for r in city} == {"Citywide"}
    assert {r.geography for r in correction} == {"Department of Correction Citywide"}
    assert {r.week_end for r in city + correction} == {date(2026, 11, 15)}


def test_housing_report_is_dated_not_read(fixtures: Path) -> None:
    from nypd_weekly_archive.parse import PARSERS, week_end

    assert week_end("housing", fixtures / "hb_eagle_report.xlsx") == date(2026, 11, 15)
    assert "housing" not in PARSERS


def test_precinct_from_filename_with_checksum_suffix() -> None:
    # Week ending 2026-11-15: a second copy saved the same day got a checksum suffix,
    # and its precinct came out blank (78 precinct files).
    from nypd_weekly_archive.parse import precinct_from_filename

    assert precinct_from_filename("ts-en-us-001.5f88d86e.xlsx") == 1
    assert precinct_from_filename("ts-en-us-001.xlsx") == 1
    assert precinct_from_filename("cs-en-us-120pct.0a1b2c3d.xlsx") == 120
    assert precinct_from_filename("ts-en-us-city.5f88d86e.xlsx") is None

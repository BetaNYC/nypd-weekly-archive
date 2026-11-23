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
    parse_housing,
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


# ------------------------------------------------- CompStat, older layout
# tests/fixtures/compstat_1/ holds two older files, from the Internet Archive:
# citywide for the week ending 2015-08-30 and the 23rd Precinct for the week
# ending 2017-07-30. Both name their one sheet "CompStat_1", declare its size
# as "A1", list "Misd. Sex Crimes", and end with a different closing note.
# Every value checked below matches NYPD's PDF for the same week.


def test_compstat_1_sheet_declares_a1(fixtures: Path) -> None:
    """Why these files looked empty: read-only openpyxl trusts the "A1" size."""
    ws = openpyxl.load_workbook(fixtures / "compstat_1" / "cs-en-us-city.xlsx", read_only=True)["CompStat_1"]
    assert ws.calculate_dimension() == "A1:A1"
    assert [r for r in ws.iter_rows(values_only=True) if any(v is not None for v in r)] == []


def test_compstat_1_citywide(fixtures: Path) -> None:
    rows = parse_compstat(fixtures / "compstat_1" / "cs-en-us-city.xlsx")
    assert len(rows) == 175
    assert {r.sheet for r in rows} == {"CompStat_1"}
    assert {r.geography for r in rows} == {"Citywide"}
    assert {(r.week_start, r.week_end) for r in rows} == {(date(2015, 8, 24), date(2015, 8, 30))}
    assert pick(rows, measure="Murder", period="ytd", year=2015) == 222
    assert pick(rows, measure="Murder", period="ytd", year=2014) == 205
    assert pick(rows, measure="Misd. Sex Crimes", period="wtd", year=2015) == 76
    assert pick(rows, measure="TOTAL", period="calendar_year", year=1990) == 527257
    assert pick(rows, measure="Misd. Sex Crimes", period="ytd_pct_change_vs_1993", year=2015) is None
    # No "Traffic Statistics" section yet in this layout.
    assert {r.section for r in rows} == {"Crime Complaints", "Historical Perspective"}


def test_compstat_1_precinct(fixtures: Path) -> None:
    path = fixtures / "compstat_1" / "cs-en-us-023pct.xlsx"
    rows = parse_compstat(path)
    assert len(rows) == 175
    assert {(r.geography, r.precinct) for r in rows} == {("23rd Precinct", 23)}
    assert pick(rows, measure="Murder", period="ytd", year=2017) == 2
    assert pick(rows, measure="Misd. Sex Crimes", period="28d", year=2017) == 7
    assert pick(rows, measure="Murder", period="ytd_pct_change_vs_2010", year=2017) == 100.0
    assert week_end("compstat", path) == date(2017, 7, 30)


def test_compstat_1_unknown_sheet_name_fails(fixtures: Path, tmp_path: Path) -> None:
    dst = tmp_path / "cs-en-us-023pct.xlsx"
    shutil.copy(fixtures / "compstat_1" / "cs-en-us-023pct.xlsx", dst)
    wb = openpyxl.load_workbook(dst)
    wb["CompStat_1"].title = "CompStat_2"
    wb.save(dst)
    with pytest.raises(LayoutError, match="sheets"):
        parse_compstat(dst)


# ----------------------------------------------------------------- housing
# hb_eagle_report.xlsx is the week ending 2026-11-15 (sheets WTD, 28 Days, ...).
# hb_eagle_report-2015-08-30.xlsx is the earliest spreadsheet on disk: numbered
# sheet names (WTD_1 ... HA YTD_35) and a sheet size declared as "A1".
# Values below were read off the cells named in each comment.


def test_housing_current_layout(fixtures: Path) -> None:
    rows = parse_housing(fixtures / "hb_eagle_report.xlsx")
    assert len(rows) == 21546
    assert {(r.week_start, r.week_end) for r in rows} == {(date(2026, 11, 9), date(2026, 11, 15))}
    assert {r.series for r in rows} == {"housing"} and {r.unit for r in rows} == {"count"}
    assert {r.period for r in rows} == {"wtd", "28d", "ytd"} and {r.year for r in rows} == {2025, 2026}
    assert len({r.sheet for r in rows}) == 35
    # WTD!E5:P5, CAREY in PSA 1, precinct 060
    assert pick(rows, sheet="WTD", section="PSA 1", geography="CAREY", precinct=60, measure="Fel. Assault",
                period="wtd", year=2026) == 0
    assert pick(rows, sheet="WTD", section="PSA 1", geography="CAREY", precinct=60, measure="Fel. Assault",
                period="wtd", year=2025) == 1
    # WTD!AD15:AE15, the precinct total
    assert pick(rows, sheet="WTD", geography="HBB 060 PCT Totals", measure="Total Index", year=2026) == 5
    assert pick(rows, sheet="WTD", geography="HBB 060 PCT Totals", measure="Total Index", year=2025) == 2
    # The same label at two levels: D24 (the PSA 1 group) and C32 (PSA 1 with its Satellite)
    assert pick(rows, sheet="WTD", section="PSA 1", geography="PSA 1 Totals", measure="Total Index", year=2026) == 10
    assert pick(rows, sheet="WTD", section="", geography="PSA 1 Totals", measure="Total Index", year=2026) == 12
    assert pick(rows, sheet="WTD", geography="PSA 1 and PSB Totals", measure="Total Index", year=2026) == 13  # B40
    # YTD_5!E6:P6
    assert pick(rows, sheet="YTD_5", geography="CARVER", precinct=23, measure="Robbery", year=2026) == 3
    assert pick(rows, sheet="YTD_5", geography="CARVER", precinct=23, measure="Fel. Assault", year=2025) == 15
    # SI holds three periods: row 31 (28 days) and row 48 (year to date)
    assert pick(rows, sheet="SI", geography="PBSI 122 PCT Totals", measure="Total Index", period="28d",
                year=2025) == 4
    assert pick(rows, sheet="SI", geography="STATEN ISLAND", section="", measure="Fel. Assault", period="ytd",
                year=2025) == 84
    # Viper WTD!C12:N12, Viper 13 covers CAREY and SITE 1B
    assert pick(rows, sheet="Viper WTD", section="Viper 13", geography="SITE 1B", precinct=60,
                measure="Fel. Assault", year=2026) == 1
    # HA WTD rows 11 and 35
    assert pick(rows, sheet="HA WTD", section="PSA 3", geography="Total", measure="Robbery", year=2025) == 3
    assert pick(rows, sheet="HA WTD", geography="HA City Wide", measure="Total Index", year=2026) == 103
    assert pick(rows, sheet="HA WTD", geography="HA City Wide", measure="Total Index", year=2025) == 95


def test_housing_numbered_sheets_and_a1_size(fixtures: Path) -> None:
    path = fixtures / "hb_eagle_report-2015-08-30.xlsx"
    # The file declares each sheet's size as "A1": a plain read-only open sees one row.
    assert openpyxl.load_workbook(path, read_only=True)["WTD_1"].max_row == 1
    rows = parse_housing(path)
    assert len(rows) == 21600
    assert {(r.week_start, r.week_end) for r in rows} == {(date(2015, 8, 24), date(2015, 8, 30))}
    assert {r.year for r in rows} == {2014, 2015}
    # WTD_1!E6:M6 and row 15
    assert pick(rows, sheet="WTD_1", geography="CONEY ISLAND", precinct=60, measure="Robbery", year=2015) == 1
    assert pick(rows, sheet="WTD_1", geography="HBB 060 PCT Totals", measure="Fel. Assault", year=2014) == 1
    # RK_29!B12 (Rockaway, week to date) and HA YTD_35!A35
    assert pick(rows, sheet="RK_29", geography="ROCKAWAY", section="", measure="Fel. Assault", period="wtd",
                year=2015) == 2
    assert pick(rows, sheet="HA YTD_35", geography="HA City Wide", measure="Murder", year=2015) == 33
    assert pick(rows, sheet="HA YTD_35", geography="HA City Wide", measure="Robbery", year=2014) == 732
    assert week_end("housing", path) == date(2015, 8, 30)


def test_housing_week_end(fixtures: Path) -> None:
    from nypd_weekly_archive.parse import PARSERS

    assert PARSERS["housing"] is parse_housing
    assert week_end("housing", fixtures / "hb_eagle_report.xlsx") == date(2026, 11, 15)


@pytest.mark.parametrize(
    ("sheet", "coord", "value", "message"),
    [
        # A development's count changed: NYPD's precinct total no longer adds up.
        ("WTD", "AA6", 1, "its rows add to"),
        # ... and when the count is one of the seven, the row's own Total Index stops adding up first.
        ("WTD", "O6", 1, "Total Index"),
        # A CH % that does not match its counts (a shifted column).
        ("WTD", "Q9", 0.5, "CH %"),
        # A development row that lost its name.
        ("WTD", "E33", None, "unrecognized row"),
        # A number beyond the last column.
        ("WTD", "AG7", 3, "beyond the table"),
        # A precinct total under the wrong precinct.
        ("WTD", "E15", "HBB 061 PCT Totals", "closes precinct"),
        # A new crime column.
        ("WTD", "AA3", "Shooting Vic.", "header"),
        # A 28-day sheet whose dates span a week.
        ("28 Days", "A2", "November 9, 2026 through November 15, 2026", "do not fit the period"),
        # A changed HA row label.
        ("HA WTD", "A33", "HB Total", "row labels"),
        # A Viper row with no Viper number.
        ("Viper WTD", "D12", None, "unrecognized row"),
    ],
)
def test_housing_layout_change_fails_loud(fixtures, tmp_path, sheet, coord, value, message) -> None:
    broken = _edit(fixtures / "hb_eagle_report.xlsx", tmp_path, sheet, coord, value)
    with pytest.raises(LayoutError, match=message):
        parse_housing(broken)


def test_housing_renamed_sheet_fails_loud(fixtures: Path, tmp_path: Path) -> None:
    dst = tmp_path / "hb.xlsx"
    shutil.copy(fixtures / "hb_eagle_report.xlsx", dst)
    wb = openpyxl.load_workbook(dst)
    wb["WTD"].title = "WTD_1"
    wb.save(dst)
    with pytest.raises(LayoutError, match="sheets"):
        parse_housing(dst)


def test_housing_one_bad_count_fails_in_the_pipeline(fixtures: Path, tmp_path: Path) -> None:
    # One wrong development count breaks its CH %, its Total Index and every
    # total above it, so the pipeline treats it as a misread, not an anomaly.
    from nypd_weekly_archive.parse import parse_file

    broken = _edit(fixtures / "hb_eagle_report.xlsx", tmp_path, "WTD", "O6", 1)
    with pytest.raises(LayoutError, match="Change mismatches"):
        parse_file("housing", broken)

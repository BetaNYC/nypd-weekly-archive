"""Turn NYPD's weekly report-layout spreadsheets into tidy long rows.

Every layout rule below was read off real files downloaded 2026-11-21
(fixtures in tests/fixtures/). The parsers are strict on purpose: a row label,
section, sheet or header they do not recognize raises LayoutError instead of
being skipped, so a change in NYPD's layout stops the pipeline loudly rather
than silently dropping numbers.

Output columns (one row per number):
    series, source_file, geography, precinct, week_start, week_end, sheet,
    section, measure, period, year, value, unit

- ``measure``, ``section`` and ``geography`` are NYPD's labels verbatim,
  footnote markers (``*``, ``+``, ``#``) included.
- ``period`` is wtd (week to date), 28d, ytd, calendar_year, or for CompStat's
  long-range comparisons ytd_pct_change_vs_<base year>.
- ``year`` is the year the value belongs to, so the prior-year figure NYPD
  prints beside each number is its own row.
- NYPD's "Change"/"Diff" columns are checked (current - prior == change) and
  then dropped, and its short-range "% +/-" columns are dropped: both can be
  recomputed. CompStat's 2-, 16- and 33-year percent changes cannot, so they
  are kept with unit=percent.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import openpyxl

FIELDS = [
    "series",
    "source_file",
    "geography",
    "precinct",
    "week_start",
    "week_end",
    "sheet",
    "section",
    "measure",
    "period",
    "year",
    "value",
    "unit",
]


class LayoutError(ValueError):
    """The spreadsheet does not match the layout this parser was built from."""


@dataclass
class Row:
    series: str
    source_file: str
    geography: str
    precinct: int | None
    week_start: date | None
    week_end: date
    sheet: str
    section: str
    measure: str
    period: str
    year: int | None
    value: float | int | None
    unit: str

    def as_dict(self) -> dict[str, object]:
        return {k: ("" if getattr(self, k) is None else getattr(self, k)) for k in FIELDS}


# ---------------------------------------------------------------- helpers


def _cells(row: tuple[object, ...]) -> list[object]:
    out: list[object] = []
    for raw in row:
        if raw is None:
            continue
        v = raw.strip() if isinstance(raw, str) else raw
        if v == "":
            continue
        out.append(v)
    return out


def _norm(row: tuple[object, ...]) -> tuple[object, ...]:
    """Strip strings; blank strings become None. Column positions are kept."""
    out = []
    for v in row:
        out.append((v.strip() or None) if isinstance(v, str) else v)
    return tuple(out)


def _rows(ws) -> list[tuple[int, list[object]]]:
    """Non-empty rows as (row number, non-blank cells in order)."""
    return [(i, c) for i, r in enumerate(ws.iter_rows(values_only=True), 1) if (c := _cells(r))]


def _grid(ws) -> list[tuple[int, list[object], tuple[object, ...]]]:
    """Non-empty rows as (row number, non-blank cells, full row with positions)."""
    out = []
    for i, r in enumerate(ws.iter_rows(values_only=True), 1):
        full = _norm(r)
        cells = [v for v in full if v is not None]
        if cells:
            out.append((i, cells, full))
    return out


def _cols(full: tuple[object, ...], skip: int = 0) -> list[int]:
    """Column indices that hold a header value (from column `skip` on)."""
    return [i for i, v in enumerate(full) if v is not None and i >= skip]


def _take(full: tuple[object, ...], cols: list[int], label_col: int, where: str) -> list[object]:
    """Values at the header's column positions; a blank cell is None.

    NYPD leaves some cells blank (CompStat history for precincts that did not
    exist yet, an occasional blank count), so positions come from the header
    row, not from counting non-blank cells. A value outside those columns is a
    layout change.
    """
    stray = [i for i, v in enumerate(full) if v is not None and i != label_col and i not in cols]
    _expect(not stray, f"{where}: values outside the header's columns at {stray}: {full}")
    return [full[i] if i < len(full) else None for i in cols]


def to_count(v: object, where: str) -> int | None:
    return None if v is None else to_int(v, where)


def to_int(v: object, where: str) -> int:
    if isinstance(v, bool):
        raise LayoutError(f"{where}: expected a count, got {v!r}")
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"-?\d{1,3}(,\d{3})*|-?\d+", v):
        return int(v.replace(",", ""))
    raise LayoutError(f"{where}: expected a count, got {v!r}")


# Markers NYPD prints where a percent cannot be computed (seen 2026-11-21).
NULL_PCT = {"***", "***.*", "n/a", "/0"}


def to_pct(v: object, where: str) -> float | None:
    if v is None or (isinstance(v, str) and v in NULL_PCT):
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and re.fullmatch(r"-?[\d,]+(\.\d+)?%?", v):
        return float(v.rstrip("%").replace(",", ""))
    raise LayoutError(f"{where}: expected a percent, got {v!r}")


def _mdy(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise LayoutError(msg)


def precinct_from_filename(name: str) -> int | None:
    # A second file saved under the same name on the same day carries an 8-character
    # checksum suffix (ts-en-us-001.5f88d86e.xlsx); site_data.SERIES allows the same.
    m = re.search(r"-(\d{3})(?:pct)?(?:\.[0-9a-f]{8})?\.xlsx$", name)
    return int(m.group(1)) if m else None


def _year_header(cells: list[object], where: str, group: list[str], ngroups: int) -> int:
    """Validate a row like [2026, 2025, 'Change', '% +/-'] * ngroups; return the current year."""
    width = 2 + len(group)
    _expect(len(cells) == width * ngroups, f"{where}: year header has {len(cells)} cells: {cells}")
    cur = to_int(cells[0], where)
    _expect(1990 < cur < 2200, f"{where}: implausible year {cur}")
    for g in range(ngroups):
        chunk = cells[g * width : (g + 1) * width]
        _expect(
            to_int(chunk[0], where) == cur and to_int(chunk[1], where) == cur - 1,
            f"{where}: year header {chunk} does not match {cur}/{cur - 1}",
        )
        _expect([str(c) for c in chunk[2:]] == group, f"{where}: header labels {chunk[2:]} != {group}")
    return cur


def _counts_with_change(
    vals: list[object], periods: list[str], cur_year: int, where: str, notes: list[str] | None = None
) -> Iterator[tuple[str, int, int | None]]:
    """Groups of (current, prior, change, pct). Yields (period, year, value) and
    checks current - prior == change, which catches any column shift. A blank
    count is kept as None (empty in the CSV) and skips the check."""
    _expect(len(vals) == 4 * len(periods), f"{where}: {len(vals)} values, expected {4 * len(periods)}")
    for i, period in enumerate(periods):
        cur, prior, change, pct = vals[4 * i : 4 * i + 4]
        c, p, d = to_count(cur, where), to_count(prior, where), to_count(change, where)
        if c is not None and p is not None and d is not None and c - p != d:
            msg = f"{where} {period}: {c} - {p} != change {d}"
            if notes is None:
                raise LayoutError(msg)
            notes.append(msg)
        to_pct(pct, where)  # validated, then dropped
        yield period, cur_year, c
        yield period, cur_year - 1, p


def _load(path: Path):
    try:
        return openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a zoo of types on a bad file
        raise LayoutError(f"{path.name}: not a readable xlsx ({exc})") from exc


# ------------------------------------------------------------- TrafficStat

TS_PERIODS = ["wtd", "28d", "ytd"]
TS_MODES_1 = ["Motor Vehicle+", "Pedestrian", "Traditional Bicycle", "Motorized Two-Wheeler+"]
TS_MODES_2 = [
    "Car",
    "SUV",
    "Other MV",
    "Pedestrian",
    "Traditional Bicycle",
    "E-bike",
    "Stand-up Scooter",
    "Moped",
    "Motorcycle",
    "Off Road (Dirt Bike/ATV)",
    "Other Device",
]
TS_LABELS: dict[str, dict[str, set[str]]] = {
    "Page1": {
        "Collisions": {"Collisions", "Injury Collisions", "Total Injuries", *TS_MODES_1},
        "Fatalities": {"Total Fatalities", *TS_MODES_1},
        "Arrests": {"DWI", "VTL 511"},
        "Parking Summonses": {
            "Total Parkers*",
            "Double Parkers",
            "Bus Stop",
            "Bus Lane",
            "Crosswalk",
            "Bicycle Lane",
        },
        "Hazardous Summonses": {
            "Total Hazardous*",
            "Disobey Sign",
            "Cell Phone",
            "Texting",
            "Speeding",
            "Red Light",
            "Improper Turns",
            "Right of Way to Peds",
            "Safety Belts",
            "Tinted Window",
        },
        "Moving Summonses": {
            "Total Movers*",
            "Livery/Taxi",
            "Motorized Two-Wheeler+",
            "Traditional Bicycle",
        },
    },
    "Page2": {
        "Collisions": {"Collisions", "Injury Collisions", "Total Injuries", *TS_MODES_2},
        "Fatalities": {"Total Fatalities", *TS_MODES_2},
        "Moving Summonses": {"Total Movers*", "Livery/Taxi", *TS_MODES_2},
    },
}
TS_COVER = re.compile(r"Report Covering:\s+([A-Z][a-z]{2} \d{1,2}, \d{4})\s+-\s+([A-Z][a-z]{2} \d{1,2}, \d{4})")
TS_FOOTNOTE = "*Total Parkers, Total Hazardous and Total Movers include other violations"
# Printed under the table in precincts whose boundaries changed (105, 113,
# 116 on 2026-11-21): "The above statistics have been modified to reflect
# Precinct boundary changes due to the creation of the 116 Precinct in
# December 2024." Kept as a known note, matched by its fixed opening.
TS_BOUNDARY_NOTE = re.compile(r"The above statistics have been modified to reflect Precinct boundary changes\b.*")


def _ts_date(s: str) -> date:
    return datetime.strptime(s, "%b %d, %Y").date()


def parse_trafficstat(path: Path, notes: list[str] | None = None) -> list[Row]:
    wb = _load(path)
    _expect(wb.sheetnames == list(TS_LABELS), f"{path.name}: sheets {wb.sheetnames}")
    out: list[Row] = []
    for name in wb.sheetnames:
        rows = _grid(wb[name])
        where = f"{path.name}[{name}]"
        head = [c for _, c, _ in rows[:4]]
        _expect(
            len(head) == 4 and head[0] == ["NYC POLICE DEPT"] and head[2] == ["TrafficStat"],
            f"{where}: title block {head}",
        )
        _expect(len(head[1]) == 1, f"{where}: geography cell {head[1]}")
        geography = str(head[1][0])
        m = TS_COVER.fullmatch(str(head[3][0])) if len(head[3]) == 1 else None
        _expect(m is not None, f"{where}: date line {head[3]}")
        week_start, week_end = _ts_date(m.group(1)), _ts_date(m.group(2))
        labels = TS_LABELS[name]
        section: str | None = None
        cur_year: int | None = None
        cols: list[int] = []
        seen_footnote = False
        for rn, cells, full in rows[4:]:
            w = f"{where} row {rn}"
            if len(cells) == 1 and isinstance(cells[0], str) and cells[0] in labels:
                section = cells[0]
            elif [str(c) for c in cells] == ["Week to Date", "28 Day", "Year to Date"]:
                pass
            elif cells and cells[0] == week_end.year and full[0] is None:
                cur_year = _year_header(cells, w, ["Change", "% +/-"], 3)
                cols = _cols(full, skip=1)
            elif isinstance(full[0], str) and len(cells) > 1 and not seen_footnote:
                _expect(section is not None and cur_year is not None, f"{w}: data before headers")
                assert section is not None and cur_year is not None
                _expect(full[0] in labels[section], f"{w}: unknown measure {full[0]!r} in {section!r}")
                vals = _take(full, cols, 0, w)
                for period, year, value in _counts_with_change(vals, TS_PERIODS, cur_year, w, notes):
                    out.append(
                        Row("trafficstat", path.name, geography, precinct_from_filename(path.name),
                            week_start, week_end, name, section, full[0], period, year, value, "count")
                    )
            elif len(cells) == 1 and str(cells[0]).startswith(TS_FOOTNOTE):
                seen_footnote = True
            elif len(cells) == 1 and seen_footnote and TS_BOUNDARY_NOTE.fullmatch(str(cells[0])):
                pass
            elif len(cells) == 1 and isinstance(cells[0], datetime) and seen_footnote:
                pass  # report run date printed at the foot of the page
            else:
                raise LayoutError(f"{w}: unrecognized row {cells}")
        _expect(seen_footnote, f"{where}: footnote row missing")
    return out


# ---------------------------------------------------------------- CompStat

CS_CRIME = {
    "Murder",
    "Rape",
    "Robbery",
    "Fel. Assault",
    "Burglary",
    "Gr. Larceny",
    "G.L.A.",
    "TOTAL",
    "Transit",
    "Housing",
    "Petit Larceny",
    "Retail Theft",
    "Misd. Assault",
    "UCR Rape*",
    "Other Sex Crimes",
    "Shooting Vic.",
    "Shooting Inc.",
    "Hate Crimes",
}
CS_SECTIONS = {
    "Crime Complaints": CS_CRIME,
    "Traffic Statistics": {"Traffic Fatalities"},
}
CS_HIST = {"Murder", "Rape", "Robbery", "Fel. Assault", "Burglary", "Gr. Larceny", "G.L.A.", "TOTAL"}
CS_COVER = re.compile(r"Report Covering the Week\s+(\d{1,2}/\d{1,2}/\d{4})\s+Through\s+(\d{1,2}/\d{1,2}/\d{4})")
CS_LONG = re.compile(r"(\d+) Year \((\d{4})\)")
CS_TERMINAL = "Figures are preliminary and subject to further analysis and revision."


def parse_compstat(path: Path, notes: list[str] | None = None) -> list[Row]:  # noqa: ARG001
    wb = _load(path)
    _expect(wb.sheetnames == ["CompStat"], f"{path.name}: sheets {wb.sheetnames}")
    rows = _grid(wb["CompStat"])
    where = f"{path.name}[CompStat]"
    geography: str | None = None
    week_start = week_end = None
    i = 0
    # Title block: everything up to the "Report Covering" line. The Mayor's and
    # Commissioner's names change with administrations, so only the anchors
    # ("CompStat" title cell, date line) are checked.
    while i < len(rows):
        _, cells, _ = rows[i]
        i += 1
        if len(cells) == 3 and cells[1] == "CompStat" and str(cells[0]).startswith("Volume"):
            geography = str(cells[2])
        elif len(cells) == 1 and (m := CS_COVER.fullmatch(str(cells[0]))):
            week_start, week_end = _mdy(m.group(1)), _mdy(m.group(2))
            break
    _expect(geography is not None and week_end is not None, f"{where}: title block not found")
    assert week_end is not None and geography is not None
    precinct = precinct_from_filename(path.name)
    out: list[Row] = []

    def emit(section: str, measure: str, period: str, year: int | None, value, unit: str) -> None:
        out.append(Row("compstat", path.name, geography, precinct, week_start, week_end,
                       "CompStat", section, measure, period, year, value, unit))

    section: str | None = None
    cur_year: int | None = None
    long_bases: list[int] = []
    cols: list[int] = []
    hist_years: list[int] | None = None
    hist_cols: list[int] = []
    terminal = False
    for rn, cells, full in rows[i:]:
        w = f"{where} row {rn}"
        texts = [str(c) for c in cells]
        if len(cells) == 1 and texts[0] in CS_SECTIONS:
            section = texts[0]
        elif len(cells) == 1 and texts[0] == "Historical Perspective":
            section = "Historical Perspective"
        elif texts[:3] == ["Week to Date", "28 Day", "Year to Date*"] and len(texts) == 6:
            _expect(texts[3] == "2 Year", f"{w}: header {texts}")
            long_bases = []
            for t in texts[4:]:
                lm = CS_LONG.fullmatch(t)
                _expect(lm is not None, f"{w}: long-range header {t!r}")
                long_bases.append(int(lm.group(2)))
        elif len(cells) == 12 and cells[0] == week_end.year and section == "Crime Complaints":
            _expect(texts[2] == "% Chg" and texts[9:] == ["% Chg"] * 3, f"{w}: year header {texts}")
            cur_year = to_int(cells[0], w)
            for g in range(3):
                _expect(to_int(cells[3 * g], w) == cur_year and to_int(cells[3 * g + 1], w) == cur_year - 1,
                        f"{w}: year header {texts}")
            cols = _cols(full, skip=1)
        elif section in CS_SECTIONS and isinstance(full[0], str) and len(cells) > 1:
            _expect(cur_year is not None and len(long_bases) == 2, f"{w}: data before headers")
            assert cur_year is not None
            _expect(full[0] in CS_SECTIONS[section], f"{w}: unknown measure {full[0]!r} in {section!r}")
            vals = _take(full, cols, 0, w)
            for g, period in enumerate(["wtd", "28d", "ytd"]):
                emit(section, texts[0], period, cur_year, to_count(vals[3 * g], w), "count")
                emit(section, texts[0], period, cur_year - 1, to_count(vals[3 * g + 1], w), "count")
                to_pct(vals[3 * g + 2], w)
            for base, v in zip([cur_year - 2, *long_bases], vals[9:12], strict=True):
                emit(section, texts[0], f"ytd_pct_change_vs_{base}", cur_year, to_pct(v, w), "percent")
        elif section == "Historical Perspective":
            if texts == ["(Historical perspective is a complete calendar year of data.)"]:
                continue
            if all(t.strip() == "%Chg" for t in texts):
                continue
            if hist_years is None and len(cells) == 9 and all(re.fullmatch(r"\d{4}", t) for t in texts[:5]):
                hist_years = [int(t) for t in texts[:5]]
                _expect(all(re.fullmatch(r"'\d\d vs '\d\d", t) for t in texts[5:]), f"{w}: {texts}")
                hist_cols = _cols(full, skip=1)
                continue
            if texts[0] == CS_TERMINAL:
                terminal = True
                break
            _expect(hist_years is not None and isinstance(full[0], str), f"{w}: unrecognized row {cells}")
            assert hist_years is not None
            _expect(texts[0] in CS_HIST, f"{w}: unknown historical measure {texts[0]!r}")
            vals = _take(full, hist_cols, 0, w)
            for y, v in zip(hist_years, vals[:5], strict=True):
                emit(section, texts[0], "calendar_year", y, to_count(v, w), "count")
            for v in vals[5:]:
                to_pct(v, w)
        else:
            raise LayoutError(f"{w}: unrecognized row {cells}")
    _expect(terminal, f"{where}: terminal note {CS_TERMINAL!r} not found")
    _expect(hist_years is not None, f"{where}: historical perspective block missing")
    return out


# ------------------------------------------------------------------ Q-Team

QS_SECTIONS: dict[str, set[str]] = {
    "Calls For Service": {
        "Total 911 Calls",
        "Total 911 QOL Calls",
        "Total 311 Calls",
        "Total QOL Calls Housing*",
        "Total QOL Calls Transit*",
    },
    "Noise Calls": {
        "Total Noise",
        "Noise - Residential",
        "Noise - Outside",
        "Noise - Business Related",
        "Noise - Vehicle",
    },
    "Vehicle Related Calls": {
        "Total Vehicle Related*",
        "Illegal Parking#",
        "Blocked Driveway",
        "Double Parking",
        "Abandoned Vehicles",
        "Dangerous Driving*",
    },
    "Disorderly Behavior Calls": {
        "Total Disorderly*",
        "Drinking",
        "Urinating in Public",
        "Graffiti*",
        "Drug Use/Sale/OD*",
        "Disorderly Persons*",
        "Disorderly Groups*",
        "Persons in Crisis Outdoors",
    },
}
QS_COVER = re.compile(r"Report covering the week of\s+(\d{1,2}/\d{1,2}/\d{4})\s+through\s+(\d{1,2}/\d{1,2}/\d{4})")
QS_TITLE = re.compile(r"(.+?)\s+Quality of Life Stat")
QS_TERMINAL = "These figures represent preliminary counts"


def parse_qteam(path: Path, notes: list[str] | None = None) -> list[Row]:
    wb = _load(path)
    _expect(wb.sheetnames == ["QOLStat_PUBLIC_Report"], f"{path.name}: sheets {wb.sheetnames}")
    rows = _rows(wb["QOLStat_PUBLIC_Report"])
    where = f"{path.name}[QOLStat_PUBLIC_Report]"
    geography: str | None = None
    week_start = week_end = None
    section: str | None = None
    cur_year: int | None = None
    terminal = False
    out: list[Row] = []
    precinct = precinct_from_filename(path.name)
    for rn, cells in rows:
        w = f"{where} row {rn}"
        texts = [str(c) for c in cells]
        if week_end is None:
            if texts in (["Police Department"], ["City of New York"]):
                continue
            if len(cells) == 2 and texts[0].startswith("Volume") and (m := QS_TITLE.fullmatch(texts[1])):
                geography = m.group(1)
                continue
            if len(cells) == 1 and (m := QS_COVER.fullmatch(texts[0])):
                week_start, week_end = _mdy(m.group(1)), _mdy(m.group(2))
                continue
            raise LayoutError(f"{w}: unrecognized title row {cells}")
        if len(cells) == 1 and texts[0] in QS_SECTIONS:
            section = texts[0]
        elif texts == ["Week To Date", "28 Day", "Year To Date"]:
            pass
        elif len(cells) == 12 and cells[0] == week_end.year:
            cur_year = _year_header(cells, w, ["Diff", "% Chg"], 3)
        elif texts[0].startswith(QS_TERMINAL):
            terminal = True
            break
        elif len(cells) == 13 and section is not None and isinstance(cells[0], str):
            _expect(cur_year is not None and geography is not None, f"{w}: data before headers")
            assert cur_year is not None and geography is not None
            _expect(texts[0] in QS_SECTIONS[section], f"{w}: unknown measure {texts[0]!r} in {section!r}")
            for period, year, value in _counts_with_change(cells[1:], TS_PERIODS, cur_year, w, notes):
                out.append(Row("qteam", path.name, geography, precinct, week_start, week_end,
                               "QOLStat_PUBLIC_Report", section, texts[0], period, year, value, "count"))
        else:
            raise LayoutError(f"{w}: unrecognized row {cells}")
    _expect(week_end is not None, f"{where}: date line not found")
    _expect(terminal, f"{where}: closing note not found")
    return out


# -------------------------------------------------------------- Fatalities

FAT_GEO = re.compile(r"PB[A-Z]{2}|PAPD|Citywide")
FAT_THROUGH = re.compile(r"through (\d{1,2}/\d{1,2}/\d{4})")


def _fat_dated_sheet(wb, name: str) -> str:
    """The one sheet that ends in a 'through MM/DD/YYYY' line.

    Seen 2026-11-21: most files have one sheet ("Sheet1"; PBMS calls it
    "Table 1"), but traffic-fatalities-papd.xlsx also carries eight undated
    tabs of 2025 figures (PBMS, PBMN, PBBX, ...) beside its current "PAPD"
    tab and an empty "Sheet1". Only the current tab has the closing date line.
    More or fewer than one dated sheet is a layout change.
    """
    dated = []
    for sheet in wb.sheetnames:
        rows = _rows(wb[sheet])
        if rows and len(rows[-1][1]) == 1 and FAT_THROUGH.fullmatch(str(rows[-1][1][0])):
            dated.append(sheet)
    _expect(len(dated) == 1, f"{name}: expected one dated sheet, found {dated} among {wb.sheetnames}")
    return dated[0]


def parse_fatalities(path: Path, notes: list[str] | None = None) -> list[Row]:
    wb = _load(path)
    sheet = _fat_dated_sheet(wb, path.name)
    rows = _grid(wb[sheet])
    where = f"{path.name}[{sheet}]"
    _expect(len(rows) >= 4, f"{where}: too few rows")
    _expect([str(c) for c in rows[0][1]] == ["7 Day", "28 Day", "YTD"], f"{where}: header {rows[0][1]}")
    yh, yfull = rows[1][1], rows[1][2]
    # The table starts in column A in most files and in column B in others
    # (pbqn, pbqs, pbsi on 2026-11-21); the "PCT" header cell anchors it.
    _expect(yh[0] == "PCT", f"{where}: year header {yh}")
    label_col = yfull.index("PCT")
    cur_year = _year_header(yh[1:], f"{where} row {rows[1][0]}", ["CHANGE", "% +/-"], 3)
    cols = _cols(yfull, skip=label_col + 1)
    m = FAT_THROUGH.fullmatch(str(rows[-1][1][0]))
    assert m is not None  # guaranteed by _fat_dated_sheet
    week_end = _mdy(m.group(1))
    out: list[Row] = []
    seen: dict[tuple[object, ...], int] = {}
    for rn, _, full in rows[2:-1]:
        w = f"{where} row {rn}"
        geo = full[label_col] if label_col < len(full) else None
        _expect(geo is not None, f"{w}: row without a geography {full}")
        vals = _take(full, cols, label_col, w)
        if isinstance(geo, (int, float)) and not isinstance(geo, bool):
            precinct: int | None = to_int(geo, w)
            geography = str(precinct)
        else:
            _expect(isinstance(geo, str) and FAT_GEO.fullmatch(geo) is not None, f"{w}: unknown geography {geo!r}")
            precinct, geography = None, str(geo)
        # The PAPD file lists PAPD twice, as its own precinct and as its own
        # borough total, with the same numbers. Keep one; differing numbers
        # under one label would be a layout change.
        key = (geography, *vals)
        if key in seen:
            continue
        _expect(not any(k[0] == geography for k in seen), f"{w}: {geography} listed twice with different numbers")
        seen[key] = rn
        for period, year, value in _counts_with_change(vals, ["7d", "28d", "ytd"], cur_year, w, notes):
            out.append(Row("fatalities", path.name, geography, precinct, None, week_end, sheet,
                           "Traffic Fatalities", "Traffic Fatalities", period, year, value, "count"))
    return out


PARSERS = {
    "trafficstat": parse_trafficstat,
    "compstat": parse_compstat,
    "qteam": parse_qteam,
    "fatalities": parse_fatalities,
}


MAX_CHANGE_MISMATCHES = 1

HOUSING_WEEK = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4}) through ([A-Z][a-z]+ \d{1,2}, \d{4})")


def housing_week(path: Path) -> date:
    """Week end of the NYCHA Housing Bureau report (hb_eagle_report.xlsx), from its WTD sheet.

    Seen 2026-11-22: 35 sheets (WTD, 28 Days, YTD, WTD_2, ...); WTD's first rows read
    "WEEK TO DATE COMPARISON" and "November 9, 2026 through November 15, 2026". Its numbers
    are not read into data/ yet; the original is kept every week.
    """
    wb = _load(path)
    if "WTD" not in wb.sheetnames:
        raise LayoutError(f"{path.name}: no WTD sheet in {wb.sheetnames[:5]}")
    for _, cells in _rows(wb["WTD"])[:6]:
        for c in cells:
            m = HOUSING_WEEK.fullmatch(str(c).replace("\xa0", " ").strip())
            if m:
                start, end = (datetime.strptime(x, "%B %d, %Y").date() for x in m.groups())
                _expect((end - start).days == 6, f"{path.name}: week {start} to {end} is not 7 days")
                return end
    raise LayoutError(f"{path.name}: no 'through' date line on the WTD sheet")


# Series whose files are kept and dated, but not yet read into data/.
DATE_ONLY = {"housing": housing_week}


def parse_file(series: str, path: Path) -> tuple[list[Row], list[str]]:
    """Parse for the pipeline: returns (rows, anomalies).

    One row whose printed Change disagrees with its two counts is treated as
    an error in NYPD's file and recorded (seen 2026-11-21: the Citywide row of
    traffic-fatalities-pbsi.xlsx has a typed-in Change of -2 beside linked
    counts of 3 and 1). More than one is treated as a shifted column and
    fails. The counts are kept as published either way.
    """
    notes: list[str] = []
    rows = PARSERS[series](path, notes)
    if len(notes) > MAX_CHANGE_MISMATCHES:
        raise LayoutError(f"{path.name}: {len(notes)} Change mismatches (shifted columns?): {notes[:3]}")
    return rows, notes


def week_end(series: str, path: Path) -> date:
    """The report's week-ending date, read from the file itself."""
    if series in DATE_ONLY:
        return DATE_ONLY[series](path)
    rows, _ = parse_file(series, path)
    if not rows:
        raise LayoutError(f"{path.name}: parsed no rows")
    ends = {r.week_end for r in rows}
    if len(ends) != 1:
        raise LayoutError(f"{path.name}: more than one week-ending date {sorted(ends)}")
    return ends.pop()

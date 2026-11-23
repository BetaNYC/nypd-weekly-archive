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
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, time
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
    m = re.search(r"-(\d{3})(?:pct)?\.xlsx$", name)
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
# Collisions involving police vehicles ("RMP", radio motor patrol). Seen once,
# at the foot of both sheets in ts-en-us-013.xlsx for the week ending
# 2024-08-04 (an Internet Archive copy); accepted on either sheet.
TS_RMP = "RMP Collisions"
for _labels in TS_LABELS.values():
    _labels[TS_RMP] = {TS_RMP}
# Sheet names as NYPD wrote them. The Internet Archive copies for the weeks
# ending 2024-08-04 to 2024-09-29 number the sheets; copies from the week
# ending 2024-11-03 on do not. The sheets hold the same tables, and the name
# is kept as written in `sheet`.
TS_SHEET_NAMES = {
    ("Page1", "Page2"): ("Page1", "Page2"),
    ("Page1_1", "Page2_2"): ("Page1", "Page2"),
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
    kinds = TS_SHEET_NAMES.get(tuple(wb.sheetnames))
    _expect(kinds is not None, f"{path.name}: sheets {wb.sheetnames}")
    assert kinds is not None
    out: list[Row] = []
    for page, (name, kind) in enumerate(zip(wb.sheetnames, kinds, strict=True), 1):
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
        labels = TS_LABELS[kind]
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
            elif (seen_footnote and len(cells) == 3 and isinstance(cells[0], datetime)
                  and cells[1] == page and isinstance(cells[2], time)):
                pass  # 2024 files: run date, page number and run time at the foot
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
    "Misd. Sex Crimes",
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
CS_TERMINALS = {
    CS_TERMINAL,
    # Older files (sheet "CompStat_1", weeks up to August 2019) end their tables with this line.
    "All figures are subject to further analysis and revision. All degrees of rape are included in the rape category.",
}
# Older files (Internet Archive copies, weeks up to 2024) name the one sheet "CompStat_1".
CS_SHEET_NAMES = (["CompStat"], ["CompStat_1"])


def parse_compstat(path: Path, notes: list[str] | None = None) -> list[Row]:  # noqa: ARG001
    wb = _load(path)
    _expect(wb.sheetnames in CS_SHEET_NAMES, f"{path.name}: sheets {wb.sheetnames}")
    sheet = wb.sheetnames[0]
    ws = wb[sheet]
    # Most older files declare the sheet's size as "A1" while holding a full
    # table; in read-only mode openpyxl trusts that and sees nothing.
    ws.reset_dimensions()
    rows = _grid(ws)
    where = f"{path.name}[{sheet}]"
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
                       sheet, section, measure, period, year, value, unit))

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
            if texts[0] in CS_TERMINALS:
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
    _expect(terminal, f"{where}: terminal note not found")
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


# ----------------------------------------------------------------- Housing
#
# NYPD Housing Bureau weekly report (hb_eagle_report.xlsx): the seven major
# felonies, shooting incidents and the "Total Index" for each NYCHA
# development, by Police Service Area (PSA) and precinct. Read off all 44
# spreadsheets on disk on 2026-11-23 (weeks from August 2015 to November 2026):
# every one has the same 35 sheets in the same order and the same headers.
# Only the sheet names differ. Files up to May 2025 number every sheet
# (WTD_1, 28 Days_2, ... HA YTD_35); later ones drop most numbers (WTD,
# 28 Days, YTD, WTD_2, ... HA YTD). Both name lists are accepted exactly.
#
# Eighteen of the older files declare a sheet size of "A1" while holding a
# full table; openpyxl's read-only mode trusts that and sees one row, so the
# size is reset before reading (ws.reset_dimensions()).
#
# Three kinds of sheet:
# - Area sheets (PSA 1-9: one sheet per period; Staten Island and Rockaway,
#   "SI" and "RK": three periods stacked in one sheet). Columns C-E hold
#   PSA, PCT and Development; NYPD merges each PSA and PCT cell down over the
#   rows it covers, so only the first row holds the label and it is carried down.
#   Totals rows are labeled in the column of the level they total: a precinct
#   ("HBB 060 PCT Totals") in E, a PSA group ("PSA 1 Satellite Totals") in D,
#   several groups ("PSA 1 Totals") in C, the whole sheet ("PSA 1 and PSB
#   Totals", "STATEN ISLAND") in B.
# - Viper sheets: developments with VIPER camera coverage, with NYPD's Viper
#   number per development, and a "Week/Period/Year Totals" row.
# - HA sheets: one row per PSA and per Patrol Services Bureau housing unit,
#   then "HB Covered", "PSB Covered" and "HA City Wide".
#
# Reshaped: one number per row. sheet = NYPD's sheet name; geography = the
# row's own label (a development, or a totals label); precinct = the precinct
# in force for a development or precinct-totals row; section = the PSA group
# (column C, carried down) for rows inside a group, "Viper <n>" on Viper
# sheets (NYPD's "Viper" column), the opening "PSA n" row's label for the
# rows of that block on HA sheets, and empty for totals that span groups.
# The "CH %" columns are checked against the two counts, then dropped. The
# PSA column of the Viper sheets ("6", "SI") is checked and dropped: the
# precinct is kept, and the area sheets give each precinct's PSA.

HB_CRIMES = ["Murder", "Rape", "Robbery", "Fel. Assault", "Burglary", "Gr. Larceny", "GLA", "Shooting Inc.",
             "Total Index"]
_HB_P3 = ("WTD", "28 Days", "YTD")
HB_SHEETS_NUMBERED = [
    *[f"{k}_{3 * i + j + 1}" for i in range(9) for j, k in enumerate(_HB_P3)],
    "SI_28", "RK_29", "Viper WTD_30", "Viper 28 Days_31", "Viper YTD_32", "HA WTD_33", "HA 28 Days_34", "HA YTD_35",
]
HB_SHEETS = [
    *_HB_P3, *[f"{k}_{i}" for i in range(2, 10) for k in _HB_P3],
    "SI", "RK", "Viper WTD", "Viper 28 Days", "Viper YTD", "HA WTD", "HA 28 Days", "HA YTD",
]
HB_PERIODS = ["wtd", "28d", "ytd"]
HB_AREA_TITLES = {"WEEK TO DATE COMPARISON": "wtd", "28 DAY COMPSTAT PERIOD COMPARISON": "28d",
                  "YEAR TO DATE COMPARISON": "ytd"}
HB_VIPER_TITLES = {"WEEK TO DATE COMPSTAT PERIOD COMPARISON": "wtd", "28 DAY COMPSTAT PERIOD COMPARISON": "28d",
                   "YEAR TO DATE COMPSTAT PERIOD COMPARISON": "ytd"}
HB_VIPER_TOTALS = {"wtd": "Week Totals", "28d": "Period Totals", "ytd": "Year Totals"}
HB_HA_TITLES = {"Week to Date HB and PSB Breakdown": "wtd", "28 Day HB and PSB Breakdown": "28d",
                "Year to Date HB and PSB Breakdown": "ytd"}
# The HA sheets' rows, identical in all 44 files on 2026-11-23.
HB_HA_LABELS = [
    "PSA 1", "PBBS Housing (PSB)", "Total", "PSA 2 Total*",
    "PSA 3", "PBBN Housing (PSB)", "Total", "PSA 4", "PBMS Housing (PSB)", "Total",
    "PSA 5", "PBMN Housing (PSB)", "Total", "PSA 6", "PBMN Housing (PSB)", "PBMS Housing (PSB)**", "Total",
    "PSA 7", "PBBX Housing (PSB)", "Total", "PSA 8", "PBBX Housing (PSB)", "Total",
    "PSA 9", "PBQN Housing (PSB)", "Total",
    "Staten Island (PSB)", "Rockaway (PSB)", "HB Covered", "PSB Covered", "HA City Wide",
]
HB_TERMINAL = "INFORMATION IS PRELIMINARY AND SUBJECT TO FURTHER ANALYSIS AND CHANGE"
HB_DATES = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4}) through ([A-Z][a-z]+ \d{1,2}, \d{4})")
HB_PCT_TOTAL = re.compile(r"[A-Z]{3,4} (\d{3}) PCT Totals")
HB_PCT = re.compile(r"\d{3}")
HB_PCT_TOLERANCE = 1e-6


def _hb_grid(ws) -> list[tuple[int, list[object], list[object]]]:
    """Non-empty rows as (row number, non-blank cells, full row padded to 40 columns)."""
    ws.reset_dimensions()  # some files declare a size of "A1"; see above
    out = []
    for i, r in enumerate(ws.iter_rows(values_only=True), 1):
        full = list(_norm(r))
        cells = [v for v in full if v is not None]
        if cells:
            _expect(len(full) <= 40, f"row {i} is {len(full)} columns wide")
            out.append((i, cells, full + [None] * (40 - len(full))))
    return out


def _hb_dates(text: object, period: str, where: str) -> tuple[date, date]:
    m = HB_DATES.fullmatch(str(text))
    _expect(m is not None, f"{where}: date line {text!r}")
    assert m is not None
    start, end = (datetime.strptime(x, "%B %d, %Y").date() for x in m.groups())
    ok = {
        "wtd": (end - start).days == 6,
        "28d": (end - start).days == 27,
        "ytd": start == date(end.year, 1, 1),
    }[period]
    _expect(ok, f"{where}: {period} dates {start} to {end} do not fit the period")
    return start, end


def _hb_headers(full: list[object], first: int, label_cols: dict[int, str], where: str) -> None:
    """The crime-name row: labels at label_cols, crimes every third column from `first`."""
    want = {**label_cols, **{first + 3 * i: c for i, c in enumerate(HB_CRIMES)}}
    got = {i: v for i, v in enumerate(full) if v is not None}
    _expect(got == want, f"{where}: header {got}")


def _hb_years(full: list[object], first: int, where: str) -> int:
    """The year row: [cur, prior, 'CH %'] for each crime from column `first`; returns cur."""
    got = {i: v for i, v in enumerate(full) if v is not None}
    _expect(set(got) == set(range(first, first + 27)), f"{where}: year header columns {sorted(got)}")
    cur = to_int(got[first], where)
    for i in range(9):
        c = first + 3 * i
        _expect(
            to_int(got[c], where) == cur and to_int(got[c + 1], where) == cur - 1 and got[c + 2] == "CH %",
            f"{where}: year header {[got[c], got[c + 1], got[c + 2]]}",
        )
    return cur


def _hb_note(notes: list[str] | None, msg: str) -> None:
    """A number NYPD's file contradicts itself on: fatal in tests, counted by parse_file in the pipeline."""
    if notes is None:
        raise LayoutError(msg)
    notes.append(msg)


Sums = Counter[tuple[str, int]]


def _hb_sum_check(notes: list[str] | None, parts: Sums, total: Sums, where: str, what: str) -> None:
    """A totals row must equal the sum of the rows it totals, for every crime and year."""
    off = [k for k in total if parts.get(k, 0) != total[k]]
    if off:
        k = off[0]
        _hb_note(notes, f"{where}: {what} {k[0]} {k[1]} is {total[k]}, its rows add to {parts.get(k, 0)}")


def _hb_values(full: list[object], first: int, cur_year: int, where: str,
               notes: list[str] | None) -> Iterator[tuple[str, int, int]]:
    """(crime, year, count) for one data row; checks every "CH %" against its two counts."""
    stray = [i for i, v in enumerate(full) if v is not None and i >= first + 27]
    _expect(not stray, f"{where}: values beyond the table at columns {stray}")
    for k in (0, 1):  # "Total Index" is the seven major felonies, not shootings
        majors = sum(to_int(full[first + 3 * i + k], where) for i in range(7))
        if majors != to_int(full[first + 24 + k], where):
            _hb_note(notes, f"{where}: Total Index {full[first + 24 + k]!r} is not the sum of the seven, {majors}")
    for i, crime in enumerate(HB_CRIMES):
        c = first + 3 * i
        cur, prior = to_int(full[c], where), to_int(full[c + 1], where)
        _expect(cur >= 0 and prior >= 0, f"{where} {crime}: negative count")
        pct = to_pct(full[c + 2], where)
        if prior == 0:
            ok = pct is None
        else:
            ok = pct is not None and abs(pct - (cur - prior) / prior) < HB_PCT_TOLERANCE
        if not ok:
            _hb_note(notes, f"{where} {crime}: CH % {full[c + 2]!r} does not match {cur} vs {prior}")
        yield crime, cur_year, cur
        yield crime, cur_year - 1, prior


class _HbOut:
    """Collects rows for one file and refuses a (sheet, section, geography, precinct, period) seen twice."""

    def __init__(self, path: Path, week_end_: date, week_start: date, notes: list[str] | None) -> None:
        self.path, self.week_end, self.week_start, self.notes = path, week_end_, week_start, notes
        self.rows: list[Row] = []
        self.seen: set[tuple[object, ...]] = set()

    def emit(self, sheet: str, section: str, geography: object, precinct: int | None, period: str,
             full: list[object], first: int, cur_year: int, where: str) -> Sums:
        _expect(isinstance(geography, str), f"{where}: label {geography!r} is not text")
        key = (sheet, section, geography, precinct, period)
        _expect(key not in self.seen, f"{where}: {key} appears twice")
        self.seen.add(key)
        sums: Sums = Counter()
        for crime, year, value in _hb_values(full, first, cur_year, where, self.notes):
            self.rows.append(Row("housing", self.path.name, str(geography), precinct, self.week_start,
                                 self.week_end, sheet, section, crime, period, year, value,
                                 "count"))
            sums[(crime, year)] = value
        return sums


def _hb_week(ws, where: str) -> tuple[date, date]:
    rows = _hb_grid(ws)
    _expect(len(rows) >= 2 and rows[0][1] == ["WEEK TO DATE COMPARISON"] and len(rows[1][1]) == 1,
            f"{where}: title block {[c for _, c, _ in rows[:2]]}")
    return _hb_dates(rows[1][1][0], "wtd", where)


def _hb_area(ws, sheet: str, periods: list[str], out: _HbOut, cur_check: int) -> None:
    """An area sheet: one block per period in `periods`, in that order."""
    where0 = f"{out.path.name}[{sheet}]"
    single = len(periods) == 1
    block = -1
    period: str | None = None
    stage = "title"  # title -> dates -> header -> years -> body
    cur_year = 0
    group: str | None = None
    pct: int | None = None
    closed = terminal = False
    # Running sums, checked at each totals row: developments in the precinct,
    # precincts in the group, groups since the last column-C total, groups in the sheet.
    in_pct: Sums = Counter()
    in_grp: Sums = Counter()
    in_span: Sums = Counter()
    in_sheet: Sums = Counter()
    notes = out.notes
    for rn, cells, full in _hb_grid(ws):
        w = f"{where0} row {rn}"
        _expect(not terminal, f"{w}: row after the closing note")
        texts = [str(c) for c in cells]
        if texts == [HB_TERMINAL] and full[0] is not None:
            _expect(closed and block == len(periods) - 1, f"{w}: closing note before the table ends")
            terminal = True
            continue
        if stage == "title":
            block += 1
            closed = False
            _expect(block < len(periods), f"{w}: more blocks than the {len(periods)} expected")
            if single:  # title alone in A, date line alone in A on the next row
                _expect(len(cells) == 1 and full[0] is not None and texts[0] in HB_AREA_TITLES,
                        f"{w}: title {texts}")
                period = HB_AREA_TITLES[texts[0]]
                stage = "dates"
            else:  # title in L and date line in W, one row
                _expect(len(cells) == 2 and full[11] is not None and full[22] is not None
                        and texts[0] in HB_AREA_TITLES, f"{w}: title {texts}")
                period = HB_AREA_TITLES[texts[0]]
                _, end = _hb_dates(full[22], period, w)
                _expect(end == out.week_end, f"{w}: ends {end}, the report week ends {out.week_end}")
                stage = "header"
            _expect(period == periods[block], f"{w}: period {period}, expected {periods[block]}")
            continue
        assert period is not None
        if stage == "dates":
            _expect(len(cells) == 1 and full[0] is not None, f"{w}: date line {texts}")
            start, end = _hb_dates(full[0], period, w)
            _expect(end == out.week_end, f"{w}: ends {end}, the report week ends {out.week_end}")
            stage = "header"
        elif stage == "header":
            _hb_headers(full, 5, {2: "PSA", 3: "PCT", 4: "Development"}, w)
            stage = "years"
        elif stage == "years":
            _expect(all(v is None for v in full[:5]), f"{w}: year header {texts}")
            cur_year = _hb_years(full, 5, w)
            _expect(cur_year == cur_check, f"{w}: year {cur_year}, expected {cur_check}")
            stage = "body"
        else:
            labels = {j: full[j] for j in range(5) if full[j] is not None}
            cols = set(labels)
            lab = {j: str(v) for j, v in labels.items()}
            if cols == {2, 3, 4} and not lab[2].endswith(" Totals") and HB_PCT.fullmatch(lab[3]):
                _expect(group is None and pct is None, f"{w}: new PSA group before the last one closed")
                group, pct = lab[2], int(lab[3])
                in_pct.update(out.emit(sheet, group, labels[4], pct, period, full, 5, cur_year, w))
            elif cols == {3, 4} and HB_PCT.fullmatch(lab[3]):
                _expect(group is not None and pct is None, f"{w}: precinct outside a PSA group")
                assert group is not None
                pct = int(lab[3])
                in_pct.update(out.emit(sheet, group, labels[4], pct, period, full, 5, cur_year, w))
            elif cols == {4} and (m := HB_PCT_TOTAL.fullmatch(lab[4])):
                _expect(group is not None and pct == int(m.group(1)), f"{w}: {lab[4]!r} closes precinct {pct}")
                assert group is not None
                t = out.emit(sheet, group, labels[4], pct, period, full, 5, cur_year, w)
                _hb_sum_check(notes, in_pct, t, w, lab[4])
                in_grp.update(t)
                in_pct = Counter()
                pct = None
            elif cols == {4}:
                _expect(group is not None and pct is not None, f"{w}: development outside a precinct")
                assert group is not None
                in_pct.update(out.emit(sheet, group, labels[4], pct, period, full, 5, cur_year, w))
            elif cols == {3} and group is not None and lab[3] == f"{group} Totals":
                _expect(pct is None, f"{w}: {lab[3]!r} before precinct {pct} closed")
                t = out.emit(sheet, group, labels[3], None, period, full, 5, cur_year, w)
                _hb_sum_check(notes, in_grp, t, w, lab[3])
                in_span.update(t)
                in_sheet.update(t)
                in_grp = Counter()
                group = None
            elif cols == {2} and lab[2].endswith(" Totals") and group is None and pct is None:
                _hb_sum_check(notes, in_span, out.emit(sheet, "", labels[2], None, period, full, 5, cur_year, w),
                              w, lab[2])
                in_span = Counter()
            elif cols == {1} and pct is None and (
                (single and group is None and lab[1].endswith(" Totals"))
                or (not single and group is not None and lab[1] == group)
            ):
                _expect(not closed, f"{w}: a second sheet total")
                t = out.emit(sheet, "", labels[1], None, period, full, 5, cur_year, w)
                # Staten Island and Rockaway have one group and no column-D total.
                _hb_sum_check(notes, in_sheet if single else in_grp, t, w, lab[1])
                in_grp, in_span, in_sheet = Counter(), Counter(), Counter()
                group, closed = None, True
                if not single:
                    stage = "title"
            else:
                raise LayoutError(f"{w}: unrecognized row {cells}")
    _expect(terminal, f"{where0}: closing note not found")


def _hb_viper(ws, sheet: str, period: str, out: _HbOut, cur_check: int) -> None:
    where0 = f"{out.path.name}[{sheet}]"
    rows = _hb_grid(ws)
    _expect(len(rows) >= 5, f"{where0}: too few rows")
    (r1, c1, f1), (r2, c2, f2), (r3, _, f3), (r4, _, f4) = rows[:4]
    _expect(len(c1) == 2 and f1[11] == "VIPER INDEX CRIME STATISTICS" and f1[21] is not None,
            f"{where0} row {r1}: title {c1}")
    _expect(len(c2) == 1 and HB_VIPER_TITLES.get(str(f2[11])) == period, f"{where0} row {r2}: title {c2}")
    _, end = _hb_dates(f1[21], period, f"{where0} row {r1}")
    _expect(end == out.week_end, f"{where0}: ends {end}, the report week ends {out.week_end}")
    _hb_headers(f3, 4, {0: "PSA", 1: "PCT", 2: "Development", 3: "Viper"}, f"{where0} row {r3}")
    _expect(all(v is None for v in f4[:4]), f"{where0} row {r4}: year header")
    cur_year = _hb_years(f4, 4, f"{where0} row {r4}")
    _expect(cur_year == cur_check, f"{where0}: year {cur_year}, expected {cur_check}")
    psa: str | None = None
    pct: int | None = None
    total = terminal = False
    devs: Sums = Counter()
    for rn, cells, full in rows[4:]:
        w = f"{where0} row {rn}"
        _expect(not terminal, f"{w}: row after the closing note")
        labels = {j: str(full[j]) for j in range(4) if full[j] is not None}
        if [str(c) for c in cells] == [HB_TERMINAL] and full[0] is not None:
            _expect(total, f"{w}: closing note before the totals row")
            terminal = True
        elif set(labels) == {0} and labels[0] == HB_VIPER_TOTALS[period] and not total:
            _hb_sum_check(out.notes, devs, out.emit(sheet, "", labels[0], None, period, full, 4, cur_year, w),
                          w, labels[0])
            total = True
        elif not total and {2, 3} <= set(labels) <= {0, 1, 2, 3} and re.fullmatch(r"\d+", labels[3]):
            if 0 in labels:
                _expect(1 in labels and re.fullmatch(r"\d|SI", labels[0]) is not None, f"{w}: PSA {labels[0]!r}")
                psa = labels[0]
            if 1 in labels:
                _expect(HB_PCT.fullmatch(labels[1]) is not None, f"{w}: precinct {labels[1]!r}")
                pct = int(labels[1])
            _expect(psa is not None and pct is not None, f"{w}: development before its PSA and precinct")
            devs.update(out.emit(sheet, f"Viper {labels[3]}", full[2], pct, period, full, 4, cur_year, w))
        else:
            raise LayoutError(f"{w}: unrecognized row {cells}")
    _expect(terminal, f"{where0}: closing note not found")


def _hb_ha(ws, sheet: str, period: str, out: _HbOut, cur_check: int) -> None:
    where0 = f"{out.path.name}[{sheet}]"
    rows = _hb_grid(ws)
    _expect(len(rows) == 4 + len(HB_HA_LABELS) + 1, f"{where0}: {len(rows)} rows")
    (r1, c1, f1), (r2, c2, f2), (r3, _, f3), (r4, _, f4) = rows[:4]
    _expect(len(c1) == 2 and f1[10] == "Housing Authority" and f1[19] is not None, f"{where0} row {r1}: {c1}")
    _expect(len(c2) == 1 and HB_HA_TITLES.get(str(f2[10])) == period, f"{where0} row {r2}: title {c2}")
    _, end = _hb_dates(f1[19], period, f"{where0} row {r1}")
    _expect(end == out.week_end, f"{where0}: ends {end}, the report week ends {out.week_end}")
    _hb_headers(f3, 1, {0: "PSA"}, f"{where0} row {r3}")
    _expect(f4[0] is None, f"{where0} row {r4}: year header")
    cur_year = _hb_years(f4, 1, f"{where0} row {r4}")
    _expect(cur_year == cur_check, f"{where0}: year {cur_year}, expected {cur_check}")
    labels = [str(full[0]) for _, _, full in rows[4:-1]]
    _expect(labels == HB_HA_LABELS, f"{where0}: row labels {labels}")
    _expect(rows[-1][1] == [HB_TERMINAL], f"{where0}: closing note {rows[-1][1]}")
    # Section: the "PSA n" row that opens a block, for that block's rows through its
    # "Total"; empty for rows outside a block. Sums checked, as NYPD's own totals:
    # "Total" = its block; "HB Covered" = the PSA rows; "PSB Covered" = the (PSB)
    # rows; "HA City Wide" = both.
    section = ""
    block: Sums = Counter()
    hb: Sums = Counter()
    psb: Sums = Counter()
    for rn, _, full in rows[4:-1]:
        w = f"{where0} row {rn}"
        label = str(full[0])
        if re.fullmatch(r"PSA \d", label):
            section = label
        t = out.emit(sheet, section, label, None, period, full, 1, cur_year, w)
        if label == "Total":
            _hb_sum_check(out.notes, block, t, w, f"{section} {label}")
            block, section = Counter(), ""
        elif label in ("HB Covered", "PSB Covered"):
            _hb_sum_check(out.notes, hb if label == "HB Covered" else psb, t, w, label)
        elif label == "HA City Wide":
            _hb_sum_check(out.notes, hb + psb, t, w, label)
        else:
            if section:
                block.update(t)
            (hb if label.startswith("PSA") else psb).update(t)


def parse_housing(path: Path, notes: list[str] | None = None) -> list[Row]:
    """NYPD Housing Bureau weekly report; see the notes above HB_CRIMES."""
    wb = _load(path)
    names = list(wb.sheetnames)
    _expect(names in (HB_SHEETS, HB_SHEETS_NUMBERED), f"{path.name}: sheets {names}")
    week_start, week_end_ = _hb_week(wb[names[0]], f"{path.name}[{names[0]}]")
    out = _HbOut(path, week_end_, week_start, notes)
    cur = week_end_.year
    for i, sheet in enumerate(names):
        ws = wb[sheet]
        if i < 27:
            _hb_area(ws, sheet, [HB_PERIODS[i % 3]], out, cur)
        elif i < 29:
            _hb_area(ws, sheet, HB_PERIODS, out, cur)
        elif i < 32:
            _hb_viper(ws, sheet, HB_PERIODS[i - 29], out, cur)
        else:
            _hb_ha(ws, sheet, HB_PERIODS[i - 32], out, cur)
    return out.rows


PARSERS = {
    "trafficstat": parse_trafficstat,
    "compstat": parse_compstat,
    "qteam": parse_qteam,
    "fatalities": parse_fatalities,
    "housing": parse_housing,
}


MAX_CHANGE_MISMATCHES = 1


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
    rows, _ = parse_file(series, path)
    if not rows:
        raise LayoutError(f"{path.name}: parsed no rows")
    ends = {r.week_end for r in rows}
    if len(ends) != 1:
        raise LayoutError(f"{path.name}: more than one week-ending date {sorted(ends)}")
    return ends.pop()

"""Write docs/data/<series>-coverage.json: which report weeks of each precinct's file this
archive holds, one file per series, for the coverage charts on the GitHub Pages site.

Run from the repo root: uv run nypd-archive site

Each precinct-week gets one state, the best copy we hold:
  3  numbers in CSV: rows in data/<series>/, or, for CompStat, in the numbers we took from
     Tim McDermott's and Josh Greenman's archives (data/compstat_mcdermott/,
     data/compstat_greenman/)
  2  spreadsheet in an older NYPD layout, saved but not read yet
  1  PDF only
  0  nothing saved

Spreadsheets carry their own report week, and so do CompStat PDFs (their cover says it). A PDF
saved from nyc.gov takes the week of the spreadsheet beside it. A file from the Internet
Archive that cannot be dated by its contents has only the date it was saved, so its week is
estimated: the last Sunday at least two days before that date. Estimated weeks are flagged in
the output, and "estimate_check" says how often the rule gives the right week for the files
that carry both dates.

Traffic fatality files cover a patrol borough each, not a precinct, but list each precinct's
deaths inside; that series is charted from the precinct rows in data/fatalities/ only.

Three reports are one file a week for the whole city (SINGLES): citywide CompStat, the
Department of Correction's CompStat, and the NYCHA Housing Bureau report. Each gets a file in
the same shape, with a single row.
"""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path("docs") / "data"


@dataclass(frozen=True)
class Series:
    key: str
    unit: re.Pattern[str] | None  # precinct file name -> (precinct, kind); None: chart from data rows only
    read_label: str
    other_data: tuple[str, ...] = ()  # data/<dir>/<year>.csv.gz from other people's archives


# An optional 8-character suffix marks a second file saved under the same name on the same day.
# CompStat files were named cs001pct.pdf until about 2016, cs-en-us-001pct.pdf after.
SERIES = {
    "trafficstat": Series("trafficstat", re.compile(r"ts-en-us-(\d{3})(?:\.[0-9a-f]{8})?\.(xlsx|pdf)$"),
                          "Spreadsheet, read into CSV"),
    "compstat": Series("compstat", re.compile(r"cs(?:-en-us-)?(\d{3})pct(?:\.[0-9a-f]{8})?\.(xlsx|pdf)$"),
                       "Numbers in CSV", ("compstat_mcdermott", "compstat_greenman")),
    "fatalities": Series("fatalities", None, "Numbers in CSV"),
    "qteam": Series("qteam", re.compile(r"qs-en-us-(\d{3})pct(?:\.[0-9a-f]{8})?\.(xlsx|pdf)$"),
                    "Spreadsheet, read into CSV"),
}


def out_path(series: str) -> Path:
    return DATA_DIR / f"{series}-coverage.json"


OUT = out_path("trafficstat")

BOROUGHS = [  # NYPD precinct numbers by borough
    ("Manhattan", range(1, 35)),
    ("Bronx", range(40, 53)),
    ("Brooklyn", range(60, 95)),
    ("Queens", range(100, 117)),
    ("Staten Island", range(120, 124)),
]
NAMES = {14: "Midtown South Precinct", 18: "Midtown North Precinct", 22: "Central Park Precinct"}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def borough(n: int) -> str | None:
    return next((b for b, r in BOROUGHS if n in r), None)


def sunday_on_or_before(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def estimated_week(ts: str) -> dt.date:
    saved = dt.date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
    return sunday_on_or_before(saved - dt.timedelta(days=2))


def _precinct(value: str) -> str | None:
    value = value.strip()
    if not value.isdigit() or borough(int(value)) is None:
        return None
    return f"{int(value):03d}"


def _read_rows(rows, read: dict[str, set[str]]) -> None:
    """rows: csv.reader over a file whose header comes first."""
    header = next(rows, None)
    if not header:
        return
    ip, iw = header.index("precinct"), header.index("week_end")
    for r in rows:
        pct = _precinct(r[ip])
        if pct:
            read[r[iw]].add(pct)


def read_weeks(root: Path, s: Series) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """(ours, others): week_end -> precincts with numbers in CSV."""
    ours: dict[str, set[str]] = defaultdict(set)
    for f in sorted((root / "data" / s.key).glob("*.csv")):
        with f.open(newline="") as fh:
            _read_rows(csv.reader(fh), ours)
    others: dict[str, set[str]] = defaultdict(set)
    for d in s.other_data:
        for f in sorted((root / "data" / d).glob("*.csv.gz")):
            with gzip.open(f, "rt", encoding="utf-8", newline="") as fh:
                _read_rows(csv.reader(fh), others)
    return ours, others


def _mark_capture(r: dict, kind: str, mark) -> None:
    """Give one saved file its week and state: 2 for a spreadsheet (3 once its rows are in
    data/, marked separately), 1 for a PDF. A file dated only by when it was saved gets an
    estimated week."""
    ds, ts = r["date_source"], r["wayback_timestamp"]
    if kind == "xlsx" and ds == "xlsx":
        mark(r["week_end"], 2)
    elif kind == "xlsx" and ds == "fetch_date_layout_error" and ts:
        mark(estimated_week(ts).isoformat(), 2, True)
    elif kind == "pdf" and ds in ("sibling_xlsx", "pdf"):
        mark(r["week_end"], 1)
    elif kind == "pdf" and ds == "wayback_capture_date" and ts:
        mark(estimated_week(ts).isoformat(), 1, True)


def build_series(root: Path, s: Series, captures: list[dict], manifest: list[dict]) -> dict | None:
    ours, others = read_weeks(root, s)
    best: dict[tuple[str, str], int] = {}
    estimated: set[tuple[str, str]] = set()
    sources: Counter = Counter()
    last_saved = ""
    checked = right = 0

    def mark(pct: str, week: str, state: int, est: bool = False) -> None:
        key = (pct, week)
        if state > best.get(key, 0):
            best[key] = state
            if est:
                estimated.add(key)
            else:
                estimated.discard(key)

    for week, pcts in list(ours.items()) + list(others.items()):
        for pct in pcts:
            mark(pct, week, 3)

    for r in captures:
        if r["series"] != s.key:
            continue
        sources[r["source"]] += 1
        last_saved = max(last_saved, r["fetched_at"])
        ds, ts = r["date_source"], r["wayback_timestamp"]
        if ts and ds in ("xlsx", "pdf"):
            checked += 1
            right += estimated_week(ts).isoformat() == r["week_end"]
        m = s.unit.search(r["path"]) if s.unit else None
        if not m:
            continue  # citywide and patrol borough files
        pct, kind = m[1], m[2]
        if _precinct(pct) is None:
            continue
        _mark_capture(r, kind, lambda week, state, est=False, pct=pct: mark(pct, week, state, est))

    if not best:
        return None
    pcts = sorted({p for p, _ in best}, key=int)
    first = min(dt.date.fromisoformat(w) for _, w in best)
    last = max(dt.date.fromisoformat(w) for _, w in best)
    weeks = []
    d = sunday_on_or_before(first)
    while d <= last:
        weeks.append(d.isoformat())
        d += dt.timedelta(days=7)

    pages = {}  # precinct -> NYPD precinct page that links its file
    for r in manifest:
        m = s.unit.search(r["url"]) if s.unit else None
        if r["series"] == s.key and m and r["kind"] == "xlsx":
            pages[m[1]] = {"page": r["found_on"], "xlsx": r["url"]}

    precincts = []
    for p in pcts:
        n = int(p)
        precincts.append({
            "id": p,
            "name": NAMES.get(n, f"{ordinal(n)} Precinct"),
            "borough": borough(n),
            "states": "".join(str(best.get((p, w), 0)) for w in weeks),
            "estimated": "".join("1" if (p, w) in estimated else "0" for w in weeks),
            "nypd_page": pages.get(p, {}).get("page", ""),
            "nypd_file": pages.get(p, {}).get("xlsx", ""),
        })

    cells = Counter(c for x in precincts for c in x["states"])
    ours_cells = {(p, w) for w, ps in ours.items() for p in ps}
    totals = {
        "precinct_weeks": len(weeks) * len(precincts),
        "read": cells["3"],
        "older_layout": cells["2"],
        "pdf_only": cells["1"],
        "none": cells["0"],
        "files_from_internet_archive": sources["wayback"],
        "files_from_nyc_gov": sources["nyc.gov"],
    }
    if s.other_data:
        totals["read_from_other_archives_only"] = sum(
            1 for w, ps in others.items() for p in ps if (p, w) not in ours_cells)
    other_files = {k: v for k, v in sorted(sources.items()) if k not in ("wayback", "nyc.gov")}
    if other_files:
        totals["files_from_other_archives"] = other_files

    return {
        "series": s.key,
        "last_file_saved": last_saved,
        "first_week": weeks[0],
        "last_week": weeks[-1],
        "weeks": weeks,
        "precincts": precincts,
        "totals": totals,
        "estimate_check": {"files_with_both_dates": checked, "estimate_right": right},
        "states": {
            "3": s.read_label,
            "2": "Spreadsheet in an older NYPD layout (saved, not read yet)",
            "1": "PDF only",
            "0": "Nothing saved",
        },
    }


@dataclass(frozen=True)
class Single:
    """A report that is one file a week for the whole city, charted as a single row."""
    key: str
    series: str  # the series name in captures.csv and data/
    file: re.Pattern[str]  # file name -> kind
    name: str  # the row's label, short enough for the chart's label column
    covers: str  # shown in the table's second column
    read_label: str
    older_label: str = "Spreadsheet in an older NYPD layout (saved, not read yet)"


SINGLES = {
    "citywide": Single("citywide", "compstat", re.compile(r"cs(?:-en-us-)?city(?:\.[0-9a-f]{8})?\.(xlsx|pdf)$", re.I),
                       "Citywide", "Whole city", "Numbers in CSV"),
    "correction": Single("correction", "compstat",
                         re.compile(r"cs(?:-en-us-)?correction(?:\.[0-9a-f]{8})?\.(xlsx|pdf)$", re.I),
                         "Correction", "Department of Correction", "Numbers in CSV"),
    "housing": Single("housing", "housing", re.compile(r"hb_eagle_report(?:\.[0-9a-f]{8})?\.(xlsx|pdf)$", re.I),
                      "NYCHA", "Public-housing developments", "Numbers in CSV",
                      "Spreadsheet saved, not read into CSV yet"),
}


def build_single(root: Path, s: Single, captures: list[dict], manifest: list[dict]) -> dict | None:
    """The same shape as build_series, with one row for the report's file."""
    best: dict[str, int] = {}
    estimated: set[str] = set()
    sources: Counter = Counter()
    last_saved = ""
    checked = right = 0

    def mark(week: str, state: int, est: bool = False) -> None:
        if state > best.get(week, 0):
            best[week] = state
            if est:
                estimated.add(week)
            else:
                estimated.discard(week)

    read: set[str] = set()
    for f in sorted((root / "data" / s.series).glob("*.csv")):
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if s.file.search(r["source_file"].rsplit("/", 1)[-1]):
                    read.add(r["week_end"])
    for week in read:
        mark(week, 3)

    for r in captures:
        m = s.file.search(r["path"].rsplit("/", 1)[-1]) if r["series"] == s.series else None
        if not m:
            continue
        sources[r["source"]] += 1
        last_saved = max(last_saved, r["fetched_at"])
        ts = r["wayback_timestamp"]
        if ts and r["date_source"] in ("xlsx", "pdf"):
            checked += 1
            right += estimated_week(ts).isoformat() == r["week_end"]
        _mark_capture(r, m[1].lower(), mark)

    if not best:
        return None
    first = min(dt.date.fromisoformat(w) for w in best)
    last = max(dt.date.fromisoformat(w) for w in best)
    weeks = []
    d = sunday_on_or_before(first)
    while d <= last:
        weeks.append(d.isoformat())
        d += dt.timedelta(days=7)

    page = next(({"page": r["found_on"], "xlsx": r["url"]} for r in manifest
                 if r["series"] == s.series and r["kind"] == "xlsx" and s.file.search(r["url"])), {})
    row = {
        "id": s.key,
        "name": s.name,
        "borough": s.covers,
        "states": "".join(str(best.get(w, 0)) for w in weeks),
        "estimated": "".join("1" if w in estimated else "0" for w in weeks),
        "nypd_page": page.get("page", ""),
        "nypd_file": page.get("xlsx", ""),
    }
    cells = Counter(row["states"])
    totals = {
        "precinct_weeks": len(weeks),
        "read": cells["3"],
        "older_layout": cells["2"],
        "pdf_only": cells["1"],
        "none": cells["0"],
        "files_from_internet_archive": sources["wayback"],
        "files_from_nyc_gov": sources["nyc.gov"],
    }
    other_files = {k: v for k, v in sorted(sources.items()) if k not in ("wayback", "nyc.gov")}
    if other_files:
        totals["files_from_other_archives"] = other_files
    return {
        "series": s.key,
        "last_file_saved": last_saved,
        "first_week": weeks[0],
        "last_week": weeks[-1],
        "weeks": weeks,
        "precincts": [row],
        "totals": totals,
        "estimate_check": {"files_with_both_dates": checked, "estimate_right": right},
        "states": {"3": s.read_label, "2": s.older_label, "1": "PDF only", "0": "Nothing saved"},
    }


def build_site_data(root: Path) -> dict[str, dict]:
    """Write one coverage file per series that has anything to chart. Returns them by series."""
    with (root / "captures.csv").open(newline="") as fh:
        captures = list(csv.DictReader(fh))
    with (root / "manifest.csv").open(newline="") as fh:
        manifest = list(csv.DictReader(fh))
    built = {}
    for key, s in SERIES.items():
        out = build_series(root, s, captures, manifest)
        if out is None:
            continue
        path = root / out_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, separators=(",", ":")) + "\n")
        built[key] = out
    for key, single in SINGLES.items():
        out = build_single(root, single, captures, manifest)
        if out is None:
            continue
        path = root / out_path(key)
        path.write_text(json.dumps(out, separators=(",", ":")) + "\n")
        built[key] = out
    return built

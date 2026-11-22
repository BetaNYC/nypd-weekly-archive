"""Write docs/data/trafficstat-coverage.json: which report weeks of each precinct's
TrafficStat file this archive holds, for the coverage chart on the GitHub Pages site.

Run from the repo root: uv run nypd-archive site

Each precinct-week gets one state, the best copy we hold:
  3  spreadsheet, read into data/trafficstat/
  2  spreadsheet in NYPD's older 2024 layout, saved but not read yet
  1  PDF only
  0  nothing saved

Spreadsheets carry their own report week. A PDF saved from nyc.gov takes the week of the
spreadsheet beside it. A PDF from the Internet Archive has only the date it was saved, so its
week is estimated: the last Sunday at least two days before that date. That rule comes from
the archive's spreadsheets, which carry both dates: nearly all were saved 2 to 8 days after
their report week ended. Estimated weeks are flagged in the output.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

OUT = Path("docs") / "data" / "trafficstat-coverage.json"
UNIT = re.compile(r"ts-en-us-(\d{3})\.(xlsx|pdf)$")

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


def borough(n: int) -> str:
    return next(b for b, r in BOROUGHS if n in r)


def sunday_on_or_before(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=(d.weekday() + 1) % 7)


def estimated_week(ts: str) -> dt.date:
    saved = dt.date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
    return sunday_on_or_before(saved - dt.timedelta(days=2))


def build_site_data(root: Path) -> dict:
    read = defaultdict(set)  # week_end -> precincts with rows in data/trafficstat/
    for f in sorted((root / "data" / "trafficstat").glob("*.csv")):
        with f.open(newline="") as fh:
            for r in csv.DictReader(fh):
                if r["precinct"]:
                    read[r["week_end"]].add(f"{int(r['precinct']):03d}")

    best: dict[tuple[str, str], int] = {}
    estimated: set[tuple[str, str]] = set()
    sources: Counter = Counter()
    last_saved = ""

    def mark(pct: str, week: str, state: int, est: bool = False) -> None:
        key = (pct, week)
        if state > best.get(key, 0):
            best[key] = state
            if est:
                estimated.add(key)
            else:
                estimated.discard(key)

    with (root / "captures.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["series"] != "trafficstat":
                continue
            sources[r["source"]] += 1
            last_saved = max(last_saved, r["fetched_at"])
            m = UNIT.search(r["path"])
            if not m:
                continue  # citywide and patrol borough files
            pct, kind = m[1], m[2]
            ds = r["date_source"]
            if kind == "xlsx" and ds == "xlsx":
                mark(pct, r["week_end"], 3 if pct in read[r["week_end"]] else 2)
            elif kind == "xlsx" and ds == "fetch_date_layout_error" and r["wayback_timestamp"]:
                mark(pct, estimated_week(r["wayback_timestamp"]).isoformat(), 2, est=True)
            elif kind == "pdf" and ds == "sibling_xlsx":
                mark(pct, r["week_end"], 1)
            elif kind == "pdf" and ds == "wayback_capture_date" and r["wayback_timestamp"]:
                mark(pct, estimated_week(r["wayback_timestamp"]).isoformat(), 1, est=True)

    pcts = sorted({p for p, _ in best})
    first = min(dt.date.fromisoformat(w) for _, w in best)
    last = max(dt.date.fromisoformat(w) for (_, w), s in best.items() if s == 3)
    weeks = []
    d = sunday_on_or_before(first)
    while d <= last:
        weeks.append(d.isoformat())
        d += dt.timedelta(days=7)

    pages = {}  # precinct -> NYPD precinct page that links its TrafficStat file
    with (root / "manifest.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            m = UNIT.search(r["url"])
            if r["series"] == "trafficstat" and m and r["kind"] == "xlsx":
                pages[m[1]] = {"page": r["found_on"], "xlsx": r["url"]}

    precincts = []
    for p in pcts:
        n = int(p)
        states = "".join(str(best.get((p, w), 0)) for w in weeks)
        est = "".join("1" if (p, w) in estimated else "0" for w in weeks)
        precincts.append({
            "id": p,
            "name": NAMES.get(n, f"{ordinal(n)} Precinct"),
            "borough": borough(n),
            "states": states,
            "estimated": est,
            "nypd_page": pages.get(p, {}).get("page", ""),
            "nypd_file": pages.get(p, {}).get("xlsx", ""),
        })

    cells = Counter(c for x in precincts for c in x["states"])
    out = {
        "series": "trafficstat",
        "last_file_saved": last_saved,
        "first_week": weeks[0],
        "last_week": weeks[-1],
        "weeks": weeks,
        "precincts": precincts,
        "totals": {
            "precinct_weeks": len(weeks) * len(precincts),
            "read": cells["3"],
            "older_layout": cells["2"],
            "pdf_only": cells["1"],
            "none": cells["0"],
            "files_from_internet_archive": sources["wayback"],
            "files_from_nyc_gov": sources["nyc.gov"],
        },
        "states": {
            "3": "Spreadsheet, read",
            "2": "Spreadsheet, older layout (saved, not read yet)",
            "1": "PDF only",
            "0": "Nothing saved",
        },
    }
    path = root / OUT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, separators=(",", ":")) + "\n")
    return out

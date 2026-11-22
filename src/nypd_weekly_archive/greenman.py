"""Import Josh Greenman's parsed CompStat weeks (https://github.com/joshgreenman1973/nypd-compstat-scraper).

His scraper reads NYPD's weekly CompStat spreadsheets and keeps the parsed
numbers as data/archive/<week end>.json (from 2026-03-01), not the
spreadsheets themselves. Josh Greenman gave BetaNYC permission to use this
data with a link to his repo (relayed by Noel Hidalgo, 2026-11-22).

His numbers are kept apart from ours: data/compstat_greenman/<year>.csv.gz,
one gzip-compressed CSV per year (see yearfiles.py), same columns as
data/compstat/. source_file is the file's path in his repo
(greenman/data/archive/<week>.json); README.md in that folder names the
commit, so every row traces to one file at one commit. Nothing here is read
from NYPD's own files.

A geography whose report week differs from the citywide one is dropped and
logged: after NYPD split Patrol Borough Bronx (May 2026) the retired Bronx
workbook stayed online with its last week, and his files repeat it.

What is converted, per geography:
- seven_major_felonies and additional_stats -> section "Crime Complaints"
  (Traffic Fatalities -> "Traffic Statistics", as in NYPD's sheet);
  total_seven_major -> measure "TOTAL".
- week_to_date / twenty_eight_day / year_to_date -> period wtd / 28d / ytd,
  current_year and prior_year -> the report's year and the year before.
- historical 2_yr_pct / 14_yr_pct / 31_yr_pct -> ytd_pct_change_vs_<base year>.
  His labels are fixed strings; for 2026 reports they hold NYPD's columns
  "2 Year (2024)", "16 Year (2010)" and "33 Year (1993)" (checked against our
  parse of the week ending 2026-11-15). Base years are listed per report
  year in BASE_YEARS; a week from any other year stops the import.
- pct_change on wtd/28d/ytd is skipped: NYPD computes it from the counts.
"""

from __future__ import annotations

import csv
import json
import re
import subprocess
import tempfile
from pathlib import Path

from .log import log
from .parse import FIELDS, _mdy
from .yearfiles import read_year_files, write_year_files

REPO = "joshgreenman1973/nypd-compstat-scraper"
OUT = Path("data") / "compstat_greenman"
PERIODS = {"week_to_date": "wtd", "twenty_eight_day": "28d", "year_to_date": "ytd"}
HIST = ["2_yr_pct", "14_yr_pct", "31_yr_pct"]
BASE_YEARS = {2026: (2024, 2010, 1993)}
PCT = re.compile(r"(\d+)(?:st|nd|rd|th) Precinct")


README = """# CompStat weeks from Josh Greenman's scraper

These {weeks} weeks ({first} to {last}) are Josh Greenman's parsed
CompStat numbers, from [{repo}](https://github.com/{repo}),
used with his permission. Thank you, Josh.

- One gzip-compressed CSV per year, `<year>.csv.gz`, weeks in order. Read it
  with `pandas.read_csv("2026.csv.gz")` or `gzip -dc 2026.csv.gz`.

- Taken from commit [`{commit}`](https://github.com/{repo}/tree/{commit}).
- `source_file` on each row is the file's path in that repo, under `greenman/`:
  `greenman/data/archive/2026-03-01.json` is
  `https://github.com/{repo}/blob/{commit}/data/archive/2026-03-01.json`.
- Same columns as `data/compstat/`, but these numbers were not read from
  NYPD's files by this repo. Where both exist, `data/compstat/` is ours.
- Made by `uv run nypd-archive import-greenman --clone <clone of his repo>`.
  See `src/nypd_weekly_archive/greenman.py` for how his fields map to ours.
"""


class GreenmanError(RuntimeError):
    pass


def _git(clone: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True, text=True).stdout.strip()


def _num(v: object) -> float | int | str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v  # type: ignore[return-value]


def rows_for_week(data: dict, source_file: str) -> tuple[list[dict[str, object]], list[str]]:
    """Rows for one of his weekly files, and the geographies dropped as stale."""
    out: list[dict[str, object]] = []
    stale: list[str] = []
    city_end = _mdy(data["citywide"]["report_period"]["week_end"].strip())
    for key, g in data.items():
        rp = g.get("report_period") or {}
        start, end = _mdy(rp["week_start"].strip()), _mdy(rp["week_end"].strip())
        if end != city_end:
            stale.append(key)
            continue
        year = end.year
        if year not in BASE_YEARS:
            raise GreenmanError(f"{source_file}: no base years known for {year}; check NYPD's column headers")
        geography = "Citywide" if key == "citywide" else key
        m = PCT.fullmatch(key)
        base = {"series": "compstat", "source_file": source_file, "geography": geography,
                "precinct": int(m.group(1)) if m else "", "week_start": start.isoformat(),
                "week_end": end.isoformat(), "sheet": "CompStat"}

        def emit(section: str, measure: str, block: dict, base: dict = base, year: int = year) -> None:
            for name, period in PERIODS.items():
                p = block.get(name) or {}
                for which, yr in (("current_year", year), ("prior_year", year - 1)):
                    if which in p:
                        out.append({**base, "section": section, "measure": measure, "period": period,
                                    "year": yr, "value": _num(p[which]), "unit": "count"})
            hist = block.get("historical") or {}
            for label, by in zip(HIST, BASE_YEARS[year], strict=True):
                if label in hist:
                    out.append({**base, "section": section, "measure": measure,
                                "period": f"ytd_pct_change_vs_{by}", "year": year,
                                "value": _num(hist[label]), "unit": "percent"})

        for measure, block in (g.get("seven_major_felonies") or {}).items():
            emit("Crime Complaints", measure, block)
        if g.get("total_seven_major"):
            emit("Crime Complaints", "TOTAL", g["total_seven_major"])
        for measure, block in (g.get("additional_stats") or {}).items():
            emit("Traffic Statistics" if measure == "Traffic Fatalities" else "Crime Complaints", measure, block)
    out.sort(key=lambda r: (str(r["geography"]), str(r["section"]), str(r["measure"]), str(r["period"]),
                            -int(r["year"])))
    return out, stale


def import_greenman(repo: Path, clone: Path) -> int:
    try:
        origin = _git(clone, "remote", "get-url", "origin")
        commit = _git(clone, "rev-parse", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise GreenmanError(f"{clone}: not a git clone ({exc})") from exc
    if REPO not in origin:
        raise GreenmanError(f"{clone}: origin {origin!r} is not {REPO}")
    out_dir = repo / OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted((clone / "data" / "archive").glob("*.json"))
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp_dir:
        weeks: dict[str, Path] = {}
        for f in files:
            rows, stale = rows_for_week(json.loads(f.read_text()), f"greenman/data/archive/{f.name}")
            if stale:
                log("greenman_stale_dropped", file=f.name, geographies=stale)
            wk = str(rows[0]["week_end"])
            if wk != f.stem:
                raise GreenmanError(f"{f.name}: citywide week ends {wk}, not {f.stem}")
            weeks[wk] = Path(tmp_dir) / f"{wk}.csv"
            with weeks[wk].open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
        years = write_year_files(out_dir, weeks)
    (out_dir / "README.md").write_text(README.format(repo=REPO, commit=commit, weeks=len(weeks),
                                                     first=files[0].stem, last=files[-1].stem))
    log("greenman_done", commit=commit, weeks=len(weeks), years=years, folder=str(OUT))
    return 0


def compare(repo: Path) -> list[dict[str, str]]:
    """Rows where Josh's numbers and ours differ, for weeks both have."""
    diffs: list[dict[str, str]] = []

    def key(r: dict[str, str]) -> tuple[str, ...]:
        return (r["geography"], r["section"], r["measure"], r["period"], r["year"])

    for wk, rows in sorted(read_year_files(repo / OUT).items()):
        ours = repo / "data" / "compstat" / f"{wk}.csv"
        if not ours.exists():
            continue
        with ours.open(newline="") as fh:
            a = {key(r): r["value"] for r in csv.DictReader(fh)}
        b = {key(r): r["value"] for r in rows}
        for k in sorted(a.keys() & b.keys()):
            x, y = a[k], b[k]
            same = x == y or (x and y and abs(float(x) - float(y)) < 1e-6)
            if not same:
                diffs.append({"week": wk, "key": "|".join(k), "ours": x, "greenman": y})
        log("greenman_compared", week=wk, both=len(a.keys() & b.keys()), only_ours=len(a.keys() - b.keys()),
            only_greenman=len(b.keys() - a.keys()), differ=sum(d["week"] == wk for d in diffs))
    return diffs

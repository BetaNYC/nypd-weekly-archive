"""Import Tim McDermott's weekly CompStat CSVs (2015 to February 2026).

Tim McDermott, a BetaNYC volunteer, turned NYPD's weekly CompStat reports
into one CSV per week (vol<VV><WW>.csv, NYPD's volume and week numbers) and
published them on data.beta.nyc. When BetaNYC retired data.beta.nyc in
February 2024, datHere re-hosted the files on data.dathere.com and put a
copy of the site's files in a Google Drive folder. Tim kept uploading to
data.dathere.com into 2026.

Sources, in the order given on the command line (the first copy of a week
wins):
1. data.dathere.com (the most complete, and the one Tim kept correcting);
2. the Drive copy (BetaNYC's copy lives in Working Group Documents >
   data-tool-preservation > "data.beta.nyc archive (via datHere, 2024)");
3. two weeks Tim sent by email in 2023.
The raw files are not in this repo; each row's source_file names the copy
and file it came from.

Each file has the same 20 columns:
Start_Date, End_Date, Crime, Current, Prior, Ratio, 28Day_Current,
28Day_Prior, 28Day_Ratio, YTD_Current, YTD_Prior, YTD_Ratio, then three
long-range percent changes, Volume_Number, Number_number, Mayor,
Commissioner, Precinct (which also holds patrol borough names).

What is converted (written to data/compstat_mcdermott/<year>.csv.gz, one
gzip-compressed CSV per year, see yearfiles.py; same columns as
data/compstat/):
- Current/Prior, 28Day_*, YTD_* -> wtd, 28d, ytd counts for the report year
  and the year before. The report year is 1993 + the volume number (NYPD's
  CompStat volumes started in 1993; volume 22 is 2015, volume 33 is 2026).
- The three long-range changes -> ytd_pct_change_vs_<year>. The first is
  always two years back. From volume 24 (2017) on, Tim's headers name 2010
  and 1993 for the other two, as NYPD's current sheets do. For 2015 and
  2016, NYPD's own PDFs label those columns inconsistently (for example
  "5Year" one week and "6Year" another), so they are kept as
  ytd_pct_change_mid and ytd_pct_change_long, without a year.
- Ratio columns are skipped: NYPD computes them from the counts.

Repairs, each logged:
- In 2022 NYPD added rows (UCR Rape*, Other Sex Crimes, Hate Crimes). The
  Drive copies of 2022 and 2023 weeks still carry the old labels on the new
  rows. In all 56 weeks where both copies exist, relabelling the Drive rows
  as RELABEL says reproduces Tim's corrected data.dathere.com files exactly
  (71,400 numbers, checked 2026-11-22). Weeks that exist only on Drive are
  relabelled the same way; their Shooting Inc. and Hate Crimes rows were
  never captured. These rows get sheet "CompStat (relabelled by BetaNYC)".
- Two June 2015 weeks name each place only on its first (Murder) row; the
  name is carried down to the rows below it.
- Rows without 20 fields, without a crime, or without a place are dropped.
- When two copies of a week exist, the one with more usable rows wins; on a
  tie, the source listed first.
  "NA" and NYPD's "***.*" become blanks.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

from .log import log
from .parse import FIELDS, _mdy
from .yearfiles import write_year_files

OUT = Path("data") / "compstat_mcdermott"
README = """# CompStat weeks from Tim McDermott

These {weeks} weeks ({first} to {last}) are NYPD's weekly CompStat
numbers as Tim McDermott, a BetaNYC volunteer, turned NYPD's weekly reports
into one CSV per week for many years and published them on data.beta.nyc.
Thank you, Tim.

- One gzip-compressed CSV per year, `<year>.csv.gz`, weeks in order. Read it
  with `pandas.read_csv("2020.csv.gz")` or `gzip -dc 2020.csv.gz`.

- After BetaNYC retired data.beta.nyc in 2024, datHere re-hosted the files
  on https://data.dathere.com/organization/betanyc and kept a copy of the
  old site in Google Drive. BetaNYC's copy of that Drive folder is in the
  shared drive Working Group Documents > data-tool-preservation.
- `source_file` names the copy each week came from: `dathere/...` or
  `databeta-drive/...`, then the dataset folder and Tim's file name.
- Same columns as `data/compstat/`, but these numbers were not read from
  NYPD's files by this repo. Where both exist, `data/compstat/` is ours.
- Rows with `sheet` "CompStat (relabelled by BetaNYC)" were moved to the
  crime name NYPD used that year; see `src/nypd_weekly_archive/mcdermott.py`.
- Tim's files list precincts and patrol boroughs, not citywide totals. His
  weeks before July 2015 have no TOTAL row.
- Made by `uv run nypd-archive import-mcdermott --source dathere=<folder>
  --source databeta-drive=<folder>`.
"""
VOL = re.compile(r"vol(\d{2})(\d{2})\.csv", re.I)
PCT = re.compile(r"(\d+)(?:st|nd|rd|th) Precinct")
NAMED = {"Midtown South Precinct": 14, "Midtown Precinct South": 14, "Midtown North Precinct": 18,
         "Midtown Precinct North": 18, "Central Park Precinct": 22}
BOROUGHS = {"Manhattan South", "Manhattan North", "Bronx", "Bronx North", "Bronx South", "Brooklyn South",
            "Brooklyn North", "Queens South", "Queens North", "Staten Island"}
RELABEL = {"Misd. Sex Crimes": "UCR Rape*", "Shooting Vic.": "Other Sex Crimes", "Shooting Inc.": "Shooting Vic."}
NEW_ROWS_FROM = 29  # volume 29 = 2022
COUNTS = [("wtd", 3, 4), ("28d", 6, 7), ("ytd", 9, 10)]


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")  # Tim's 2016-2019 files: the apostrophe in "O'Neill"


class BadValue(ValueError):
    pass


def _num(s: str) -> int | float | str:
    s = s.strip().replace(",", "")  # some files write 1,058
    if not s or s.startswith("***") or s.lower() in {"n/a", "na", "/0"}:
        return ""
    try:
        v = float(s)
    except ValueError as exc:
        raise BadValue(s) from exc
    return int(v) if v.is_integer() and "." not in s else v


def crime_name(s: str) -> str:
    s = s.strip()
    return s[:-1] if s.endswith("..") or s == "Other Sex Crimes." else s


def place(s: str) -> tuple[str, int | str] | None:
    s = s.strip()
    if not s or s == "Precinct":
        return None
    if m := PCT.fullmatch(s):
        return s, int(m.group(1))
    if s in NAMED:
        return s, NAMED[s]
    if s in BOROUGHS:
        return s, ""
    return None  # e.g. a Commissioner's name in a shifted row


def rows_for_file(data: bytes, source_file: str, vol: int) -> tuple[list[dict[str, object]], Counter]:
    """Rows for one weekly file, and counts of what was dropped or repaired."""
    notes: Counter = Counter()
    raw = list(csv.reader(io.StringIO(decode(data))))
    body = [[c.strip() for c in r] for r in raw[1:] if r and not r[0].strip().lower().startswith("start")]
    good = []
    last_place = ""
    for r in body:
        if len(r) != 20:
            notes["dropped_short_or_long_row"] += 1
            continue
        if not r[19] and r[2] and last_place and crime_name(r[2]) != "Murder":
            # Two June 2015 weeks name each place on its first (Murder) row only.
            r[19] = last_place
            notes["place_filled_from_row_above"] += 1
        if not r[2] or place(r[19]) is None:
            notes["dropped_no_crime_or_place"] += 1
            if crime_name(r[2]) == "Murder":
                last_place = ""  # an unnamed block: its rows must not inherit the block above
            continue
        last_place = r[19]
        good.append(r)
    if not good:
        notes["no_usable_rows"] += 1
        return [], notes
    # NYPD's volume, printed in the file ("22", "022" or "22.00"), sets the report
    # year. It beats the file name: Tim named the week of 12/28/2015 to 1/3/2016
    # vol2253, but NYPD printed volume 23, and its year-to-date counts are 2016's.
    printed = Counter(int(float(r[15])) for r in good if r[15])
    if printed:
        if printed.most_common(1)[0][0] != vol:
            notes["year_from_printed_volume_not_file_name"] += 1
        vol = printed.most_common(1)[0][0]
    year = 1993 + vol
    old_layout = vol >= NEW_ROWS_FROM and not any(crime_name(r[2]) == "UCR Rape*" for r in good)
    out: list[dict[str, object]] = []
    for r in good:
        try:
            [_num(r[i]) for i in (3, 4, 6, 7, 9, 10, 12, 13, 14)]
        except BadValue:
            notes["dropped_bad_number"] += 1
            continue
        geography, precinct = place(r[19])  # type: ignore[misc]
        start, end = _mdy(r[0]), _mdy(r[1])
        measure, sheet = crime_name(r[2]), "CompStat"
        if old_layout and measure in RELABEL:
            measure, sheet = RELABEL[measure], "CompStat (relabelled by BetaNYC)"
            notes["relabelled_row"] += 1
        base = {"series": "compstat", "source_file": source_file, "geography": geography, "precinct": precinct,
                "week_start": start.isoformat(), "week_end": end.isoformat(), "sheet": sheet,
                "section": "Crime Complaints", "measure": measure}
        for period, cur, prior in COUNTS:
            out.append({**base, "period": period, "year": year, "value": _num(r[cur]), "unit": "count"})
            out.append({**base, "period": period, "year": year - 1, "value": _num(r[prior]), "unit": "count"})
        long = [f"vs_{year - 2}", "vs_2010", "vs_1993"] if vol >= 24 else [f"vs_{year - 2}", "mid", "long"]
        for label, col in zip(long, (12, 13, 14), strict=True):
            out.append({**base, "period": f"ytd_pct_change_{label}", "year": year, "value": _num(r[col]),
                        "unit": "percent"})
    out.sort(key=lambda r: (str(r["geography"]), str(r["measure"]), str(r["period"]), -int(r["year"])))
    return out, notes


def week_end_of(rows: list[dict[str, object]]) -> date:
    ends = Counter(str(r["week_end"]) for r in rows)
    return date.fromisoformat(ends.most_common(1)[0][0])


def import_mcdermott(repo: Path, sources: list[tuple[str, Path]]) -> dict[str, int]:
    """sources: (label, folder) pairs, best first. Each folder is searched for vol*.csv."""
    out_dir = repo / OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp_dir:
        stats = _convert(sources, Path(tmp_dir))
        weeks = {p.stem: p for p in Path(tmp_dir).glob("*.csv")}
        years = write_year_files(out_dir, weeks)
    (out_dir / "README.md").write_text(README.format(weeks=len(weeks), first=min(weeks), last=max(weeks)))
    log("mcdermott_done", years=years, **stats)
    return stats


def _convert(sources: list[tuple[str, Path]], out_dir: Path) -> dict[str, int]:
    """One CSV per week in out_dir, the best copy of each week."""
    seen: dict[str, str] = {}
    seen_rows: dict[str, int] = {}
    stats: Counter = Counter()
    for label, folder in sources:
        for f in sorted(folder.rglob("*.csv")):
            if not (m := VOL.fullmatch(f.name)):
                continue
            rel = f"{label}/{f.relative_to(folder).as_posix()}"
            rows, notes = rows_for_file(f.read_bytes(), rel, int(m.group(1)))
            for k, v in notes.items():
                stats[k] += v
            if not rows:
                log("mcdermott_skipped", file=rel, notes=dict(notes))
                continue
            wk = week_end_of(rows).isoformat()
            if wk in seen and seen_rows[wk] >= len(rows):
                stats["duplicate_week_skipped"] += 1
                continue
            if wk in seen:
                log("mcdermott_better_copy", week_end=wk, replaced=seen[wk], by=rel)
                stats["weeks_from_" + seen[wk].split("/")[0]] -= 1
            seen[wk], seen_rows[wk] = rel, len(rows)
            if notes:
                log("mcdermott_repaired", file=rel, week_end=wk, notes=dict(notes))
            tmp = out_dir / f"{wk}.csv.tmp"
            with tmp.open("w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
            tmp.replace(out_dir / f"{wk}.csv")
            stats[f"weeks_from_{label}"] += 1
    stats["weeks"] = len(seen)
    return dict(stats)

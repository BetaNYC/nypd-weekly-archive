"""Join weekly CSVs into one gzip-compressed CSV per year.

Used for numbers we took from other people's archives (data/compstat_greenman/,
data/compstat_mcdermott/), where one file per week made the repo large: Tim
McDermott's 531 weeks are 957 MB as weekly CSVs and about 33 MB this way.

Each year file has the same columns as data/<series>/<week>.csv, weeks in
order, each week's rows in the order its weekly file had them. A week belongs
to the year its week_end falls in. The gzip header carries no file name and
no timestamp (mtime=0), so rebuilding the same rows gives the same bytes and
git sees no change.

Read one with any CSV reader that handles gzip, for example
pandas.read_csv("2020.csv.gz"), or `gzip -dc 2020.csv.gz`.
"""

from __future__ import annotations

import csv
import gzip
import io
from collections import defaultdict
from pathlib import Path

from .parse import FIELDS


def write_year_files(out_dir: Path, weeks: dict[str, Path]) -> dict[str, int]:
    """weeks: week_end (YYYY-MM-DD) -> weekly CSV. Returns rows written per year."""
    by_year: dict[str, list[str]] = defaultdict(list)
    for wk in sorted(weeks):
        by_year[wk[:4]].append(wk)
    counts: dict[str, int] = {}
    for year, wks in sorted(by_year.items()):
        tmp = out_dir / f"{year}.csv.gz.tmp"
        n = 0
        with tmp.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz, \
                io.TextIOWrapper(gz, encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            for wk in wks:
                with weeks[wk].open(newline="") as src:
                    for row in csv.DictReader(src):
                        w.writerow(row)
                        n += 1
        tmp.replace(out_dir / f"{year}.csv.gz")
        counts[year] = n
    return counts


def read_year_files(out_dir: Path) -> dict[str, list[dict[str, str]]]:
    """week_end -> rows, from every <year>.csv.gz in out_dir."""
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for f in sorted(out_dir.glob("*.csv.gz")):
        with gzip.open(f, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                out[row["week_end"]].append(row)
    return dict(out)

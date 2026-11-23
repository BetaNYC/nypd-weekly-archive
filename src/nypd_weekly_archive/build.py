"""Rebuild data/<series>/<week_end>.csv from the archived xlsx files.

The tidy CSVs are partitioned by week rather than kept as one file per
series: a single file would be rewritten every week and pass GitHub's
100 MB per-file limit within about a year (TrafficStat alone is ~36,000
rows a week). Concatenate the weekly files to get the full series.

Rows of NYPD's files that disagree with themselves but did not stop the
parse are listed in data/anomalies.csv.

A week is rebuilt only when all of its original files are on disk. In CI,
raw/ holds just the files captured in that run (the rest live in GitHub
Releases), so weeks whose files are absent keep their existing CSV, and
their lines in anomalies.csv are carried over.

Only the latest capture of each (series, file, week) is used, so a mid-week
revision by NYPD replaces the earlier numbers rather than duplicating them.
The earlier file stays in raw/ and in captures.csv.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from .capture import _stem, read_ledger
from .log import log
from .parse import FIELDS, PARSERS, LayoutError, Row, parse_file


def latest_xlsx(ledger: list[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    """(series, week_end) -> the last ledger row for each file in that week."""
    last: dict[tuple[str, str, str], dict[str, str]] = {}
    for r in ledger:
        if r["date_source"] == "xlsx" and r["url"].lower().endswith(".xlsx"):
            last[(r["series"], _stem(r["url"]), r["week_end"])] = r
    out: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for (series, _, wk), r in sorted(last.items()):
        out[(series, wk)].append(r)
    return out


def build(repo: Path, ledger_path: Path, data_dir: Path) -> list[str]:
    """Write one CSV per (series, week). Returns layout errors (empty if none)."""
    errors: list[str] = []
    anomalies: list[dict[str, str]] = []
    rebuilt: set[tuple[str, str]] = set()
    groups = latest_xlsx(read_ledger(ledger_path))
    for (series, wk), entries in sorted(groups.items()):
        if series not in PARSERS:  # kept and dated, not read yet (parse.DATE_ONLY)
            continue
        missing = [e["path"] for e in entries if not (repo / e["path"]).exists()]
        if missing:
            log("build_skipped_week", series=series, week_end=wk, missing=len(missing),
                reason="originals not on disk; existing CSV kept")
            continue
        rebuilt.add((series, wk))
        rows: list[Row] = []
        for e in entries:
            try:
                parsed, notes = parse_file(series, repo / e["path"])
            except LayoutError as exc:
                errors.append(f"{e['path']}: {exc}")
                log("layout_error", path=e["path"], error=str(exc))
                continue
            for n in notes:
                anomalies.append({"series": series, "week_end": wk, "source_file": e["path"], "note": n})
                log("anomaly", path=e["path"], note=n)
            for r in parsed:
                r.source_file = e["path"]
            rows.extend(parsed)
        out = data_dir / series / f"{wk}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        rows.sort(key=lambda r: (r.source_file, r.sheet, r.section, r.measure, r.geography, r.period, -(r.year or 0)))
        tmp = out.with_suffix(".csv.tmp")
        with tmp.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            for r in rows:
                w.writerow(r.as_dict())
        tmp.replace(out)
        log("built", series=series, week_end=wk, files=len(entries), rows=len(rows))
    path = data_dir / "anomalies.csv"
    if path.exists():
        with path.open(newline="") as f:
            kept = [a for a in csv.DictReader(f) if (a["series"], a["week_end"]) not in rebuilt]
        anomalies = sorted(kept + anomalies, key=lambda a: (a["series"], a["week_end"], a["source_file"], a["note"]))
    _write_anomalies(path, anomalies)
    _write_stale(data_dir / "stale_files.csv", read_ledger(ledger_path))
    return errors


STALE_DAYS = 7


def stale_files(ledger: list[dict[str, str]]) -> list[dict[str, str]]:
    """Files whose newest in-file week is behind the newest week in their series.

    A file nyc.gov still serves with a 200 can be months old: the archive
    check (2026-11-21) found the pre-split Bronx file cs-en-us-pbbx.xlsx still
    answering with an outdated report. The week inside the file is the only
    reliable signal, so that is what is compared. Q-Team as a whole runs a few
    weeks behind; that is not staleness, because it is measured within the series.
    """
    from datetime import date

    latest: dict[tuple[str, str], str] = {}
    for r in ledger:
        if r["date_source"] != "xlsx":
            continue
        key = (r["series"], _stem(r["url"]))
        latest[key] = max(latest.get(key, ""), r["week_end"])
    newest: dict[str, str] = {}
    for (series, _), wk in latest.items():
        newest[series] = max(newest.get(series, ""), wk)
    out = []
    for (series, stem), wk in sorted(latest.items()):
        lag = (date.fromisoformat(newest[series]) - date.fromisoformat(wk)).days
        if lag >= STALE_DAYS:
            out.append({"series": series, "file": stem, "file_week_end": wk,
                        "series_week_end": newest[series], "days_behind": str(lag)})
    return out


def _write_stale(path: Path, ledger: list[dict[str, str]]) -> None:
    rows = stale_files(ledger)
    for r in rows:
        log("stale_file", **r)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["series", "file", "file_week_end", "series_week_end", "days_behind"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _write_anomalies(path: Path, anomalies: list[dict[str, str]]) -> None:
    """Problems in NYPD's own files that did not stop the parse (rebuilt each run)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["series", "week_end", "source_file", "note"], lineterminator="\n")
        w.writeheader()
        w.writerows(anomalies)

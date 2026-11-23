"""One-time backfill of weekly collision counts from NYPD's TrafficStat dashboard.

The dashboard at https://trafficsafetyforum.nypdonline.org/ has no published
API documentation. Everything below was read off the requests the dashboard
itself made in a browser on 2026-11-21 (Chromium, developer network log),
and the code stops on any response that does not match those shapes.

Observed calls (all unauthenticated POSTs with a JSON body):

- Precinct list: POST /api/filters/ec81a9e0-27d3-4c69-8a49-d02ce3236b69/data,
  body {}. Returns a list of {key, label, imageUrl, relatedKeyValue, groupBy};
  precinct keys are three digits ("075") with relatedKeyValue = patrol borough.
- Book: POST /api/reports/b805fa11-d5d2-43f7-8c23-1649f5d387f1/data, body
      [{"key":"PRECINCTKey","values":["Citywide"]},
       {"key":"BOROKey","values":["Citywide"]},
       {"key":"RECORDID","values":[]}]
  Rows carry columns whose filterValue is a record id such as
  "WTD_COLLISIONS_Injury Collisions"; prefixes are WTD, SPLYWTD, 28D,
  SPLY28D, YTD, SPLYYTD; 30 metrics each, all collisions, injuries and
  fatalities. No summons or arrest metric is exposed.
- Timeline, "52 Week" option: POST
  /api/reports/82155271-ff46-4ff5-aa97-26fb7ed5ba8f/data with the same body
  and RECORDID set to one record id. Selecting a precinct in the app sends
  PRECINCTKey ["075"] and leaves BOROKey ["Citywide"]. The response is a list
  of points with categoryValue = week-ending date "MM/DD/YY", itemValue = the
  count, and chartTitle "Timeline - 52 Week \\r\\n<label> - <geo> - <start> - <end>".
  WTD_ gives the 53 weeks ending with the current report week (weeks end
  Sunday). SPLYWTD_ gives 53 weeks ending on the same calendar date one year
  earlier (2025-11-15, a Saturday, when the current week ends 2026-11-15), so
  the two windows do not form one continuous Monday-Sunday series. They are
  kept apart in the `window` column.
- Weeks with a count of zero are left out of the response (observed:
  precinct 075 "Fat E-bike" returned a single point). Vital City's
  compstat-decoder documents the same for the CompStat dashboard
  (scripts/archive_weekly_series.py). Missing weeks inside the 53-week grid
  are therefore written as 0 with imputed=1.

Requests are sequential and at least 2 seconds apart. Raw responses are kept
under raw/trafficstat_dashboard/<run date>/responses.jsonl.gz.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .log import log
from .net import Fetcher, utc_now_iso

HOST = "https://trafficsafetyforum.nypdonline.org"
GEO_FILTER_URL = f"{HOST}/api/filters/ec81a9e0-27d3-4c69-8a49-d02ce3236b69/data"
BOOK_URL = f"{HOST}/api/reports/b805fa11-d5d2-43f7-8c23-1649f5d387f1/data"
TIMELINE_URL = f"{HOST}/api/reports/82155271-ff46-4ff5-aa97-26fb7ed5ba8f/data"
WINDOWS = ("WTD", "SPLYWTD")
BOOK_PREFIXES = {"WTD", "SPLYWTD", "28D", "SPLY28D", "YTD", "SPLYYTD"}
GRID_METRIC = "COLLISIONS_Collisions"  # dense at Citywide; defines each window's weeks
GRID_WEEKS = 53

# Keys of every point object, as observed.
POINT_KEYS = {
    "title", "content", "metric", "value", "tooltipHtml", "headerHtml", "filterKey", "columns",
    "chartTitle", "seriesColor", "seriesLabel", "seriesOrderBy", "seriesValue", "itemValue",
    "categoryLabel", "categoryOrderBy", "categoryValue", "categoryImageUrl",
}
GEO_KEYS = {"key", "label", "imageUrl", "relatedKeyValue", "groupBy"}
TITLE = re.compile(
    r"Timeline - 52 Week\s*\r?\n(?P<label>.+) - (?P<geo>[^-]+?) - (?P<start>\d\d/\d\d/\d\d) - (?P<end>\d\d/\d\d/\d\d)"
)
OUT_FIELDS = ["geography", "window", "record_id", "metric", "series_label", "week_end", "value", "imputed",
              "fetched_on"]


class ShapeError(ValueError):
    """The dashboard answered with something other than the observed shape."""


def body(geo: str, record_id: str | None) -> list[dict[str, object]]:
    return [
        {"key": "PRECINCTKey", "values": [geo]},
        {"key": "BOROKey", "values": ["Citywide"]},
        {"key": "RECORDID", "values": [record_id] if record_id else []},
    ]


def _mdy2(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%y").date()


def geos_from_filter(resp: object, keys: set[str] = GEO_KEYS) -> list[str]:
    if not isinstance(resp, list) or not resp:
        raise ShapeError(f"precinct filter: expected a non-empty list, got {type(resp).__name__}")
    precincts = set()
    for item in resp:
        if not isinstance(item, dict) or set(item) != keys:
            raise ShapeError(f"precinct filter: unexpected item {item!r}")
        if re.fullmatch(r"\d{3}", str(item["key"])):
            precincts.add(item["key"])
    if len(precincts) < 70:
        raise ShapeError(f"precinct filter: only {len(precincts)} precincts")
    return ["Citywide", *sorted(precincts)]


def metrics_from_book(resp: object) -> list[str]:
    if not isinstance(resp, list) or not resp:
        raise ShapeError("book: expected a non-empty list")
    ids = set()
    for row in resp:
        for col in (row.get("columns") or []) if isinstance(row, dict) else []:
            fv = col.get("filterValue")
            if fv:
                prefix, _, metric = fv.partition("_")
                if prefix not in BOOK_PREFIXES or not metric:
                    raise ShapeError(f"book: unknown record id {fv!r}")
                ids.add((prefix, metric))
    metrics = sorted({m for p, m in ids if p == "WTD"})
    if not metrics or GRID_METRIC not in metrics:
        raise ShapeError(f"book: no WTD record ids, or {GRID_METRIC} missing")
    for p in WINDOWS:
        missing = [m for m in metrics if (p, m) not in ids]
        if missing:
            raise ShapeError(f"book: {p} lacks {missing}")
    return metrics


@dataclass
class Timeline:
    label: str | None
    end: date | None
    points: dict[date, int]


def parse_timeline(resp: object, geo: str) -> Timeline:
    if not isinstance(resp, list):
        raise ShapeError(f"timeline {geo}: expected a list, got {type(resp).__name__}")
    points: dict[date, int] = {}
    label = end = None
    for item in resp:
        if not isinstance(item, dict) or set(item) != POINT_KEYS:
            shown = sorted(item) if isinstance(item, dict) else item
            raise ShapeError(f"timeline {geo}: unexpected point keys {shown}")
        m = TITLE.fullmatch(str(item["chartTitle"]).strip())
        if m is None:
            raise ShapeError(f"timeline {geo}: chart title {item['chartTitle']!r}")
        if m.group("geo").strip() != geo:
            raise ShapeError(f"timeline: asked for {geo}, title says {m.group('geo')!r}")
        label, end = item["seriesValue"], _mdy2(m.group("end"))
        if not re.fullmatch(r"\d\d/\d\d/\d\d", str(item["categoryValue"])):
            raise ShapeError(f"timeline {geo}: category {item['categoryValue']!r}")
        v = item["itemValue"]
        if not isinstance(v, int) or isinstance(v, bool):
            if isinstance(v, float) and v.is_integer():
                v = int(v)
            else:
                raise ShapeError(f"timeline {geo}: value {v!r}")
        wk = _mdy2(item["categoryValue"])
        if wk in points:
            raise ShapeError(f"timeline {geo}: week {wk} twice")
        points[wk] = v
    return Timeline(label, end, points)


def grid_from(tl: Timeline) -> list[date]:
    weeks = sorted(tl.points)
    if len(weeks) != GRID_WEEKS:
        raise ShapeError(f"grid series has {len(weeks)} weeks, expected {GRID_WEEKS}")
    for a, b in zip(weeks, weeks[1:], strict=False):
        if b - a != timedelta(days=7):
            raise ShapeError(f"grid series not weekly between {a} and {b}")
    return weeks


# --------------------------------------------------------------- fetching


def run_dir(raw_root: Path, day: str) -> Path:
    return raw_root / "trafficstat_dashboard" / day


def _done(path: Path) -> set[str]:
    """Requests already answered in an interrupted run. A process stopped
    mid-write can leave a partial last line; it is cut off and refetched."""
    keys = set()
    if not path.exists():
        return keys
    text = path.read_text()
    lines = text.split("\n")
    good: list[str] = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            if i >= len(lines) - 2:  # only the last line may be partial
                log("dashboard_partial_line_dropped", path=str(path))
                break
            raise
        good.append(line)
        keys.add(json.dumps(rec["request"], sort_keys=True) + rec["url"])
    if len(good) != sum(1 for ln in lines if ln.strip()):
        path.write_text("".join(ln + "\n" for ln in good))
    return keys


def backfill(fetcher: Fetcher, raw_root: Path, geos: list[str] | None = None, day: str | None = None) -> Path:
    """Fetch every (geo, metric, window) timeline once. Resumable within a day."""
    day = day or datetime.now().date().isoformat()
    out_dir = run_dir(raw_root, day)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "responses.jsonl"
    if (out_dir / "responses.jsonl.gz").exists():
        raise ShapeError(f"{out_dir} is already complete")
    done = _done(jsonl)

    def call(url: str, req: object) -> object:
        key = json.dumps(req, sort_keys=True) + url
        resp = fetcher.post_json(url, req)
        data = resp.json()
        if key not in done:
            with jsonl.open("a") as f:
                f.write(json.dumps({"fetched_at": utc_now_iso(), "url": url, "request": req,
                                    "status": resp.status_code, "response": data}) + "\n")
            done.add(key)
        return data

    all_geos = geos_from_filter(call(GEO_FILTER_URL, {}))
    metrics = metrics_from_book(call(BOOK_URL, body("Citywide", None)))
    wanted = all_geos if geos is None else [g for g in all_geos if g in geos]
    if geos is not None and "Citywide" not in wanted:
        wanted = ["Citywide", *wanted]  # the grid comes from Citywide
    log("dashboard_plan", geos=len(wanted), metrics=len(metrics), calls=len(wanted) * len(metrics) * len(WINDOWS))
    n = 0
    for window in WINDOWS:
        for geo in wanted:
            for metric in metrics:
                req = body(geo, f"{window}_{metric}")
                if json.dumps(req, sort_keys=True) + TIMELINE_URL in done:
                    continue
                parse_timeline(call(TIMELINE_URL, req), geo)  # stop at once on a shape change
                n += 1
                if n % 100 == 0:
                    log("dashboard_progress", calls=n, window=window, geo=geo)
    with jsonl.open("rb") as src, gzip.open(out_dir / "responses.jsonl.gz", "wb") as dst:
        dst.write(src.read())
    jsonl.unlink()
    log("dashboard_done", dir=str(out_dir), calls=n)
    return out_dir


# --------------------------------------------------------------- building


def _records(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as f:
        return [json.loads(line) for line in f if line.strip()]


def rows_from_run(records: list[dict], fetched_on: str) -> tuple[list[dict[str, object]], list[str]]:
    """Tidy rows for one run, with zero-filled weeks, plus reconciliation notes."""
    series: dict[tuple[str, str, str], Timeline] = {}
    for rec in records:
        if rec["url"] != TIMELINE_URL:
            continue
        req = {d["key"]: d["values"] for d in rec["request"]}
        geo, rid = req["PRECINCTKey"][0], req["RECORDID"][0]
        window, _, metric = rid.partition("_")
        series[(window, geo, metric)] = parse_timeline(rec["response"], geo)
    rows: list[dict[str, object]] = []
    notes: list[str] = []
    for window in WINDOWS:
        ref = series.get((window, "Citywide", GRID_METRIC))
        if ref is None:
            continue
        grid = grid_from(ref)
        for (w, geo, metric), tl in sorted(series.items()):
            if w != window:
                continue
            extra = set(tl.points) - set(grid)
            if extra:
                raise ShapeError(f"{window} {geo} {metric}: weeks outside the grid {sorted(extra)}")
            for wk in grid:
                rows.append({
                    "geography": geo, "window": window, "record_id": f"{window}_{metric}", "metric": metric,
                    "series_label": tl.label or "", "week_end": wk.isoformat(),
                    "value": tl.points.get(wk, 0), "imputed": 0 if wk in tl.points else 1,
                    "fetched_on": fetched_on,
                })
        # Precincts should add up to Citywide (Vital City found a missing 116th
        # this way). Reported, not fatal.
        totals: dict[tuple[str, str], int] = defaultdict(int)
        city: dict[tuple[str, str], int] = {}
        for r in rows:
            if r["window"] != window:
                continue
            k = (str(r["metric"]), str(r["week_end"]))
            if r["geography"] == "Citywide":
                city[k] = int(r["value"])  # type: ignore[arg-type]
            else:
                totals[k] += int(r["value"])  # type: ignore[arg-type]
        bad = [k for k in city if totals.get(k, 0) != city[k]]
        if bad and len({g for (_, g, _) in series if g != "Citywide"}) > 1:
            notes.append(f"{window}: {len(bad)} of {len(city)} metric-weeks where precincts do not sum to Citywide")
    return rows, notes


def build_dashboard(raw_root: Path, out_csv: Path) -> list[str]:
    runs = sorted((raw_root / "trafficstat_dashboard").glob("*/responses.jsonl*"))
    merged: dict[tuple[str, str, str, str], dict[str, object]] = {}
    notes: list[str] = []
    for path in runs:  # oldest first, so a later fetch of the same week wins
        rows, n = rows_from_run(_records(path), path.parent.name)
        notes += n
        for r in rows:
            merged[(str(r["geography"]), str(r["window"]), str(r["metric"]), str(r["week_end"]))] = r
    if not merged:
        return notes
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_csv.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, lineterminator="\n")
        w.writeheader()
        for k in sorted(merged):
            w.writerow(merged[k])
    tmp.replace(out_csv)
    for n in notes:
        log("dashboard_reconcile", note=n)
    log("dashboard_built", rows=len(merged), path=str(out_csv))
    return notes

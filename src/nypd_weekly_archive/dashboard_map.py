"""Weekly save of the crash points on NYPD's TrafficStat dashboard map.

The dashboard at https://trafficsafetyforum.nypdonline.org/ maps each crash
behind a number in its table. It only answers for this week, the last 28
days and the year to date (and the same stretch a year earlier), so a week
that is not saved is gone. Everything below was read off the requests the
dashboard made in a browser on 2026-11-22 (Playwright, network log); see
betanyc-ai-workspace team/research/crash-mapper/2026-11-22-trafficsafetyforum-map.md.
The code stops on any response that does not match those shapes.

Observed calls (unauthenticated JSON POSTs, same body shape as dashboard.py):

- Book (the table): BOOK_URL with RECORDID []. Its first row's title reads
  "Week of 11/09/26 - 11/15/26". Cells carry filterValue (a record id such as
  "WTD_COLLISIONS_Fat E-bike") and value, the count as digits ("1459", "0").
  Each cell appears twice, in two sections of the table, with the same value.
- Map: MAP_URL with RECORDID [one record id]. The answer is a list of points
  with the 18 keys of POINT_KEYS. Only three are filled in:
    metric       the count at that spot (crashes at the same spot are merged;
                 for fatality metrics it counts deaths, so a point can say 2
                 and list one crash)
    value        "lat,lng", 7 decimals
    tooltipHtml  <h5>metric name</h5>, <h6>N Occurences</h6> when metric > 1,
                 then two <span>s per crash: the crash type (cut to about 17
                 characters, the full text in title="...") and "MM/DD/YY 5AM".
  The footer says only the first 5,000 records of a field are mapped, so the
  points are requested one precinct at a time and their counts must add up
  to the table's number.

Per run: the Citywide book (the week and the 30 week-to-date metrics), the
precinct list, then for each precinct its own book and one map request per
metric whose count is not zero; last, the Citywide book again, to make sure
the week did not change during the run. Requests are sequential and at least
2 seconds apart. Raw answers go to raw/trafficstat_dashboard_map/<week end>/
responses.jsonl.gz (released with the week's originals), and one row per
crash to data/trafficstat_dashboard_map/<week end>.csv.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

from .dashboard import BOOK_URL, GEO_FILTER_URL, HOST, POINT_KEYS, ShapeError, _done, body, geos_from_filter
from .log import log
from .net import Fetcher, utc_now_iso

MAP_URL = f"{HOST}/api/reports/7d9951ab-45d8-41af-bcc7-10e2f1874de0/data"
MAP_CAP = 5000
RAW_DIR = "trafficstat_dashboard_map"
WEEK = re.compile(r"Week of (\d\d/\d\d/\d\d) - (\d\d/\d\d/\d\d)")
LATLNG = re.compile(r"\s*(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)\s*")
WHEN = re.compile(r"(\d\d/\d\d/\d\d) (\d{1,2})(AM|PM)")
OCCURRENCES = re.compile(r"(\d+) Occurences")  # NYPD's spelling
OUT_FIELDS = ["week_start", "week_end", "precinct", "record_id", "metric", "point", "latitude", "longitude",
              "point_count", "crash", "crash_type", "date", "hour", "fetched_on"]


def _mdy2(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%y").date()


def book_week(resp: object) -> tuple[date, date]:
    if not isinstance(resp, list):
        raise ShapeError("book: expected a list")
    for row in resp:
        m = WEEK.search(str(row.get("title") or "")) if isinstance(row, dict) else None
        if m:
            return _mdy2(m[1]), _mdy2(m[2])
    raise ShapeError("book: no 'Week of' title")


def book_counts(resp: object) -> dict[str, int]:
    """Week-to-date record id -> the table's count."""
    if not isinstance(resp, list):
        raise ShapeError("book: expected a list")
    out: dict[str, int] = {}
    for row in resp:
        for col in (row.get("columns") or []) if isinstance(row, dict) else []:
            rid = col.get("filterValue")
            if not rid or not rid.startswith("WTD_"):
                continue
            v = str(col.get("value"))
            if not v.isdigit():
                raise ShapeError(f"book: {rid} value {v!r}")
            if out.get(rid, int(v)) != int(v):
                raise ShapeError(f"book: {rid} shows {out[rid]} and {v}")
            out[rid] = int(v)
    if "WTD_COLLISIONS_Collisions" not in out:
        raise ShapeError("book: no WTD_COLLISIONS_Collisions cell")
    return out


class _Tooltip(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tag: str | None = None
        self.title: str | None = None
        self.h5 = self.h6 = ""
        self.spans: list[tuple[str, str | None]] = []  # (text, title)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h5", "h6", "span"):
            self.tag, self.title = tag, dict(attrs).get("title")
            if tag == "span":
                self.spans.append(("", self.title))

    def handle_endtag(self, tag: str) -> None:
        if tag == self.tag:
            self.tag = None

    def handle_data(self, data: str) -> None:
        if self.tag == "h5":
            self.h5 += data
        elif self.tag == "h6":
            self.h6 += data
        elif self.tag == "span":
            text, title = self.spans[-1]
            self.spans[-1] = (text + data, title)


def parse_points(resp: object, where: str) -> list[dict[str, object]]:
    """One dict per crash. `where` names the request in error messages."""
    if not isinstance(resp, list):
        raise ShapeError(f"map {where}: expected a list, got {type(resp).__name__}")
    if len(resp) >= MAP_CAP:
        raise ShapeError(f"map {where}: {len(resp)} points, at the {MAP_CAP} cap")
    if len(resp) == 1 and isinstance(resp[0], dict) and set(resp[0]) == POINT_KEYS and resp[0]["metric"] == 0:
        # "Nothing to map": one point with count 0 at the map's default center and a caption
        # such as "Incident Map \r\nWeek to Date - Pedestrian - 044 CMD - 11/09/26 - 11/15/26"
        # (seen 2026-11-22 for cells the table counts but whose incidents have no location).
        if not str(resp[0]["tooltipHtml"] or "").startswith("Incident Map") or not LATLNG.fullmatch(
                str(resp[0]["value"])):
            raise ShapeError(f"map {where}: a count-0 point that is not the empty map: {resp[0]!r}")
        return []
    rows: list[dict[str, object]] = []
    for i, item in enumerate(resp, 1):
        if not isinstance(item, dict) or set(item) != POINT_KEYS:
            shown = sorted(item) if isinstance(item, dict) else item
            raise ShapeError(f"map {where}: unexpected point keys {shown}")
        n = item["metric"]
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ShapeError(f"map {where}: metric {n!r}")
        m = LATLNG.fullmatch(str(item["value"]))
        if m is None:
            raise ShapeError(f"map {where}: value {item['value']!r}")
        tip = _Tooltip()
        tip.feed(str(item["tooltipHtml"] or ""))
        occ = OCCURRENCES.fullmatch(tip.h6.strip())
        if (n > 1) != bool(occ) or (occ and int(occ[1]) != n):
            raise ShapeError(f"map {where}: count {n} but heading {tip.h6.strip()!r}")
        if not tip.h5.strip() or len(tip.spans) % 2:
            raise ShapeError(f"map {where}: tooltip {item['tooltipHtml']!r}")
        crashes = [tip.spans[j:j + 2] for j in range(0, len(tip.spans), 2)]
        if not 1 <= len(crashes) <= n:
            raise ShapeError(f"map {where}: count {n} but {len(crashes)} crashes listed")
        for k, ((label, full), (when, _)) in enumerate(crashes, 1):
            w = WHEN.fullmatch(when.strip())
            if w is None:
                raise ShapeError(f"map {where}: date {when!r}")
            hour = int(w[2]) % 12 + (12 if w[3] == "PM" else 0)
            rows.append({"point": i, "latitude": m[1], "longitude": m[2], "point_count": n, "crash": k,
                         "crash_type": (full or label).strip(), "date": _mdy2(w[1]).isoformat(), "hour": hour})
    return rows


# --------------------------------------------------------------- fetching


def save_week(
    fetcher: Fetcher, raw_root: Path, data_dir: Path, geos: list[str] | None = None
) -> tuple[Path | None, int]:
    """Save the current week's points. Returns (csv path or None if already saved, mismatches)."""
    city = body("Citywide", None)
    first_resp = fetcher.post_json(BOOK_URL, city)
    first = first_resp.json()
    start, end = book_week(first)
    metrics = sorted(book_counts(first))
    out_csv = data_dir / RAW_DIR / f"{end.isoformat()}.csv"
    if out_csv.exists():
        log("dashboard_map_skip", week_end=end.isoformat(), reason="already saved")
        return None, 0
    out_dir = raw_root / RAW_DIR / end.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    gz = out_dir / "responses.jsonl.gz"
    if gz.exists():  # fetched earlier, CSV not written yet
        return build_week(gz, out_csv)
    jsonl = out_dir / "responses.jsonl"
    done = _done(jsonl)

    def keep(url: str, req: object, status: int, data: object) -> None:
        key = json.dumps(req, sort_keys=True) + url
        if key not in done:
            with jsonl.open("a") as f:
                f.write(json.dumps({"fetched_at": utc_now_iso(), "url": url, "request": req,
                                    "status": status, "response": data}) + "\n")
            done.add(key)

    def call(url: str, req: object) -> object:
        resp = fetcher.post_json(url, req)
        data = resp.json()
        keep(url, req, resp.status_code, data)
        return data

    keep(BOOK_URL, city, first_resp.status_code, first)
    precincts = [g for g in geos_from_filter(call(GEO_FILTER_URL, {})) if g != "Citywide"]
    if geos is not None:
        precincts = [g for g in precincts if g in geos]
    log("dashboard_map_plan", week_end=end.isoformat(), precincts=len(precincts), metrics=len(metrics))
    n = 0
    for geo in precincts:
        book = call(BOOK_URL, body(geo, None))
        if book_week(book) != (start, end):
            raise ShapeError(f"book {geo}: week {book_week(book)}, Citywide says {(start, end)}")
        counts = book_counts(book)
        for rid in metrics:
            req = body(geo, rid)
            if counts.get(rid, 0) == 0 or json.dumps(req, sort_keys=True) + MAP_URL in done:
                continue
            parse_points(call(MAP_URL, req), f"{geo} {rid}")  # stop at once on a shape change
            n += 1
        log("dashboard_map_progress", precinct=geo, map_calls=n)
    last = fetcher.post_json(BOOK_URL, city).json()
    if book_week(last) != (start, end):
        raise ShapeError(f"the dashboard moved to {book_week(last)} during the run; nothing written")
    with jsonl.open("rb") as src, gzip.open(gz, "wb") as dst:
        dst.write(src.read())
    jsonl.unlink()
    log("dashboard_map_fetched", dir=str(out_dir), map_calls=n)
    return build_week(gz, out_csv)


# --------------------------------------------------------------- building


def build_week(gz: Path, out_csv: Path) -> tuple[Path, int]:
    """Write the CSV from one week's raw answers. Returns (csv, count mismatches)."""
    with gzip.open(gz, "rt") as f:
        records = [json.loads(line) for line in f if line.strip()]
    counts: dict[str, dict[str, int]] = {}
    week: tuple[date, date] | None = None
    rows: list[dict[str, object]] = []
    for rec in records:
        req = {d["key"]: d["values"] for d in rec["request"]} if isinstance(rec["request"], list) else {}
        geo = (req.get("PRECINCTKey") or [""])[0]
        if rec["url"] == BOOK_URL:
            wk = book_week(rec["response"])
            if week not in (None, wk):
                raise ShapeError(f"{gz}: two weeks, {week} and {wk}")
            week = wk
            counts[geo] = book_counts(rec["response"])
        elif rec["url"] == MAP_URL:
            rid = req["RECORDID"][0]
            for r in parse_points(rec["response"], f"{geo} {rid}"):
                rows.append({"precinct": geo, "record_id": rid, "metric": rid.partition("_")[2],
                             "fetched_on": rec["fetched_at"][:10], **r})
    if week is None:
        raise ShapeError(f"{gz}: no book answer")
    mismatches = 0
    got: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["crash"] == 1:
            k = (str(r["precinct"]), str(r["record_id"]))
            got[k] = got.get(k, 0) + int(r["point_count"])  # type: ignore[call-overload]
    # Fewer points than the table is expected now and then (incidents with no location) and is
    # only logged. More points than the table, or points for a cell the table lacks, exits 1.
    for (geo, rid), total in sorted(got.items()):
        want = counts.get(geo, {}).get(rid)
        if want is not None and total < want:
            log("dashboard_map_short", precinct=geo, record_id=rid, points=total, table=want)
        elif want != total:
            mismatches += 1
            log("dashboard_map_mismatch", precinct=geo, record_id=rid, points=total, table=want)
    for r in rows:
        r["week_start"], r["week_end"] = week[0].isoformat(), week[1].isoformat()
    rows.sort(key=lambda r: (r["precinct"], r["record_id"], r["point"], r["crash"]))  # type: ignore[arg-type,return-value]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_csv.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out_csv)
    log("dashboard_map_built", path=str(out_csv), rows=len(rows), mismatches=mismatches)
    return out_csv, mismatches

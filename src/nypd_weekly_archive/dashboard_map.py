"""Save the incident points on the maps of NYPD's two dashboards.

Two dashboards run on the same platform and answer the same requests:
TrafficStat (https://trafficsafetyforum.nypdonline.org/, crashes) and
CompStat 2.0 (https://compstat.nypdonline.org/, crime complaints). Each maps
the incidents behind a number in its table, but only for this week (WTD), the
last 28 days (28D) and the year to date (YTD), plus the same stretch a year
earlier (SPLYWTD, SPLY28D, SPLYYTD). A week that is not saved is gone.
Everything below was read off the requests the dashboards made in a browser
on 2026-11-22 (Playwright, network log); see betanyc-ai-workspace
team/research/crash-mapper/2026-11-22-trafficsafetyforum-map.md and
2026-11-22-compstat-map.md. The code stops on any answer that does not match
those shapes.

Observed calls (unauthenticated JSON POSTs, same body shape as dashboard.py,
same report ids on both hosts):

- Book (the table): reports/b805fa11-... with RECORDID []. Its first row's
  title reads "Week of 11/09/26 - 11/15/26". Cells carry filterValue, a record
  id "<window>_<family>_<measure>" such as "WTD_COLLISIONS_Fat E-bike" or
  "YTD_COMPLAINTS_Robbery", and value, the count as digits ("1459", "0").
  TrafficStat repeats each cell in two sections of the table, with the same
  value.
- Map: reports/7d9951ab-... with RECORDID [one record id]. The answer is a
  list of points with the 18 keys of POINT_KEYS. Only three are filled in:
    metric       the count at that spot (incidents at the same spot are
                 merged; for fatality measures it counts deaths, so a point
                 can say 2 and list one crash)
    value        "lat,lng", 7 decimals
    tooltipHtml  <h5>measure</h5>, <h6>N Occurences</h6> when metric > 1,
                 then per incident one or more labels in <span>s (cut to about
                 17 characters, the full text in title="...") and a last
                 <span> "MM/DD/YY 5AM". TrafficStat gives one label (the crash
                 type); CompStat gives one (such as the robbery type) and
                 sometimes a second one.
  TrafficStat's footer says only the first 5,000 records of a field are
  mapped, so points are requested one precinct at a time.

Per run: the Citywide book (the week and the measures), the precinct list,
then for each precinct its own book and one map request per record id whose
count is not zero; last, the Citywide book again, to make sure the week did
not change during the run. Requests are sequential and at least 2 seconds
apart. The points' counts are compared with the book. Fewer points than the
book is logged only (CompStat draws one point for an incident with two
victims); more points than the book is logged and makes the run exit 1.
Raw answers go to raw/<site>_dashboard_map/<week end>[_<windows>]/
responses.jsonl.gz, and one row per incident to
data/<site>_dashboard_map/<week end>[_<windows>].csv.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path

from .dashboard import GEO_KEYS, POINT_KEYS, ShapeError, _done, body, geos_from_filter
from .log import log
from .net import Fetcher, utc_now_iso

BOOK_ID = "b805fa11-d5d2-43f7-8c23-1649f5d387f1"
GEO_FILTER_ID = "ec81a9e0-27d3-4c69-8a49-d02ce3236b69"
MAP_ID = "7d9951ab-45d8-41af-bcc7-10e2f1874de0"
MAP_CAP = 5000
WINDOWS = ("WTD", "SPLYWTD", "28D", "SPLY28D", "YTD", "SPLYYTD")


@dataclass(frozen=True)
class Site:
    key: str
    host: str
    family: str  # the middle part of every record id
    anchor: str  # a measure every book has
    public: bool  # may its points be written inside this (public) repository?
    geo_keys: frozenset[str] = frozenset(GEO_KEYS)  # keys of each item in the precinct list

    @property
    def book_url(self) -> str:
        return f"{self.host}/api/reports/{BOOK_ID}/data"

    @property
    def geo_url(self) -> str:
        return f"{self.host}/api/filters/{GEO_FILTER_ID}/data"

    @property
    def map_url(self) -> str:
        return f"{self.host}/api/reports/{MAP_ID}/data"

    @property
    def dir(self) -> str:
        return f"{self.key}_dashboard_map"


SITES = {
    "trafficstat": Site("trafficstat", "https://trafficsafetyforum.nypdonline.org", "COLLISIONS", "Collisions", True),
    # CompStat points stay on a local machine: never in this public repository or its releases.
    # Its precinct list has no groupBy key (observed 2026-11-22).
    "compstat": Site("compstat", "https://compstat.nypdonline.org", "COMPLAINTS", "Robbery", False,
                     frozenset(GEO_KEYS - {"groupBy"})),
}
TRAFFICSTAT = SITES["trafficstat"]
MAP_URL = TRAFFICSTAT.map_url
WEEK = re.compile(r"Week of (\d\d/\d\d/\d\d) - (\d\d/\d\d/\d\d)")
LATLNG = re.compile(r"\s*(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)\s*")
WHEN = re.compile(r"(\d\d/\d\d/\d\d) (\d{1,2})(AM|PM)")
OCCURRENCES = re.compile(r"(\d+) Occurences")  # NYPD's spelling
OUT_FIELDS = ["site", "week_start", "week_end", "precinct", "record_id", "window", "measure", "point", "latitude",
              "longitude", "point_count", "incident", "label", "extra_labels", "date", "hour", "fetched_on"]


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


def book_counts(resp: object, site: Site = TRAFFICSTAT, windows: tuple[str, ...] = ("WTD",)) -> dict[str, int]:
    """Record id -> the table's count, for the given windows."""
    if not isinstance(resp, list):
        raise ShapeError("book: expected a list")
    out: dict[str, int] = {}
    for row in resp:
        for col in (row.get("columns") or []) if isinstance(row, dict) else []:
            rid = col.get("filterValue")
            if not rid:
                continue
            window, _, rest = rid.partition("_")
            if window not in WINDOWS or not rest.startswith(site.family + "_"):
                raise ShapeError(f"book: unknown record id {rid!r}")
            if window not in windows:
                continue
            v = str(col.get("value"))
            if not v.isdigit():
                raise ShapeError(f"book: {rid} value {v!r}")
            if out.get(rid, int(v)) != int(v):
                raise ShapeError(f"book: {rid} shows {out[rid]} and {v}")
            out[rid] = int(v)
    anchor = f"WTD_{site.family}_{site.anchor}"
    if "WTD" in windows and anchor not in out:
        raise ShapeError(f"book: no {anchor} cell")
    if not out:
        raise ShapeError(f"book: no cells for {windows}")
    return out


class _Tooltip(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tag: str | None = None
        self.h5 = self.h6 = ""
        self.spans: list[tuple[str, str | None]] = []  # (text, title)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("h5", "h6", "span"):
            self.tag = tag
            if tag == "span":
                self.spans.append(("", dict(attrs).get("title")))

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
    """One dict per incident. `where` names the request in error messages."""
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
        if not tip.h5.strip():
            raise ShapeError(f"map {where}: tooltip {item['tooltipHtml']!r}")
        # Each incident is its labels, then its date.
        incidents: list[tuple[list[str], re.Match[str]]] = []
        labels: list[str] = []
        for text, title in tip.spans:
            w = WHEN.fullmatch(text.strip())
            if w:
                if not labels:
                    raise ShapeError(f"map {where}: a date with no label in {item['tooltipHtml']!r}")
                incidents.append((labels, w))
                labels = []
            else:
                labels.append((title or text).strip())
        if labels or not 1 <= len(incidents) <= n:
            raise ShapeError(f"map {where}: count {n} but {len(incidents)} incidents in {item['tooltipHtml']!r}")
        for k, (labs, w) in enumerate(incidents, 1):
            hour = int(w[2]) % 12 + (12 if w[3] == "PM" else 0)
            rows.append({"point": i, "latitude": m[1], "longitude": m[2], "point_count": n, "incident": k,
                         "label": labs[0], "extra_labels": "; ".join(labs[1:]),
                         "date": _mdy2(w[1]).isoformat(), "hour": hour})
    return rows


def run_name(end: date, windows: tuple[str, ...], geos: list[str] | None = None) -> str:
    """2026-11-15 for the weekly save, 2026-11-15_YTD-SPLYYTD for other windows, and
    _only-075 for a run limited to some precincts, so it never stands in for a full week."""
    name = end.isoformat() if windows == ("WTD",) else f"{end.isoformat()}_{'-'.join(windows)}"
    return name + ("_only-" + "-".join(sorted(geos)) if geos is not None else "")


# --------------------------------------------------------------- fetching


def save_week(
    fetcher: Fetcher, raw_root: Path, data_dir: Path, geos: list[str] | None = None,
    site: Site = TRAFFICSTAT, windows: tuple[str, ...] = ("WTD",),
) -> tuple[Path | None, int]:
    """Save the points for the dashboard's current week. Returns (csv, or None if already saved; mismatches)."""
    city = body("Citywide", None)
    first_resp = fetcher.post_json(site.book_url, city)
    first = first_resp.json()
    start, end = book_week(first)
    measures = sorted(book_counts(first, site, windows))
    name = run_name(end, windows, geos)
    out_csv = data_dir / site.dir / f"{name}.csv"
    if out_csv.exists():
        log("dashboard_map_skip", site=site.key, run=name, reason="already saved")
        return None, 0
    out_dir = raw_root / site.dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    gz = out_dir / "responses.jsonl.gz"
    if gz.exists():  # fetched earlier, CSV not written yet
        return build_week(gz, out_csv, site, windows)
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

    keep(site.book_url, city, first_resp.status_code, first)
    precincts = [g for g in geos_from_filter(call(site.geo_url, {}), set(site.geo_keys)) if g != "Citywide"]
    if geos is not None:
        precincts = [g for g in precincts if g in geos]
    log("dashboard_map_plan", site=site.key, run=name, precincts=len(precincts), measures=len(measures))
    n = 0
    for geo in precincts:
        book = call(site.book_url, body(geo, None))
        if book_week(book) != (start, end):
            raise ShapeError(f"book {geo}: week {book_week(book)}, Citywide says {(start, end)}")
        counts = book_counts(book, site, windows)
        for rid in measures:
            req = body(geo, rid)
            if counts.get(rid, 0) == 0 or json.dumps(req, sort_keys=True) + site.map_url in done:
                continue
            parse_points(call(site.map_url, req), f"{geo} {rid}")  # stop at once on a shape change
            n += 1
        log("dashboard_map_progress", site=site.key, precinct=geo, map_calls=n)
    last = fetcher.post_json(site.book_url, city).json()
    if book_week(last) != (start, end):
        raise ShapeError(f"the dashboard moved to {book_week(last)} during the run; nothing written")
    with jsonl.open("rb") as src, gzip.open(gz, "wb") as dst:
        dst.write(src.read())
    jsonl.unlink()
    log("dashboard_map_fetched", site=site.key, dir=str(out_dir), map_calls=n)
    return build_week(gz, out_csv, site, windows)


# --------------------------------------------------------------- building


def build_week(gz: Path, out_csv: Path, site: Site = TRAFFICSTAT,
               windows: tuple[str, ...] = ("WTD",)) -> tuple[Path, int]:
    """Write the CSV from one run's raw answers. Returns (csv, cells with more points than the table)."""
    with gzip.open(gz, "rt") as f:
        records = [json.loads(line) for line in f if line.strip()]
    counts: dict[str, dict[str, int]] = {}
    week: tuple[date, date] | None = None
    rows: list[dict[str, object]] = []
    for rec in records:
        req = {d["key"]: d["values"] for d in rec["request"]} if isinstance(rec["request"], list) else {}
        geo = (req.get("PRECINCTKey") or [""])[0]
        if rec["url"] == site.book_url:
            wk = book_week(rec["response"])
            if week not in (None, wk):
                raise ShapeError(f"{gz}: two weeks, {week} and {wk}")
            week = wk
            counts[geo] = book_counts(rec["response"], site, windows)
        elif rec["url"] == site.map_url:
            rid = req["RECORDID"][0]
            window, _, measure = rid.partition("_")
            for r in parse_points(rec["response"], f"{geo} {rid}"):
                rows.append({"site": site.key, "precinct": geo, "record_id": rid, "window": window,
                             "measure": measure, "fetched_on": rec["fetched_at"][:10], **r})
    if week is None:
        raise ShapeError(f"{gz}: no book answer")
    # Fewer points than the table is expected now and then (CompStat draws one point for an
    # incident with two victims) and is only logged. More points than the table, or points
    # for a cell the table does not have, is a problem: the run exits 1.
    mismatches = short = 0
    got: dict[tuple[str, str], int] = {}
    for r in rows:
        if r["incident"] == 1:
            k = (str(r["precinct"]), str(r["record_id"]))
            got[k] = got.get(k, 0) + int(r["point_count"])  # type: ignore[call-overload]
    for (geo, rid), total in sorted(got.items()):
        want = counts.get(geo, {}).get(rid)
        if want is not None and total < want:
            short += 1
            log("dashboard_map_short", site=site.key, precinct=geo, record_id=rid, points=total, table=want)
        elif want != total:
            mismatches += 1
            log("dashboard_map_mismatch", site=site.key, precinct=geo, record_id=rid, points=total, table=want)
    for r in rows:
        r["week_start"], r["week_end"] = week[0].isoformat(), week[1].isoformat()
    rows.sort(key=lambda r: (r["precinct"], r["record_id"], r["point"], r["incident"]))  # type: ignore[arg-type,return-value]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_csv.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out_csv)
    log("dashboard_map_built", site=site.key, path=str(out_csv), rows=len(rows), short=short, mismatches=mismatches)
    return out_csv, mismatches

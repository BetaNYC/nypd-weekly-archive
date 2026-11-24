"""Recount NYPD's weekly report numbers from NYC Open Data and compare them with ours.

``uv run nypd-archive recount`` asks NYC Open Data (Socrata, data.cityofnewyork.us) for the
records behind each weekly report, counts them the way the report seems to, and writes one row
per compared number to ``data/recount/<check>.csv``, plus ``data/recount/summary.csv`` with one
row per check, source, week and period. Nothing here changes ``data/<series>/``.

Field names and dataset ids are from each dataset's own metadata
(``https://data.cityofnewyork.us/api/views/<id>.json``), read 2026-11-24:

- Complaints, current year ``5uac-w243`` and historic ``qgea-i56i``: ``rpt_dt`` (report date),
  ``ky_cd`` (offense code), ``addr_pct_cd`` (precinct), ``jurisdiction_code`` (72 is
  "DEPT OF CORRECTIONS"), ``hadevelopt`` (NYCHA development).
- Crashes ``h9gi-nx95``: ``crash_date``, ``latitude``, ``longitude``,
  ``number_of_persons_injured``, ``number_of_persons_killed``. No precinct column.
- Police Precincts ``y76i-bdw7``: ``precinct``, ``the_geom`` (multipolygon), version 26c.
- 311 Service Requests ``erm2-nwe9``: ``created_date``, ``agency``, ``complaint_type``,
  ``descriptor``, ``police_precinct`` (text such as "Precinct 1").

Politeness: one request at a time, at least 2 seconds apart (``Fetcher``), aggregated SoQL
wherever a count will do. The only row-level pull is crash coordinates for one year, needed to
place crashes in precincts. Answers are cached under ``raw/recount/<run date>/`` (not in git),
so a rerun on the same day sends nothing.

An empty answer to a query that must have rows (a week of complaints, a week of 311 requests)
is an error, not a week of zeros: on 2026-11-24 the historic complaint dataset once answered a
valid week with an empty 200 response and the same query a minute later returned 385 rows.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from .log import log
from .net import Fetcher

BASE = "https://data.cityofnewyork.us/resource"
COMPLAINTS_CURRENT = "5uac-w243"
COMPLAINTS_HISTORIC = "qgea-i56i"
CRASHES = "h9gi-nx95"
PRECINCTS = "y76i-bdw7"
SR311 = "erm2-nwe9"
CALLS911 = "n2zq-pubd"

# CompStat's seven major felonies -> complaint offense code (ky_cd). NYPD's names in our files.
MAJOR = {"Murder": 101, "Rape": 104, "Robbery": 105, "Fel. Assault": 106, "Burglary": 107,
         "Gr. Larceny": 109, "G.L.A.": 110}
# The NYCHA Housing Bureau report names grand larceny of a motor vehicle "GLA".
HOUSING_MAJOR = {**{k: v for k, v in MAJOR.items() if k != "G.L.A."}, "GLA": 110}
DOC_JURISDICTION = 72  # "DEPT OF CORRECTIONS" in jurisdiction_code / juris_desc

CHECKS = ("compstat", "housing", "trafficstat", "fatalities", "qteam")
PAGE_SIZE = 50000
BIG_ROWS = 20000  # a comparison file with more rows than this is written gzip-compressed


class RecountError(RuntimeError):
    """Open Data answered something we cannot use. Never turned into zeros."""


# ---------------------------------------------------------------- SoQL and the client


def soql_url(dataset: str, fmt: str = "csv", **params: str | int) -> str:
    """A SoQL URL in the readable form the About page links to: spaces as %20, quotes kept."""
    parts = [f"${k}={quote(str(v), safe=chr(39) + '(),*=<>')}" for k, v in params.items()]
    return f"{BASE}/{dataset}.{fmt}?" + "&".join(parts)


def _in(codes: Iterable[int]) -> str:
    return "in(" + ",".join(str(c) for c in sorted(codes)) + ")"


class OpenData:
    """Socrata reads through a polite Fetcher, with a same-day file cache."""

    def __init__(self, fetcher: Fetcher, cache: Path | None = None, token: str | None = None) -> None:
        if fetcher.min_interval < 2.0:
            raise ValueError("Open Data requests must be at least 2 seconds apart")
        self.fetcher = fetcher
        self.cache = cache
        self.token = token
        self.sent = 0

    def _get(self, url: str) -> str:
        path = None
        if self.cache is not None:
            path = self.cache / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".txt")
            if path.exists():
                return path.read_text()
        headers = {"X-App-Token": self.token} if self.token else None
        resp = self.fetcher.get(url, headers=headers)
        self.sent += 1
        if resp.status_code != 200:
            raise RecountError(f"{url}: HTTP {resp.status_code}")
        text = resp.text
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        return text

    def rows(self, dataset: str, *, required: bool = True, **params: str | int) -> tuple[list[dict], str]:
        """Rows of one SoQL query, and its URL. ``required``: an empty answer is an error."""
        params.setdefault("limit", PAGE_SIZE)
        url = soql_url(dataset, **params)
        text = self._get(url)
        if text.lstrip().startswith("{"):
            raise RecountError(f"{url}: {text[:200]}")
        out = list(csv.DictReader(io.StringIO(text)))
        if required and not out:
            if self.cache is not None:  # do not keep an empty answer for the rest of the day
                (self.cache / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".txt")).unlink(missing_ok=True)
            raise RecountError(f"{url}: no rows, which cannot be right for this query")
        if len(out) >= int(params["limit"]):
            raise RecountError(f"{url}: hit the {params['limit']} row limit; page it")
        return out, url

    def all_rows(self, dataset: str, order: str, **params: str | int) -> list[dict]:
        """Every row, in pages of PAGE_SIZE, ordered by ``order`` so pages do not overlap."""
        out: list[dict] = []
        offset = 0
        while True:
            url = soql_url(dataset, order=order, limit=PAGE_SIZE, offset=offset, **params)
            text = self._get(url)
            if text.lstrip().startswith("{"):
                raise RecountError(f"{url}: {text[:200]}")
            page = list(csv.DictReader(io.StringIO(text)))
            out.extend(page)
            if len(page) < PAGE_SIZE:
                return out
            offset += PAGE_SIZE

    def geojson(self, dataset: str, **params: str | int) -> dict:
        params.setdefault("limit", 1000)
        return json.loads(self._get(soql_url(dataset, fmt="geojson", **params)))


# ---------------------------------------------------------------- our files


def _day(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def _read_csvs(paths: Iterable[Path]) -> Iterator[dict]:
    for p in sorted(paths):
        opener = gzip.open if p.suffix == ".gz" else open
        with opener(p, "rt", newline="") as fh:  # type: ignore[operator]
            yield from csv.DictReader(fh)


SOURCES = {  # CompStat numbers we hold, by who read them from NYPD's files
    "ours": ("compstat", "*.csv"),
    "greenman": ("compstat_greenman", "*.csv.gz"),
    "mcdermott": ("compstat_mcdermott", "*.csv.gz"),
}


@dataclass(frozen=True, order=True)
class Week:
    start: dt.date
    end: dt.date

    @property
    def spans_new_year(self) -> bool:
        return self.start.year != self.end.year


def _week(row: dict) -> Week | None:
    end = _day(row["week_end"])
    start = _day(row["week_start"]) if row.get("week_start") else end - dt.timedelta(days=6)
    if (end - start).days != 6:
        return None
    return Week(start, end)


def _num(v: str) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compstat_cells(root: Path, source: str) -> dict[Week, dict[tuple[str, str], float]]:
    """{week: {(geography key, measure): this-year week-to-date value}} for the seven majors.

    Geography key: the precinct number as text, "Citywide", or "DOC" (Department of Correction).
    """
    folder, pattern = SOURCES[source]
    out: dict[Week, dict[tuple[str, str], float]] = defaultdict(dict)
    for r in _read_csvs((root / "data" / folder).glob(pattern)):
        if r["section"] != "Crime Complaints" or r["period"] != "wtd" or r["measure"] not in MAJOR:
            continue
        wk = _week(r)
        if wk is None or str(wk.end.year) != r["year"]:
            continue
        v = _num(r["value"])
        if v is None:
            continue
        if r["precinct"]:
            key = str(int(float(r["precinct"])))
        elif r["geography"] == "Citywide":
            key = "Citywide"
        elif r["geography"] == "Department of Correction Citywide":
            key = "DOC"
        else:
            continue
        out[wk][(key, r["measure"])] = v
    return dict(out)


# ---------------------------------------------------------------- comparison output


@dataclass
class Compared:
    check: str
    source: str
    week_end: dt.date
    period: str
    geography: str
    measure: str
    report: float
    open_data: float

    def row(self) -> dict:
        return {"check": self.check, "source": self.source, "week_end": self.week_end.isoformat(),
                "period": self.period, "geography": self.geography, "measure": self.measure,
                "report": _fmt(self.report), "open_data": _fmt(self.open_data),
                "difference": _fmt(self.open_data - self.report)}


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


COMPARED_FIELDS = ["check", "source", "week_end", "period", "geography", "measure", "report", "open_data",
                   "difference"]
SUMMARY_FIELDS = ["check", "source", "week_end", "period", "compared", "exact", "report_total",
                  "open_data_total", "note", "query"]


def summarize(rows: list[Compared], queries: dict[tuple[str, dt.date], str],
              notes: dict[tuple[str, dt.date], str] | None = None) -> list[dict]:
    groups: dict[tuple[str, str, dt.date, str], list[Compared]] = defaultdict(list)
    for c in rows:
        groups[(c.check, c.source, c.week_end, c.period)].append(c)
    out = []
    for (check, source, week_end, period), cs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1],
                                                                                         kv[0][2], kv[0][3])):
        out.append({"check": check, "source": source, "week_end": week_end.isoformat(), "period": period,
                    "compared": len(cs), "exact": sum(1 for c in cs if c.report == c.open_data),
                    "report_total": _fmt(sum(c.report for c in cs)),
                    "open_data_total": _fmt(sum(c.open_data for c in cs)),
                    "note": (notes or {}).get((check, week_end), ""),
                    "query": queries.get((check, week_end), "")})
    return out


def write_csv(path: Path, fields: list[str], rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    data = buf.getvalue().encode()
    if path.suffix == ".gz":  # mtime 0, so the same rows give the same bytes
        data = gzip.compress(data, mtime=0)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---------------------------------------------------------------- complaints: CompStat, citywide, DOC


def complaint_dataset(wk: Week, current_from: dt.date, current_to: dt.date,
                      historic_to: dt.date) -> str | None:
    """Which complaint dataset holds this whole week by report date, or None."""
    if wk.spans_new_year:
        return None  # we do not know whether NYPD's week count for such a week starts on January 1
    if current_from <= wk.start and wk.end <= current_to:
        return COMPLAINTS_CURRENT
    if wk.end <= historic_to:
        return COMPLAINTS_HISTORIC
    return None


def complaint_week(od: OpenData, dataset: str, wk: Week) -> tuple[list[dict], str]:
    return od.rows(
        dataset,
        select="addr_pct_cd,jurisdiction_code,ky_cd,count(*) AS n",
        where=f"rpt_dt between '{wk.start}' and '{wk.end}' and ky_cd {_in(MAJOR.values())}",
        group="addr_pct_cd,jurisdiction_code,ky_cd",
        order="addr_pct_cd,jurisdiction_code,ky_cd",
    )


def complaint_counts(rows: list[dict]) -> dict[tuple[str, str], float]:
    """{(geography key, measure): count} from one week's grouped complaint rows."""
    by_code = {v: k for k, v in MAJOR.items()}
    out: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        code = int(float(r["ky_cd"]))
        if code not in by_code:
            raise RecountError(f"unexpected ky_cd {r['ky_cd']}")
        m, n = by_code[code], float(r["n"])
        out[("Citywide", m)] += n
        if r.get("addr_pct_cd"):
            out[(str(int(float(r["addr_pct_cd"]))), m)] += n
        if r.get("jurisdiction_code") and int(float(r["jurisdiction_code"])) == DOC_JURISDICTION:
            out[("DOC", m)] += n
    return dict(out)


def compare_complaints(ours: dict[tuple[str, str], float], od: dict[tuple[str, str], float], source: str,
                       wk: Week) -> list[Compared]:
    out = []
    for (geo, measure), v in sorted(ours.items()):
        check = {"Citywide": "citywide", "DOC": "correction"}.get(geo, "compstat")
        out.append(Compared(check, source, wk.end, "wtd", geo, measure, v, od.get((geo, measure), 0.0)))
    return out


def complaint_coverage(od: OpenData) -> tuple[dt.date, dt.date, dt.date]:
    cur, _ = od.rows(COMPLAINTS_CURRENT, select="min(rpt_dt) AS lo,max(rpt_dt) AS hi")
    hist, _ = od.rows(COMPLAINTS_HISTORIC, select="max(rpt_dt) AS hi")
    return _day(cur[0]["lo"]), _day(cur[0]["hi"]), _day(hist[0]["hi"])


def run_complaints(od: OpenData, root: Path) -> tuple[list[Compared], list[dict]]:
    cur_lo, cur_hi, hist_hi = complaint_coverage(od)
    log("recount_coverage", current=f"{cur_lo}..{cur_hi}", historic_to=str(hist_hi))
    cells = {s: compstat_cells(root, s) for s in SOURCES}
    weeks = sorted({wk for by in cells.values() for wk in by})
    compared: list[Compared] = []
    queries: dict[tuple[str, dt.date], str] = {}
    notes: dict[tuple[str, dt.date], str] = {}
    for wk in weeks:
        ds = complaint_dataset(wk, cur_lo, cur_hi, hist_hi)
        if ds is None:
            continue
        rows, url = complaint_week(od, ds, wk)
        counts = complaint_counts(rows)
        log("recount_week", check="compstat", week_end=wk.end.isoformat(), dataset=ds, sent=od.sent)
        for check in ("compstat", "citywide", "correction"):
            queries[(check, wk.end)] = url
            notes[(check, wk.end)] = ds
        for source, by in cells.items():
            if wk in by:
                compared.extend(compare_complaints(by[wk], counts, source, wk))
    return compared, summarize(compared, queries, notes)


# ---------------------------------------------------------------- NYCHA housing, by development


def norm_name(s: str) -> str:
    return " ".join(s.upper().split())


def housing_cells(root: Path) -> dict[Week, dict[tuple[str, str], float]]:
    """{week: {(development, measure): this-year week-to-date value}} from the PSA "WTD" sheets."""
    out: dict[Week, dict[tuple[str, str], float]] = defaultdict(dict)
    for r in _read_csvs((root / "data" / "housing").glob("*.csv")):
        if (r["period"] != "wtd" or not r["sheet"].startswith("WTD") or r["geography"].endswith("Totals")
                or r["measure"] not in HOUSING_MAJOR):
            continue
        wk = _week(r)
        v = _num(r["value"])
        if wk is None or v is None or str(wk.end.year) != r["year"]:
            continue
        out[wk][(norm_name(r["geography"]), r["measure"])] = v
    return dict(out)


def housing_names(od: OpenData, dataset: str) -> set[str]:
    rows, _ = od.rows(dataset, select="hadevelopt,count(*) AS n", where="hadevelopt IS NOT NULL",
                      group="hadevelopt", order="hadevelopt")
    return {norm_name(r["hadevelopt"]) for r in rows if r.get("hadevelopt")}


def housing_week(od: OpenData, dataset: str, wk: Week) -> tuple[list[dict], str]:
    return od.rows(
        dataset,
        select="hadevelopt,ky_cd,count(*) AS n",
        where=(f"rpt_dt between '{wk.start}' and '{wk.end}' and ky_cd {_in(MAJOR.values())} "
               "and hadevelopt IS NOT NULL"),
        group="hadevelopt,ky_cd",
        order="hadevelopt,ky_cd",
    )


def compare_housing(ours: dict[tuple[str, str], float], rows: list[dict], known: set[str],
                    wk: Week) -> list[Compared]:
    """Developments whose name Open Data uses somewhere; a name it never uses is not compared."""
    by_code = {v: k for k, v in HOUSING_MAJOR.items()}
    od: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        od[(norm_name(r["hadevelopt"]), by_code[int(float(r["ky_cd"]))])] += float(r["n"])
    return [Compared("housing", "ours", wk.end, "wtd", name, m, v, od.get((name, m), 0.0))
            for (name, m), v in sorted(ours.items()) if name in known]


def run_housing(od: OpenData, root: Path) -> tuple[list[Compared], list[dict], dict]:
    cur_lo, cur_hi, hist_hi = complaint_coverage(od)
    cells = housing_cells(root)
    names: dict[str, set[str]] = {}
    compared: list[Compared] = []
    queries: dict[tuple[str, dt.date], str] = {}
    notes: dict[tuple[str, dt.date], str] = {}
    ours_names: set[str] = set()
    for wk in sorted(cells):
        ds = complaint_dataset(wk, cur_lo, cur_hi, hist_hi)
        if ds is None:
            continue
        if ds not in names:
            names[ds] = housing_names(od, ds)
        rows, url = housing_week(od, ds, wk)
        queries[("housing", wk.end)] = url
        week_names = {n for n, _ in cells[wk]}
        ours_names |= week_names
        missing = len(week_names - names[ds])
        notes[("housing", wk.end)] = f"{ds}; {missing} of {len(week_names)} development names not in Open Data"
        compared.extend(compare_housing(cells[wk], rows, names[ds], wk))
    all_known = set().union(*names.values()) if names else set()
    info = {"developments": len(ours_names), "not_in_open_data": sorted(ours_names - all_known)}
    return compared, summarize(compared, queries, notes), info


# ---------------------------------------------------------------- crashes: TrafficStat and fatalities


def windows(week_end: dt.date, week_start: dt.date | None = None) -> dict[str, tuple[dt.date, dt.date]]:
    start = week_start or week_end - dt.timedelta(days=6)
    return {"wtd": (start, week_end), "7d": (week_end - dt.timedelta(days=6), week_end),
            "28d": (week_end - dt.timedelta(days=27), week_end), "ytd": (dt.date(week_end.year, 1, 1), week_end)}


TRAFFIC_MEASURES = {("Collisions", "Collisions"): "crashes", ("Collisions", "Total Injuries"): "injured",
                    ("Fatalities", "Total Fatalities"): "killed"}


def trafficstat_cells(root: Path) -> dict[Week, dict[tuple[str, str, str], float]]:
    """{week: {(geography key, measure, period): this-year value}} from TrafficStat page 1."""
    out: dict[Week, dict[tuple[str, str, str], float]] = defaultdict(dict)
    for r in _read_csvs((root / "data" / "trafficstat").glob("*.csv")):
        m = TRAFFIC_MEASURES.get((r["section"], r["measure"]))
        if r["sheet"] != "Page1" or m is None or r["period"] not in ("wtd", "28d", "ytd"):
            continue
        wk = _week(r)
        v = _num(r["value"])
        if wk is None or v is None or str(wk.end.year) != r["year"]:
            continue
        if r["precinct"]:
            key = str(int(float(r["precinct"])))
        elif r["geography"] == "Citywide":
            key = "Citywide"
        else:
            continue
        out[wk][(key, r["measure"], r["period"])] = v
    return dict(out)


def fatality_cells(root: Path) -> dict[dt.date, dict[tuple[str, str], float]]:
    """{week end: {(geography key, period): this-year deaths}} from the traffic fatalities files."""
    out: dict[dt.date, dict[tuple[str, str], float]] = defaultdict(dict)
    for r in _read_csvs((root / "data" / "fatalities").glob("*.csv")):
        end = _day(r["week_end"])
        v = _num(r["value"])
        if v is None or str(end.year) != r["year"] or r["period"] not in ("7d", "28d", "ytd"):
            continue
        if r["precinct"]:
            key = str(int(float(r["precinct"])))
        elif r["geography"] == "Citywide":
            key = "Citywide"
        else:
            continue
        out[end][(key, r["period"])] = v
    return dict(out)


def crash_days(od: OpenData, start: dt.date, end: dt.date) -> tuple[dict[dt.date, tuple[float, float, float]], str]:
    rows, url = od.rows(
        CRASHES,
        select=("crash_date,count(*) AS crashes,sum(number_of_persons_injured) AS injured,"
                "sum(number_of_persons_killed) AS killed"),
        where=f"crash_date between '{start}' and '{end}'",
        group="crash_date", order="crash_date",
    )
    out = {_day(r["crash_date"]): (float(r["crashes"]), float(r["injured"] or 0), float(r["killed"] or 0))
           for r in rows}
    return out, url


def sum_days(days: dict[dt.date, tuple[float, float, float]], lo: dt.date, hi: dt.date) -> tuple[float, ...]:
    tot = [0.0, 0.0, 0.0]
    for d, vals in days.items():
        if lo <= d <= hi:
            for i in range(3):
                tot[i] += vals[i]
    return tuple(tot)


# Point in polygon, for placing a crash in a precinct. GeoJSON: [lon, lat]; rings closed.


def _in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


@dataclass
class Area:
    key: str
    polygons: list[list[list[list[float]]]]  # multipolygon: polygons of rings, first ring outer
    bbox: tuple[float, float, float, float]

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        for poly in self.polygons:
            if _in_ring(x, y, poly[0]) and not any(_in_ring(x, y, hole) for hole in poly[1:]):
                return True
        return False


def precinct_areas(gj: dict) -> list[Area]:
    out = []
    for f in gj["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        xs = [p[0] for poly in polys for ring in poly for p in ring]
        ys = [p[1] for poly in polys for ring in poly for p in ring]
        out.append(Area(str(int(float(f["properties"]["precinct"]))), polys, (min(xs), min(ys), max(xs), max(ys))))
    if len(out) < 70:
        raise RecountError(f"Police Precincts map has {len(out)} precincts")
    return out


def place(areas: list[Area], lat: float, lon: float) -> str | None:
    hits = [a.key for a in areas if a.contains(lon, lat)]
    return hits[0] if len(hits) == 1 else None


@dataclass
class Crash:
    day: dt.date
    precinct: str | None  # None: no usable coordinates, or outside every precinct
    injured: float
    killed: float


def crash_points(od: OpenData, areas: list[Area], start: dt.date, end: dt.date) -> list[Crash]:
    rows = od.all_rows(
        CRASHES, order="collision_id",
        select="collision_id,crash_date,latitude,longitude,number_of_persons_injured,number_of_persons_killed",
        where=f"crash_date between '{start}' and '{end}'",
    )
    out = []
    for r in rows:
        lat, lon = _num(r.get("latitude", "")), _num(r.get("longitude", ""))
        pct = place(areas, lat, lon) if lat and lon else None
        out.append(Crash(_day(r["crash_date"]), pct, _num(r.get("number_of_persons_injured", "")) or 0.0,
                         _num(r.get("number_of_persons_killed", "")) or 0.0))
    return out


def precinct_sums(crashes: list[Crash], lo: dt.date, hi: dt.date) -> dict[str, tuple[float, float, float]]:
    out: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for c in crashes:
        if c.precinct and lo <= c.day <= hi:
            s = out[c.precinct]
            s[0] += 1
            s[1] += c.injured
            s[2] += c.killed
    return {k: (v[0], v[1], v[2]) for k, v in out.items()}


MEASURE_INDEX = {"Collisions": 0, "Total Injuries": 1, "Total Fatalities": 2}


def run_crashes(od: OpenData, root: Path, per_precinct_year: int | None) -> tuple[list[Compared], list[dict], dict]:
    ts = trafficstat_cells(root)
    fat = fatality_cells(root)
    ends = [wk.end for wk in ts] + list(fat)
    if not ends:
        return [], [], {}
    lo = dt.date(min(ends).year, 1, 1)
    hi = max(ends)
    days, days_url = crash_days(od, lo, hi)
    newest = max(days)
    compared: list[Compared] = []
    queries: dict[tuple[str, dt.date], str] = {}
    info: dict = {"crash_data_newest": newest.isoformat()}

    crashes: list[Crash] = []
    if per_precinct_year:
        areas = precinct_areas(od.geojson(PRECINCTS))
        crashes = crash_points(od, areas, dt.date(per_precinct_year, 1, 1),
                               min(hi, dt.date(per_precinct_year, 12, 31)))
        placed = sum(1 for c in crashes if c.precinct)
        info.update(points=len(crashes), placed=placed)

    for wk, cells in sorted(ts.items()):
        if wk.spans_new_year or wk.end > newest:
            continue
        queries[("trafficstat", wk.end)] = days_url
        w = windows(wk.end, wk.start)
        sums = {p: sum_days(days, *w[p]) for p in ("wtd", "28d", "ytd")}
        by_pct = {}
        if per_precinct_year == wk.end.year:
            by_pct = {p: precinct_sums(crashes, *w[p]) for p in ("wtd", "28d", "ytd")}
        for (geo, measure, period), v in sorted(cells.items()):
            i = MEASURE_INDEX[measure]
            if geo == "Citywide":
                compared.append(Compared("trafficstat", "ours", wk.end, period, geo, measure, v, sums[period][i]))
            elif by_pct:
                got = by_pct[period].get(geo, (0.0, 0.0, 0.0))[i]
                compared.append(Compared("trafficstat_precinct", "ours", wk.end, period, geo, measure, v, got))

    for end, cells in sorted(fat.items()):
        if end > newest:
            continue
        queries[("fatalities", end)] = days_url
        w = windows(end)
        by_pct = ({p: precinct_sums(crashes, *w[p]) for p in ("7d", "28d", "ytd")}
                  if per_precinct_year == end.year else {})
        for (geo, period), v in sorted(cells.items()):
            if geo == "Citywide":
                got = sum_days(days, *w[period])[2]
            elif by_pct:
                got = by_pct[period].get(geo, (0.0, 0.0, 0.0))[2]
            else:
                continue
            compared.append(Compared("fatalities", "ours", end, period, geo, "Traffic Fatalities", v, got))
    return compared, summarize(compared, queries), info


# ---------------------------------------------------------------- quality of life, from 311


def _is_double(descriptor: str) -> bool:
    return descriptor.startswith("Double Parked")


# Each 311-only line of the report -> a test on (complaint_type, descriptor) of an NYPD 311 request.
# Worked out on 2026-11-24 from the 1st Precinct's report for October 19-25, 2026, where all
# twelve matched exactly. Then, over all 78 precincts that week, "Noise - Outside" matched 38 times
# with Street/Sidewalk alone and 76 times with Noise - Park added; "Noise - Business Related"
# matched 56 times with Commercial alone and 76 with Noise - House of Worship added. The same
# held for the 28-day column (10 -> 76, 36 -> 76), which was not used to choose.
# Lines marked * in the report also count 911 calls; they are not here.
QOL_311 = {
    "Total 311 Calls": lambda t, d: True,
    "Total Noise": lambda t, d: t.startswith("Noise"),
    "Noise - Residential": lambda t, d: t == "Noise - Residential",
    "Noise - Outside": lambda t, d: t in ("Noise - Street/Sidewalk", "Noise - Park"),
    "Noise - Business Related": lambda t, d: t in ("Noise - Commercial", "Noise - House of Worship"),
    "Noise - Vehicle": lambda t, d: t == "Noise - Vehicle",
    "Illegal Parking#": lambda t, d: t == "Illegal Parking" and not _is_double(d),
    "Double Parking": lambda t, d: t == "Illegal Parking" and _is_double(d),
    "Blocked Driveway": lambda t, d: t == "Blocked Driveway",
    "Abandoned Vehicles": lambda t, d: t == "Abandoned Vehicle",
    "Drinking": lambda t, d: t == "Drinking",
    "Urinating in Public": lambda t, d: t == "Urinating in Public",
}


def qteam_cells(root: Path) -> dict[Week, dict[tuple[str, str, str], float]]:
    out: dict[Week, dict[tuple[str, str, str], float]] = defaultdict(dict)
    for r in _read_csvs((root / "data" / "qteam").glob("*.csv")):
        if r["measure"] not in QOL_311 or r["period"] not in ("wtd", "28d", "ytd") or not r["precinct"]:
            continue
        wk = _week(r)
        v = _num(r["value"])
        if wk is None or v is None or str(wk.end.year) != r["year"]:
            continue
        out[wk][(str(int(float(r["precinct"]))), r["measure"], r["period"])] = v
    return dict(out)


def sr311_window(od: OpenData, lo: dt.date, hi: dt.date) -> tuple[list[dict], str]:
    """NYPD 311 requests by precinct and complaint type, plus the double-parking descriptors.

    Two light queries rather than one grouped by descriptor: the grouped one timed out or
    answered 503 for a month of data on 2026-11-24. The first query's URL is returned.
    """
    when = f"agency='NYPD' and created_date between '{lo}T00:00:00' and '{hi}T23:59:59.999'"
    rows, url = od.rows(SR311, select="police_precinct,complaint_type,count(*) AS n", where=when,
                        group="police_precinct,complaint_type", order="police_precinct,complaint_type")
    double, _ = od.rows(SR311, required=False, select="police_precinct,count(*) AS n",
                        where=(f"{when} and complaint_type='Illegal Parking' "
                               "and starts_with(descriptor, 'Double Parked')"),
                        group="police_precinct", order="police_precinct")
    out = [{**r, "descriptor": ""} for r in rows]
    for r in double:  # move double parking out of the plain Illegal Parking count
        out.append({**r, "complaint_type": "Illegal Parking", "descriptor": "Double Parked"})
        out.append({**r, "complaint_type": "Illegal Parking", "descriptor": "", "n": str(-float(r["n"]))})
    return out, url


def chunks(lo: dt.date, hi: dt.date, days: int = 28) -> list[tuple[dt.date, dt.date]]:
    """Split a window into pieces of at most ``days`` days, ending at ``hi``.

    311 timed out on a year-to-date query and on a single month (2026-11-24); 28 days worked.
    """
    out = []
    end = hi
    while end >= lo:
        start = max(lo, end - dt.timedelta(days=days - 1))
        out.append((start, end))
        end = start - dt.timedelta(days=1)
    return sorted(out)


def qol_counts(rows: list[dict]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        p = (r.get("police_precinct") or "").removeprefix("Precinct ").strip()
        if not p.isdigit():
            continue
        t, d, n = r.get("complaint_type") or "", r.get("descriptor") or "", float(r["n"])
        for measure, test in QOL_311.items():
            if test(t, d):
                out[(str(int(p)), measure)] += n
    return dict(out)


def run_qteam(od: OpenData, root: Path) -> tuple[list[Compared], list[dict]]:
    compared: list[Compared] = []
    queries: dict[tuple[str, dt.date], str] = {}
    for wk, cells in sorted(qteam_cells(root).items()):
        if wk.spans_new_year:
            continue
        w = windows(wk.end, wk.start)
        counts = {}
        for period in ("wtd", "28d", "ytd"):
            total: dict[tuple[str, str], float] = defaultdict(float)
            for lo, hi in chunks(*w[period]):
                rows, url = sr311_window(od, lo, hi)
                for k, v in qol_counts(rows).items():
                    total[k] += v
                if period == "wtd":
                    queries[("qteam", wk.end)] = url
            counts[period] = dict(total)
        for (pct, measure, period), v in sorted(cells.items()):
            compared.append(Compared("qteam", "ours", wk.end, period, pct, measure, v,
                                     counts[period].get((pct, measure), 0.0)))
    return compared, summarize(compared, queries)


# ---------------------------------------------------------------- entry point


def recount(root: Path, fetcher: Fetcher, checks: Iterable[str] = CHECKS, out: Path | None = None,
            per_precinct_year: int | None = None) -> dict:
    """Run the checks; write data/recount/*.csv; return what to log."""
    out = out or root / "data" / "recount"
    today = dt.date.today().isoformat()
    od = OpenData(fetcher, cache=root / "raw" / "recount" / today, token=os.environ.get("SOCRATA_APP_TOKEN"))
    checks = list(checks)
    compared: list[Compared] = []
    summary: list[dict] = []
    info: dict = {"run_on": today}
    if "compstat" in checks:
        c, s = run_complaints(od, root)
        compared += c
        summary += s
    if "housing" in checks:
        c, s, i = run_housing(od, root)
        compared += c
        summary += s
        info["housing"] = i
    if "trafficstat" in checks or "fatalities" in checks:
        c, s, i = run_crashes(od, root, per_precinct_year)
        compared += c
        summary += s
        info["crashes"] = i
    if "qteam" in checks:
        c, s = run_qteam(od, root)
        compared += c
        summary += s
    by_check: dict[str, list[dict]] = defaultdict(list)
    for c in compared:
        by_check[c.check].append(c.row())
    for check, rows in by_check.items():
        big = len(rows) > BIG_ROWS  # gzip, as data/compstat_mcdermott/ does
        stale = out / (f"{check}.csv" if big else f"{check}.csv.gz")
        if stale.exists():
            stale.unlink()
        write_csv(out / (f"{check}.csv.gz" if big else f"{check}.csv"), COMPARED_FIELDS, rows)
    summary_path = out / "summary.csv"
    keep = []
    if summary_path.exists():  # keep other checks' rows when only some checks ran
        ran = {s["check"] for s in summary}
        with summary_path.open(newline="") as fh:
            keep = [r for r in csv.DictReader(fh) if r["check"] not in ran]
    write_csv(summary_path, SUMMARY_FIELDS, sorted(keep + summary, key=lambda r: (r["check"], r["source"],
                                                                                    r["week_end"], r["period"])))
    run_path = out / "run.json"
    merged = json.loads(run_path.read_text()) if run_path.exists() else {}
    merged.update({k: v for k, v in info.items() if k != "run_on"})
    merged.setdefault("run_on", {})
    for check in checks:
        merged["run_on"][check] = today
    run_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    info["requests"] = od.sent
    return info

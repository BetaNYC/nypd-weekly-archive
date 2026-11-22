"""Dashboard map parsing against answers observed in the browser on 2026-11-22 (precinct 075)."""

from __future__ import annotations

import copy
import csv
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from nypd_weekly_archive.dashboard import BOOK_URL, GEO_FILTER_URL, ShapeError, body
from nypd_weekly_archive.dashboard_map import MAP_URL, book_counts, book_week, parse_points, save_week

from .conftest import make_fetcher


def load(fixtures: Path, folder: str, name: str):
    return json.loads((fixtures / folder / name).read_text())


def test_book_week_and_counts(fixtures: Path) -> None:
    book = load(fixtures, "dashboard", "book_citywide.json")
    assert book_week(book) == (date(2026, 11, 9), date(2026, 11, 15))
    counts = book_counts(book)
    assert len(counts) == 30
    assert counts["WTD_COLLISIONS_Collisions"] == 1459 and counts["WTD_COLLISIONS_Fat E-bike"] == 0


def test_points_one_row_per_crash(fixtures: Path) -> None:
    resp = load(fixtures, "dashboard_map", "map_075_WTD_collisions.json")
    rows = parse_points(resp, "075")
    assert len(resp) == 41 and len(rows) == 43  # the book says 43 collisions
    assert {r["date"] for r in rows} <= {f"2026-11-{d:02d}" for d in range(9, 16)}
    two = [r for r in rows if r["latitude"] == "40.6803917"]
    assert [(r["crash"], r["point_count"], r["crash_type"], r["date"], r["hour"]) for r in two] == [
        (1, 2, "UNKNOWN", "2026-11-15", 0), (2, 2, "UNKNOWN", "2026-11-11", 0)]


def test_fatalities_count_deaths_not_crashes(fixtures: Path) -> None:
    resp = load(fixtures, "dashboard_map", "map_075_SPLYYTD_fatalities.json")
    rows = parse_points(resp, "075")
    assert sum(p["metric"] for p in resp) == 7  # the book's 2025 year-to-date deaths
    two = [r for r in rows if r["point_count"] == 2]
    assert len(two) == 1  # 2 deaths, 1 crash listed
    assert two[0]["crash_type"] == "LEFT TURN (OPP DIR)"  # full text from title=, not "LEFT TURN (OPP D.."
    assert (two[0]["date"], two[0]["hour"]) == ("2025-09-02", 20)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d[0].update(newField=1),
        lambda d: d[0].update(value="40.68 -73.87"),
        lambda d: d[0].update(metric="2"),
        lambda d: d[0].update(tooltipHtml=d[0]["tooltipHtml"].replace("12AM", "midnight")),
        lambda d: d[0].update(metric=d[0]["metric"] + 1),  # heading no longer matches the count
        lambda d: d.extend([d[0]] * 5000),
    ],
)
def test_shape_change_fails_loud(fixtures: Path, mutate) -> None:
    d = load(fixtures, "dashboard_map", "map_075_WTD_collisions.json")
    mutate(d)
    with pytest.raises(ShapeError):
        parse_points(d, "075")


def _book(fixtures: Path, collisions: int, week: str = "11/09/26 - 11/15/26") -> list:
    """The Citywide book, with every week-to-date cell 0 except Collisions."""
    b = copy.deepcopy(load(fixtures, "dashboard", "book_citywide.json"))
    b[0]["title"] = b[0]["title"].replace("11/09/26 - 11/15/26", week)
    for row in b:
        for c in row.get("columns") or []:
            if (c.get("filterValue") or "").startswith("WTD_"):
                c["value"] = str(collisions) if c["filterValue"] == "WTD_COLLISIONS_Collisions" else "0"
    return b


def _site(fixtures: Path, calls: list, precinct_collisions: int = 43, last_week: str = "11/09/26 - 11/15/26"):
    city = load(fixtures, "dashboard", "book_citywide.json")
    points = load(fixtures, "dashboard_map", "map_075_WTD_collisions.json")
    seen_city = []

    def handler(req: httpx.Request) -> httpx.Response:
        url, b = str(req.url), json.loads(req.content)
        calls.append((url, b))
        if url == GEO_FILTER_URL:
            return httpx.Response(200, json=load(fixtures, "dashboard", "filter_geos.json"))
        if url == BOOK_URL:
            if b[0]["values"] == ["Citywide"]:
                seen_city.append(1)
                return httpx.Response(200, json=city if len(seen_city) == 1 else _book(fixtures, 1459, last_week))
            return httpx.Response(200, json=_book(fixtures, precinct_collisions))
        assert url == MAP_URL and b == body("075", "WTD_COLLISIONS_Collisions")
        return httpx.Response(200, json=points)

    return handler


def test_save_week_end_to_end(fixtures: Path, tmp_path: Path) -> None:
    calls: list = []
    out, mismatches = save_week(make_fetcher(_site(fixtures, calls)), tmp_path / "raw", tmp_path / "data",
                                geos=["075"])
    # Citywide book, precincts, 075 book, one map call (every other cell is 0), Citywide book again
    assert [u.rsplit("/", 2)[-2][:8] for u, _ in calls] == ["b805fa11", "ec81a9e0", "b805fa11", "7d9951ab", "b805fa11"]
    assert out == tmp_path / "data" / "trafficstat_dashboard_map" / "2026-11-15.csv" and mismatches == 0
    assert (tmp_path / "raw" / "trafficstat_dashboard_map" / "2026-11-15" / "responses.jsonl.gz").exists()
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 43
    assert rows[0]["week_start"] == "2026-11-09" and rows[0]["precinct"] == "075"
    assert rows[0]["metric"] == "COLLISIONS_Collisions"

    # Saturday's run finds the week saved and asks only for the book.
    calls.clear()
    assert save_week(make_fetcher(_site(fixtures, calls)), tmp_path / "raw", tmp_path / "data") == (None, 0)
    assert len(calls) == 1


def test_count_mismatch_is_reported(fixtures: Path, tmp_path: Path) -> None:
    calls: list = []
    _, mismatches = save_week(make_fetcher(_site(fixtures, calls, precinct_collisions=44)), tmp_path / "raw",
                              tmp_path / "data", geos=["075"])
    assert mismatches == 1


def test_week_change_during_run_writes_nothing(fixtures: Path, tmp_path: Path) -> None:
    calls: list = []
    with pytest.raises(ShapeError, match="moved"):
        save_week(make_fetcher(_site(fixtures, calls, last_week="11/16/26 - 11/22/26")), tmp_path / "raw",
                  tmp_path / "data", geos=["075"])
    assert not (tmp_path / "data" / "trafficstat_dashboard_map").exists()

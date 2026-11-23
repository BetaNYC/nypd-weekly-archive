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
from nypd_weekly_archive.dashboard_map import MAP_URL, SITES, book_counts, book_week, parse_points, save_week

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
    assert [(r["incident"], r["point_count"], r["label"], r["date"], r["hour"]) for r in two] == [
        (1, 2, "UNKNOWN", "2026-11-15", 0), (2, 2, "UNKNOWN", "2026-11-11", 0)]


def test_fatalities_count_deaths_not_crashes(fixtures: Path) -> None:
    resp = load(fixtures, "dashboard_map", "map_075_SPLYYTD_fatalities.json")
    rows = parse_points(resp, "075")
    assert sum(p["metric"] for p in resp) == 7  # the book's 2025 year-to-date deaths
    two = [r for r in rows if r["point_count"] == 2]
    assert len(two) == 1  # 2 deaths, 1 crash listed
    assert two[0]["label"] == "LEFT TURN (OPP DIR)"  # full text from title=, not "LEFT TURN (OPP D.."
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
    assert out == tmp_path / "data" / "trafficstat_dashboard_map" / "2026-11-15_only-075.csv" and mismatches == 0
    assert (tmp_path / "raw" / "trafficstat_dashboard_map" / "2026-11-15_only-075" / "responses.jsonl.gz").exists()
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 43
    assert rows[0]["week_start"] == "2026-11-09" and rows[0]["precinct"] == "075"
    assert rows[0]["measure"] == "COLLISIONS_Collisions" and rows[0]["window"] == "WTD"
    assert rows[0]["site"] == "trafficstat" and rows[0]["extra_labels"] == ""

    # Saturday's run finds the week saved and asks only for the book.
    calls.clear()
    assert save_week(make_fetcher(_site(fixtures, calls)), tmp_path / "raw", tmp_path / "data",
                     geos=["075"]) == (None, 0)
    assert len(calls) == 1


@pytest.mark.parametrize(("table", "problems"), [(44, 0), (42, 1)])
def test_count_check(fixtures: Path, tmp_path: Path, table: int, problems: int) -> None:
    """43 points: a table of 44 is only logged, a table of 42 is a problem."""
    calls: list = []
    _, mismatches = save_week(make_fetcher(_site(fixtures, calls, precinct_collisions=table)), tmp_path / "raw",
                              tmp_path / "data", geos=["075"])
    assert mismatches == problems


def test_week_change_during_run_writes_nothing(fixtures: Path, tmp_path: Path) -> None:
    calls: list = []
    with pytest.raises(ShapeError, match="moved"):
        save_week(make_fetcher(_site(fixtures, calls, last_week="11/16/26 - 11/22/26")), tmp_path / "raw",
                  tmp_path / "data", geos=["075"])
    assert not (tmp_path / "data" / "trafficstat_dashboard_map").exists()


# --------------------------------------------------------------- CompStat 2.0
# Robbery and murder answers for 075 observed on 2026-11-22. No sex-crime answers are kept in this repository.

CS = SITES["compstat"]


def test_compstat_book(fixtures: Path) -> None:
    book = load(fixtures, "compstat_map", "book-075.json")
    assert book_week(book) == (date(2026, 11, 9), date(2026, 11, 15))
    counts = book_counts(book, CS)
    assert len(counts) == 19 and counts["WTD_COMPLAINTS_Robbery"] == 11
    ytd = book_counts(book, CS, ("YTD", "SPLYYTD"))
    assert len(ytd) == 38 and all(k.startswith(("YTD_", "SPLYYTD_")) for k in ytd)
    with pytest.raises(ShapeError, match="unknown record id"):
        book_counts(load(fixtures, "dashboard", "book_citywide.json"), CS)  # a TrafficStat book


def test_compstat_points(fixtures: Path) -> None:
    rob = parse_points(load(fixtures, "compstat_map", "map-075-wtd-robbery.json"), "075")
    assert len(rob) == 11 and rob[0]["label"] == "DWELLING" and (rob[0]["date"], rob[0]["hour"]) == ("2026-11-13", 15)
    murder = parse_points(load(fixtures, "compstat_map", "map-075-ytd-murder.json"), "075")
    assert len(murder) == 10 and {r["label"] for r in murder} == {"HOMICIDE"}


def test_second_label_is_kept(fixtures: Path) -> None:
    d = load(fixtures, "compstat_map", "map-075-wtd-robbery.json")
    d[0]["tooltipHtml"] = d[0]["tooltipHtml"].replace(
        "<span>DWELLING</span>", "<span>DWELLING</span></div><div><span>SECOND LABEL</span>")
    r = parse_points(d, "075")[0]
    assert (r["label"], r["extra_labels"], r["date"]) == ("DWELLING", "SECOND LABEL", "2026-11-13")


def test_label_with_no_date_fails(fixtures: Path) -> None:
    d = load(fixtures, "compstat_map", "map-075-wtd-robbery.json")
    d[0]["tooltipHtml"] = d[0]["tooltipHtml"].replace("<span>11/13/26 3PM</span>", "")
    with pytest.raises(ShapeError):
        parse_points(d, "075")


def test_compstat_save_with_year_to_date_windows(fixtures: Path, tmp_path: Path) -> None:
    """Every cell is answered with the murder points; counts differ from the table, which is logged."""
    calls: list = []
    murder = load(fixtures, "compstat_map", "map-075-ytd-murder.json")

    def handler(req: httpx.Request) -> httpx.Response:
        url, b = str(req.url), json.loads(req.content)
        calls.append(b)
        if url == CS.geo_url:
            return httpx.Response(200, json=load(fixtures, "compstat_map", "precinct-filter.json"))
        if url == CS.book_url:
            name = "book-citywide.json" if b[0]["values"] == ["Citywide"] else "book-075.json"
            return httpx.Response(200, json=load(fixtures, "compstat_map", name))
        assert url == CS.map_url and b[2]["values"][0].startswith(("YTD_", "SPLYYTD_"))
        return httpx.Response(200, json=murder)

    out, mismatches = save_week(make_fetcher(handler), tmp_path / "raw", tmp_path / "data", geos=["075"],
                                site=CS, windows=("YTD", "SPLYYTD"))
    assert out == tmp_path / "data" / "compstat_dashboard_map" / "2026-11-15_YTD-SPLYYTD_only-075.csv"
    nonzero = [k for k, v in book_counts(load(fixtures, "compstat_map", "book-075.json"), CS,
                                         ("YTD", "SPLYYTD")).items() if v]
    assert len(calls) == 4 + len(nonzero)
    rows = list(csv.DictReader(out.open()))
    assert len(rows) == 10 * len(nonzero) and rows[0]["site"] == "compstat"
    table = book_counts(load(fixtures, "compstat_map", "book-075.json"), CS, ("YTD", "SPLYYTD"))
    assert mismatches == sum(1 for k in nonzero if table[k] < 10)  # 10 murder points answer every cell


def test_cli_keeps_compstat_out_of_the_repo(tmp_path: Path) -> None:
    from nypd_weekly_archive.cli import main

    (tmp_path / "pyproject.toml").write_text("")
    assert main(["dashboard-map", "--site", "compstat", "--root", str(tmp_path)]) == 2
    assert main(["dashboard-map", "--site", "compstat", "--root", str(tmp_path),
                 "--out", str(tmp_path / "data")]) == 2


def test_empty_map_answer_is_zero_points(fixtures: Path) -> None:
    """Seen 2026-11-22 for 044 WTD_COLLISIONS_Col Pedestrian: the table counts it, the map has no location."""
    point = load(fixtures, "dashboard_map", "map_075_WTD_collisions.json")[0]
    empty = {**{k: None for k in point}, "metric": 0, "value": " 40.7304,-73.9811",
             "tooltipHtml": "Incident Map \r\nWeek to Date - Pedestrian - 044 CMD - 11/09/26 - 11/15/26"}
    assert parse_points([empty], "044") == []
    with pytest.raises(ShapeError, match="not the empty map"):
        parse_points([{**empty, "tooltipHtml": "<h5>Total Collisions</h5>"}], "044")

"""Dashboard parsing against responses observed in the browser on 2026-11-21."""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from nypd_weekly_archive.dashboard import (
    BOOK_URL,
    GEO_FILTER_URL,
    TIMELINE_URL,
    ShapeError,
    backfill,
    body,
    build_dashboard,
    geos_from_filter,
    metrics_from_book,
    parse_timeline,
)

from .conftest import make_fetcher


def load(fixtures: Path, name: str):
    return json.loads((fixtures / "dashboard" / name).read_text())


def test_geos_and_metrics(fixtures: Path) -> None:
    geos = geos_from_filter(load(fixtures, "filter_geos.json"))
    assert geos[0] == "Citywide" and "116" in geos and "014" in geos and "075" in geos
    metrics = metrics_from_book(load(fixtures, "book_citywide.json"))
    assert len(metrics) == 30
    assert "COLLISIONS_Fat E-bike" in metrics and "COLLISIONS_Collisions" in metrics


def test_timeline_wtd_and_sply(fixtures: Path) -> None:
    wtd = parse_timeline(load(fixtures, "timeline_citywide_WTD_injury_collisions.json"), "Citywide")
    assert len(wtd.points) == 53 and wtd.end == date(2026, 11, 15)
    assert wtd.points[date(2026, 11, 15)] == 722  # same as the book and ts-en-us-Citywide.xlsx
    sply = parse_timeline(load(fixtures, "timeline_citywide_SPLYWTD_injury_collisions.json"), "Citywide")
    assert max(sply.points) == date(2025, 11, 15) and min(sply.points) == date(2024, 11, 16)


def test_sparse_timeline_omits_zero_weeks(fixtures: Path) -> None:
    tl = parse_timeline(load(fixtures, "timeline_075_WTD_fat_ebike.json"), "075")
    assert tl.points == {date(2026, 8, 23): 1}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d[0].update(itemValue="12"),
        lambda d: d[0].update(newField=1),
        lambda d: d[0].update(chartTitle="Timeline - Year to Year"),
        lambda d: d[0].update(categoryValue="2026-11-15"),
    ],
)
def test_shape_change_fails_loud(fixtures: Path, mutate) -> None:
    d = load(fixtures, "timeline_citywide_WTD_injury_collisions.json")
    mutate(d)
    with pytest.raises(ShapeError):
        parse_timeline(d, "Citywide")


def test_wrong_geo_in_title_fails(fixtures: Path) -> None:
    with pytest.raises(ShapeError, match="asked for 075"):
        parse_timeline(load(fixtures, "timeline_citywide_WTD_injury_collisions.json"), "075")


def test_backfill_end_to_end_with_zero_fill(fixtures: Path, tmp_path: Path) -> None:
    """Replays observed responses; every Citywide metric reuses the injury series."""
    wtd = load(fixtures, "timeline_citywide_WTD_injury_collisions.json")
    sply = load(fixtures, "timeline_citywide_SPLYWTD_injury_collisions.json")
    sparse = load(fixtures, "timeline_075_WTD_fat_ebike.json")
    calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        b = json.loads(req.content)
        calls.append(b)
        if url == GEO_FILTER_URL:
            return httpx.Response(200, json=load(fixtures, "filter_geos.json"))
        if url == BOOK_URL:
            return httpx.Response(200, json=load(fixtures, "book_citywide.json"))
        assert url == TIMELINE_URL
        geo = b[0]["values"][0]
        rid = b[2]["values"][0]
        if geo == "Citywide":
            return httpx.Response(200, json=wtd if rid.startswith("WTD_") else sply)
        if rid == "WTD_COLLISIONS_Fat E-bike":
            return httpx.Response(200, json=sparse)
        return httpx.Response(200, json=[])

    raw = tmp_path / "raw"
    out = backfill(make_fetcher(handler), raw, geos=["075"], day="2026-11-21")
    assert (out / "responses.jsonl.gz").exists() and not (out / "responses.jsonl").exists()
    assert calls[2] == body("Citywide", "WTD_" + metrics_from_book(load(fixtures, "book_citywide.json"))[0])
    assert len(calls) == 2 + 2 * 2 * 30
    with gzip.open(out / "responses.jsonl.gz", "rt") as f:
        assert sum(1 for _ in f) == 2 + 120

    csv_path = tmp_path / "data" / "dash.csv"
    build_dashboard(raw, csv_path)
    lines = csv_path.read_text().splitlines()
    assert lines[0] == "geography,window,record_id,metric,series_label,week_end,value,imputed,fetched_on"
    assert len(lines) - 1 == 2 * 30 * 2 * 53  # 2 geos x 30 metrics x 2 windows x 53 weeks
    ebike = [ln for ln in lines if ln.startswith("075,WTD,WTD_COLLISIONS_Fat E-bike,")]
    assert len(ebike) == 53
    assert sum(ln.endswith(",0,1,2026-11-21") for ln in ebike) == 52
    assert any(",2026-08-23,1,0," in ln for ln in ebike)

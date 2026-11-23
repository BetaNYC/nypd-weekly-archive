from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from nypd_weekly_archive.capture import LEDGER_FIELDS, capture, read_ledger, write_ledger
from nypd_weekly_archive.release import PackageError, package, read_backfills, tag_for

from .conftest import make_fetcher
from .test_capture import MANIFEST, setup

NOW = datetime(2026, 11, 21, 14, 23, tzinfo=UTC)


def test_tags() -> None:
    base = {"fetched_at": "2026-11-21T14:30:00+00:00", "week_end": "2026-11-15", "source": "nyc.gov"}
    assert tag_for({**base, "date_source": "xlsx"}) == ("week-2026-11-15", "2026-11-15")
    assert tag_for({**base, "date_source": "sibling_xlsx"})[0] == "week-2026-11-15"
    assert tag_for({**base, "date_source": "fetch_date_layout_error"})[0] == "fetched-2026-11-21"
    assert tag_for({**base, "date_source": "xlsx", "source": "wayback"}) == (
        "backfill-wayback-2026-11-21", "wayback-2026-11-21")


def test_package_zips_records_and_is_idempotent(tmp_path: Path, fixtures: Path) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    repo = raw.parent
    dash = raw / "trafficstat_dashboard" / "2026-11-21"
    dash.mkdir(parents=True)
    (dash / "responses.jsonl.gz").write_bytes(b"gz")
    pts = raw / "trafficstat_dashboard_map" / "2026-11-15"
    pts.mkdir(parents=True)
    (pts / "responses.jsonl.gz").write_bytes(b"gz")

    plan = package(repo, repo / "dist", now=NOW)
    assert [(t, p.name) for t, p in plan] == [
        ("week-2026-11-15", "trafficstat_2026-11-15_20261121T1423Z.zip"),
        ("backfill-dashboard-2026-11-21", "trafficstat-dashboard_2026-11-21_20261121T1423Z.zip"),
        ("week-2026-11-15", "trafficstat-dashboard-map_2026-11-15_20261121T1423Z.zip"),
    ]
    with zipfile.ZipFile(plan[0][1]) as z:
        assert sorted(z.namelist()) == [
            "raw/trafficstat/2026-11-15/ts-en-us-034.pdf",
            "raw/trafficstat/2026-11-15/ts-en-us-034.xlsx",
        ]
        member = "raw/trafficstat/2026-11-15/ts-en-us-034.xlsx"
        assert z.read(member) == (repo / member).read_bytes()
    rows = read_ledger(ledger)
    assert {(r["release_tag"], r["release_asset"]) for r in rows} == {
        ("week-2026-11-15", "trafficstat_2026-11-15_20261121T1423Z.zip")}
    assert read_backfills(repo / "backfills.csv")[0]["release_tag"] == "backfill-dashboard-2026-11-21"
    assert (repo / "dist" / "upload-plan.tsv").read_text().splitlines() == [
        "week-2026-11-15\tdist/trafficstat_2026-11-15_20261121T1423Z.zip",
        "backfill-dashboard-2026-11-21\tdist/trafficstat-dashboard_2026-11-21_20261121T1423Z.zip",
        "week-2026-11-15\tdist/trafficstat-dashboard-map_2026-11-15_20261121T1423Z.zip",
    ]
    # Nothing new: no zips, ledger unchanged, plan kept for the pending upload.
    before = ledger.read_text()
    assert package(repo, repo / "dist", now=datetime(2026, 11, 22, tzinfo=UTC)) == []
    assert ledger.read_text() == before
    assert len((repo / "dist" / "upload-plan.tsv").read_text().splitlines()) == 3


def test_package_refuses_when_originals_missing(tmp_path: Path, fixtures: Path) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    (raw / "trafficstat/2026-11-15/ts-en-us-034.pdf").unlink()
    with pytest.raises(PackageError, match="not on disk"):
        package(raw.parent, raw.parent / "dist", now=NOW)


def test_wayback_downloads_on_two_days_get_two_zips(tmp_path: Path) -> None:
    # 2026-11-23: two download days of compstat both became compstat_wayback_<stamp>.zip,
    # and the second zip overwrote the first.
    rows = []
    for day, name in (("2026-11-22", "cs-en-us-001pct.xlsx"), ("2026-11-23", "cs-en-us-city.xlsx")):
        path = f"raw/compstat/2026-11-15/{name}"
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(name.encode())
        rows.append({k: "" for k in LEDGER_FIELDS} | {
            "fetched_at": f"{day}T05:00:00+00:00", "series": "compstat", "path": path, "week_end": "2026-11-15",
            "date_source": "xlsx", "source": "wayback"})
    write_ledger(tmp_path / "captures.csv", rows)

    plan = package(tmp_path, tmp_path / "dist", now=NOW)
    assert [(t, p.name) for t, p in plan] == [
        ("backfill-wayback-2026-11-22", "compstat_wayback-2026-11-22_20261121T1423Z.zip"),
        ("backfill-wayback-2026-11-23", "compstat_wayback-2026-11-23_20261121T1423Z.zip"),
    ]
    for (_, zip_path), row in zip(plan, rows, strict=True):
        with zipfile.ZipFile(zip_path) as z:
            assert z.namelist() == [row["path"]]

"""The Pages site's coverage data: one state per precinct-week, best copy wins."""

from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

from nypd_weekly_archive.site_data import OUT, build_site_data, estimated_week, ordinal

LEDGER = ["fetched_at", "series", "url", "path", "sha256", "bytes", "last_modified", "week_end", "date_source",
          "source", "wayback_timestamp", "release_tag", "release_asset"]
XLSX = "https://www.nyc.gov/assets/nypd/downloads/excel/traffic_data/traffic-statistics/ts-en-us-{}.xlsx"
PAGE = "https://www.nyc.gov/site/nypd/bureaus/patrol/precincts/{}-precinct.page"


def row(path: str, week_end: str, date_source: str, source: str = "wayback", ts: str = "",
        fetched: str = "2026-11-21T05:00:00+00:00") -> dict:
    return {"fetched_at": fetched, "series": "trafficstat", "url": "u", "path": path, "sha256": "x", "bytes": "1",
            "last_modified": "", "week_end": week_end, "date_source": date_source, "source": source,
            "wayback_timestamp": ts, "release_tag": "", "release_asset": ""}


def make_repo(root: Path, rows: list[dict], read: dict[str, list[str]]) -> None:
    with (root / "captures.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, LEDGER)
        w.writeheader()
        w.writerows(rows)
    with (root / "manifest.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["series", "kind", "url", "found_on"])
        for p in ("001", "034"):
            w.writerow(["trafficstat", "xlsx", XLSX.format(p), PAGE.format(ordinal(int(p)))])
    d = root / "data" / "trafficstat"
    d.mkdir(parents=True)
    for week, pcts in read.items():
        with (d / f"{week}.csv").open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["series", "precinct", "week_end", "value"])
            for p in pcts:
                w.writerow(["trafficstat", str(int(p)), week, "1"])


def test_estimated_week_is_last_sunday_two_days_before_saving() -> None:
    assert estimated_week("20250915120000") == dt.date(2025, 9, 7)   # Monday -> the Sunday before last
    assert estimated_week("20250916120000") == dt.date(2025, 9, 14)  # Tuesday -> the Sunday just past
    assert estimated_week("20250921120000") == dt.date(2025, 9, 14)  # Sunday -> the previous Sunday


def test_ordinal() -> None:
    assert [ordinal(n) for n in (1, 2, 3, 11, 12, 13, 22, 101, 113, 123)] == [
        "1st", "2nd", "3rd", "11th", "12th", "13th", "22nd", "101st", "113th", "123rd"]


def test_best_copy_wins_and_weeks_fill_gaps(tmp_path: Path) -> None:
    make_repo(tmp_path, [
        # 034: read spreadsheet for the week ending 2025-09-14, and a PDF of the same week
        row("raw/trafficstat/2025-09-14/ts-en-us-034.xlsx", "2025-09-14", "xlsx"),
        row("raw/trafficstat/2025-09-16/ts-en-us-034.pdf", "2025-09-16", "wayback_capture_date", ts="20250916100000"),
        # 034: PDF only, two weeks earlier (saved Wednesday 2025-09-03 -> week ending 2025-08-31)
        row("raw/trafficstat/2025-09-03/ts-en-us-034.pdf", "2025-09-03", "wayback_capture_date", ts="20250903100000"),
        # 001: an older-layout spreadsheet saved Tuesday 2025-09-02 -> week ending 2025-08-31
        row("raw/trafficstat/2025-09-02/ts-en-us-001.xlsx", "2025-09-02", "fetch_date_layout_error",
            ts="20250902100000"),
        # 001: spreadsheet dated by its contents but not in data/ -> saved, not read
        row("raw/trafficstat/2025-09-07/ts-en-us-001.xlsx", "2025-09-07", "xlsx"),
        # citywide file: counted as a download, not a precinct
        row("raw/trafficstat/2025-09-14/ts-en-us-cw.xlsx", "2025-09-14", "xlsx",
            fetched="2026-11-21T06:00:00+00:00"),
        row("raw/trafficstat/2025-09-14/ts-en-us-001.pdf", "2025-09-14", "sibling_xlsx", source="nyc.gov"),
    ], read={"2025-09-14": ["034"]})

    out = build_site_data(tmp_path)

    assert out["weeks"] == ["2025-08-31", "2025-09-07", "2025-09-14"]
    by = {p["id"]: p for p in out["precincts"]}
    assert list(by) == ["001", "034"]
    assert by["034"]["states"] == "103"
    assert by["034"]["estimated"] == "100"
    assert by["001"]["states"] == "221"
    assert by["001"]["estimated"] == "100"
    assert by["001"]["name"] == "1st Precinct" and by["001"]["borough"] == "Manhattan"
    assert by["034"]["nypd_page"] == PAGE.format("34th")
    assert out["totals"] == {"precinct_weeks": 6, "read": 1, "older_layout": 2, "pdf_only": 2, "none": 1,
                             "files_from_internet_archive": 6, "files_from_nyc_gov": 1}
    assert out["last_file_saved"] == "2026-11-21T06:00:00+00:00"
    assert json.loads((tmp_path / OUT).read_text()) == out


def test_output_is_stable(tmp_path: Path) -> None:
    make_repo(tmp_path, [row("raw/trafficstat/2025-09-14/ts-en-us-034.xlsx", "2025-09-14", "xlsx")],
              read={"2025-09-14": ["034"]})
    build_site_data(tmp_path)
    first = (tmp_path / OUT).read_bytes()
    build_site_data(tmp_path)
    assert (tmp_path / OUT).read_bytes() == first

"""Capture is idempotent, dates files from their contents, and keeps revisions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx

from nypd_weekly_archive.build import build
from nypd_weekly_archive.capture import capture, read_ledger
from nypd_weekly_archive.discover import ManifestRow

from .conftest import make_fetcher

XLSX = "https://www.nyc.gov/assets/nypd/downloads/excel/traffic_data/traffic-statistics/ts-en-us-034.xlsx"
PDF = "https://www.nyc.gov/assets/nypd/downloads/pdf/traffic_data/traffic-statistics/ts-en-us-034.pdf"
MANIFEST = [ManifestRow("trafficstat", "pdf", PDF, "p"), ManifestRow("trafficstat", "xlsx", XLSX, "p")]
LM = "Tue, 17 Nov 2026 16:36:50 GMT"


class Site:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.requests: list[httpx.Request] = []
        self.honor_ims = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.honor_ims and req.headers.get("If-Modified-Since") == LM:
            return httpx.Response(304)
        return httpx.Response(200, content=self.files[str(req.url)], headers={"Last-Modified": LM})


def setup(tmp_path: Path, fixtures: Path) -> tuple[Path, Path, Site]:
    repo = tmp_path / "repo"
    (repo / "raw").mkdir(parents=True)
    site = Site({XLSX: (fixtures / "ts-en-us-034.xlsx").read_bytes(), PDF: b"%PDF-1.4 fake"})
    return repo / "raw", repo / "captures.csv", site


def files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_first_capture_dates_from_file(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    res = capture(make_fetcher(site), MANIFEST, raw, ledger, today=datetime(2026, 11, 21, tzinfo=UTC))
    assert len(res.new) == 2 and not res.failed
    assert files_under(raw) == [
        "trafficstat/2026-11-15/ts-en-us-034.pdf",
        "trafficstat/2026-11-15/ts-en-us-034.xlsx",
    ]
    rows = read_ledger(ledger)
    assert {r["date_source"] for r in rows} == {"xlsx", "sibling_xlsx"}
    assert rows[0]["path"].startswith("raw/") and rows[0]["last_modified"] == LM


def test_rerun_stores_no_duplicate(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    before = (files_under(raw), ledger.read_text())
    res = capture(make_fetcher(site), MANIFEST, raw, ledger)  # same bytes, no 304 support
    assert res.new == [] and res.unchanged == 2
    assert (files_under(raw), ledger.read_text()) == before


def test_rerun_sends_if_modified_since(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    site.honor_ims = True
    site.requests.clear()
    res = capture(make_fetcher(site), MANIFEST, raw, ledger)
    assert res.unchanged == 2 and res.new == []
    assert all(r.headers["If-Modified-Since"] == LM for r in site.requests)


def test_revision_same_week_kept_alongside(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    site.files[PDF] = b"%PDF-1.4 revised"
    res = capture(make_fetcher(site), MANIFEST, raw, ledger)
    assert len(res.new) == 1
    names = files_under(raw)
    assert "trafficstat/2026-11-15/ts-en-us-034.pdf" in names
    assert any(n.startswith("trafficstat/2026-11-15/ts-en-us-034.") and n.endswith(".pdf") and n.count(".") == 2
               for n in names)


def test_layout_error_still_keeps_file(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    site.files[XLSX] = b"PK\x03\x04 zip-shaped but not a workbook"
    res = capture(make_fetcher(site), MANIFEST, raw, ledger, today=datetime(2026, 11, 21, tzinfo=UTC))
    assert len(res.layout_errors) == 1
    assert "trafficstat/2026-11-21/ts-en-us-034.xlsx" in files_under(raw)
    assert {r["date_source"] for r in read_ledger(ledger)} == {"fetch_date_layout_error", "fetch_date"}


def test_failed_download_reported_not_fatal(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404) if str(req.url) == PDF else site(req)

    res = capture(make_fetcher(handler), MANIFEST, raw, ledger)
    assert res.failed == [PDF] and len(res.new) == 1


def test_build_writes_weekly_csv(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    errors = build(raw.parent, ledger, raw.parent / "data")
    assert errors == []
    out = (raw.parent / "data" / "trafficstat" / "2026-11-15.csv").read_text().splitlines()
    assert out[0].startswith("series,source_file,geography,precinct,week_start,week_end")
    assert len(out) == 415
    # Rebuilding is deterministic.
    first = "\n".join(out)
    build(raw.parent, ledger, raw.parent / "data")
    assert (raw.parent / "data" / "trafficstat" / "2026-11-15.csv").read_text().rstrip("\n") == first


def test_redate_after_parser_fix(tmp_path, fixtures) -> None:
    from nypd_weekly_archive.capture import redate

    raw, ledger, site = setup(tmp_path, fixtures)
    good = site.files[XLSX]
    site.files[XLSX] = b"PK\x03\x04 not yet parseable"
    capture(make_fetcher(site), MANIFEST, raw, ledger, today=datetime(2026, 11, 21, tzinfo=UTC))
    assert files_under(raw) == ["trafficstat/2026-11-21/ts-en-us-034.pdf", "trafficstat/2026-11-21/ts-en-us-034.xlsx"]
    # Simulate the parser now accepting the file.
    (raw / "trafficstat/2026-11-21/ts-en-us-034.xlsx").write_bytes(good)
    assert redate(raw.parent, ledger) == []
    assert files_under(raw) == ["trafficstat/2026-11-15/ts-en-us-034.pdf", "trafficstat/2026-11-15/ts-en-us-034.xlsx"]
    assert [r["date_source"] for r in read_ledger(ledger)] == ["xlsx", "sibling_xlsx"]
    assert not (raw / "trafficstat/2026-11-21").exists()


def test_html_error_page_served_as_200_is_not_stored(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    site.files[PDF] = b"<html><body>Access Denied</body></html>"
    res = capture(make_fetcher(site), MANIFEST, raw, ledger)
    assert res.failed == [PDF]
    assert not any(n.endswith(".pdf") for n in files_under(raw))


def test_sniff() -> None:
    from nypd_weekly_archive.capture import sniff

    assert sniff(b"PK\x03\x04rest") == "xlsx"
    assert sniff(b"%PDF-1.7") == "pdf"
    assert sniff(b"<!DOCTYPE html>") == "unknown"


def test_stale_file_flagged_by_in_file_week() -> None:
    from nypd_weekly_archive.build import stale_files

    def row(url, wk):
        return {"series": "compstat", "url": url, "week_end": wk, "date_source": "xlsx"}

    ledger = [
        row("https://x/cs-en-us-pbxn.xlsx", "2026-11-15"),
        row("https://x/cs-en-us-pbbx.xlsx", "2026-05-17"),  # pre-split Bronx file, still 200
    ]
    out = stale_files(ledger)
    assert [r["file"] for r in out] == ["cs-en-us-pbbx"] and out[0]["days_behind"] == "182"


def _empty_raw(raw: Path) -> None:
    """Simulate a CI checkout: the ledger is in git, raw/ is not."""
    import shutil

    shutil.rmtree(raw)
    raw.mkdir()


def test_ci_empty_raw_existing_ledger_stores_nothing(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    build(raw.parent, ledger, raw.parent / "data")
    csv_before = (raw.parent / "data" / "trafficstat" / "2026-11-15.csv").read_text()
    ledger_before = ledger.read_text()
    _empty_raw(raw)

    res = capture(make_fetcher(site), MANIFEST, raw, ledger)  # server ignores If-Modified-Since
    assert res.new == [] and res.unchanged == 2
    assert files_under(raw) == [] and ledger.read_text() == ledger_before

    assert build(raw.parent, ledger, raw.parent / "data") == []
    assert (raw.parent / "data" / "trafficstat" / "2026-11-15.csv").read_text() == csv_before


def test_ci_revision_gets_its_own_name_when_earlier_file_is_absent(tmp_path, fixtures) -> None:
    raw, ledger, site = setup(tmp_path, fixtures)
    capture(make_fetcher(site), MANIFEST, raw, ledger)
    _empty_raw(raw)
    site.files[PDF] = b"%PDF-1.4 revised"
    res = capture(make_fetcher(site), MANIFEST, raw, ledger)
    assert len(res.new) == 1
    paths = [r["path"] for r in read_ledger(ledger)]
    assert len(paths) == len(set(paths)) == 3
    assert res.new[0]["path"] != "raw/trafficstat/2026-11-15/ts-en-us-034.pdf"

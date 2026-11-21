from __future__ import annotations

import csv
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from nypd_weekly_archive.capture import LEDGER_FIELDS, append_ledger, read_ledger
from nypd_weekly_archive.wayback_backfill import FIELDS, backfill_wayback, parse_cdx, wayback_digest

from .conftest import make_fetcher

ORIG = "https://www.nyc.gov/assets/nypd/downloads/excel/traffic_data/traffic-statistics/ts-en-us-034.xlsx"
ORIG_PDF = ORIG.replace("/excel/", "/pdf/").replace(".xlsx", ".pdf")


def test_parse_cdx_dedupes_and_checks_header() -> None:
    rows = [
        FIELDS,
        ["20250413000000", ORIG, "text/plain", "200", "AAA", "10"],
        ["20250420000000", ORIG, "application/vnd...", "200", "BBB", "10"],
        ["20250427000000", ORIG, "text/plain", "200", "AAA", "10"],  # non-adjacent duplicate
    ]
    assert [r["digest"] for r in parse_cdx(rows)] == ["AAA", "BBB"]
    assert parse_cdx([]) == []
    try:
        parse_cdx([["urlkey", "timestamp"]])
    except ValueError as exc:
        assert "unexpected header" in str(exc)
    else:
        raise AssertionError("bad header accepted")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "raw").mkdir(parents=True)
    return repo


def test_backfill_stores_verified_copies(tmp_path: Path, fixtures: Path) -> None:
    repo = make_repo(tmp_path)
    xlsx = (fixtures / "ts-en-us-034.xlsx").read_bytes()
    pdf = b"%PDF-1.4 archived"
    good, bad = wayback_digest(xlsx), "WRONGDIGEST"
    cdx_queries: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://web.archive.org/cdx/"):
            q = parse_qs(urlsplit(url).query)
            cdx_queries.append(q)
            if "/excel/" in q["url"][0]:
                return httpx.Response(200, json=[
                    FIELDS,
                    ["20251116120000", ORIG, "text/plain", "200", good, "1"],
                    ["20251123120000", ORIG, "text/plain", "200", bad, "1"],
                ])
            return httpx.Response(200, json=[FIELDS, ["20251117000000", ORIG_PDF, "application/pdf", "200",
                                                       wayback_digest(pdf), "1"]])
        if "20251116120000id_/" in url or "20251123120000id_/" in url:
            return httpx.Response(200, content=xlsx)
        if "20251117000000id_/" in url:
            return httpx.Response(200, content=pdf)
        return httpx.Response(404)

    status = backfill_wayback(make_fetcher(handler), repo, ["trafficstat"])
    assert status == 1  # the digest mismatch is reported
    q = cdx_queries[0]
    assert q["matchType"] == ["prefix"] and q["filter"] == ["statuscode:200"] and q["collapse"] == ["digest"]
    rows = read_ledger(repo / "captures.csv")
    assert [(r["source"], r["date_source"], r["week_end"]) for r in rows] == [
        ("wayback", "xlsx", "2026-11-15"),
        ("wayback", "wayback_capture_date", "2025-11-17"),
    ]
    assert (repo / "raw/trafficstat/2026-11-15/ts-en-us-034.xlsx").read_bytes() == xlsx
    assert rows[0]["wayback_timestamp"] == "20251116120000"

    # Re-running skips what is already in the ledger.
    status = backfill_wayback(make_fetcher(handler), repo, ["trafficstat"], limit=5)
    assert len(read_ledger(repo / "captures.csv")) == 2


def test_ledger_gains_columns_without_losing_rows(tmp_path: Path) -> None:
    path = tmp_path / "captures.csv"
    old = LEDGER_FIELDS[:9]
    with path.open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(old)
        w.writerow(["t", "qteam", "u", "p", "s", "1", "", "2026-10-25", "xlsx"])
    append_ledger(path, {"fetched_at": "t2", "series": "qteam", "url": "u2", "path": "p2", "sha256": "s2",
                         "bytes": "2", "last_modified": "", "week_end": "2026-10-25", "date_source": "xlsx",
                         "source": "wayback", "wayback_timestamp": "2025"})
    rows = read_ledger(path)
    assert path.read_text().splitlines()[0] == ",".join(LEDGER_FIELDS)
    assert [r["source"] for r in rows] == ["nyc.gov", "wayback"]

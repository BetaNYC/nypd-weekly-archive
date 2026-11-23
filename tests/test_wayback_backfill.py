from __future__ import annotations

import csv
import gzip
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from nypd_weekly_archive.capture import LEDGER_FIELDS, append_ledger, read_ledger
from nypd_weekly_archive.wayback_backfill import FIELDS, backfill_wayback, decode_body, parse_cdx, wayback_digest

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


def test_compstat_pdfs_are_dated_by_their_cover(tmp_path: Path) -> None:
    from .test_github_backfill import cover

    repo = make_repo(tmp_path)
    orig = "https://www.nyc.gov/assets/nypd/downloads/pdf/crime_statistics/cs-en-us-001pct.pdf"
    body = cover("11/9/2026", "11/15/2026")

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://web.archive.org/cdx/"):
            if "/excel/" in parse_qs(urlsplit(url).query)["url"][0]:
                return httpx.Response(200, json=[])
            return httpx.Response(200, json=[FIELDS, ["20261120000000", orig, "application/pdf", "200",
                                                       wayback_digest(body), "1"]])
        if "20261120000000id_/" in url:
            return httpx.Response(200, content=body)
        return httpx.Response(404)

    assert backfill_wayback(make_fetcher(handler), repo, ["compstat"]) == 0
    [row] = read_ledger(repo / "captures.csv")
    assert (row["week_end"], row["date_source"]) == ("2026-11-15", "pdf")
    assert (repo / "raw/compstat/2026-11-15/cs-en-us-001pct.pdf").exists()


# --- Old /html/ tree, old names, housing (fixtures: rows from the CDX answers saved 2026-11-23) ---

CDX_FIXTURES = Path(__file__).parent / "fixtures" / "wayback_cdx"


def cdx_rows(name: str) -> list[list[str]]:
    import json

    return json.loads((CDX_FIXTURES / f"{name}.json").read_text())


def synthetic(rows: list[list[str]], body_for) -> tuple[list[list[str]], dict[str, bytes]]:
    """Swap each real digest for one of a test body, so identical digests stay identical.

    Returns (rows, timestamp -> body)."""
    out, bodies, by_digest = [rows[0]], {}, {}
    for r in rows[1:]:
        if r[4] not in by_digest:
            by_digest[r[4]] = body_for(r)
        body = by_digest[r[4]]
        out.append([r[0], r[1], r[2], r[3], wayback_digest(body), r[5]])
        bodies[r[0]] = body
    return out, bodies


def test_prefixes_cover_both_trees_hosts_and_old_names() -> None:
    from nypd_weekly_archive.wayback_backfill import prefixes

    cs = [p for _, p in prefixes("compstat")]
    assert "www.nyc.gov/assets/nypd/downloads/pdf/crime_statistics/cs-en-us-" in cs
    for host in ("www.nyc.gov", "www1.nyc.gov"):
        assert f"{host}/html/nypd/downloads/pdf/crime_statistics/cs-en-us-" in cs
        assert f"{host}/html/nypd/downloads/pdf/crime_statistics/cscity." in cs
        assert f"{host}/html/nypd/downloads/pdf/crime_statistics/cscorrection." in cs
    hb = prefixes("housing")
    assert ("xlsx", "www.nyc.gov/html/nypd/downloads/excel/crime_statistics/hb_") in hb
    assert ("pdf", "www1.nyc.gov/html/nypd/downloads/pdf/crime_statistics/hb_") in hb
    assert not any("cscity" in p for _, p in hb)


def test_url_key_and_file_stem_fold_like_the_cdx() -> None:
    from nypd_weekly_archive.wayback_backfill import file_stem, url_key

    a = cdx_rows("html-pdf-csCorrection")[1][1]  # http://www.nyc.gov/html/.../csCorrection.pdf
    assert url_key(a) == url_key(a.replace("http://www.nyc.gov", "https://www.nyc.gov:443"))
    assert url_key("http://www.nyc.gov:80/x/A.pdf") == url_key("https://www.nyc.gov/x/a.pdf")
    assert url_key("https://www1.nyc.gov/x.pdf") != url_key("https://www.nyc.gov/x.pdf")
    assert file_stem(a) == "cscorrection"
    assert file_stem("https://www.nyc.gov/a/cs-en-us-City.pdf?ref=x") == "cs-en-us-city"


def old_tree_handler(answers: dict[str, list[list[str]]], bodies: dict[str, bytes], downloads: list[str],
                     queries: list[tuple[str, str]] | None = None):
    """answers: 'kind:last part of the CDX url' (e.g. 'pdf:cscity.pdf' or 'pdf:hb_') -> rows, for the
    /html/ tree only. The archive folds hosts, so www and www1 get the same answer."""

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://web.archive.org/cdx/"):
            q = parse_qs(urlsplit(url).query)
            queries.append((q["matchType"][0], q["url"][0]))
            prefix = q["url"][0]
            if "/html/" not in prefix:
                return httpx.Response(200, json=[])
            kind = "pdf" if "/pdf/" in prefix else "xlsx"
            rows = answers.get(f"{kind}:{prefix.rsplit('/', 1)[-1]}")
            return httpx.Response(200, json=rows or [])
        ts = url.split("/web/", 1)[1].split("id_/", 1)[0]
        downloads.append(url)
        return httpx.Response(200, content=bodies[ts]) if ts in bodies else httpx.Response(404)

    queries = queries if queries is not None else []
    return handler


def test_old_tree_old_names_each_digest_once_and_resumable(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    city, b1 = synthetic(cdx_rows("html-pdf-cscity"), lambda r: b"%PDF-1.4 " + r[4].encode())
    corr, b2 = synthetic(cdx_rows("html-pdf-csCorrection"), lambda r: b"%PDF-1.4 " + r[4].encode())
    answers = {"pdf:cscity.pdf": city, "pdf:cscorrection.pdf": corr}
    downloads: list[str] = []
    queries: list[tuple[str, str]] = []
    handler = old_tree_handler(answers, {**b1, **b2}, downloads, queries)

    names = ["cscity", "csCorrection", "cs-en-us-city"]
    assert backfill_wayback(make_fetcher(handler), repo, ["compstat"], names=names) == 0
    rows = read_ledger(repo / "captures.csv")
    # cscity: 5 rows, 4 distinct digests (one repeated, at :80); csCorrection: 2. Each listed on
    # both hosts, downloaded once.
    assert len(downloads) == 6 and len(rows) == 6
    # Named files are asked for by exact address, never by folder: in both trees, on both hosts.
    assert {m for m, _ in queries} == {"exact"}
    assert ("exact", "www1.nyc.gov/html/nypd/downloads/pdf/crime_statistics/cscity.pdf") in queries
    assert ("exact", "www.nyc.gov/assets/nypd/downloads/excel/crime_statistics/cs-en-us-city.xlsx") in queries
    assert not any("cscity" in u and "/assets/" in u for _, u in queries)
    assert {r["date_source"] for r in rows} == {"wayback_capture_date"}
    first = next(r for r in rows if r["wayback_timestamp"] == "20080516234625")
    assert first["week_end"] == "2008-05-16"
    assert first["path"] == "raw/compstat/2008-05-16/cscity.pdf"
    assert any(r["path"].endswith("csCorrection.pdf") for r in rows)

    # A second run lists again and downloads nothing.
    downloads.clear()
    assert backfill_wayback(make_fetcher(handler), repo, ["compstat"], names=names) == 0
    assert downloads == [] and len(read_ledger(repo / "captures.csv")) == 6


def test_digest_held_under_the_other_tree_is_not_downloaded(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    old = cdx_rows("html-pdf-cs-en-us-city")
    body = b"%PDF-1.4 same bytes"
    d = wayback_digest(body)
    assets_url = "https://www1.nyc.gov/assets/nypd/downloads/pdf/crime_statistics/cs-en-us-city.pdf"
    # Held from an earlier run under /assets/, saved with https and no port.
    append_ledger(repo / "captures.csv", {
        "fetched_at": "t", "series": "compstat", "url": assets_url, "path": "p", "sha256": "s",
        "bytes": "1", "last_modified": "", "week_end": "2017-08-06", "date_source": "pdf",
        "source": "wayback", "wayback_timestamp": "20170809000000"})
    answers_assets = [FIELDS, ["20170809000000", assets_url.replace("https://", "http://").replace(
        ".gov/", ".gov:80/", 1), "application/pdf", "200", d, "1"]]
    answers_old = [FIELDS, [old[1][0], old[1][1], old[1][2], old[1][3], d, old[1][5]]]
    downloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://web.archive.org/cdx/"):
            prefix = parse_qs(urlsplit(url).query)["url"][0]
            if prefix.endswith("/pdf/crime_statistics/cs-en-us-city.pdf"):
                return httpx.Response(200, json=answers_old if "/html/" in prefix else answers_assets)
            return httpx.Response(200, json=[])
        downloads.append(url)
        return httpx.Response(200, content=body)

    assert backfill_wayback(make_fetcher(handler), repo, ["compstat"], names=["cs-en-us-city"]) == 0
    assert downloads == []
    assert len(read_ledger(repo / "captures.csv")) == 1


def test_housing_from_the_old_tree(tmp_path: Path, fixtures: Path) -> None:
    repo = make_repo(tmp_path)
    good = (fixtures / "hb_eagle_report.xlsx").read_bytes()
    no_wtd = (fixtures / "cs-en-us-city.xlsx").read_bytes()  # a workbook with no WTD sheet
    xl = iter([good, no_wtd])
    xlsx, bx = synthetic(cdx_rows("html-xlsx-hb_eagle_report"), lambda r: next(xl))
    pdf, bp = synthetic(cdx_rows("html-pdf-hb_eagle_report"), lambda r: b"%PDF-1.4 " + r[4].encode())
    answers = {"xlsx:hb_": xlsx, "pdf:hb_": pdf}
    downloads: list[str] = []
    status = backfill_wayback(make_fetcher(old_tree_handler(answers, {**bx, **bp}, downloads)), repo, ["housing"])
    assert status == 1  # a layout error is reported, as for any series
    rows = read_ledger(repo / "captures.csv")
    got = {r["wayback_timestamp"]: (r["week_end"], r["date_source"]) for r in rows}
    assert got["20150906073250"][1] == "xlsx"  # read from the WTD sheet
    assert got["20151008063106"] == ("2015-10-08", "fetch_date_layout_error")
    # PDFs carry no readable week: filed by capture date and flagged, for site_data to estimate.
    assert got["20140912203159"] == ("2014-09-12", "wayback_capture_date")
    assert len(rows) == 5 and {r["series"] for r in rows} == {"housing"}


def test_stops_after_five_failures_in_a_row(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    rows = cdx_rows("html-pdf-cscity")
    downloads: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://web.archive.org/cdx/"):
            prefix = parse_qs(urlsplit(url).query)["url"][0]
            more = [[f"2010010{i}000000", rows[1][1], "application/pdf", "200", f"D{i}", "1"] for i in range(1, 9)]
            return httpx.Response(200, json=[FIELDS, *more] if prefix.endswith("/pdf/crime_statistics/cscity.pdf")
                                  else [])
        downloads.append(url)
        return httpx.Response(403)  # what a block would look like

    assert backfill_wayback(make_fetcher(handler), repo, ["compstat"], names=["cscity"]) == 1
    assert len(downloads) == 5


class WireBytes(httpx.SyncByteStream):
    def __init__(self, data: bytes) -> None:
        self.data = data

    def __iter__(self):  # noqa: ANN204
        yield self.data


def test_gzip_encoded_capture_is_checked_as_sent_and_stored_decoded(tmp_path: Path) -> None:
    # cscity.pdf, capture 20140720002526: served with Content-Encoding: gzip, and the CDX
    # digest is the SHA-1 of the gzip bytes, not of the PDF (2026-11-23).
    repo = make_repo(tmp_path)
    pdf = b"%PDF-1.6 archived with gzip"
    sent = gzip.compress(pdf)

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url.startswith("https://web.archive.org/cdx/"):
            if "/pdf/" in parse_qs(urlsplit(url).query)["url"][0]:
                return httpx.Response(200, json=[FIELDS, ["20251117000000", ORIG_PDF, "application/pdf", "200",
                                                           wayback_digest(sent), "1"]])
            return httpx.Response(200, json=[])
        # A stream, not content=, so httpx does not read and decode it before the Fetcher sees it.
        return httpx.Response(200, stream=WireBytes(sent), headers={"Content-Encoding": "gzip"})

    assert backfill_wayback(make_fetcher(handler), repo, ["trafficstat"]) == 0
    rows = read_ledger(repo / "captures.csv")
    assert len(rows) == 1 and rows[0]["bytes"] == str(len(pdf))
    assert (repo / rows[0]["path"]).read_bytes() == pdf


def test_decode_body() -> None:
    body = b"some bytes"
    assert decode_body(body, None) == body
    assert decode_body(gzip.compress(body), "gzip") == body
    assert decode_body(zlib.compress(body), "deflate") == body
    try:
        decode_body(body, "br")
    except ValueError:
        pass
    else:
        raise AssertionError("br accepted")

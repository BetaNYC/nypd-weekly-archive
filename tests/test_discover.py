"""Discovery against the real NYPD pages saved 2026-11-21 (tests/fixtures/pages/)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from nypd_weekly_archive.discover import (
    PRECINCT_DIRECTORY,
    SERIES,
    DiscoveryError,
    ManifestRow,
    check_shrink,
    discover,
    extract_links,
    file_links,
    precinct_pages,
    read_manifest,
    write_manifest,
)
from nypd_weekly_archive.net import FetchError

from .conftest import make_fetcher

PAGES = {
    SERIES["trafficstat"].page: "traffic-data-trafficstat.html",
    SERIES["fatalities"].page: "traffic-fatalities.html",
    SERIES["compstat"].page: "borough-and-precinct-crime-stats.html",
    SERIES["qteam"].page: "quality-of-life-stats.html",
    PRECINCT_DIRECTORY: "precincts.html",
}


def html(fixtures: Path, name: str) -> str:
    return (fixtures / "pages" / name).read_text(encoding="utf-8")


def test_series_pages(fixtures: Path) -> None:
    counts = {}
    for name, s in SERIES.items():
        links = extract_links(html(fixtures, PAGES[s.page]), s.page)
        counts[name] = len(set(file_links(links, s)))
    # Counted by hand from the saved pages.
    assert counts == {"trafficstat": 20, "fatalities": 20, "compstat": 174, "qteam": 2}


def test_fatalities_excludes_monthly_cityacc(fixtures: Path) -> None:
    s = SERIES["fatalities"]
    urls = file_links(extract_links(html(fixtures, PAGES[s.page]), s.page), s)
    assert not any("cityacc" in u for u in urls)
    # pbxn/pbxs links exist only inside an HTML comment on the live page.
    assert not any("pbxn" in u for u in urls)
    assert any(u.endswith("traffic-fatalities-pbbx%20north.xlsx") or u.endswith("pbbx north.xlsx") for u in urls)


def test_precinct_directory(fixtures: Path) -> None:
    pages = precinct_pages(extract_links(html(fixtures, "precincts.html"), PRECINCT_DIRECTORY))
    assert len(pages) == 78
    assert "https://www.nyc.gov/site/nypd/bureaus/patrol/precincts/34th-precinct.page" in pages


def _site(fixtures: Path, fail: str | None = None):
    p34 = html(fixtures, "34th-precinct.html")

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if url == fail:
            return httpx.Response(404)
        if url in PAGES:
            return httpx.Response(200, text=html(fixtures, PAGES[url]))
        if url.endswith("34th-precinct.page"):
            return httpx.Response(200, text=p34)
        if "/precincts/" in url:
            return httpx.Response(200, text="<html></html>")
        return httpx.Response(404)

    return handler


def test_discover_end_to_end(fixtures: Path) -> None:
    rows = discover(make_fetcher(_site(fixtures)), sorted(SERIES))
    by = {s: [r for r in rows if r.series == s] for s in SERIES}
    # Series pages, plus the 034 precinct files found only on the 34th precinct page.
    assert len(by["trafficstat"]) == 22
    assert len(by["qteam"]) == 4
    assert len(by["compstat"]) == 174
    assert len(by["fatalities"]) == 20
    assert any(r.url.endswith("ts-en-us-034.xlsx") and r.found_on.endswith("34th-precinct.page") for r in rows)


def test_discover_fails_loud_when_a_page_fails(fixtures: Path) -> None:
    with pytest.raises(FetchError):
        discover(make_fetcher(_site(fixtures, fail=SERIES["qteam"].page)), sorted(SERIES))
    with pytest.raises(FetchError):
        discover(make_fetcher(_site(fixtures, fail=PRECINCT_DIRECTORY)), ["trafficstat"])


def test_discover_fails_when_page_has_no_files(fixtures: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>redesigned</html>")

    with pytest.raises(DiscoveryError, match="no fatalities files"):
        discover(make_fetcher(handler), ["fatalities"])


def test_shrink_guard(tmp_path: Path) -> None:
    old = [ManifestRow("qteam", "xlsx", f"https://x/{i}.xlsx", "p") for i in range(100)]
    check_shrink(old, old[:95])
    with pytest.raises(DiscoveryError, match="shrank"):
        check_shrink(old, old[:80])
    write_manifest(tmp_path / "m.csv", old)
    assert read_manifest(tmp_path / "m.csv") == old

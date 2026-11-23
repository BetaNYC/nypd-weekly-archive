"""Build the list of files to capture by reading NYPD's own pages.

Nothing here constructs a file URL from a pattern. Every URL in the manifest
was an <a href> on one of these pages (all fetched and read 2026-11-21):

- TrafficStat: https://www.nyc.gov/site/nypd/stats/traffic-data/traffic-data-trafficstat.page
  links the Citywide and 8 patrol-borough files only; precinct files are
  linked from each precinct page.
- Traffic fatalities: https://www.nyc.gov/site/nypd/stats/traffic-data/traffic-fatalities.page
  links one file per patrol borough plus Port Authority PD.
- CompStat: https://www.nyc.gov/site/nypd/stats/crime-statistics/borough-and-precinct-crime-stats.page
  links 78 precinct and the patrol-borough files.
- Citywide crime statistics (read 2026-11-22):
  https://www.nyc.gov/site/nypd/stats/crime-statistics/citywide-crime-stats.page
  links the citywide and Department of Correction CompStat sheets
  (cs-en-us-city, cs-en-us-Correction; filed under compstat) and the NYCHA
  Housing Bureau weekly report (hb_eagle_report; series housing).
- Q-Team: https://www.nyc.gov/site/nypd/stats/quality-of-life-stats/quality-of-life-stats.page
  links the CITYWIDE file only; precinct files are on each precinct page.
- Precinct directory: https://www.nyc.gov/site/nypd/bureaus/patrol/precincts-landing.page
  (78 precinct pages; each links its cs-, ts- and qs- files).
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from .log import log
from .net import Fetcher

BASE = "https://www.nyc.gov"
PRECINCT_DIRECTORY = f"{BASE}/site/nypd/bureaus/patrol/precincts-landing.page"
PRECINCT_PAGE_PREFIX = "/site/nypd/bureaus/patrol/precincts/"
CITYWIDE_CRIME = f"{BASE}/site/nypd/stats/crime-statistics/citywide-crime-stats.page"


@dataclass(frozen=True)
class Series:
    name: str
    page: str
    # Path fragment that a linked file must contain to belong to this series.
    path_marker: str
    on_precinct_pages: bool
    # More listing pages for the same series; each must link at least one file.
    extra_pages: tuple[str, ...] = ()


SERIES: dict[str, Series] = {
    s.name: s
    for s in (
        Series(
            "trafficstat",
            f"{BASE}/site/nypd/stats/traffic-data/traffic-data-trafficstat.page",
            "/traffic_data/traffic-statistics/ts-en-us-",
            True,
        ),
        Series(
            "fatalities",
            f"{BASE}/site/nypd/stats/traffic-data/traffic-fatalities.page",
            # The page also links cityacc-en-us.xlsx, a monthly collision
            # report last modified January 2024. It is not a weekly file and
            # is excluded by this marker.
            "/traffic_data/fatalities/traffic-fatalities-",
            False,
        ),
        Series(
            "compstat",
            f"{BASE}/site/nypd/stats/crime-statistics/borough-and-precinct-crime-stats.page",
            "/crime_statistics/cs-en-us-",
            True,
            (CITYWIDE_CRIME,),
        ),
        Series(
            "housing",
            CITYWIDE_CRIME,
            # NYCHA Housing Bureau weekly report, crime by public-housing development.
            "/crime_statistics/hb_",
            False,
        ),
        Series(
            "qteam",
            f"{BASE}/site/nypd/stats/quality-of-life-stats/quality-of-life-stats.page",
            "/qteam_data/qteam-statistics/qs-en-us-",
            True,
        ),
    )
}

FILE_EXTS = (".xlsx", ".pdf")
MANIFEST_FIELDS = ["series", "kind", "url", "found_on"]


class DiscoveryError(RuntimeError):
    pass


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v.strip())


def extract_links(html: str, page_url: str) -> list[str]:
    p = _Links()
    p.feed(html)
    return [urljoin(page_url, h) for h in p.hrefs]


def file_links(links: list[str], series: Series) -> list[str]:
    out = []
    for url in links:
        path = unquote(urlsplit(url).path)
        if path.lower().endswith(FILE_EXTS) and series.path_marker in path:
            out.append(url)
    return out


def precinct_pages(links: list[str]) -> list[str]:
    return sorted(
        {
            u
            for u in links
            if urlsplit(u).path.startswith(PRECINCT_PAGE_PREFIX) and u.endswith(".page")
        }
    )


@dataclass(frozen=True, order=True)
class ManifestRow:
    series: str
    kind: str
    url: str
    found_on: str


def _kind(url: str) -> str:
    return urlsplit(url).path.rsplit(".", 1)[-1].lower()


def _get_html(fetcher: Fetcher, url: str) -> str:
    resp = fetcher.get(url)
    if resp.status_code != 200:
        raise DiscoveryError(f"{url}: expected 200, got {resp.status_code}")
    return resp.text


def discover(fetcher: Fetcher, series_names: list[str]) -> list[ManifestRow]:
    """Fetch every listing page. Any failure raises; there is no fallback list."""
    found: dict[tuple[str, str], ManifestRow] = {}
    wanted = [SERIES[n] for n in series_names]

    def add(series: Series, url: str, page: str) -> None:
        key = (series.name, url)
        if key not in found:
            found[key] = ManifestRow(series.name, _kind(url), url, page)

    html: dict[str, str] = {}  # one fetch per page, though two series share one
    for s in wanted:
        for page in (s.page, *s.extra_pages):
            if page not in html:
                html[page] = _get_html(fetcher, page)
            files = file_links(extract_links(html[page], page), s)
            if not files:
                raise DiscoveryError(f"{page}: no {s.name} files linked; page layout changed?")
            for u in files:
                add(s, u, page)
            log("discover_page", series=s.name, page=page, files=len(files))

    if any(s.on_precinct_pages for s in wanted):
        pages = precinct_pages(extract_links(_get_html(fetcher, PRECINCT_DIRECTORY), PRECINCT_DIRECTORY))
        if not pages:
            raise DiscoveryError(f"{PRECINCT_DIRECTORY}: no precinct pages linked")
        log("discover_directory", precinct_pages=len(pages))
        for page in pages:
            links = extract_links(_get_html(fetcher, page), page)
            for s in wanted:
                if s.on_precinct_pages:
                    for u in file_links(links, s):
                        add(s, u, page)

    rows = sorted(found.values())
    for s in wanted:
        n = sum(1 for r in rows if r.series == s.name)
        log("discover_series", series=s.name, files=n)
    return rows


def write_manifest(path: Path, rows: list[ManifestRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r.__dict__)


def read_manifest(path: Path) -> list[ManifestRow]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [ManifestRow(**r) for r in csv.DictReader(f)]


def check_shrink(old: list[ManifestRow], new: list[ManifestRow], tolerance: float = 0.10) -> None:
    """Fail if a series lost more than `tolerance` of its files since the last run.

    A sudden drop usually means a page changed shape, not that NYPD stopped
    publishing. Override with --allow-shrink after looking.
    """
    for series in sorted({r.series for r in old}):
        before = sum(1 for r in old if r.series == series)
        after = sum(1 for r in new if r.series == series)
        if after < before * (1 - tolerance):
            raise DiscoveryError(
                f"{series}: manifest shrank from {before} to {after} files; "
                "check the NYPD pages, then rerun with --allow-shrink if it is real"
            )

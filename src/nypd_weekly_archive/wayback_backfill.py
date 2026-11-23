"""Backfill earlier weeks from copies the Internet Archive already holds.

Listing uses the Wayback CDX server, documented at
https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md
(read 2026-11-21):
- endpoint http://web.archive.org/cdx/search/cdx (we use https)
- matchType=prefix matches everything under a path
- filter=statuscode:200 keeps successful captures
- collapse=digest drops adjacent captures with the same content digest; it
  does not drop non-adjacent duplicates, so we also de-duplicate by digest
- output=json returns a header row of field names, then one array per capture
- fl= picks and orders the fields

Download uses the archived original bytes: https://web.archive.org/web/<timestamp>id_/<original>.
The "id_" modifier means "no content rewriting is performed on the response,
and the original, un-rewritten content is returned" (pywb documentation,
https://pywb.readthedocs.io/en/latest/manual/rewriter.html, which implements
the Wayback URL scheme). Every download is checked against the CDX digest
(base32 SHA-1 of the content) before it is kept, so a rewritten or truncated
response is never stored as a capture.

Requests are sequential, at least 3 seconds apart, and wait at least 60
seconds after a 429 or a 5xx. Five download failures in a row stop the run
(a block or a CAPTCHA page would look like that); a re-run resumes, because
every capture already in the ledger is skipped.

Two folder trees (Wayback inventory, 2026-11-23, read from archived copies of
NYPD's crime-statistics pages and the CDX folder listings):
- www.nyc.gov/assets/nypd/downloads/ from May 2017 on;
- www.nyc.gov/html/nypd/downloads/ before mid-2017, still captured now and then
  until 2026. Its captures carry both www.nyc.gov and www1.nyc.gov addresses,
  so both hosts are listed.
Before the cs-en-us- names, the citywide and Department of Correction CompStat
files were cscity.pdf and csCorrection.pdf, in the old tree only. The CDX key
ignores case, so one prefix finds every case variant.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from .capture import _stem, append_ledger, read_ledger, safe_name, sniff
from .discover import SERIES
from .github_backfill import cover_week
from .log import log
from .net import Fetcher, FetchError, utc_now_iso
from .parse import LayoutError, week_end

CDX = "https://web.archive.org/cdx/search/cdx"
FIELDS = ["timestamp", "original", "mimetype", "statuscode", "digest", "length"]
DOWNLOADS = "www.nyc.gov/assets/nypd/downloads"
OLD_TREES = ("www.nyc.gov/html/nypd/downloads", "www1.nyc.gov/html/nypd/downloads")
# Names used before cs-en-us-, old tree only (cscity.pdf 2008-2015, csCorrection.pdf 2014-2015).
OLD_NAMES = {"compstat": ("/crime_statistics/cscity.", "/crime_statistics/cscorrection.")}
MAX_FAILURES_IN_A_ROW = 5


def cdx_url(prefix: str, match_type: str = "prefix") -> str:
    q = {
        "url": prefix,
        "matchType": match_type,
        "filter": "statuscode:200",
        "collapse": "digest",
        "output": "json",
        "fl": ",".join(FIELDS),
    }
    return f"{CDX}?{urlencode(q)}"


def prefixes(series: str) -> list[tuple[str, str]]:
    """(kind, CDX prefix) for a series, from the same path marker discovery uses.

    The current tree first, then the old /html/ tree on both hosts, with any
    older file names the series used there.
    """
    marker = SERIES[series].path_marker  # e.g. /traffic_data/traffic-statistics/ts-en-us-
    out = [("xlsx", f"{DOWNLOADS}/excel{marker}"), ("pdf", f"{DOWNLOADS}/pdf{marker}")]
    for tree in OLD_TREES:
        for m in (marker, *OLD_NAMES.get(series, ())):
            out += [("xlsx", f"{tree}/excel{m}"), ("pdf", f"{tree}/pdf{m}")]
    return out


def exact_urls(series: str, names: list[str]) -> list[tuple[str, str]]:
    """(kind, address) for each named file of a series, in every tree and host.

    Listing a whole folder of the old tree can be refused (HTTP 503 five times
    running, 2026-11-23, for /html/.../excel/crime_statistics/cs-en-us-); asking
    for a few exact addresses is light. A name belongs to the series when its
    address falls under one of the series' prefixes.
    """
    out = []
    for kind, prefix in prefixes(series):
        folder = prefix.rsplit("/", 1)[0]
        for n in names:
            address = f"{folder}/{n.lower()}.{'xlsx' if kind == 'xlsx' else 'pdf'}"
            if address.lower().startswith(prefix.lower()) and (kind, address) not in out:
                out.append((kind, address))
    return out


def url_key(url: str) -> str:
    """An address without scheme, default port or case: how the CDX folds captures together."""
    rest = url.split("://", 1)[-1]
    host, _, path = rest.partition("/")
    host = host.removesuffix(":80").removesuffix(":443")
    return f"{host}/{path}".lower()


def file_stem(url: str) -> str:
    """cs-en-us-City.pdf?x=1 -> cs-en-us-city"""
    return safe_name(url.split("?", 1)[0]).rsplit(".", 1)[0].lower()


def parse_cdx(data: object) -> list[dict[str, str]]:
    if data == []:
        return []
    if not isinstance(data, list) or not data or data[0] != FIELDS:
        head = data[0] if isinstance(data, list) and data else data
        raise ValueError(f"CDX: unexpected header {head!r}")
    rows = [dict(zip(FIELDS, r, strict=True)) for r in data[1:]]
    seen: set[tuple[str, str]] = set()
    out = []
    for r in rows:
        key = (r["original"].lower(), r["digest"])
        if r["statuscode"] == "200" and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def wayback_digest(body: bytes) -> str:
    return base64.b32encode(hashlib.sha1(body).digest()).decode()  # noqa: S324 - matching the CDX field


def safe_listing_name(prefix: str) -> str:
    """A file name for a saved CDX answer: www.nyc.gov/html/.../pdf/crime_statistics/cscity. -> www-html-cscity"""
    host, _, path = prefix.partition("/")
    tree = path.split("/", 1)[0]
    last = path.rstrip("./").rsplit("/", 1)[-1].rstrip("-_.") or "all"
    return f"{host.split('.')[0]}-{tree}-{last}"


def raw_url(row: dict[str, str]) -> str:
    return f"https://web.archive.org/web/{row['timestamp']}id_/{row['original']}"


def backfill_wayback(
    fetcher: Fetcher,
    repo: Path,
    series_names: list[str],
    limit: int | None = None,
    names: list[str] | None = None,
) -> int:
    """names: keep only files with these names, without extension, any case (e.g. cs-en-us-city)."""
    keep = {n.lower() for n in names} if names else None
    raw = repo / "raw"
    ledger_path = repo / "captures.csv"
    ledger = read_ledger(ledger_path)
    have_sha = {r["sha256"] for r in ledger}
    have_wb = {(url_key(r["url"]), r["wayback_timestamp"]) for r in ledger if r["source"] == "wayback"}
    listing_dir = raw / "wayback_cdx" / datetime.now().date().isoformat()
    listing_dir.mkdir(parents=True, exist_ok=True)

    listed: list[tuple[str, str, dict[str, str]]] = []
    for s in series_names:
        queries = [(k, p, "exact") for k, p in exact_urls(s, names)] if names else [
            (k, p, "prefix") for k, p in prefixes(s)]
        for kind, prefix, match_type in queries:
            try:
                data = fetcher.get(cdx_url(prefix, match_type)).json()
            except (FetchError, ValueError) as exc:
                log("cdx_failed", series=s, kind=kind, prefix=prefix, error=str(exc))
                return 1
            (listing_dir / f"{s}-{kind}-{safe_listing_name(prefix)}.json").write_text(json.dumps(data))
            rows = [r for r in parse_cdx(data) if r["original"].lower().split("?")[0].endswith("." + kind)]
            if keep is not None:
                rows = [r for r in rows if file_stem(r["original"]) in keep]
            log("cdx_listed", series=s, kind=kind, prefix=prefix, captures=len(rows))
            listed += [(s, kind, r) for r in rows]

    # The same bytes can be listed more than once: under both trees, both hosts,
    # or two spellings of a name. Keep each digest once, at its first capture,
    # and drop any digest already in the ledger.
    held = {r["digest"] for _, _, r in listed if (url_key(r["original"]), r["timestamp"]) in have_wb}
    listed.sort(key=lambda t: t[2]["timestamp"])
    todo: list[tuple[str, str, dict[str, str]]] = []
    seen: set[str] = set(held)
    for t in listed:
        if t[2]["digest"] not in seen:
            seen.add(t[2]["digest"])
            todo.append(t)
    log("wayback_todo", listed=len(listed), already_held=len(held), to_download=len(todo))

    # Spreadsheets first, oldest first.
    todo.sort(key=lambda t: (t[1] != "xlsx", t[2]["timestamp"]))
    if limit is not None:
        todo = todo[:limit]
    status = 0
    stored = skipped = layout_errors = failed_in_a_row = 0
    for s, kind, row in todo:
        if failed_in_a_row >= MAX_FAILURES_IN_A_ROW:
            log("wayback_stopped", reason=f"{failed_in_a_row} download failures in a row", stored=stored)
            status = 1
            break
        try:
            body = fetcher.get(raw_url(row)).content
        except FetchError as exc:
            log("wayback_download_failed", url=row["original"], timestamp=row["timestamp"], error=str(exc))
            status = 1
            failed_in_a_row += 1
            continue
        if wayback_digest(body) != row["digest"]:
            log("wayback_digest_mismatch", url=row["original"], timestamp=row["timestamp"],
                bytes=len(body), starts=body[:40].decode("latin-1"))
            status = 1
            failed_in_a_row += 1
            continue
        failed_in_a_row = 0
        if sniff(body) != kind:
            log("wayback_not_a_file", url=row["original"], timestamp=row["timestamp"], content=sniff(body))
            continue
        sha = hashlib.sha256(body).hexdigest()
        if sha in have_sha:
            skipped += 1
            continue
        name = safe_name(row["original"])
        captured_day = datetime.strptime(row["timestamp"][:8], "%Y%m%d").date().isoformat()
        tmp = raw / ".incoming" / name
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(body)
        if kind == "xlsx":
            try:
                wk, src = week_end(s, tmp).isoformat(), "xlsx"
            except LayoutError as exc:
                # Kept and flagged; `redate` files it by week once the parser knows this layout.
                log("layout_error", url=row["original"], timestamp=row["timestamp"], error=str(exc))
                wk, src = captured_day, "fetch_date_layout_error"
                layout_errors += 1
        elif s == "compstat" and (week := cover_week(body)):
            # CompStat PDFs print their week on the cover.
            wk, src = week[1].isoformat(), "pdf"
        else:
            # Other PDFs are not read. Archived PDFs and spreadsheets were captured
            # at different times, so the capture date is all we know; flagged.
            wk, src = captured_day, "wayback_capture_date"
        dest = raw / s / wk / name
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}.{sha[:8]}{dest.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dest)
        url = row["original"].replace("http://", "https://", 1)
        append_ledger(ledger_path, {
            "fetched_at": utc_now_iso(), "series": s, "url": url,
            "path": dest.relative_to(repo).as_posix(), "sha256": sha, "bytes": str(len(body)),
            "last_modified": "", "week_end": wk, "date_source": src,
            "source": "wayback", "wayback_timestamp": row["timestamp"],
        })
        have_sha.add(sha)
        stored += 1
        log("wayback_captured", series=s, path=dest.relative_to(repo).as_posix(), week_end=wk,
            date_source=src, stem=_stem(url))
    incoming = raw / ".incoming"
    if incoming.exists() and not any(incoming.iterdir()):
        incoming.rmdir()
    log("wayback_backfill_done", stored=stored, duplicates=skipped, attempted=len(todo), layout_errors=layout_errors)
    return 1 if layout_errors else status

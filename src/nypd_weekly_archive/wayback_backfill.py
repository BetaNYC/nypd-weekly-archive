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
seconds after a 429 or 503.
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


def cdx_url(prefix: str) -> str:
    q = {
        "url": prefix,
        "matchType": "prefix",
        "filter": "statuscode:200",
        "collapse": "digest",
        "output": "json",
        "fl": ",".join(FIELDS),
    }
    return f"{CDX}?{urlencode(q)}"


def prefixes(series: str) -> list[tuple[str, str]]:
    """(kind, CDX prefix) for a series, from the same path marker discovery uses."""
    marker = SERIES[series].path_marker  # e.g. /traffic_data/traffic-statistics/ts-en-us-
    return [("xlsx", f"{DOWNLOADS}/excel{marker}"), ("pdf", f"{DOWNLOADS}/pdf{marker}")]


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


def raw_url(row: dict[str, str]) -> str:
    return f"https://web.archive.org/web/{row['timestamp']}id_/{row['original']}"


def backfill_wayback(fetcher: Fetcher, repo: Path, series_names: list[str], limit: int | None = None) -> int:
    raw = repo / "raw"
    ledger_path = repo / "captures.csv"
    ledger = read_ledger(ledger_path)
    have_sha = {r["sha256"] for r in ledger}
    have_wb = {(r["url"].lower(), r["wayback_timestamp"]) for r in ledger if r["source"] == "wayback"}
    listing_dir = raw / "wayback_cdx" / datetime.now().date().isoformat()
    listing_dir.mkdir(parents=True, exist_ok=True)

    todo: list[tuple[str, str, dict[str, str]]] = []
    for s in series_names:
        for kind, prefix in prefixes(s):
            try:
                data = fetcher.get(cdx_url(prefix)).json()
            except (FetchError, ValueError) as exc:
                log("cdx_failed", series=s, kind=kind, error=str(exc))
                return 1
            (listing_dir / f"{s}-{kind}.json").write_text(json.dumps(data))
            rows = [r for r in parse_cdx(data) if r["original"].lower().split("?")[0].endswith("." + kind)]
            log("cdx_listed", series=s, kind=kind, captures=len(rows))
            todo += [(s, kind, r) for r in rows]

    # Spreadsheets first, oldest first, so PDFs can borrow a dated sibling.
    todo.sort(key=lambda t: (t[1] != "xlsx", t[2]["timestamp"]))
    todo = [t for t in todo if (t[2]["original"].lower(), t[2]["timestamp"]) not in have_wb]
    if limit is not None:
        todo = todo[:limit]
    status = 0
    stored = skipped = layout_errors = 0
    for s, kind, row in todo:
        try:
            body = fetcher.get(raw_url(row)).content
        except FetchError as exc:
            log("wayback_download_failed", url=row["original"], timestamp=row["timestamp"], error=str(exc))
            status = 1
            continue
        if wayback_digest(body) != row["digest"]:
            log("wayback_digest_mismatch", url=row["original"], timestamp=row["timestamp"])
            status = 1
            continue
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

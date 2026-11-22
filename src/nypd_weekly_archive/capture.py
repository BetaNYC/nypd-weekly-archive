"""Download each file in the manifest and keep a copy only when it changed.

Layout: raw/<series>/<week_end>/<filename>, where week_end is the report's
week-ending date read from the xlsx itself. A PDF takes the date of the xlsx
with the same name in the same series (date_source=sibling_xlsx); failing
that, the fetch date (date_source=fetch_date), which is flagged in the ledger.

Idempotency: a file is stored only when its sha256 differs from the last
capture of that URL in captures.csv. We also send If-Modified-Since with the
last Last-Modified seen; nyc.gov answers 304 when nothing changed (tested
2026-11-21), which saves NYPD the bandwidth.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from .discover import ManifestRow
from .log import log
from .net import Fetcher, FetchError, utc_now_iso
from .parse import LayoutError, week_end

LEDGER_FIELDS = [
    "fetched_at",
    "series",
    "url",
    "path",
    "sha256",
    "bytes",
    "last_modified",
    "week_end",
    "date_source",
    # Added with the Wayback backfill. Rows written before it read as nyc.gov.
    "source",
    "wayback_timestamp",
    # Where the original file lives once published: raw/ is not kept in git,
    # each file goes into a zip attached to a GitHub Release (see release.py).
    "release_tag",
    "release_asset",
    # Added with the GitHub backfill: where a copy from someone else's archive
    # came from (repo, commit and path), so it can be checked against theirs.
    "source_ref",
]


# Content is identified by its first bytes, never by Content-Type: archived
# copies of these files are often labeled text/plain, and a server error page
# can arrive as a 200 (2026-11-21 archive check, team research note).
# xlsx is a ZIP container (local file header "PK\x03\x04"); PDF starts "%PDF-".
MAGIC = {"xlsx": b"PK\x03\x04", "pdf": b"%PDF-"}


def sniff(body: bytes) -> str:
    for kind, magic in MAGIC.items():
        if body.startswith(magic):
            return kind
    return "unknown"


def safe_name(url: str) -> str:
    return PurePosixPath(unquote(urlsplit(url).path)).name.replace(" ", "_")


def read_ledger(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["source"] = r.get("source") or "nyc.gov"
        r["wayback_timestamp"] = r.get("wayback_timestamp") or ""
        r["release_tag"] = r.get("release_tag") or ""
        r["release_asset"] = r.get("release_asset") or ""
        r["source_ref"] = r.get("source_ref") or ""
    return rows


def append_ledger(path: Path, row: dict[str, str]) -> None:
    row = {"source": "nyc.gov", "wayback_timestamp": "", "release_tag": "", "release_asset": "", "source_ref": "",
           **row}
    new = not path.exists()
    if not new:
        with path.open(newline="") as f:
            header = next(csv.reader(f), [])
        if header != LEDGER_FIELDS:  # older column set: rewrite once with the new header
            write_ledger(path, [*read_ledger(path), row])
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(row)


@dataclass
class CaptureResult:
    new: list[dict[str, str]] = field(default_factory=list)
    unchanged: int = 0
    failed: list[str] = field(default_factory=list)
    layout_errors: list[str] = field(default_factory=list)


def _stem(url: str) -> str:
    return safe_name(url).rsplit(".", 1)[0].lower()


def capture(
    fetcher: Fetcher,
    manifest: list[ManifestRow],
    root: Path,
    ledger_path: Path,
    today: datetime | None = None,
) -> CaptureResult:
    ledger = read_ledger(ledger_path)
    last: dict[str, dict[str, str]] = {}
    # Everything "already captured" is decided from the ledger, never from
    # files on disk: in CI raw/ is empty (originals live in GitHub Releases).
    taken = {r["path"] for r in ledger}
    for r in ledger:
        last[r["url"]] = r
    # Latest week_end known for each (series, stem) of an xlsx, for PDFs.
    xlsx_week: dict[tuple[str, str], str] = {
        (r["series"], _stem(r["url"])): r["week_end"]
        for r in ledger
        if r["url"].lower().endswith(".xlsx") and r["date_source"] == "xlsx"
    }
    fetch_day = (today or datetime.now(UTC)).date().isoformat()
    result = CaptureResult()

    # xlsx before pdf, so a PDF can take its sibling's freshly read date.
    ordered = sorted(manifest, key=lambda m: (m.kind != "xlsx", m.series, m.url))
    for m in ordered:
        prev = last.get(m.url)
        headers = {"If-Modified-Since": prev["last_modified"]} if prev and prev["last_modified"] else None
        try:
            resp = fetcher.get(m.url, headers=headers)
        except FetchError as exc:
            log("capture_failed", url=m.url, error=str(exc))
            result.failed.append(m.url)
            continue
        if resp.status_code == 304:
            result.unchanged += 1
            continue
        body = resp.content
        if sniff(body) != m.kind:
            log("capture_failed", url=m.url, error=f"content is {sniff(body)}, not {m.kind}",
                first_bytes=body[:16].hex())
            result.failed.append(m.url)
            continue
        sha = hashlib.sha256(body).hexdigest()
        if prev and prev["sha256"] == sha:
            result.unchanged += 1
            continue

        name = safe_name(m.url)
        tmp = root / ".incoming" / name
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(body)

        if m.kind == "xlsx":
            try:
                wk, src = week_end(m.series, tmp).isoformat(), "xlsx"
                xlsx_week[(m.series, _stem(m.url))] = wk
            except LayoutError as exc:
                # Keep the file anyway; preserving it is the whole point.
                log("layout_error", url=m.url, error=str(exc))
                result.layout_errors.append(f"{m.url}: {exc}")
                wk, src = fetch_day, "fetch_date_layout_error"
        else:
            sib = xlsx_week.get((m.series, _stem(m.url)))
            wk, src = (sib, "sibling_xlsx") if sib else (fetch_day, "fetch_date")

        dest = root / m.series / wk / name
        if dest.relative_to(root.parent).as_posix() in taken or dest.exists():
            # NYPD revised a file within the same week: keep both, under
            # different names, whether or not the earlier one is on disk.
            dest = dest.with_name(f"{dest.stem}.{sha[:8]}{dest.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.replace(dest)

        row = {
            "fetched_at": utc_now_iso(),
            "series": m.series,
            "url": m.url,
            "path": dest.relative_to(root.parent).as_posix(),
            "sha256": sha,
            "bytes": str(len(body)),
            "last_modified": resp.headers.get("Last-Modified", ""),
            "week_end": wk,
            "date_source": src,
        }
        append_ledger(ledger_path, row)
        taken.add(row["path"])
        last[m.url] = row
        result.new.append(row)
        log("captured", series=m.series, path=row["path"], week_end=wk, date_source=src)

    incoming = root / ".incoming"
    if incoming.exists() and not any(incoming.iterdir()):
        incoming.rmdir()
    return result


def write_ledger(path: Path, rows: list[dict[str, str]]) -> None:
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def _move(repo: Path, row: dict[str, str], wk: str) -> None:
    src = repo / row["path"]
    dest = src.parent.parent / wk / src.name
    if dest.exists() and dest != src:
        dest = dest.with_name(f"{dest.stem}.{row['sha256'][:8]}{dest.suffix}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.replace(dest)
    if not any(src.parent.iterdir()):
        src.parent.rmdir()
    row["path"] = dest.relative_to(repo).as_posix()
    row["week_end"] = wk


def redate(repo: Path, ledger_path: Path) -> list[str]:
    """Re-read files that were filed under their fetch date and move them to
    their report week. Use after fixing a parser for a layout it rejected.
    Returns the files that still do not parse."""
    rows = read_ledger(ledger_path)
    still: list[str] = []
    for r in rows:
        if r["date_source"] != "fetch_date_layout_error":
            continue
        try:
            wk = week_end(r["series"], repo / r["path"]).isoformat()
        except LayoutError as exc:
            still.append(f"{r['path']}: {exc}")
            continue
        old = r["path"]
        _move(repo, r, wk)
        r["date_source"] = "xlsx"
        log("redated", old=old, new=r["path"])
    # A PDF is re-dated only from a sibling xlsx fetched the same day.
    xlsx_week = {
        (r["series"], _stem(r["url"]), r["fetched_at"][:10]): r["week_end"]
        for r in rows
        if r["url"].lower().endswith(".xlsx") and r["date_source"] == "xlsx"
    }
    for r in rows:
        key = (r["series"], _stem(r["url"]), r["fetched_at"][:10])
        if r["date_source"] == "fetch_date" and (wk := xlsx_week.get(key)):
            old = r["path"]
            _move(repo, r, wk)
            r["date_source"] = "sibling_xlsx"
            log("redated", old=old, new=r["path"])
    write_ledger(ledger_path, rows)
    return still

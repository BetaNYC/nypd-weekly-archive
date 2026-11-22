"""Backfill earlier weeks from someone else's GitHub archive of NYPD's files.

The one source so far is ajschumacher/nypd (https://github.com/ajschumacher/nypd),
which ran archive.sh every Tuesday from 2014-01-18 to 2017-12-12: wget fetched
http://www.nyc.gov/html/nypd/html/crime_prevention/crime_statistics.shtml with
--include /html/nypd/downloads/pdf/crime_statistics and moved the PDFs into a
folder named for the day it ran (archive.sh in that repo, read 2026-11-22).
So each file is NYPD's own PDF, and its nyc.gov URL is that path plus its name.

We read a local clone rather than fetching from GitHub: one `git clone` is
one polite request, and the clone's commit is recorded on every ledger row
(source_ref), so each copy can be checked against the repo it came from.

Only CompStat files are kept: names starting "cs" (precincts, patrol
boroughs, citywide, Department of Correction). The same folders also hold
park crime reports, a Housing Bureau report and a Council parks report;
those are skipped.

Each PDF is dated by its own cover line, "Report Covering the Week
<start> Through <end>", read with pypdf. A PDF whose cover line can't be
read is kept, dated by its folder (the day it was saved), and flagged
(date_source=github_folder_date).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from pypdf import PdfReader

from .capture import _stem, append_ledger, read_ledger, sniff
from .log import log
from .net import utc_now_iso
from .parse import CS_COVER, _mdy

logging.getLogger("pypdf").setLevel(logging.ERROR)  # it warns on harmless quirks in NYPD's PDFs


@dataclass(frozen=True)
class GitHubArchive:
    repo: str  # owner/name
    series: str
    nyc_url: str  # format string with {name}
    keep: re.Pattern[str]


SOURCES = {
    "ajschumacher/nypd": GitHubArchive(
        repo="ajschumacher/nypd",
        series="compstat",
        nyc_url="http://www.nyc.gov/html/nypd/downloads/pdf/crime_statistics/{name}",
        keep=re.compile(r"cs[^/]*\.pdf"),
    ),
}
FOLDER = re.compile(r"\d{4}-\d{2}-\d{2}")


class SourceError(RuntimeError):
    pass


def _git(clone: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True, text=True).stdout.strip()


def identify(clone: Path) -> tuple[GitHubArchive, str]:
    """Which known archive a clone is, and its commit. Refuses anything else."""
    try:
        origin = _git(clone, "remote", "get-url", "origin")
        commit = _git(clone, "rev-parse", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise SourceError(f"{clone}: not a git clone ({exc})") from exc
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", origin)
    if not m or m.group(1) not in SOURCES:
        raise SourceError(f"{clone}: origin {origin!r} is not a known archive ({', '.join(SOURCES)})")
    return SOURCES[m.group(1)], commit


def cover_week(body: bytes) -> tuple[date, date] | None:
    """(week start, week end) from a CompStat PDF's first page, or None."""
    try:
        text = PdfReader(io.BytesIO(body)).pages[0].extract_text() or ""
    except Exception:  # noqa: BLE001 - any unreadable PDF is kept and flagged, never fatal
        return None
    m = CS_COVER.search(" ".join(text.split()))
    if not m:
        return None
    start, end = _mdy(m.group(1)), _mdy(m.group(2))
    # Usually 7 days; the week that crosses New Year can run 8
    # (12/28/2014 Through 1/4/2015). Anything else is not trusted.
    return (start, end) if timedelta(days=6) <= end - start <= timedelta(days=7) else None


def backfill_github(repo: Path, clone: Path, limit: int | None = None) -> int:
    src, commit = identify(clone)
    raw = repo / "raw"
    ledger_path = repo / "captures.csv"
    have_sha = {r["sha256"] for r in read_ledger(ledger_path)}
    folders = sorted(p for p in clone.iterdir() if p.is_dir() and FOLDER.fullmatch(p.name))
    files = [f for d in folders for f in sorted(d.iterdir()) if f.is_file() and src.keep.fullmatch(f.name)]
    if limit is not None:
        files = files[:limit]
    stored = duplicates = undated = 0
    for f in files:
        body = f.read_bytes()
        if sniff(body) != "pdf":
            log("github_not_a_pdf", path=f"{f.parent.name}/{f.name}")
            continue
        sha = hashlib.sha256(body).hexdigest()
        if sha in have_sha:
            duplicates += 1
            continue
        week = cover_week(body)
        if week:
            wk, ds = week[1].isoformat(), "pdf"
        else:
            wk, ds = f.parent.name, "github_folder_date"
            undated += 1
            log("github_undated", path=f"{f.parent.name}/{f.name}")
        dest = raw / src.series / wk / f.name
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}.{sha[:8]}{dest.suffix}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        url = src.nyc_url.format(name=f.name)
        append_ledger(ledger_path, {
            "fetched_at": utc_now_iso(), "series": src.series, "url": url,
            "path": dest.relative_to(repo).as_posix(), "sha256": sha, "bytes": str(len(body)),
            "last_modified": "", "week_end": wk, "date_source": ds,
            "source": f"github:{src.repo}",
            "source_ref": f"https://github.com/{src.repo}/blob/{commit}/{f.parent.name}/{f.name}",
        })
        have_sha.add(sha)
        stored += 1
        if stored % 500 == 0:
            log("github_progress", stored=stored, of=len(files), stem=_stem(url))
    log("github_backfill_done", source=src.repo, commit=commit, files=len(files), stored=stored,
        duplicates=duplicates, undated=undated)
    return 0

"""Backfill from a local clone of someone else's GitHub archive (ajschumacher/nypd)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nypd_weekly_archive.capture import read_ledger
from nypd_weekly_archive.github_backfill import SourceError, backfill_github, cover_week, identify
from nypd_weekly_archive.release import tag_for


def pdf(text: str) -> bytes:
    """A one-page PDF whose text layer is `text` (enough for pypdf to read)."""
    stream = f"BT /F1 10 Tf 20 100 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 200] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def cover(start: str, end: str) -> bytes:
    return pdf(f"CompStat 1st Precinct Report Covering the Week {start} Through {end}")


def make_clone(path: Path, files: dict[str, bytes], origin: str = "https://github.com/ajschumacher/nypd.git") -> str:
    path.mkdir()
    for rel, body in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_bytes(body)
    git = ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "remote", "add", "origin", origin], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_cover_week_reads_the_cover_line() -> None:
    assert [d.isoformat() for d in cover_week(cover("12/30/2013", "1/5/2014"))] == ["2013-12-30", "2014-01-05"]
    assert cover_week(pdf("no date here")) is None
    assert cover_week(cover("12/28/2014", "1/4/2015"))[1].isoformat() == "2015-01-04"  # New Year week: 8 days
    assert cover_week(cover("1/1/2014", "1/10/2014")) is None  # not a week
    assert cover_week(cover("1/9/2014", "1/2/2014")) is None  # backwards
    assert cover_week(b"%PDF-1.4 broken") is None


def test_identify_refuses_unknown_repos(tmp_path: Path) -> None:
    make_clone(tmp_path / "other", {"a.txt": b"x"}, origin="https://github.com/someone/else.git")
    with pytest.raises(SourceError, match="not a known archive"):
        identify(tmp_path / "other")
    with pytest.raises(SourceError, match="not a git clone"):
        identify(tmp_path)


def test_backfill_keeps_compstat_dated_by_cover_and_skips_duplicates(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    same = cover("12/30/2013", "1/5/2014")
    commit = make_clone(tmp_path / "clone", {
        "2014-01-18/cs001pct.pdf": same,
        "2014-01-18/cscity.pdf": pdf("Citywide, no cover line"),
        "2014-01-18/nyc_park_crime_stats_q3_2013.pdf": cover("12/30/2013", "1/5/2014"),  # not CompStat
        "2014-01-18/crime_statistics.shtml": b"<html>",
        "2014-01-21/cs001pct.pdf": same,  # same bytes a week later: a duplicate
        "2014-01-21/cs005pct.pdf": cover("1/6/2014", "1/12/2014"),
        "2014-01-28/cs001pct.pdf": pdf("Report Covering the Week 12/30/2013 Through 1/5/2014 corrected"),
        "archive.sh": b"#!/bin/sh",
    })

    assert backfill_github(repo, tmp_path / "clone") == 0

    rows = read_ledger(repo / "captures.csv")
    got = {(r["path"], r["week_end"], r["date_source"]) for r in rows}
    assert got == {
        ("raw/compstat/2014-01-05/cs001pct.pdf", "2014-01-05", "pdf"),
        ("raw/compstat/2014-01-18/cscity.pdf", "2014-01-18", "github_folder_date"),
        ("raw/compstat/2014-01-12/cs005pct.pdf", "2014-01-12", "pdf"),
        # same week, different bytes: kept beside the first with a hash suffix
        (next(r["path"] for r in rows if r["source_ref"].endswith("2014-01-28/cs001pct.pdf")), "2014-01-05", "pdf"),
    }
    assert sum(r["path"].startswith("raw/compstat/2014-01-05/cs001pct.") for r in rows) == 2
    first = next(r for r in rows if r["path"] == "raw/compstat/2014-01-05/cs001pct.pdf")
    assert first["source"] == "github:ajschumacher/nypd"
    assert first["url"] == "http://www.nyc.gov/html/nypd/downloads/pdf/crime_statistics/cs001pct.pdf"
    assert first["source_ref"] == f"https://github.com/ajschumacher/nypd/blob/{commit}/2014-01-18/cs001pct.pdf"
    assert (repo / first["path"]).read_bytes() == same

    # Running again stores nothing new.
    backfill_github(repo, tmp_path / "clone")
    assert len(read_ledger(repo / "captures.csv")) == len(rows)


def test_release_groups_github_copies_by_year() -> None:
    row = {"fetched_at": "2026-11-22T05:00:00+00:00", "source": "github:ajschumacher/nypd",
           "date_source": "pdf", "week_end": "2015-03-01"}
    assert tag_for(row) == ("backfill-github-2026-11-22", "ajschumacher-2015")

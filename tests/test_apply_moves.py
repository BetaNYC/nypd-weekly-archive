"""scripts/apply_moves.py replays moves.csv safely on a copy of raw/."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "apply_moves", Path(__file__).resolve().parents[1] / "scripts" / "apply_moves.py")
assert _spec and _spec.loader
am = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(am)


def _file(root: Path, rel: str, body: bytes) -> str:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _moves(root: Path, rows: list[tuple[str, str, str]]) -> Path:
    path = root / "moves.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=am.FIELDS, lineterminator="\n")
        w.writeheader()
        for sha, old, new in rows:
            w.writerow({"moved_on": "2026-11-23", "series": "compstat", "sha256": sha, "old_path": old,
                        "new_path": new, "release_tag": "t", "release_asset": "a.zip", "reason": "test"})
    return path


OLD, NEW = "raw/compstat/2017-08-06/cs-en-us-023pct.xlsx", "raw/compstat/2017-07-30/cs-en-us-023pct.xlsx"


def test_moves_matching_file_then_skips_on_rerun(tmp_path: Path) -> None:
    sha = _file(tmp_path, OLD, b"a")
    moves = _moves(tmp_path, [(sha, OLD, NEW)])
    assert am.apply_moves(tmp_path, moves)["would_move"] == 1
    assert (tmp_path / OLD).exists()  # dry run by default
    assert am.apply_moves(tmp_path, moves, apply=True)["moved"] == 1
    assert not (tmp_path / OLD).exists() and (tmp_path / NEW).read_bytes() == b"a"
    assert am.apply_moves(tmp_path, moves, apply=True) == {"already_applied": 1}


def test_checksum_mismatch_is_left_alone(tmp_path: Path) -> None:
    _file(tmp_path, OLD, b"a")
    moves = _moves(tmp_path, [("0" * 64, OLD, NEW)])
    assert am.apply_moves(tmp_path, moves, apply=True) == {"mismatch": 1}
    assert (tmp_path / OLD).read_bytes() == b"a" and not (tmp_path / NEW).exists()
    assert am.main(["--root", str(tmp_path), "--apply"]) == 1


def test_never_overwrites(tmp_path: Path) -> None:
    sha = _file(tmp_path, OLD, b"a")
    _file(tmp_path, NEW, b"something else")
    moves = _moves(tmp_path, [(sha, OLD, NEW)])
    assert am.apply_moves(tmp_path, moves, apply=True) == {"conflict": 1}
    assert (tmp_path / OLD).read_bytes() == b"a"
    assert (tmp_path / NEW).read_bytes() == b"something else"


def test_missing_and_chained_moves(tmp_path: Path) -> None:
    sha = _file(tmp_path, OLD, b"a")
    later = "raw/compstat/2017-07-23/cs-en-us-023pct.xlsx"
    moves = _moves(tmp_path, [(sha, OLD, NEW), (sha, NEW, later), ("1" * 64, "raw/x/1/a.pdf", "raw/x/2/a.pdf")])
    assert am.apply_moves(tmp_path, moves, apply=True) == {"moved": 2, "missing": 1}
    assert (tmp_path / later).read_bytes() == b"a"


def test_rejects_paths_outside_raw(tmp_path: Path) -> None:
    sha = _file(tmp_path, OLD, b"a")
    moves = _moves(tmp_path, [(sha, OLD, "raw/../../elsewhere.xlsx")])
    with pytest.raises(ValueError, match="unsafe"):
        am.apply_moves(tmp_path, moves, apply=True)
    assert (tmp_path / OLD).exists()


def test_reads_the_file_redate_writes(tmp_path: Path) -> None:
    from nypd_weekly_archive.capture import MOVES_FIELDS

    assert am.FIELDS == MOVES_FIELDS

#!/usr/bin/env python3
"""Replay moves.csv on a copy of raw/: move each file from old_path to new_path.

`nypd-archive redate` files a spreadsheet under its real report week and
records the move in moves.csv. The zips on GitHub Releases keep each file
under the path it had when the zip was made, so anyone holding raw/ (or files
unpacked from a zip) runs this to catch up:

    python scripts/apply_moves.py --root /path/to/checkout          # dry run
    python scripts/apply_moves.py --root /path/to/checkout --apply

Paths in moves.csv start with raw/ and are relative to --root.

For each row, in order:
- old file present, new path free, checksum matches: moved (with --apply).
- new path already holds the file (same checksum): already applied, skipped.
- old file's checksum differs from the row: left alone and reported.
- new path holds a different file: left alone and reported.
- neither present: reported as missing (normal for a partial copy).

It never deletes or overwrites a file. Exit status 1 if any row was a
checksum mismatch or a conflict, otherwise 0.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import sys
from collections import Counter
from pathlib import Path, PurePosixPath

FIELDS = ["moved_on", "series", "sha256", "old_path", "new_path", "release_tag", "release_asset", "reason"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe(rel: str) -> PurePosixPath:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != "raw":
        raise ValueError(f"unsafe path in moves.csv: {rel!r}")
    return p


def apply_moves(root: Path, moves: Path, apply: bool = False) -> Counter[str]:
    """Returns a count of outcomes: moved (or would_move), already_applied,
    missing, mismatch, conflict."""
    with moves.open(newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError(f"{moves}: header {reader.fieldnames} != {FIELDS}")
        rows = list(reader)
    out: Counter[str] = Counter()
    for n, r in enumerate(rows, 2):
        old, new = root / _safe(r["old_path"]), root / _safe(r["new_path"])
        want = r["sha256"]
        where = f"moves.csv line {n}"
        if new.exists():
            if sha256(new) == want:
                out["already_applied"] += 1
            else:
                out["conflict"] += 1
                print(f"{where}: {r['new_path']} holds a different file; nothing moved", file=sys.stderr)
            continue
        if not old.exists():
            out["missing"] += 1
            continue
        if sha256(old) != want:
            out["mismatch"] += 1
            print(f"{where}: {r['old_path']} does not match the recorded checksum; nothing moved", file=sys.stderr)
            continue
        if not apply:
            out["would_move"] += 1
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        # Hard-link then unlink the old name: the link fails if new appeared
        # in the meantime, so an existing file can never be replaced.
        try:
            os.link(old, new)
        except FileExistsError:
            out["conflict"] += 1
            print(f"{where}: {r['new_path']} appeared during the run; nothing moved", file=sys.stderr)
            continue
        except OSError:
            # Some network and FAT disks have no hard links: check, then rename.
            if new.exists():
                out["conflict"] += 1
                continue
            old.rename(new)
        else:
            old.unlink()  # the file now lives at new; this drops only the old name
        out["moved"] += 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="folder that holds raw/ (default: cwd)")
    ap.add_argument("--moves", type=Path, help="moves.csv (default: <root>/moves.csv)")
    ap.add_argument("--apply", action="store_true", help="move files; without it, only report")
    args = ap.parse_args(argv)
    counts = apply_moves(args.root, args.moves or args.root / "moves.csv", apply=args.apply)
    print(" ".join(f"{k}={counts[k]}" for k in
                   ["moved", "would_move", "already_applied", "missing", "mismatch", "conflict"]))
    return 1 if counts["mismatch"] or counts["conflict"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

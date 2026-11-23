"""Choose the PDFs to read, read them in parallel, and write the review files.

Phase 1 is a dry run: nothing here writes under data/. ``read-pdfs --dry-run`` writes to an
output folder outside data/ (default ``pdf-dry-run/``, git-ignored):

- ``review-<kind>.csv``  one line per flagged printed row or page, with page and bbox, and
  empty ``decision`` and ``note`` columns for a person (see PDF-READER.md for the decisions);
- ``refused-<kind>.csv`` every PDF that was not read, and why (a damaged file, an unknown layout);
- ``rows/<kind>/<week_end>.csv`` the rows that would be written, with their provenance columns;
- ``summary.json``       counts per kind and layout.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..parse import LayoutError
from .common import PDF_FIELDS, REVIEW_FIELDS, ReadResult, fmt_bbox
from .guard import PdfRefused

KINDS = ["precinct", "citywide", "correction", "housing"]


def kind_of(name: str) -> str | None:
    from .compstat import kind_of as cs_kind

    n = os.path.basename(name).lower()
    if n == "hb_eagle_report.pdf":
        return "housing"
    return cs_kind(n)


def stem(url_or_path: str) -> str:
    """A file's name without extension, folding NYPD's two naming schemes (cs001pct, cs-en-us-001pct)."""
    n = os.path.basename(url_or_path.split("?", maxsplit=1)[0]).lower().rsplit(".", 1)[0]
    return n.replace("cs-en-us-", "cs")


def ledger_pdfs(ledger: list[dict[str, str]], root: Path, kinds: Iterable[str]) -> dict[str, list[dict[str, str]]]:
    """kind -> ledger rows of PDFs of that kind that are on disk."""
    want = set(kinds)
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in ledger:
        if not r["url"].lower().split("?")[0].endswith(".pdf"):
            continue
        k = kind_of(r["path"])
        if k in want and (root / r["path"]).exists():
            out[k].append(r)
    return out


def xlsx_weeks(ledger: list[dict[str, str]]) -> dict[tuple[str, str], list[dict[str, str]]]:
    """(stem, week_end) -> ledger rows of spreadsheets dated from their own contents."""
    out: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for r in ledger:
        if r["url"].lower().split("?")[0].endswith(".xlsx") and r["date_source"] == "xlsx":
            out[(stem(r["url"]), r["week_end"])].append(r)
    return out


def latest_per_week(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """One PDF per (file, week): the last capture, as build.py does for spreadsheets."""
    last: dict[tuple[str, str], dict[str, str]] = {}
    for r in rows:
        last[(stem(r["url"]), r["week_end"])] = r
    return [last[k] for k in sorted(last)]


def _read_one(args: tuple[str, str, str, str]) -> tuple[str, ReadResult | None, str | None, str | None]:
    kind, root, path, week = args
    from .compstat import read_compstat_pdf
    from .housing import read_housing_pdf

    try:
        fn: Callable[..., ReadResult] = read_housing_pdf if kind == "housing" else read_compstat_pdf
        return path, fn(Path(root) / path, path, week or None), None, None
    except PdfRefused as exc:
        return path, None, exc.reason, exc.detail
    except LayoutError as exc:
        return path, None, "layout", str(exc)


def read_many(kind: str, root: Path, rows: list[dict[str, str]], workers: int | None = None):
    """Yield (path, result, refused_reason, refused_detail) for each ledger row, in order."""
    # A Wayback copy dated by its capture day says nothing about the report's week.
    jobs = [(kind, str(root), r["path"], r["week_end"] if r["date_source"] != "wayback_capture_date" else "")
            for r in rows]
    if workers == 1 or len(jobs) < 4:
        yield from map(_read_one, jobs)
        return
    with ProcessPoolExecutor(workers or min(8, os.cpu_count() or 1)) as ex:
        yield from ex.map(_read_one, jobs, chunksize=8)


def review_lines(kind: str, res: ReadResult) -> list[dict[str, object]]:
    out = []
    for i, f in enumerate(res.flags, 1):
        out.append({"review_id": f"{kind}:{res.week_end}:{Path(str(res.path)).name}:{i}", "kind": kind,
                    "week_end": res.week_end, "source_file": res.rows[0]["source_file"] if res.rows else "",
                    "pdf_sha256": res.sha256, "page": f.page, "bbox": fmt_bbox(f.bbox), "layout": res.layout,
                    "table": f.table, "row_label": f.row_label, "checks": ";".join(f.checks),
                    "detail": " | ".join(f.detail), "printed": f.printed, "decision": "", "note": ""})
    return out


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def dry_run(root: Path, ledger: list[dict[str, str]], kinds: list[str], out_dir: Path, *, pdf_only: bool = True,
            limit: int | None = None, weeks: set[str] | None = None, workers: int | None = None) -> dict:
    """Read the PDFs and write review files to out_dir. Never writes under root/data."""
    out_dir = out_dir.resolve()
    data = (root / "data").resolve()
    if out_dir == data or data in out_dir.parents:
        raise ValueError(f"{out_dir} is inside data/; the dry run never writes there")
    have_xlsx = xlsx_weeks(ledger)
    by_kind = ledger_pdfs(ledger, root, kinds)
    summary: dict = {}
    for kind in kinds:
        rows = latest_per_week(by_kind.get(kind, []))
        if pdf_only:
            rows = [r for r in rows if (stem(r["url"]), r["week_end"]) not in have_xlsx]
        if weeks:
            rows = [r for r in rows if r["week_end"] in weeks]
        if limit:
            rows = rows[:limit]
        review: list[dict[str, object]] = []
        refused: list[dict[str, object]] = []
        layouts: Counter[str] = Counter()
        flagged_files = 0
        n_rows = n_flagged = 0
        for path, res, reason, detail in read_many(kind, root, rows, workers):
            if res is None:
                refused.append({"source_file": path, "reason": reason, "detail": detail})
                continue
            layouts[res.layout] += 1
            review += review_lines(kind, res)
            flagged_files += bool(res.flags)
            n_rows += len(res.rows)
            n_flagged += sum(1 for r in res.rows if r["check"] != "ok")
            dest = out_dir / "rows" / kind / f"{res.week_end}.csv"
            prior: list[dict[str, object]] = []
            if dest.exists():
                with dest.open(newline="") as f:
                    prior = [r for r in csv.DictReader(f) if r["source_file"] != path]
            _write_csv(dest, PDF_FIELDS, prior + res.rows)
        _write_csv(out_dir / f"review-{kind}.csv", REVIEW_FIELDS, review)
        _write_csv(out_dir / f"refused-{kind}.csv", ["source_file", "reason", "detail"], refused)
        summary[kind] = {"pdfs": len(rows), "read": sum(layouts.values()), "refused": len(refused),
                         "refused_by_reason": dict(Counter(str(r["reason"]) for r in refused)),
                         "layouts": dict(sorted(layouts.items())), "files_with_flags": flagged_files,
                         "review_items": len(review), "numbers": n_rows, "numbers_flagged": n_flagged}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


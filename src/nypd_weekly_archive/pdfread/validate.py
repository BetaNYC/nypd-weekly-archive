"""Check the PDF reader against weeks where NYPD's spreadsheet survives too.

For every (file, week) with both a PDF and a spreadsheet on disk, the PDF is read with this
package and the spreadsheet with the repo's own parser (parse.py), which is the truth. Every
number both hold is compared. When a week has several spreadsheet captures (NYPD revises files
during the week), the PDF is compared with the capture it agrees with most, and the report
says how many captures there were.

A mismatch on a row the reader did not flag is a reader bug. A mismatch on a flagged row is the
flag working. Rows the reader flags with no mismatch are counted too: they cost a person time.

``mcdermott`` compares precinct PDFs with Tim McDermott's weekly CSVs (data/compstat_mcdermott/,
2015-2026). Those numbers were not read by this repo, so agreement there is a second opinion,
not proof; it covers older layouts that no surviving spreadsheet does.
"""

from __future__ import annotations

import csv
import gzip
from collections import Counter, defaultdict
from pathlib import Path

from ..parse import LayoutError, parse_compstat
from .run import latest_per_week, ledger_pdfs, read_many, stem, xlsx_weeks

KEY = ("section", "measure", "period", "year")


def _num(v: object) -> float | None:
    if v is None or v == "":
        return None
    return float(v)  # type: ignore[arg-type]


def _same(a: object, b: object, printed: str = "") -> bool:
    """Equal; a percent the PDF prints rounded ("-14.8") equals a spreadsheet value that rounds
    to it (-14.8148...), since NYPD's spreadsheet keeps every digit and its PDF prints one or two."""
    x, y = _num(a), _num(b)
    if x is None or y is None:
        return x is None and y is None
    t = printed.replace(",", "")
    if "." in t:
        return abs(x - y) <= 0.5 * 10 ** -len(t.split(".")[1]) + 1e-9
    return abs(x - y) < 1e-9


def _key(r: dict[str, object]) -> tuple[str, ...]:
    return tuple(str(r[k]) for k in KEY)


def _blank_stats() -> dict:
    return {"weeks": 0, "xlsx_captures": 0, "cells": 0, "matched": 0, "mismatch_ok_row": 0,
            "mismatch_flagged_row": 0, "flagged_cells": 0, "flagged_rows": 0, "pdf_only_cells": 0,
            "xlsx_only_cells": 0, "refused": 0, "examples": []}


def compare_rows(pdf_rows: list[dict[str, object]], truth: dict[tuple[str, ...], object], st: dict, where: str) -> None:
    pkeys = set()
    for r in pdf_rows:
        k = _key(r)
        pkeys.add(k)
        if r["check"] != "ok":
            st["flagged_cells"] += 1
        if k not in truth:
            st["pdf_only_cells"] += 1
            continue
        st["cells"] += 1
        if _same(r["value"], truth[k], str(r.get("printed", ""))):
            st["matched"] += 1
        elif r["check"] == "ok":
            st["mismatch_ok_row"] += 1
            if len(st["examples"]) < 20:
                st["examples"].append(f"{where} {k}: pdf {r['value']!r} spreadsheet {truth[k]!r}")
        else:
            st["mismatch_flagged_row"] += 1
    st["xlsx_only_cells"] += len(set(truth) - pkeys)


def validate_compstat(root: Path, ledger: list[dict[str, str]], kinds: list[str], workers: int | None = None) -> dict:
    """{kind: {layout: stats}} for weeks with both a PDF and a spreadsheet."""
    have = xlsx_weeks(ledger)
    by_kind = ledger_pdfs(ledger, root, kinds)
    out: dict = {}
    for kind in kinds:
        rows = [r for r in latest_per_week(by_kind.get(kind, [])) if (stem(r["url"]), r["week_end"]) in have]
        stats: dict[str, dict] = defaultdict(_blank_stats)
        for path, res, reason, detail in read_many(kind, root, rows, workers):
            ledger_row = next(r for r in rows if r["path"] == path)
            if res is None:
                st = stats[f"refused:{reason}"]
                st["refused"] += 1
                if len(st["examples"]) < 20:
                    st["examples"].append(f"{path}: {detail}")
                continue
            truths = []
            for x in have[(stem(ledger_row["url"]), ledger_row["week_end"])]:
                p = root / x["path"]
                if not p.exists():
                    continue
                try:
                    truths.append({tuple(str(v) for v in (r.section, r.measure, r.period, r.year)): r.value
                                   for r in parse_compstat(p)})
                except LayoutError:
                    continue
            st = stats[res.layout]
            if not truths:
                continue
            best = max(truths, key=lambda t: sum(_same(r["value"], t.get(_key(r)), str(r["printed"]))
                                                 for r in res.rows))
            st["weeks"] += 1
            st["xlsx_captures"] += len(truths)
            st["flagged_rows"] += sum(1 for f in res.flags if f.table != "page")
            compare_rows(res.rows, best, st, path)
        out[kind] = dict(stats)
    return out


def housing_xlsx_grid(path: Path) -> list[list[tuple[list[str], list[object]]]]:
    """Per sheet, in order: every table row of a NYCHA spreadsheet as (label cells, the 27 number
    cells). A plain cell-for-cell read with openpyxl; the year row ("CH %" nine times) fixes the
    27 columns, and a data row is one whose first number cell holds a count."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        ws.reset_dimensions()  # some files declare a size of "A1" (see PR #32)
        cols: list[int] = []
        rows = []
        for r in ws.iter_rows(values_only=True):
            vals = [v.replace("\xa0", " ").strip() if isinstance(v, str) else v for v in r]
            if sum(1 for v in vals if v == "CH %") == 9:
                cols = [i for i, v in enumerate(vals) if v is not None and v != ""][-27:]
                continue
            if not cols or len(vals) <= cols[0] or not isinstance(vals[cols[0]], (int, float)):
                continue
            labels = [str(v) for v in vals[:cols[0]] if v not in (None, "")]
            rows.append((labels, [vals[i] if i < len(vals) else None for i in cols]))
        out.append(rows)
    return out


def _hb_same(printed: str, x: object) -> bool:
    """A printed NYCHA cell against the spreadsheet's: counts exact; "CH %" printed as "-100.0%"
    against the spreadsheet's fraction (-1), at the printed precision; "***" for no percent."""
    if printed in ("***", "***.*", ""):
        return x in ("***", "***.*", None, "")
    if printed.endswith("%"):
        if not isinstance(x, (int, float)):
            return False
        return _same(float(printed[:-1].replace(",", "")), x * 100, printed[:-1])
    return _same(printed.replace(",", ""), x)


def validate_housing(root: Path, ledger: list[dict[str, str]], workers: int | None = None) -> dict:
    """{layout: stats}: every readable NYCHA PDF whose own week has a spreadsheet, cell for cell."""
    xl: dict[str, list[Path]] = defaultdict(list)
    for r in ledger:
        if r["series"] == "housing" and r["date_source"] == "xlsx" and (root / r["path"]).exists():
            xl[r["week_end"]].append(root / r["path"])
    rows = latest_per_week(ledger_pdfs(ledger, root, ["housing"]).get("housing", []))
    stats: dict[str, dict] = defaultdict(_blank_stats)
    for path, res, reason, detail in read_many("housing", root, rows, workers):
        if res is None:
            st = stats[f"refused:{reason}"]
            st["refused"] += 1
            st["examples"].append(f"{path}: {detail}")
            continue
        if res.week_end not in xl:
            continue
        st = stats[res.layout]
        st["weeks"] += 1
        st["flagged_rows"] += sum(1 for f in res.flags if f.row_label)
        best = None
        for xp in xl[res.week_end]:
            sheets = housing_xlsx_grid(xp)
            st["xlsx_captures"] += 1
            pdf_by_sheet: dict[int, list] = defaultdict(list)
            for pno, labels, texts, flagged in res.grid:
                pdf_by_sheet[pno - 2].append((labels, texts, flagged))
            tally = Counter()
            ex = []
            for si, xrows in enumerate(sheets):
                prows = pdf_by_sheet.get(si, [])
                if len(prows) != len(xrows):
                    tally["row_count_differs"] += 1
                    ex.append(f"{path} page {si + 2}: {len(prows)} PDF rows, {len(xrows)} spreadsheet rows")
                # Rows are matched by their own label (a development, a totals line) and how many
                # times it has come up on the sheet: the Viper sheets list developments in a
                # different order in the PDF and in the spreadsheet.
                def keyed(rows_, labels_of):
                    seen: Counter = Counter()
                    out_ = {}
                    for row in rows_:
                        lab = " ".join(str(labels_of(row)[-1]).split()) if labels_of(row) else ""
                        seen[lab] += 1
                        out_[(lab, seen[lab])] = row
                    return out_

                pk = keyed(prows, lambda r: [x for x in r[0] if x])
                xk = keyed(xrows, lambda r: r[0])
                tally["rows_unmatched"] += len(set(pk) ^ set(xk))
                for key in set(pk) & set(xk):
                    plab, ptexts, flagged = pk[key]
                    xlab, xvals = xk[key]
                    if [" ".join(x.split()) for x in plab if x][-2:] != [" ".join(x.split()) for x in xlab][-2:]:
                        tally["label_differs"] += 1
                        if len(ex) < 20:
                            ex.append(f"{path} page {si + 2}: labels {plab} vs {xlab}")
                    for t, x in zip(ptexts, xvals, strict=True):
                        tally["cells"] += 1
                        if _hb_same(t, x):
                            tally["matched"] += 1
                        elif flagged:
                            tally["mismatch_flagged_row"] += 1
                        else:
                            tally["mismatch_ok_row"] += 1
                            if len(ex) < 20:
                                ex.append(f"{path} page {si + 2} {plab}: pdf {t!r} spreadsheet {x!r}")
            if best is None or tally["matched"] > best[0]["matched"]:
                best = (tally, ex)
        assert best is not None
        for k, v in best[0].items():
            st[k] = st.get(k, 0) + v
        st["examples"] += best[1][:20 - len(st["examples"])]
    return dict(stats)


def load_mcdermott(root: Path) -> dict[tuple[str, str], dict[tuple[str, ...], object]]:
    """(precinct, week_end) -> {key: value} from data/compstat_mcdermott/*.csv.gz, counts and the
    two-year percent change only (Tim's other percent columns do not name their base year)."""
    out: dict[tuple[str, str], dict[tuple[str, ...], object]] = defaultdict(dict)
    for f in sorted((root / "data" / "compstat_mcdermott").glob("*.csv.gz")):
        with gzip.open(f, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                if not r["precinct"] or r["period"] in ("ytd_pct_change_long", "ytd_pct_change_mid"):
                    continue
                out[(r["precinct"], r["week_end"])][_key(r)] = r["value"]
    return out


MCD_CLASSES = ["matched", "his_blank", "his_label_shift", "rounding_tie", "flagged_row", "pdf_blank", "unexplained"]


def classify_mcdermott(res_rows: list[dict[str, object]], truth: dict[tuple[str, ...], object]) -> Counter:
    """Each number both hold, sorted into MCD_CLASSES. Only "unexplained" would point at this
    reader: his_blank (his file has no value), his_label_shift (his value is what the PDF prints
    for another crime in the same column, give or take a rounding tie: his columns were shifted
    when NYPD added a row), rounding_tie (one unit apart in the last printed digit: an exact half
    rounded the other way), pdf_blank (the PDF prints ***.*, no percent, where he has one).
    """
    out: Counter = Counter()
    byk = {_key(r): r for r in res_rows}

    def near(r: dict[str, object], t: object) -> bool:
        """Equal, or one unit apart in the last printed digit (an exact half rounded the other way)."""
        printed = str(r["printed"]).replace(",", "")
        if _same(r["value"], t, printed):
            return True
        if r["value"] == "" or t in ("", None) or "." not in printed:
            return False
        return abs(float(r["value"]) - float(t)) <= 10 ** -len(printed.split(".")[1]) + 1e-9  # type: ignore[arg-type]

    for r in res_rows:
        k = _key(r)
        if k not in truth:
            continue
        t = truth[k]
        if _same(r["value"], t, str(r["printed"])):
            out["matched"] += 1
        elif t in ("", None):
            out["his_blank"] += 1
        elif r["check"] != "ok":
            out["flagged_row"] += 1
        elif any(kk[0] == k[0] and kk[2:] == k[2:] and kk != k and near(rr, t) for kk, rr in byk.items()):
            out["his_label_shift"] += 1
        elif r["value"] == "":
            out["pdf_blank"] += 1
        elif near(r, t):
            out["rounding_tie"] += 1
        else:
            out["unexplained"] += 1
    return out


def validate_mcdermott(root: Path, ledger: list[dict[str, str]], workers: int | None = None,
                       limit: int | None = None) -> dict:
    """{layout: Counter of MCD_CLASSES, plus weeks and flagged_rows}: precinct PDFs against Tim
    McDermott's CSVs, for every precinct-week both hold."""
    tim = load_mcdermott(root)
    rows = [r for r in latest_per_week(ledger_pdfs(ledger, root, ["precinct"]).get("precinct", []))
            if (str(int(stem(r["url"])[2:5])), r["week_end"]) in tim]
    if limit:
        rows = rows[:limit]
    stats: dict[str, Counter] = defaultdict(Counter)
    for _path, res, reason, _ in read_many("precinct", root, rows, workers):
        if res is None:
            stats[f"refused:{reason}"]["files"] += 1
            continue
        st = stats[res.layout]
        st["weeks"] += 1
        st["flagged_rows"] += sum(1 for f in res.flags if f.table != "page")
        st.update(classify_mcdermott(res.rows, tim[(str(res.rows[0]["precinct"]), res.week_end)]))
    return {k: dict(v) for k, v in stats.items()}


def mcdermott_table(results: dict) -> str:
    head = ["Layout", "Weeks", "Numbers both hold", *MCD_CLASSES]
    lines = ["| " + " | ".join(head) + " |", "|---|" + "---:|" * (len(head) - 1)]
    for layout, st in sorted(results.items()):
        if layout.startswith("refused:"):
            continue
        both = sum(st.get(c, 0) for c in MCD_CLASSES)
        lines.append("| " + " | ".join([layout, f"{st.get('weeks', 0):,}", f"{both:,}",
                                         *[f"{st.get(c, 0):,}" for c in MCD_CLASSES]]) + " |")
    return "\n".join(lines)


def table(results: dict, kinds_order: list[str]) -> str:
    """A plain markdown table of the validation counts."""
    lines = ["| Series | Layout | Weeks | Cells compared | Cells matched | Mismatch, unflagged row | "
             "Mismatch, flagged row | Rows flagged |", "|---|---|---:|---:|---:|---:|---:|---:|"]
    for kind in kinds_order:
        for layout, st in sorted(results.get(kind, {}).items()):
            if layout.startswith("refused:"):
                lines.append(f"| {kind} | {layout} | {st['refused']} files | | | | | |")
                continue
            lines.append(f"| {kind} | {layout} | {st['weeks']:,} | {st['cells']:,} | {st['matched']:,} | "
                         f"{st['mismatch_ok_row']:,} | {st['mismatch_flagged_row']:,} | {st['flagged_rows']:,} |")
    return "\n".join(lines)

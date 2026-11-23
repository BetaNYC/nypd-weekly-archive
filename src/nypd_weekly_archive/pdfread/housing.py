"""Read one NYCHA Housing Bureau report PDF (hb_eagle_report.pdf) into checked rows.

The report is 36 landscape pages: a cover ("Compstat Period Ending <date>"), then one page per
spreadsheet sheet, in the spreadsheet's order: PSA 1-9 (three pages each: week to date, 28 days,
year to date), Staten Island and Rockaway (three tables stacked on one page), the Viper pages
and the Housing Authority pages (three each). Every table is fully ruled.

**Vector grid.** pdfplumber's lines strategy finds each table's cells from the drawn rules; each
character goes to the cell holding its centre (the budget reader's rule). The year row
("2025 2024 CH %" nine times) fixes the 27 number columns; cells left of them are the row's
labels (PSA, PCT, Development, Viper). NYPD merges a PSA or PCT cell down over the rows it
covers, so only the first of those rows holds it.

**Image grid.** From the rendered page alone: the drawn rules are found as long thin runs of ink
(a morphological opening, as in New-York-City-Budget ``code/ocr/grid.py``). Each vector row must
sit between two image rules, and its cells' edges must be image rules that cross that row.
The characters inside each image cell (from the text layer) must read the same as the vector
cell.

**Checks NYPD's numbers allow.** Each "CH %" against its two counts; "Total Index" against the
sum of the seven major crimes (shootings are not part of it), this year and last.

Output rows use the data/ columns. ``sheet`` is the spreadsheet sheet the page prints (by page
order); ``section`` is the PSA cell in force (carried down, as printed); ``geography`` is the
row's own label; ``precinct`` the PCT in force. How these become data/housing/ follows the
spreadsheet reader once it is merged (PR #32); phase 1 does not write them.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from ..parse import LayoutError
from . import geom
from .common import Flag, ReadResult, fmt_bbox, parse_count, parse_pct, pct_agrees
from .guard import PdfRefused, check_pdf

CRIMES = ["Murder", "Rape", "Robbery", "Fel. Assault", "Burglary", "Gr. Larceny", "GLA", "Shooting Inc.",
          "Total Index"]
MAJORS = CRIMES[:7]
_P3 = ("WTD", "28 Days", "YTD")
SHEETS = [*_P3, *[f"{k}_{i}" for i in range(2, 10) for k in _P3],
          "SI", "RK", "Viper WTD", "Viper 28 Days", "Viper YTD", "HA WTD", "HA 28 Days", "HA YTD"]
TITLES = {"WEEK TO DATE COMPARISON": "wtd", "28 DAY COMPSTAT PERIOD COMPARISON": "28d",
          "YEAR TO DATE COMPARISON": "ytd", "WEEK TO DATE COMPSTAT PERIOD COMPARISON": "wtd",
          "YEAR TO DATE COMPSTAT PERIOD COMPARISON": "ytd", "Week to Date HB and PSB Breakdown": "wtd",
          "28 Day HB and PSB Breakdown": "28d", "Year to Date HB and PSB Breakdown": "ytd"}
DATES = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4}) through ([A-Z][a-z]+ \d{1,2}, \d{4})")
COVER = re.compile(r"Compstat Period Ending\s+(?:[A-Z][a-z]+,\s+)?([A-Z][a-z]+) (\d{1,2}), (\d{4})")
LAYOUT = "hb-2014"  # 36 pages, the same tables from 2014 to 2026
# pdfplumber's default snap and join tolerances (3 pt) pull nearby rules together, which moved
# some Staten Island and Rockaway column edges 3.6 pt off the drawn rule; 1 pt keeps them on it.
TABLE_SETTINGS = {"snap_tolerance": 1, "join_tolerance": 1}
EDGE_TOL = 2.5  # pt: one line in both readings (a thick rule: 2.1 pt); a NYCHA column is >= 28 pt


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise LayoutError(msg)


def _date(s: str) -> date:
    return datetime.strptime(s, "%B %d, %Y").date()


class _HRow:
    def __init__(self, top: float, bottom: float, cells: list[tuple[list[float], list[dict]]]) -> None:
        self.top, self.bottom = top, bottom
        self.cells = cells  # (bbox, chars) left to right
        self.texts = [geom.chars_to_text(cs).replace("\n", " ").replace("\xa0", " ").strip() for _, cs in cells]
        self.checks: list[str] = []
        self.detail: list[str] = []

    def flag(self, check: str, detail: str) -> None:
        if check not in self.checks:
            self.checks.append(check)
        self.detail.append(detail)


def _plumber_dy(page) -> float:
    """pdfplumber's ``top`` minus the true distance from the page top (see geom.origin)."""
    _, oy = geom.origin(page)
    c = page.chars[0]
    return c["top"] - (oy - c["y1"])


def read_housing_pdf(path: Path, source_file: str | None = None, expected_week_end: str | None = None) -> ReadResult:
    import pdfplumber

    from .image import PageImage, render

    path = Path(path)
    info = check_pdf(path)
    src = source_file or str(path)
    where = path.name
    res: ReadResult | None = None
    with pdfplumber.open(path) as pdf:
        # One copy (week of 2023-06-09) is a 34-page report with other tables; it is refused.
        _expect(len(pdf.pages) == 36, f"{where}: {len(pdf.pages)} pages; the known layout has 36")
        cover = " ".join(ln.text for ln in geom.lines_of(geom.page_chars(pdf.pages[0])[0]))
        cm = COVER.search(cover)
        _expect(cm is not None, f"{where}: cover {cover[:80]!r}")
        week_end: date | None = None
        rows_out: list[dict[str, object]] = []
        flags: list[Flag] = []
        grid: list[tuple[int, list[str], list[str], bool]] = []
        for pno in range(2, 37):
            page = pdf.pages[pno - 1]
            sheet = SHEETS[pno - 2]
            chars, _ = geom.page_chars(page)
            if len(chars) < 200:
                raise PdfRefused("no_text_layer", f"{where}: page {pno} has {len(chars)} characters; this reader "
                                                  "never uses OCR")
            lines = geom.lines_of(chars)
            dy = _plumber_dy(page)
            ox, _ = geom.origin(page)
            tables = sorted(page.find_tables(TABLE_SETTINGS), key=lambda t: t.bbox[1])
            n_want = 3 if sheet in ("SI", "RK") else 1
            _expect(len(tables) == n_want, f"{where} page {pno}: {len(tables)} tables, expected {n_want}")
            img = PageImage(render(path, pno - 1))
            shapes = geom.dark_shapes(page)
            for ti, t in enumerate(tables):
                tb = [t.bbox[0] - ox, t.bbox[1] - dy, t.bbox[2] - ox, t.bbox[3] - dy]
                # period and dates: the nearest title line above this table
                above = [ln for ln in lines if ln.bottom <= tb[1] + 1]
                period = None
                dm = None
                for ln in reversed(above):
                    for k, v in TITLES.items():
                        if k in ln.text and period is None:
                            period = v
                    dm = dm or DATES.search(ln.text)
                    if period and dm:
                        break
                _expect(period is not None and dm is not None, f"{where} page {pno} table {ti + 1}: no title or dates")
                assert period is not None and dm is not None
                start, end = _date(dm.group(1)), _date(dm.group(2))
                if period == "wtd" and week_end is None:
                    _week_start, week_end = start, end
                _expect(week_end is None or end == week_end, f"{where} page {pno}: ends {end}, report week {week_end}")
                cells = sorted({tuple(geom.r2(v) for v in (c[0] - ox, c[1] - dy, c[2] - ox, c[3] - dy))
                                for c in t.cells}, key=lambda b: (b[1], b[0]))
                tchars = [c for c in chars if tb[0] <= (c["x0"] + c["x1"]) / 2 <= tb[2]
                          and tb[1] <= (c["top"] + c["bottom"]) / 2 <= tb[3]]
                by_cell: dict[int, list[dict]] = {}
                for c in tchars:
                    xc, yc = (c["x0"] + c["x1"]) / 2, (c["top"] + c["bottom"]) / 2
                    hit = [k for k, b in enumerate(cells) if b[0] <= xc < b[2] and b[1] <= yc < b[3]]
                    if hit:
                        by_cell.setdefault(hit[0], []).append(c)
                tops = sorted({b[1] for b in cells})
                hrows = []
                for top in tops:
                    rc = [(list(b), by_cell.get(k, [])) for k, b in enumerate(cells) if b[1] == top]
                    rc.sort(key=lambda x: x[0][0])
                    hrows.append(_HRow(top, min(b[3] for b, _ in rc), rc))
                # The year row prints "2025 2024 CH %" nine times ("% CH" in 2014-2015). Where
                # NYPD's drawing leaves out a rule, pdfplumber merges a header cell into the one
                # below (some 2021-2025 Staten Island and Rockaway pages: "Murder 2021 2020 CH %"
                # in one cell). Then the 27 number columns are taken from the first full data row,
                # and every row of that table is flagged so a person looks at the header.
                def n_pct(r: _HRow) -> int:
                    return sum(1 for x in r.texts if x in ("CH %", "% CH"))

                irregular = ""
                yr = [i for i, r in enumerate(hrows) if n_pct(r) == 9]
                if len(yr) == 1:
                    yi = yr[0]
                    num_cells = [b for b, _ in hrows[yi].cells][-27:]
                    ytexts = ["CH %" if x == "% CH" else x for x in hrows[yi].texts[-27:]]
                    cur = int(ytexts[0]) if ytexts[0].isdigit() else 0
                    _expect(ytexts == [str(cur), str(cur - 1), "CH %"] * 9, f"{where} page {pno}: year row {ytexts}")
                    ctexts = [x for x in hrows[yi - 1].texts if x] if yi > 0 else []
                    _expect([x for x in ctexts if x in CRIMES] == CRIMES, f"{where} page {pno}: crime row {ctexts}")
                else:
                    cand = [i for i, r in enumerate(hrows) if n_pct(r) >= 6]
                    _expect(len(cand) == 1, f"{where} page {pno} table {ti + 1}: year row not found")
                    yi = cand[0]
                    full = [r for r in hrows[yi + 1:] if len(r.cells) >= 28
                            and all(parse_count(x)[1] or parse_pct(x)[1] for x in r.texts[-27:])]
                    _expect(bool(full), f"{where} page {pno} table {ti + 1}: no full data row")
                    num_cells = [b for b, _ in full[0].cells][-27:]
                    years = [int(x) for r in hrows[:yi + 1] for x in " ".join(r.texts).split()
                             if re.fullmatch(r"20\d\d", x)]
                    cur = max(years) if years else end.year
                    irregular = (f"the header row reads {' | '.join(hrows[yi].texts[:5])} ...: a rule is not drawn, "
                                 "so header cells run together; the 27 columns come from the data rows")
                group = pct = ""
                for r in hrows[yi + 1:]:
                    vals: list[list[dict]] = [[] for _ in range(27)]
                    vboxes: list[list[float]] = [[] for _ in range(27)]  # type: ignore[misc]
                    for (b, cs) in r.cells:
                        k = next((k for k, nb in enumerate(num_cells)
                                  if abs(nb[0] - b[0]) < 1 and abs(nb[2] - b[2]) < 1), None)
                        if k is not None:
                            vals[k], vboxes[k] = cs, b
                        elif b[0] >= num_cells[0][0] - 1 and geom.chars_to_text(cs):
                            r.flag("vector_cell",
                                   f"text {geom.chars_to_text(cs)!r} in a cell that is not one of the 27 columns")
                    # Labels: every cell left of the numbers that spans this row, merged ones from
                    # above included, read at this row's height only. Staten Island and Rockaway
                    # pages draw no rule between developments, so one tall cell holds them all.
                    span = [b for b in cells if b[1] <= r.top + 0.5 and b[3] >= r.bottom - 0.5]
                    labels: list[tuple[list[float], str]] = []
                    for b in sorted(span, key=lambda b: b[0]):
                        if b[2] > num_cells[0][0] + 1:
                            continue
                        inside = [c for c in tchars if b[0] <= (c["x0"] + c["x1"]) / 2 < b[2]
                                  and r.top <= (c["top"] + c["bottom"]) / 2 < r.bottom]
                        labels.append((list(b), geom.chars_to_text(inside).replace("\n", " ").replace("\xa0", " ")
                                       .strip()))
                    texts27 = [geom.chars_to_text(cs) for cs in vals]
                    if not any(texts27) and not any(x for _, x in labels):
                        continue
                    if irregular:
                        r.flag("header_irregular", irregular)
                    lab = [x for _, x in labels if x]
                    if len(labels) >= 3 and labels[0][1] and labels[1][1] and re.fullmatch(r"\d{3}", labels[1][1]):
                        group, pct = labels[0][1], labels[1][1]
                    elif len(labels) >= 2 and re.fullmatch(r"\d{3}", labels[-2][1] or ""):
                        pct = labels[-2][1]
                    geography = lab[-1] if lab else ""
                    nums: list[int | None] = []
                    for g in range(9):
                        c_, ok1 = parse_count(texts27[3 * g])
                        p_, ok2 = parse_count(texts27[3 * g + 1])
                        _, ok3 = parse_pct(texts27[3 * g + 2])
                        for ok, k in ((ok1, 3 * g), (ok2, 3 * g + 1), (ok3, 3 * g + 2)):
                            if not ok:
                                r.flag("unreadable", f"{CRIMES[k // 3]}: {texts27[k]!r} is not a number")
                        if pct_agrees(texts27[3 * g + 2], c_, p_) is False:
                            r.flag("pct_mismatch", f"{CRIMES[g]} CH % {texts27[3 * g + 2]!r} does not follow from "
                                                   f"{c_} and {p_}")
                        nums += [c_, p_]
                    for y in (0, 1):
                        parts = [nums[2 * g + y] for g in range(7)]
                        tot = nums[16 + y]
                        if tot is not None and all(p is not None for p in parts) and sum(parts) != tot:  # type: ignore[arg-type]
                            r.flag("total_mismatch", f"Total Index {tot} ({cur - y}), the seven crimes add to "
                                                     f"{sum(parts)}")  # type: ignore[arg-type]
                    # image: this row lies between two drawn rules, and each of its cell edges is
                    # a vertical rule crossing it
                    hy = img.hrules_near([r.top, r.bottom], tb[0], tb[2])
                    if not all(hy):
                        r.flag("image_grid", "the page image shows no rule at this row's top or bottom")
                    # every cell that spans this row, merged ones from above included
                    # (the edges between two cells; a table's outer border is sometimes drawn grey)
                    edges = sorted({b[0] for b in span} | {b[2] for b in span})[1:-1]
                    vx = img.vrules_crossing(r.top, r.bottom, edges[0] - 4, edges[-1] + 4) if edges else []
                    missing = [e for e in edges if not any(abs(e - x) <= EDGE_TOL for x in vx)]
                    extra = [x for x in vx if not any(abs(e - x) <= EDGE_TOL for e in edges)]
                    if missing or extra:
                        r.flag("image_grid", f"cell edges the image does not show {missing[:4]}; image rules "
                                             f"the vector grid does not have {extra[:4]}")
                    else:
                        xs = sorted(vx)
                        for (b, cs) in r.cells:
                            # the image cell: between the image rules nearest this cell's edges
                            ia = min(xs, key=lambda x: abs(x - b[0])) if xs and b[0] > edges[0] - 1 else b[0]
                            ib = min(xs, key=lambda x: abs(x - b[2])) if xs and b[2] < edges[-1] + 1 else b[2]
                            inside = [c for c in chars if ia <= (c["x0"] + c["x1"]) / 2 <= ib
                                      and r.top <= (c["top"] + c["bottom"]) / 2 <= r.bottom]
                            # a tall merged cell is compared at this row's height only
                            mine = [c for c in cs if r.top <= (c["top"] + c["bottom"]) / 2 <= r.bottom]
                            if geom.chars_to_text(inside) != geom.chars_to_text(mine):
                                r.flag("image_differs", f"cell at x {b[0]:.1f}: image {geom.chars_to_text(inside)!r} "
                                                        f"vector {geom.chars_to_text(mine)!r}")
                    for k, cs in enumerate(vals):
                        blank = [c for c in cs if c["text"].strip() and
                                 img.ink_under([c["x0"], c["top"], c["x1"], c["bottom"]]) == 0]
                        if blank:
                            r.flag("no_ink", f"{CRIMES[k // 3]}: {geom.chars_to_text(blank)!r} has no ink on the page")
                    rbox = [geom.r2(r.cells[0][0][0]), geom.r2(r.top), geom.r2(r.cells[-1][0][2]), geom.r2(r.bottom)]
                    for g in range(9):
                        for y in (0, 1):
                            k = 3 * g + y
                            b = vboxes[k] or [num_cells[k][0], r.top, num_cells[k][2], r.bottom]
                            rows_out.append({
                                "series": "housing", "source_file": src, "geography": geography,
                                "precinct": int(pct) if pct else "", "week_start": start.isoformat(),
                                "week_end": end.isoformat(), "sheet": sheet, "section": group, "measure": CRIMES[g],
                                "period": period, "year": cur - y, "value": "" if nums[2 * g + y] is None
                                else nums[2 * g + y], "unit": "count", "pdf_sha256": info.sha256, "page": pno,
                                "bbox": fmt_bbox(b), "check": "flagged" if r.checks else "ok",
                                "check_notes": "; ".join(r.detail), "layout": LAYOUT, "printed": texts27[k]})
                    grid.append((pno, [x for _, x in labels], texts27, bool(r.checks)))
                    if r.checks:
                        flags.append(Flag(pno, rbox, f"{sheet} table {ti + 1}", geography, r.checks, r.detail,
                                          " | ".join([*(x for _, x in labels), *texts27])))
                # ink nobody read, inside this table
                for reg in img.coverage(tb, [[c["x0"], c["top"], c["x1"], c["bottom"]] for c in chars
                                             if c["text"].strip()], shapes):
                    flags.append(Flag(pno, reg["bbox_pt"], f"{sheet} table {ti + 1}", "", ["uncovered_ink"],
                                      [f"{reg['ink_px']} inked px that no character explains"], ""))
        _expect(week_end is not None, f"{where}: no week-to-date table")
        assert week_end is not None
        assert cm is not None
        mon = cm.group(1)[:3]
        cover_end = datetime.strptime(f"{mon} {cm.group(2)} {cm.group(3)}", "%b %d %Y").date()
        if cover_end != week_end:
            flags.append(Flag(1, [0, 0, 0, 0], "cover", "", ["cover_date"],
                              [f"cover says {cover_end}, the tables {week_end}"], cover))
        if expected_week_end and expected_week_end != week_end.isoformat():
            flags.append(Flag(1, [0, 0, 0, 0], "cover", "", ["week_mismatch"],
                              [f"captures.csv says {expected_week_end}, the tables {week_end}"], ""))
        res = ReadResult(path, "housing", LAYOUT, week_end.isoformat(), info.sha256, rows_out, flags, grid)
    return res

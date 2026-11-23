"""Read one CompStat PDF (precinct, citywide, Department of Correction) into checked rows.

Every CompStat PDF from 2009 to 2026 has one page with two tables: Crime Complaints (week to
date, 28 days and year to date, each with this year, last year and the percent change; then
three long-range percent changes) and the Historical Perspective (five calendar years and four
percent changes). Some years print part of the footer on a second page; the table never runs on.

**Vector grid (the main reading).** NYPD draws the rule above and below the TOTAL row as one
segment per column, and the Historical Perspective's bottom rule the same way. The segment end
points are the column edges. Each character goes to the column whose band holds its centre, and
to the text line it sits on. That is the budget reader's rule (``schedc/vector.py``), with the
grid taken from segmented rules rather than from boxes.

**Image grid (the second reading).** From the rendered page alone: text rows are bands of
inked pixel rows, and each row splits into inked runs at blank strips. A run's column is the
header label whose right edge it lines up with (numbers are right-aligned under their headers).
Each run's characters come from the text layer. The row agrees when the image side finds the
same label and the same text in every column as the vector side.

**Checks NYPD's own numbers allow.** Every short-range percent change against its two counts;
every Historical Perspective percent change against its two years; TOTAL against the sum of the
seven major crimes in every count column; every row label found exactly once; the Historical
Perspective's right-hand label equal to its left-hand one; every character read has ink under
it; no ink in the tables is left unexplained.

A row that fails anything is flagged (``check`` = flagged) and kept as printed. Nothing is
corrected. A page whose structure is not one of the known layouts raises LayoutError; a damaged
file raises PdfRefused (guard.py).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from ..parse import LayoutError
from . import geom
from .common import Flag, ReadResult, fmt_bbox, parse_count, parse_pct, pct_agrees
from .guard import PdfRefused, check_pdf

MAJORS = ["Murder", "Rape", "Robbery", "Fel. Assault", "Burglary", "Gr. Larceny", "G.L.A."]
HIST_LABELS = [*MAJORS, "TOTAL"]
# Every row label seen in the main table, 2009-2026, as NYPD prints it.
MAIN_LABELS = {
    *HIST_LABELS, "Transit", "Housing", "Petit Larceny", "Retail Theft", "Misd. Assault", "Misd. Sex Crimes",
    "UCR Rape*", "Other Sex Crimes", "Shooting Vic.", "Shooting Inc.", "Hate Crimes", "Traffic Fatalities",
}
SECTIONS = {"Crime Complaints", "Traffic Statistics"}

# Layouts, by the rows the main table prints (read off all 16,000 PDFs on disk, 2026-11-23).
# The first year each set appears in our copies; the 2008 citywide layout draws no column
# rules and is refused (see read_compstat_pdf).
_A = ["TOTAL"]
LAYOUTS: list[tuple[str, tuple[str, ...]]] = [
    ("cs-2009", (*MAJORS, *_A)),
    ("cs-2011", (*MAJORS, *_A, "Petit Larceny", "Misd. Assault", "Misd. Sex Crimes")),
    ("cs-2012", (*MAJORS, *_A, "Petit Larceny", "Misd. Assault", "Misd. Sex Crimes", "Shooting Inc.")),
    ("cs-2014a", (*MAJORS, *_A, "Petit Larceny", "Misd. Assault", "Misd. Sex Crimes", "Shooting Vic.",
                  "Shooting Inc.")),
    ("cs-2014", (*MAJORS, *_A, "Transit", "Housing", "Petit Larceny", "Misd. Assault", "Misd. Sex Crimes",
                 "Shooting Vic.", "Shooting Inc.")),
    ("cs-2019", (*MAJORS, *_A, "Transit", "Housing", "Petit Larceny", "Misd. Assault", "UCR Rape*",
                 "Other Sex Crimes", "Shooting Vic.", "Shooting Inc.")),
    ("cs-2020", (*MAJORS, *_A, "Transit", "Housing", "Petit Larceny", "Misd. Assault", "UCR Rape*",
                 "Other Sex Crimes", "Shooting Vic.", "Shooting Inc.", "Hate Crimes")),
    ("cs-2024", (*MAJORS, *_A, "Transit", "Housing", "Petit Larceny", "Retail Theft", "Misd. Assault",
                 "UCR Rape*", "Other Sex Crimes", "Shooting Vic.", "Shooting Inc.", "Hate Crimes",
                 "Traffic Fatalities")),
]

COVER = re.compile(r"Report Covering the Week(?: of)?\s+(\d{1,2}/\d{1,2}/\d{4})\s+Through\s+(\d{1,2}/\d{1,2}/\d{4})")
PRECINCT = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th) Precinct\b")
# Three precincts go by name, as in NYPD's spreadsheets.
NAMED_PRECINCTS = {"Central Park Precinct": 22, "Midtown Precinct North": 18, "Midtown Precinct South": 14}
LONG = re.compile(r"^(\d+)\s*Year(?:\s*\((\d{4})\))?$")
VS = re.compile(r"^'(\d\d) vs '(\d\d)$")
FILE_PCT = re.compile(r"(\d{3})pct\.pdf$", re.IGNORECASE)

IMG_MIN_GUTTER = 0.6   # pt: a blank strip this wide down every row of a table separates two columns
IMG_LABEL_GAP = 6.0    # pt: row labels end at least this far left of the first header label
IMG_ALIGN = 4.5        # pt: a column's right edge is this close to its header label's (NYPD: <= 3)


def _mdy(s: str) -> date:
    return datetime.strptime(s, "%m/%d/%Y").date()


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise LayoutError(msg)


def kind_of(name: str) -> str | None:
    """precinct, citywide or correction from a CompStat PDF's file name; None for others."""
    n = name.lower()
    if FILE_PCT.search(n) and n.startswith("cs"):
        return "precinct"
    if re.fullmatch(r"cs(-en-us-)?city\.pdf", n):
        return "citywide"
    if re.fullmatch(r"cs(-en-us-)?correction\.pdf", n):
        return "correction"
    return None


class _Row:
    """One printed row as the vector grid reads it."""

    def __init__(self, line: geom.Line, label_chars: list[dict], cells: list[list[dict]], edges: list[float],
                 trail: list[dict] | None = None) -> None:
        self.line = line
        self.label = geom.chars_to_text(label_chars)
        self.cells = cells
        self.texts = [geom.chars_to_text(c).replace("\n", " ") for c in cells]
        self.edges = edges
        self.trail = geom.chars_to_text(trail) if trail is not None else None
        self.checks: list[str] = []
        self.detail: list[str] = []
        allc = label_chars + [c for cs in cells for c in cs] + (trail or [])
        self.bbox = geom.bbox_of(allc)

    def cell_bbox(self, i: int) -> list[float]:
        if self.cells[i]:
            return geom.bbox_of(self.cells[i])
        return [geom.r2(self.edges[i]), geom.r2(self.line.top), geom.r2(self.edges[i + 1]), geom.r2(self.line.bottom)]

    def flag(self, check: str, detail: str) -> None:
        if check not in self.checks:
            self.checks.append(check)
        self.detail.append(detail)

    @property
    def printed(self) -> str:
        parts = [self.label, *[t or "_" for t in self.texts]]
        if self.trail is not None:
            parts.append(self.trail)
        return " | ".join(parts)


RIGHT_SLACK = 3.0  # pt; see _split


def _split(line: geom.Line, edges: list[float], label_right: float, trail_left: float | None = None,
           slack: float = RIGHT_SLACK):
    """A line's characters by column: (label chars, [chars per column], trailing-label chars, stray).

    Each character goes to the column whose band holds its centre (the budget reader's rule).
    NYPD right-aligns every value at the end of its column's rule segment, and a glyph's box
    runs up to about 2.6 pt past its ink (the Traffic Fatalities row's "***.*" ends 2.6 pt past
    the rule), so each band is moved RIGHT_SLACK to the right: (edge_i + 3, edge_i+1 + 3]. Not
    whole words: NYPD's glyph boxes overlap, so a "1" right-aligned in one column and "200.00"
    in the next touch in the text layer (Correction, week ending 2026-05-31, TOTAL row). Header
    labels are centred over their columns, not right-aligned, so header lines use slack=0."""
    label, trail = [], []
    cells: list[list[dict]] = [[] for _ in range(len(edges) - 1)]
    stray = []
    bands = [e + slack for e in edges]
    for c in line.chars:
        xc = (c["x0"] + c["x1"]) / 2
        if xc <= label_right + slack:
            label.append(c)
        elif trail_left is not None and xc > trail_left + slack:
            trail.append(c)
        else:
            i = geom.column_of(xc, bands)
            if i is not None:
                cells[i].append(c)
            elif c["text"].strip():
                stray.append(c)
    return label, cells, trail, stray


def _value_rule(rules: list[geom.Rule], top: float, bottom: float, min_points: int) -> geom.Rule | None:
    for r in rules:
        if top < r.y < bottom and len(r.points()) >= min_points:
            return r
    return None


def read_compstat_pdf(path: Path, source_file: str | None = None, expected_week_end: str | None = None) -> ReadResult:
    """Read and check one CompStat PDF. Raises PdfRefused or LayoutError; see the module docstring."""
    import pdfplumber

    from .image import PageImage, render

    path = Path(path)
    kind = kind_of(path.name)
    _expect(kind is not None, f"{path.name}: not a precinct, citywide or Correction CompStat PDF")
    info = check_pdf(path)
    src = source_file or str(path)
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        chars, cnotes = geom.page_chars(page)
        if len(chars) < 200:
            raise PdfRefused("no_text_layer", f"{path.name}: page 1 has {len(chars)} characters; it may be a scan, "
                                              "and this reader never uses OCR")
        lines = geom.lines_of(chars)
        texts = [ln.text for ln in lines]
        rules = geom.hrules(page)
        shapes = geom.dark_shapes(page)
        for pno, extra in enumerate(pdf.pages[1:], 2):
            etext = extra.extract_text() or ""
            _expect(not re.search(r"^(Murder|TOTAL|Robbery)\b.*\d", etext, re.M),
                    f"{path.name}: page {pno} holds table rows; this reader reads one-page tables")
        width = float(page.width)

    where = path.name
    i_wtd = next((i for i, t in enumerate(texts) if t.startswith("Week to Date")), None)
    i_hist = next((i for i, t in enumerate(texts) if t == "Historical Perspective"), None)
    _expect(i_wtd is not None and i_hist is not None and i_wtd < i_hist,
            f"{where}: 'Week to Date' header or 'Historical Perspective' heading not found")
    assert i_wtd is not None and i_hist is not None
    title = " ".join(texts[:i_wtd])
    m = COVER.search(title)
    _expect(m is not None, f"{where}: 'Report Covering the Week ... Through ...' not found in {title[:200]!r}")
    assert m is not None
    week_start, week_end = _mdy(m.group(1)), _mdy(m.group(2))
    precinct: int | None = None
    if kind == "correction":
        _expect("Department of Correction" in title and "Citywide" in title, f"{where}: Correction title {title!r}")
        geography = "Department of Correction Citywide"  # as NYPD's spreadsheet labels it
    elif kind == "citywide":
        _expect("Citywide" in title, f"{where}: no 'Citywide' in the title")
        geography = "Citywide"
    else:
        named = [n for n in NAMED_PRECINCTS if n in title]
        pm = PRECINCT.search(title)
        _expect(pm is not None or len(named) == 1, f"{where}: no 'Nth Precinct' in the title")
        if named:
            geography, precinct = named[0], NAMED_PRECINCTS[named[0]]
        else:
            assert pm is not None
            geography, precinct = pm.group(0), int(pm.group(1))
        fm = FILE_PCT.search(path.name)
        _expect(fm is not None and int(fm.group(1)) == precinct,
                f"{where}: file name says precinct {fm.group(1) if fm else '?'}, the page says {geography}")

    # ---- vector grid
    main_rule = _value_rule(rules, lines[i_wtd].top, lines[i_hist].top, 13)
    if main_rule is None:
        raise LayoutError(f"{where}: no segmented rule in the main table (the 2008 citywide layout and the "
                          "week ending 2021-08-15 draw none); without a vector grid this reader does not read the page")
    edges = main_rule.points()[-13:]
    label_right = edges[0]
    hist_top = lines[i_hist].bottom
    hist_rule = _value_rule(rules, hist_top, 10_000, 10)
    hist_one_year = False
    if hist_rule is None:
        # The Department of Correction pages of 2014-2015 print one year (the last) in the
        # Historical Perspective, with no rule. That block is not read; the page says so.
        nxt = [t for t in texts[i_hist + 1:i_hist + 4] if not t.startswith("(Historical")]
        _expect(kind == "correction" and bool(nxt) and re.fullmatch(r"\d{4}", nxt[0]) is not None,
                f"{where}: no segmented rule in the Historical Perspective")
        hist_one_year = True
        hp = [0.0] * 10
    else:
        hp = hist_rule.points()
    if hp[0] < 60 and not hist_one_year:
        hp = hp[1:]
    if hp[-1] > 560:
        hp = hp[:-1]
    _expect(len(hp) == 10, f"{where}: Historical Perspective rule has {len(hp) - 1} value columns, expected 9")

    # header block: lines from "Week to Date" to the first labeled row
    i_first = None
    for i in range(i_wtd + 1, i_hist):
        lab, *_ = _split(lines[i], edges, label_right)
        if geom.chars_to_text(lab) in MAIN_LABELS:
            i_first = i
            break
    _expect(i_first is not None, f"{where}: no data rows")
    assert i_first is not None
    header = lines[i_wtd:i_first]
    yh = [ln for ln in header if "% Chg" in ln.text or "% Change" in ln.text]
    _expect(len(yh) == 1, f"{where}: year header line not found in {[ln.text for ln in header]}")
    year_line = yh[0]
    _, ycells, _, ystray = _split(year_line, edges, label_right, slack=0)
    yt = [geom.chars_to_text(c) for c in ycells]
    _expect(not ystray, f"{where}: year header has text outside the columns")
    try:
        cur_year = int(yt[0])
    except ValueError:
        raise LayoutError(f"{where}: year header {yt}") from None
    want = [str(cur_year), str(cur_year - 1), "% Chg"] * 3 + ["% Chg"] * 3
    # The percent header reads "% Chg", "% Change" (2008-2009), or a bare "%" (the week-to-date
    # column of some 2019 citywide pages).
    norm = ["% Chg" if t in ("% Chg", "% Change", "%") else t for t in yt]
    _expect(norm == want, f"{where}: year header {yt}, expected {want}")
    _expect(cur_year == week_end.year, f"{where}: header year {cur_year}, week ends {week_end}")
    # The long-range headers ("2 Year", "16 Year", "33 Year"; some years print the base year
    # under them, "(2001)") are centred over the last three columns and sometimes broken over
    # two lines ("2" / "Year"), so they are read as the text over those columns, in order.
    # Some 2012-2013 citywide pages print "2 Year 11 Year 19" a few points above the "Week to
    # Date" line, so lines up to 8 pt above it count too.
    long_parts = []
    above = [ln for ln in lines[:i_wtd] if ln.top >= lines[i_wtd].top - 8]
    for ln in [*above, *header]:
        if ln is year_line:
            continue
        over = [c for c in ln.chars if (c["x0"] + c["x1"]) / 2 > edges[9] - 6]
        long_parts.append(geom.chars_to_text(over))
    long_txt = " ".join(long_parts)
    spans = [int(v) for v in re.findall(r"(\d+)\s*Y\s*e\s*a\s*r", long_txt)]
    printed = [int(v) for v in re.findall(r"\((\d{4})\)", long_txt)]
    # (the tail of "Year to Date*" can reach into the window: "ate*")
    long_txt = re.sub(r"^[A-Za-z]*\*\s*", "", long_txt.strip())
    loose = re.sub(r"\(\d{4}\)|\d+\s*Y\s*e\s*a\s*r|\s+", "", long_txt)
    if len(spans) != 3:
        # "2" on one line and "Year" on the next (week ending 2021-08-15)
        nums = [int(v) for v in re.findall(r"(?<![\d(])(\d{1,2})(?![\d)])", long_txt)]
        if len(nums) == 3 and long_txt.count("Year") == 3:
            spans, loose = nums, ""
    _expect(len(spans) == 3 and spans[0] == 2 and not loose and len(printed) in (0, 2),
            f"{where}: long-range header {long_txt!r}")
    bases = [cur_year - n for n in spans]
    bases_printed = bool(printed)
    _expect(printed in ([], bases[1:]), f"{where}: long-range header prints {printed}, {cur_year} - {spans} = {bases}")

    # main table rows
    section = "Crime Complaints"
    main: list[tuple[str, _Row]] = []
    seen: set[str] = set()
    body = list(lines[i_first:i_hist])
    offset_labels: set[int] = set()
    for i, ln in enumerate(body):
        # A label printed on its own line up to 6 pt off its numbers (2013 citywide, "Shooting
        # Vic."): the two lines are one row, and the row is flagged.
        lab, *_ = _split(ln, edges, label_right)
        if not lab and ln.text not in SECTIONS:
            near = [j for j in (i - 1, i + 1) if 0 <= j < len(body) and abs(body[j].yc - ln.yc) <= 6
                    and all((c["x0"] + c["x1"]) / 2 <= label_right + RIGHT_SLACK for c in body[j].chars)]
            if len(near) == 1:
                j = near[0]
                body[i] = geom.Line(min(ln.top, body[j].top), max(ln.bottom, body[j].bottom),
                                    sorted(ln.chars + body[j].chars, key=lambda c: c["x0"]))
                body[j] = geom.Line(body[j].top, body[j].bottom, [])
                offset_labels.add(i)
    for i, ln in enumerate(body):
        if not ln.chars:
            continue
        if ln.text in SECTIONS:
            section = ln.text
            continue
        if ln.text.startswith("(") and re.fullmatch(r"(\(\d{4}\)\s*)+", ln.text):
            continue
        lab, cells, _, stray = _split(ln, edges, label_right)
        row = _Row(ln, lab, cells, edges)
        _expect(row.label in MAIN_LABELS, f"{where}: unknown row {ln.text!r}")
        _expect(row.label not in seen, f"{where}: row {row.label!r} printed twice")
        seen.add(row.label)
        if stray:
            row.flag("vector_stray", f"text outside every column: {geom.chars_to_text(stray)!r}")
        if i in offset_labels:
            row.flag("label_offset", "the label is printed on its own line, a few points off its numbers")
        main.append((section, row))
    labels = tuple(r.label for _, r in main)
    layout = next((name for name, want_l in LAYOUTS if want_l == labels), None)
    _expect(layout is not None, f"{where}: rows {labels} match no known layout")
    if bases_printed:
        layout = f"{layout}+bases"

    if hist_one_year:
        layout = f"{layout}+hist1"
    # historical perspective
    hist: list[_Row] = []
    years: list[int] = []
    hvs: list[str] = []
    i_h1 = i_hist + 1
    if not hist_one_year:
        i_h1 = None
        for i in range(i_hist + 1, len(lines)):
            lab, *_ = _split(lines[i], hp, hp[0], hp[-1])
            if geom.chars_to_text(lab) in HIST_LABELS:
                i_h1 = i
                break
        _expect(i_h1 is not None, f"{where}: no Historical Perspective rows")
        assert i_h1 is not None
        hyears: list[int | None] = [None] * 5
        hvs = [""] * 4
        for ln in lines[i_hist + 1:i_h1]:
            if ln.text.startswith("(Historical perspective"):
                continue
            _, cs, _, _ = _split(ln, hp, hp[0], hp[-1], slack=0)
            for k in range(5):
                t = geom.chars_to_text(cs[k])
                if re.fullmatch(r"\d{4}", t):
                    _expect(hyears[k] is None, f"{where}: two years over Historical column {k}")
                    hyears[k] = int(t)
            for k in range(4):
                t = geom.chars_to_text(cs[5 + k])
                if VS.fullmatch(t):
                    hvs[k] = t
        _expect(all(y is not None for y in hyears) and all(hvs), f"{where}: Historical header {hyears} {hvs}")
        years = [y for y in hyears if y is not None]
        for k, t in enumerate(hvs):
            vm = VS.fullmatch(t)
            assert vm is not None
            _expect((int(vm.group(1)), int(vm.group(2))) == (years[4] % 100, years[3 - k] % 100),
                    f"{where}: Historical header {t!r} does not match years {years}")
        hseen: set[str] = set()
        for ln in lines[i_h1:]:
            lab, cells, trail, stray = _split(ln, hp, hp[0], hp[-1])
            label = geom.chars_to_text(lab)
            if label not in HIST_LABELS:
                break
            _expect(label not in hseen, f"{where}: Historical row {label!r} twice")
            hseen.add(label)
            row = _Row(ln, lab, cells, hp, trail)
            if stray:
                row.flag("vector_stray", f"text outside every column: {geom.chars_to_text(stray)!r}")
            if row.trail != row.label:
                row.flag("trailing_label", f"right-hand label {row.trail!r} differs from {row.label!r}")
            hist.append(row)
            if label == "TOTAL":
                break
        _expect([r.label for r in hist] == HIST_LABELS, f"{where}: Historical rows {[r.label for r in hist]}")

    # ---- values and NYPD's own arithmetic
    def count(row: _Row, i: int) -> int | None:
        v, ok = parse_count(row.texts[i])
        if not ok:
            row.flag("unreadable", f"column {i + 1}: {row.texts[i]!r} is not a count")
        return v

    def pct(row: _Row, i: int) -> float | None:
        v, ok = parse_pct(row.texts[i])
        if not ok:
            row.flag("unreadable", f"column {i + 1}: {row.texts[i]!r} is not a percent")
        return v

    mvals: dict[str, list] = {}
    for _, row in main:
        v: list = [None] * 12
        for g, period in enumerate(["wtd", "28d", "ytd"]):
            c, p = count(row, 3 * g), count(row, 3 * g + 1)
            v[3 * g], v[3 * g + 1] = c, p
            pct(row, 3 * g + 2)
            ok = pct_agrees(row.texts[3 * g + 2], c, p)
            if ok is False:
                row.flag("pct_mismatch", f"{period} % {row.texts[3 * g + 2]!r} does not follow from {c} and {p}")
        for k in range(3):
            v[9 + k] = pct(row, 9 + k)
        mvals[row.label] = v
    hvals: dict[str, list] = {}
    for row in hist:
        v = [count(row, k) for k in range(5)] + [pct(row, 5 + k) for k in range(4)]
        for k in range(4):
            ok = pct_agrees(row.texts[5 + k], v[4], v[3 - k])
            if ok is False:
                row.flag("pct_mismatch", f"{hvs[k]} % {row.texts[5 + k]!r} does not follow from {v[4]} and {v[3 - k]}")
        hvals[row.label] = v

    def sums(rows: list[_Row], vals: dict[str, list], cols: list[int], what: str) -> None:
        for i in cols:
            parts = [vals[lbl][i] for lbl in MAJORS]
            tot = vals["TOTAL"][i]
            if tot is None or any(p is None for p in parts):
                continue
            if sum(parts) != tot:
                for row in rows:
                    if row.label in HIST_LABELS:
                        row.flag("total_mismatch", f"{what} column {i + 1}: TOTAL {tot}, the seven crimes add to "
                                                   f"{sum(parts)}")

    sums([r for _, r in main], mvals, [0, 1, 3, 4, 6, 7], "main")
    if hist:
        sums(hist, hvals, [0, 1, 2, 3, 4], "Historical")

    # ---- the page image
    img = PageImage(render(path, 0))
    all_rows = [r for _, r in main] + hist

    def chars_in(a: float, b: float, y0: float, y1: float) -> list[dict]:
        return [c for c in chars if a <= (c["x0"] + c["x1"]) / 2 <= b and y0 <= (c["top"] + c["bottom"]) / 2 <= y1]

    def row_band(row: _Row) -> tuple[float, float] | None:
        bands = [b for b in img.bands(0, width, row.line.top - 4, row.line.bottom + 4)
                 if b[0] <= row.line.yc <= b[1]]
        if len(bands) != 1:
            row.flag("image_no_row", "the page image shows no single text row here")
            return None
        others = [ln for ln in lines if ln is not row.line and bands[0][0] < ln.yc < bands[0][1]]
        if others:
            row.flag("image_merged_rows", f"the image row also holds {others[0].text!r}")
        return bands[0]

    def header_labels(header_lines: list[geom.Line]) -> list[list[float]]:
        """Header labels, left to right, as ink spans. A label is a printed word of the text
        layer ("2026", "Chg"), joined to the next where NYPD's label is two or three words
        ("% Chg", "'25 vs '01"); its extent is the ink under it. Labels of several header lines
        that overlap are one column's."""
        labels: list[list[dict]] = []
        for ln in header_lines:
            words: list[list[dict]] = []
            for c in ln.chars:
                if not c["text"].strip():
                    words.append([])
                elif words and words[-1] and c["x0"] - words[-1][-1]["x1"] <= 1.5 and c["text"] not in "%'":
                    words[-1].append(c)
                else:
                    words.append([c])
            first = len(labels)
            for w in [w for w in words if w]:
                t = "".join(c["text"] for c in w)
                prev = "".join(c["text"] for c in labels[-1]) if len(labels) > first else ""
                if prev and (t in ("Chg", "Change", "vs") or prev.endswith("vs")):
                    labels[-1].extend(w)
                else:
                    labels.append(list(w))
        spans: list[list[float]] = []
        for lab in labels:
            x0, top, x1, bottom = geom.bbox_of(lab)
            a, b = img.ink_span(x0, top, x1, bottom)
            if a is not None:
                spans.append([a, b])
        # Labels on different header lines that overlap name one column; labels that only
        # touch ("2014" and "% Chg" in 2015 precinct pages) do not.
        merged: list[list[float]] = []
        for a, b in sorted(spans):
            if merged and a < merged[-1][1] - 1.0:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return merged

    def image_table(rows: list[_Row], header_lines: list[geom.Line], want: int, trail_ok: bool, what: str) -> None:
        """The image grid for one table: rows from pixel bands, columns from the blank strips
        that run down every row, each column named by the header label nearest its right edge."""
        hdr = header_labels(header_lines)
        bands = {id(r): row_band(r) for r in rows}
        live = [b for b in bands.values() if b is not None]
        cols = img.columns(live, 0, width, IMG_MIN_GUTTER) if live else []
        problem = None
        if len(hdr) != want:
            problem = f"the page image shows {len(hdr)} {what} header labels, expected {want}"
        # Each strip of ink that ends at a header label's right edge is that label's column;
        # strips left of it that end nowhere near a label are the left parts of its wider
        # numbers (right-aligned), so they join it.
        spans: dict[object, tuple[float, float]] = {}
        if problem is None:
            pending: float | None = None
            last: object = None
            for a, b in cols:
                if b < hdr[0][0] - IMG_LABEL_GAP:
                    lo = spans.get("label", (a, b))[0]
                    spans["label"] = (lo, b)
                    continue
                if trail_ok and a > hdr[-1][1]:
                    lo = spans.get("trail", (a, b))[0]
                    spans["trail"] = (lo, b)
                    continue
                k = min(range(want), key=lambda k: abs(hdr[k][1] - b))
                start = a if pending is None else pending
                if abs(hdr[k][1] - b) <= IMG_ALIGN:
                    if k in spans and last == k:
                        # "***." then "*": both end near the label; the last one closes it.
                        spans[k] = (spans[k][0], b)
                    elif k in spans:
                        problem = f"two image columns end at header label {k + 1} (x {b:.1f})"
                        break
                    else:
                        spans[k] = (start, b)
                    pending, last = None, k
                else:
                    pending = start
            if problem is None and pending is not None:
                problem = f"ink from x {pending:.1f} ends at no header label"
        for r in rows:
            band = bands[id(r)]
            if band is None:
                continue
            if problem:
                r.flag("image_grid", problem)
                continue
            y0, y1 = band
            got: dict[object, str] = {}
            for key, (a, b) in spans.items():
                t = geom.chars_to_text(chars_in(a, b, y0, y1)).replace("\n", " ")
                if t:
                    got[key] = t
            diffs = []
            if got.get("label", "") != r.label:
                diffs.append(f"label image {got.get('label', '')!r}")
            for k, t in enumerate(r.texts):
                if got.get(k, "") != t:
                    diffs.append(f"column {k + 1} image {got.get(k, '')!r} vector {t!r}")
            if trail_ok and got.get("trail", "") != (r.trail or ""):
                diffs.append(f"right label image {got.get('trail', '')!r}")
            if diffs:
                r.flag("image_differs", "; ".join(diffs))

    image_table([r for _, r in main], [year_line], 12, False, "main")
    if hist:
        # The year line and the "'25 vs '01" line(s) name the columns; a "%Chg" line above
        # them is centred differently and would add four labels.
        hlines = [ln for ln in lines[i_hist + 1:i_h1] if re.search(r"\d{4}|vs", ln.text)
                  and not ln.text.startswith("(Historical")]
        image_table(hist, hlines, 9, True, "Historical")

    # ink: every character read has ink; no ink in the tables is left unexplained
    for row in all_rows:
        for k, cs in enumerate(row.cells):
            blank = [c for c in cs
                     if c["text"].strip() and img.ink_under([c["x0"], c["top"], c["x1"], c["bottom"]]) == 0]
            if blank:
                row.flag("no_ink", f"column {k + 1}: {geom.chars_to_text(blank)!r} has no ink on the page")
    last_line = hist[-1].line if hist else main[-1][1].line
    roi = [0.0, year_line.top - 1, width, last_line.bottom + 2]
    regions = img.coverage(roi, [[c["x0"], c["top"], c["x1"], c["bottom"]] for c in chars if c["text"].strip()], shapes)
    page_flags: list[Flag] = []
    for reg in regions:
        b = reg["bbox_pt"]
        hit = [r for r in all_rows if r.line.top - 1 < b[3] and b[1] < r.line.bottom + 1]
        for r in hit:
            r.flag("uncovered_ink", f"{reg['ink_px']} inked px at {fmt_bbox(b)} that no character explains")
        if not hit:
            page_flags.append(Flag(1, b, "page", "", ["uncovered_ink"],
                                   [f"{reg['ink_px']} inked px at {fmt_bbox(b)} that no character explains"], ""))
    if cnotes["overprint"]:
        page_flags.append(Flag(1, [0, 0, width, 0], "page", "", ["overprint"],
                               [f"{cnotes['overprint']} glyphs drawn twice at one spot were read once"], ""))
    if hist_one_year:
        page_flags.append(Flag(1, geom.bbox_of([c for ln in lines[i_hist:] for c in ln.chars if c["text"].strip()][:1]
                                              or year_line.chars), "page", "Historical Perspective",
                               ["not_read"], ["one-year Historical Perspective with no drawn rule; not read"], ""))
    if expected_week_end and expected_week_end != week_end.isoformat():
        page_flags.append(Flag(1, geom.bbox_of(year_line.chars), "page", "",
                               ["week_mismatch"], [f"captures.csv says {expected_week_end}, the page {week_end}"],
                               ""))

    # ---- rows out
    res = ReadResult(path, kind, layout, week_end.isoformat(), info.sha256)
    base = {"series": "compstat", "source_file": src, "geography": geography,
            "precinct": "" if precinct is None else precinct, "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(), "sheet": "CompStat", "pdf_sha256": info.sha256, "page": 1,
            "layout": layout}

    def out(row: _Row, section: str, i: int, period: str, year: int, value, unit: str) -> None:
        res.rows.append({**base, "section": section, "measure": row.label, "period": period, "year": year,
                         "value": "" if value is None else value, "unit": unit,
                         "bbox": fmt_bbox(row.cell_bbox(i)), "check": "flagged" if row.checks else "ok",
                         "check_notes": "; ".join(row.detail), "printed": row.texts[i]})

    for section, row in main:
        v = mvals[row.label]
        for g, period in enumerate(["wtd", "28d", "ytd"]):
            out(row, section, 3 * g, period, cur_year, v[3 * g], "count")
            out(row, section, 3 * g + 1, period, cur_year - 1, v[3 * g + 1], "count")
        for k, b in enumerate(bases):
            out(row, section, 9 + k, f"ytd_pct_change_vs_{b}", cur_year, v[9 + k], "percent")
    for row in hist:
        v = hvals[row.label]
        for k, y in enumerate(years):
            out(row, "Historical Perspective", k, "calendar_year", y, v[k], "count")
    for table, rows_ in (("main", [r for _, r in main]), ("historical", hist)):
        for r in rows_:
            if r.checks:
                res.flags.append(Flag(1, r.bbox, table, r.label, r.checks, r.detail, r.printed))
    res.flags.extend(page_flags)
    return res

"""The text layer and drawn rules of one PDF page, in points from the page's top-left corner.

Borrowed from BetaNYC/New-York-City-Budget ``code/schedc/vector.py`` (MIT, BetaNYC):
``origin``, ``shift``, ``chars_to_text``, ``ZERO_WIDTH``, ``LINE_TOL``, ``X_TOLERANCE`` and
``dark`` (``_dark`` there), unchanged except for names and type hints. The budget reader
assigns each PDF character to the one cell that holds its centre; so does this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Two characters are on one text line when their tops are this close (pt). NYPD's CompStat
# body text is 9-9.5 pt on a 12.2 pt pitch, so 2 pt joins a line without merging neighbours.
LINE_TOL = 2.0
# A gap wider than this between two glyphs on a line is a word break (pdfplumber's default).
X_TOLERANCE = 3.0
ZERO_WIDTH = {"\u200b", "‌", "‍", "﻿", "\xad"}


def r2(v: float) -> float:
    return round(float(v) + 0.0, 2)


def bbox_of(chars: list[dict]) -> list[float]:
    return [r2(min(c["x0"] for c in chars)), r2(min(c["top"] for c in chars)),
            r2(max(c["x1"] for c in chars)), r2(max(c["bottom"] for c in chars))]


def origin(page) -> tuple[float, float]:
    """The page box's left edge and top edge, in PDF user space (y up).

    The budget reader shifts by pdfplumber's ``page.bbox`` corner. That is not enough here:
    NYPD's 2015-2026 CompStat PDFs declare an upside-down MediaBox, [0 0 612 -792], for which
    pdfplumber reports ``top`` values 1,584 pt too large. So positions are taken from the
    PDF's own ``y0``/``y1`` and the box as written (the crop box, which is what pdfium renders):
    top = box top - y1. On an ordinary page this is the same as pdfplumber's ``top``.
    """
    box = page.page_obj.cropbox or page.page_obj.mediabox
    return min(box[0], box[2]), max(box[1], box[3])


def shift(objs: list[dict], ox: float, oy: float) -> list[dict]:
    """pdfplumber objects with x0/x1/top/bottom measured from the page box's top-left corner."""
    return [dict(o, x0=o["x0"] - ox, x1=o["x1"] - ox, top=oy - o["y1"], bottom=oy - o["y0"]) for o in objs]


def chars_to_text(chars: list[dict], x_tolerance: float = X_TOLERANCE) -> str:
    """Characters -> raw text. Lines top to bottom joined with newlines; glyphs left to right,
    with a space where the PDF has a space glyph or the gap exceeds x_tolerance. Nothing else
    is changed."""
    if not chars:
        return ""
    lines: list[list] = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        if lines and abs(c["top"] - lines[-1][0]) <= LINE_TOL:
            lines[-1][1].append(c)
        else:
            lines.append([c["top"], [c]])
    out = []
    for _, cs in lines:
        cs.sort(key=lambda c: c["x0"])
        s: list[str] = []
        prev = None
        for c in cs:
            if prev is not None and c["x0"] - prev["x1"] > x_tolerance and s and s[-1] != " " and c["text"] != " ":
                s.append(" ")
            s.append(c["text"])
            prev = c
        out.append("".join(s).strip(" "))
    return "\n".join(ln for ln in out if ln != "")


def dark(color) -> bool:
    """pdfplumber colour -> True if it renders darker than mid-grey."""
    if color is None:
        return True
    if isinstance(color, (int, float)):
        return color < 0.5
    c = list(color)
    if len(c) == 1:
        return c[0] < 0.5
    if len(c) == 3:
        return sum(c) / 3 < 0.5
    if len(c) == 4:  # CMYK
        return (1 - min(1, c[0] + c[3])) * 0.3 + (1 - min(1, c[1] + c[3])) * 0.59 + \
            (1 - min(1, c[2] + c[3])) * 0.11 < 0.5
    return True


@dataclass
class Line:
    """One text line: its characters left to right, and their joined text."""

    top: float
    bottom: float
    chars: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return chars_to_text(self.chars)

    @property
    def yc(self) -> float:
        return (self.top + self.bottom) / 2


def page_chars(page) -> tuple[list[dict], dict[str, int]]:
    """The page's characters, shifted to the page corner. Zero-width glyphs are left out; space
    glyphs are kept (they are NYPD's word breaks) but carry no ink. Exact overprints (the same
    glyph drawn twice at one spot) are counted and dropped, so a doubled glyph cannot read as
    two digits."""
    ox, oy = origin(page)
    raw = shift(page.chars, ox, oy)
    notes = {"zero_width": 0, "overprint": 0}
    seen: set[tuple] = set()
    out = []
    for c in raw:
        if c["text"] in ZERO_WIDTH:
            notes["zero_width"] += 1
            continue
        if not c["text"].strip():
            out.append(c)
            continue
        key = (c["text"], round(c["x0"], 1), round(c["top"], 1))
        if key in seen:
            notes["overprint"] += 1
            continue
        seen.add(key)
        out.append(c)
    return out, notes


def lines_of(chars: list[dict]) -> list[Line]:
    """Characters grouped into text lines, top to bottom."""
    out: list[Line] = []
    for c in sorted(chars, key=lambda c: (c["top"], c["x0"])):
        if out and abs(c["top"] - out[-1].top) <= LINE_TOL:
            out[-1].chars.append(c)
            out[-1].bottom = max(out[-1].bottom, c["bottom"])
        else:
            out.append(Line(c["top"], c["bottom"], [c]))
    for ln in out:
        ln.chars.sort(key=lambda c: c["x0"])
    return out


@dataclass
class Rule:
    """A horizontal rule drawn as one or more abutting segments."""

    y: float
    segments: list[tuple[float, float]]

    def points(self, tol: float = 0.5) -> list[float]:
        """Segment end points, left to right, merged within tol: the column edges."""
        pts: list[float] = []
        for v in sorted([a for a, _ in self.segments] + [b for _, b in self.segments]):
            if not pts or v - pts[-1] > tol:
                pts.append(r2(v))
        return pts


def hrules(page, merge: float = 1.0) -> list[Rule]:
    """Dark horizontal rules, from drawn lines and from thin filled boxes."""
    ox, oy = origin(page)
    raw: list[tuple[float, float, float]] = []
    for ln in shift(page.lines, ox, oy):
        if abs(ln["top"] - ln["bottom"]) <= 1.0 and dark(ln.get("stroking_color")):
            raw.append(((ln["top"] + ln["bottom"]) / 2, ln["x0"], ln["x1"]))
    for r in shift(page.rects, ox, oy):
        if r["bottom"] - r["top"] <= 1.5 and r.get("fill") and dark(r.get("non_stroking_color")):
            raw.append(((r["top"] + r["bottom"]) / 2, r["x0"], r["x1"]))
    groups: list[list[tuple[float, float, float]]] = []
    for y, a, b in sorted(raw):
        if groups and y - groups[-1][-1][0] <= merge:
            groups[-1].append((y, a, b))
        else:
            groups.append([(y, a, b)])
    return [Rule(r2(sum(v[0] for v in g) / len(g)), sorted((a, b) for _, a, b in g)) for g in groups]


def dark_shapes(page) -> list[tuple[float, float, float, float]]:
    """Every dark vector mark on the page, as (x0, top, x1, bottom) in pt from the page corner.
    Ported from New-York-City-Budget ``code/schedc_cv/ink.py`` ``rule_shapes``."""
    ox, oy = origin(page)
    out = []
    for r in shift(page.rects, ox, oy):
        if r.get("fill") and dark(r.get("non_stroking_color")):
            out.append((r["x0"], r["top"], r["x1"], r["bottom"]))
        if r.get("stroke") and dark(r.get("stroking_color")):
            lw = (r.get("linewidth") or 1) / 2
            x0, t, x1, b = r["x0"], r["top"], r["x1"], r["bottom"]
            out += [(x0 - lw, t - lw, x1 + lw, t + lw), (x0 - lw, b - lw, x1 + lw, b + lw),
                    (x0 - lw, t - lw, x0 + lw, b + lw), (x1 - lw, t - lw, x1 + lw, b + lw)]
    for ln in shift(page.lines, ox, oy):
        if dark(ln.get("stroking_color")):
            lw = (ln.get("linewidth") or 1) / 2
            out.append((ln["x0"] - lw, ln["top"] - lw, ln["x1"] + lw, ln["bottom"] + lw))
    return out


def column_of(x: float, edges: list[float]) -> int | None:
    """Index i of the band (edges[i], edges[i+1]] holding x, or None."""
    for i in range(len(edges) - 1):
        if edges[i] < x <= edges[i + 1]:
            return i
    return None

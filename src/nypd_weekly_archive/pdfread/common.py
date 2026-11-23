"""What every PDF reader here returns: tidy rows with provenance, and flags for a person."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..parse import FIELDS

# The data/ columns, then where each number was read and whether it passed every check.
#   pdf_sha256   the exact bytes read (source_file is the PDF's path in raw/)
#   page         1-based page number
#   bbox         x0 top x1 bottom, in points from the page's top-left corner (1/72 inch)
#   check        ok, or flagged: the printed row failed a check and waits for a person
#   check_notes  the failed checks, ";"-separated, with what each found
#   layout       the layout the page was read as (PDF-READER.md lists them)
#   printed      the cell's text exactly as printed ("1,407", "-12.66", "***.*")
PROVENANCE = ["pdf_sha256", "page", "bbox", "check", "check_notes", "layout", "printed"]
PDF_FIELDS = [*FIELDS, *PROVENANCE]
REVIEW_FIELDS = ["review_id", "kind", "week_end", "source_file", "pdf_sha256", "page", "bbox", "layout", "table",
                 "row_label", "checks", "detail", "printed", "decision", "note"]


def fmt_bbox(b: list[float] | tuple[float, ...]) -> str:
    return " ".join(f"{v:.2f}" for v in b)


@dataclass
class Flag:
    """One printed row (or one page) a person must decide."""

    page: int
    bbox: list[float]
    table: str
    row_label: str
    checks: list[str]
    detail: list[str]
    printed: str


@dataclass
class ReadResult:
    path: Path
    kind: str
    layout: str
    week_end: str
    sha256: str
    rows: list[dict[str, object]] = field(default_factory=list)
    flags: list[Flag] = field(default_factory=list)
    # NYCHA only: every printed table row as (page, label cells, the 27 number cells as printed,
    # flagged), in page order, for the cell-for-cell check against the spreadsheet.
    grid: list[tuple[int, list[str], list[str], bool]] = field(default_factory=list)


COUNT = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
PCT = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
# Markers NYPD prints where a percent cannot be computed (parse.NULL_PCT, plus "*.*").
PCT_NULL = {"***.*", "***", "*.*", "/0", "n/a"}


def parse_count(text: str) -> tuple[int | None, bool]:
    """(value, readable). A blank cell is (None, True)."""
    if text == "":
        return None, True
    if COUNT.fullmatch(text):
        return int(text.replace(",", "")), True
    return None, False


def parse_pct(text: str) -> tuple[float | None, bool]:
    t = text.rstrip("%")
    if text == "" or text in PCT_NULL:
        return None, True
    if PCT.fullmatch(t):
        return float(t.replace(",", "")), True
    return None, False


def pct_agrees(printed: str, cur: int | None, prior: int | None, scale: float = 100.0) -> bool | None:
    """Is the printed percent change a correct rounding of (cur - prior) / prior?

    None when it cannot be checked (a count is blank). NYPD prints a null marker (***.*) where
    the prior count is 0; anything else there disagrees. The printed value may be rounded either
    way at an exact half, so it agrees when it is within half a unit of its last printed digit.
    """
    if cur is None or prior is None:
        return None
    t = printed.rstrip("%")
    if prior == 0:
        return printed in PCT_NULL
    if not PCT.fullmatch(t):
        return False
    decimals = len(t.split(".")[1]) if "." in t else 0
    exact = (cur - prior) / prior * scale
    return abs(exact - float(t.replace(",", ""))) <= 0.5 * 10 ** -decimals + 1e-9

"""Refuse a PDF that is damaged, before any of it is read.

Three NYCHA Housing Bureau PDFs in the archive (weeks 2021-01-19, 2024-03-05, 2024-09-08) are
exactly 1,048,576 bytes: downloads cut off at 1 MiB. Their first pages may still open, so a
reader that is not careful would return part of a report as if it were all of it. The checks
here are structural and need nothing from the page content:

- the file starts with ``%PDF-`` and ends (after trailing whitespace) with ``%%EOF``;
- the ``startxref`` offset before that ``%%EOF`` points inside the file, at an ``xref`` table
  or a cross-reference stream object;
- pypdf opens it in strict mode and can read every page's content stream.

A file that fails any of them is refused whole. Its size being an exact multiple of 1 MiB is
named in the reason, because that is how a cut-off download usually looks, but size alone
never refuses a file.
"""

from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

MIB = 1 << 20


class PdfRefused(Exception):  # noqa: N818 -- a refusal, not a bug
    """The PDF is not read at all. ``reason`` is a short code, ``detail`` says why."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail = reason, detail


@dataclass
class PdfInfo:
    path: Path
    sha256: str
    size: int
    pages: int


STARTXREF = re.compile(rb"startxref\s+(\d+)\s+%%EOF\s*$")
XREF_AT = re.compile(rb"\s*(xref\b|\d+\s+\d+\s+obj\b)")


def check_pdf(path: Path) -> PdfInfo:
    data = Path(path).read_bytes()
    size = len(data)
    cut = f" The file is exactly {size // MIB} MiB ({size:,} bytes), the usual sign of a cut-off download." \
        if size and size % MIB == 0 else ""
    if not data.startswith(b"%PDF-"):
        raise PdfRefused("not_a_pdf", f"{path.name} does not start with %PDF-.")
    tail = data[-2048:].rstrip(b"\r\n\t \x00")
    if not tail.endswith(b"%%EOF"):
        raise PdfRefused("truncated", f"{path.name} does not end with %%EOF.{cut}")
    m = STARTXREF.search(tail)
    if not m:
        raise PdfRefused("truncated", f"{path.name} has no startxref before its last %%EOF.{cut}")
    off = int(m.group(1))
    if off >= size or not XREF_AT.match(data, off):
        raise PdfRefused("truncated", f"{path.name}: startxref {off} does not point at a cross-reference.{cut}")
    import logging

    import pypdf

    logging.getLogger("pypdf").setLevel(logging.ERROR)  # "Xref table not zero-indexed" on most NYCHA files
    try:
        reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        n = len(reader.pages)
        for p in reader.pages:
            p.get_contents()
    except Exception as exc:  # pypdf raises many types on a damaged file
        raise PdfRefused("unreadable", f"{path.name}: pypdf could not read it in strict mode ({exc!r}).{cut}") from exc
    if n == 0:
        raise PdfRefused("unreadable", f"{path.name} has no pages.")
    return PdfInfo(Path(path), hashlib.sha256(data).hexdigest(), size, n)

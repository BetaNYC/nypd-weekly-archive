"""The page as a picture: the second reading and the ink checks.

The page is rendered by pdfium at 300 dpi in grayscale. Nothing here reads text from pixels;
there is no OCR. The picture is used three ways:

- Image grid. Text rows are found from the picture alone (bands of inked pixel rows once the
  drawn rules are taken out), and the columns of a row from the blank strips between its
  inked runs. Each run is placed under a header label by its right edge, NYPD's numbers being
  right-aligned under their headers. The characters inside each run come from the text layer,
  as in the budget reader, so this reading checks the placement of every value (which column,
  which row), not the glyphs themselves.
- Ink coverage: every inked pixel in the table must be explained by a text-layer character or a
  drawn rule. Ink that is not is something printed that the text layer does not hold.
- Character ink: every character the reader takes a number from must have ink under it. A
  character with none is text the page does not show.

``find_uncovered_regions`` and ``_tight_bbox`` are ported from Christian Moscardi's
``nyc_executive_orders/vlm_ocr.py`` (BetaNYC/nyc-executive-orders @ 84df8193, MIT), by way of
New-York-City-Budget ``code/schedc_cv/ink.py``, unchanged except that the module constants are
passed in. The rule mask is the morphological opening of New-York-City-Budget
``code/ocr/grid.py`` ``rule_masks`` (Christian Moscardi, PR #43), with its absolute kernel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

DPI = 300
INK = 128                 # Christian's fixed threshold (grid.binarize): darker than this is ink
RULE_KERNEL_PX = 60       # ~14 pt at 300 dpi: longer than any glyph stroke (budget SEG_KERNEL_PX)
V_KERNEL_PX = 30          # ~7 pt: taller than NYCHA's glyphs, shorter than its ~13 pt rows
CELL_PX = 36              # ink.py constants at 300 dpi
MIN_REGION_INK = 900
UNCOVERED_CELL_MIN_INK_FRACTION = 0.025
MAX_UNCOVERED_REGIONS = 12


def render(pdf_path: Path, page_index: int, dpi: int = DPI) -> np.ndarray:
    """One page as a 2-D uint8 grayscale array (255 = white)."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        arr = doc[page_index].render(scale=dpi / 72.0, grayscale=True).to_numpy()
    finally:
        doc.close()
    if arr.ndim == 3:
        arr = arr[:, :, 0]
    return np.ascontiguousarray(arr)


@dataclass
class PageImage:
    gray: np.ndarray
    dpi: int = DPI

    def __post_init__(self) -> None:
        import cv2

        self.s = self.dpi / 72.0
        self.ink = self.gray < INK
        ink8 = self.ink.astype(np.uint8) * 255
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (RULE_KERNEL_PX, 1))
        rules = cv2.morphologyEx(ink8, cv2.MORPH_OPEN, k) > 0
        # A filled bar (NYPD's black section headings) is a stack of rule rows; its white
        # lettering leaves short dark pieces between letters. Grow the rule mask by a few
        # pixels sideways and down/up so those pieces go with the bar, not with the text.
        rules = cv2.dilate(rules.astype(np.uint8), np.ones((3, 9), np.uint8)) > 0
        self.text_ink = self.ink & ~rules

    def px(self, v: float) -> int:
        return int(round(v * self.s))

    def pt(self, v: float) -> float:
        return round(v / self.s, 2)

    def bands(self, x0_pt: float, x1_pt: float, y0_pt: float, y1_pt: float) -> list[tuple[float, float]]:
        """Text rows between y0 and y1 (pt): runs of pixel rows holding text ink."""
        x0, x1, y0, y1 = self.px(x0_pt), self.px(x1_pt), self.px(y0_pt), self.px(y1_pt)
        prof = self.text_ink[y0:y1, x0:x1].any(axis=1)
        out = []
        start = None
        for i, v in enumerate(prof):
            if v and start is None:
                start = i
            elif not v and start is not None:
                out.append((self.pt(y0 + start), self.pt(y0 + i)))
                start = None
        if start is not None:
            out.append((self.pt(y0 + start), self.pt(y1)))
        return out

    def runs(self, y0_pt: float, y1_pt: float, x0_pt: float, x1_pt: float, gap_pt: float) -> list[tuple[float, float]]:
        """Inked runs along a text row, split where a blank strip is at least gap_pt wide."""
        x0, x1 = self.px(x0_pt), self.px(x1_pt)
        prof = self.text_ink[self.px(y0_pt):self.px(y1_pt), x0:x1].any(axis=0)
        cols = np.nonzero(prof)[0]
        if cols.size == 0:
            return []
        gap = gap_pt * self.s
        out = []
        a = b = int(cols[0])
        for raw in cols[1:]:
            c = int(raw)
            if c - b - 1 >= gap:
                out.append((self.pt(x0 + a), self.pt(x0 + b + 1)))
                a = c
            b = c
        out.append((self.pt(x0 + a), self.pt(x0 + b + 1)))
        return out

    def columns(self, bands_pt: list[tuple[float, float]], x0_pt: float, x1_pt: float,
                min_gutter_pt: float) -> list[tuple[float, float]]:
        """Columns of a table from its blank strips: x spans holding text ink in any of the
        given rows, split where every row is blank for at least min_gutter_pt."""
        x0, x1 = self.px(x0_pt), self.px(x1_pt)
        prof = np.zeros(x1 - x0, dtype=bool)
        for y0, y1 in bands_pt:
            prof |= self.text_ink[self.px(y0):self.px(y1), x0:x1].any(axis=0)
        cols = np.nonzero(prof)[0]
        if cols.size == 0:
            return []
        gap = min_gutter_pt * self.s
        out = []
        a = b = int(cols[0])
        for raw in cols[1:]:
            c = int(raw)
            if c - b - 1 >= gap:
                out.append((self.pt(x0 + a), self.pt(x0 + b + 1)))
                a = c
            b = c
        out.append((self.pt(x0 + a), self.pt(x0 + b + 1)))
        return out

    def _rule_masks(self) -> tuple[np.ndarray, np.ndarray]:
        """Horizontal and vertical drawn rules: ink that survives an opening with a long thin
        kernel (grid.py ``rule_masks``, absolute kernel). Vertical rules of a ruled table are cut
        at every row, so their kernel is shorter than a row (~13 pt) but taller than a glyph."""
        if not hasattr(self, "_masks"):
            import cv2

            ink8 = self.ink.astype(np.uint8) * 255
            h = cv2.morphologyEx(ink8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (RULE_KERNEL_PX, 1)))
            v = cv2.morphologyEx(ink8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, V_KERNEL_PX)))
            self._masks = (h > 0, v > 0)
        return self._masks

    def hrules_near(self, ys_pt: list[float], x0_pt: float, x1_pt: float, tol_pt: float = 1.5,
                    min_cover: float = 0.5) -> list[bool]:
        """For each y: is a horizontal rule drawn within tol_pt of it, over min_cover of x0..x1?"""
        h, _ = self._rule_masks()
        x0, x1 = self.px(x0_pt), self.px(x1_pt)
        out = []
        for y in ys_pt:
            band = h[max(0, self.px(y - tol_pt)):self.px(y + tol_pt) + 1, x0:x1]
            out.append(bool(band.size) and float(band.any(axis=0).mean()) >= min_cover)
        return out

    def vrules_crossing(self, top_pt: float, bottom_pt: float, x0_pt: float, x1_pt: float,
                        min_cover: float = 0.8) -> list[float]:
        """x (pt) of every vertical rule that crosses the band top..bottom (inset 1.5 pt)."""
        _, v = self._rule_masks()
        x0, x1 = self.px(x0_pt), self.px(x1_pt)
        band = v[self.px(top_pt + 1.5):self.px(bottom_pt - 1.5), x0:x1]
        if band.shape[0] == 0:
            return []
        cols = np.nonzero(band.mean(axis=0) >= min_cover)[0]
        out: list[float] = []
        run: list[int] = []
        for raw in cols:
            c = int(raw)
            if run and c - run[-1] > 1:
                out.append(self.pt(x0 + (run[0] + run[-1] + 1) / 2))
                run = []
            run.append(c)
        if run:
            out.append(self.pt(x0 + (run[0] + run[-1] + 1) / 2))
        return out

    def ink_span(self, x0_pt: float, y0_pt: float, x1_pt: float, y1_pt: float) -> tuple[float | None, float | None]:
        """Left and right ends (pt) of the text ink inside a box, or (None, None)."""
        x0, x1 = self.px(x0_pt), self.px(x1_pt)
        cols = np.nonzero(self.text_ink[self.px(y0_pt):self.px(y1_pt), x0:x1].any(axis=0))[0]
        if cols.size == 0:
            return None, None
        return self.pt(x0 + int(cols[0])), self.pt(x0 + int(cols[-1]) + 1)

    def ink_under(self, bbox_pt: list[float]) -> int:
        """Inked pixels inside a box (pt)."""
        x0, y0, x1, y1 = (self.px(v) for v in bbox_pt)
        return int(self.ink[y0:max(y1, y0 + 1), x0:max(x1, x0 + 1)].sum())

    def coverage(self, roi_pt: list[float], char_boxes: list[list[float]], shapes: list[tuple[float, ...]],
                 char_pad_px: int = 3, rule_pad_px: int = 2) -> list[dict]:
        """Regions of ink inside roi that no character or drawn mark explains (ink.py coverage)."""
        H, W = self.ink.shape

        def box(b, pad):
            return (max(0, int(math.floor(b[0] * self.s)) - pad), max(0, int(math.floor(b[1] * self.s)) - pad),
                    min(W, int(math.ceil(b[2] * self.s)) + pad), min(H, int(math.ceil(b[3] * self.s)) + pad))

        covered = np.zeros_like(self.ink)
        for b in char_boxes:
            x0, y0, x1, y1 = box(b, char_pad_px)
            covered[y0:y1, x0:x1] = True
        for b in shapes:
            x0, y0, x1, y1 = box(b, rule_pad_px)
            covered[y0:y1, x0:x1] = True
        x0, y0, x1, y1 = box(roi_pt, 0)
        left = self.ink[y0:y1, x0:x1] & ~covered[y0:y1, x0:x1]
        if not left.any():
            return []
        return [dict(bbox_pt=[self.pt(v) for v in r["bbox"]], ink_px=r["ink_px"])
                for r in find_uncovered_regions(left, CELL_PX, MIN_REGION_INK, (x0, y0))]


def find_uncovered_regions(uncovered, cell_px, min_region_ink, offset,
                           min_ink_fraction=UNCOVERED_CELL_MIN_INK_FRACTION,
                           max_regions=MAX_UNCOVERED_REGIONS):
    """Group leftover ink pixels into a handful of reportable rectangles (ported; see module
    docstring). A coarse cell counts as inked once it holds enough ink to be type rather than
    speckle; adjacent inked cells merge; a group must carry min_region_ink pixels."""
    h, w = uncovered.shape
    if h == 0 or w == 0:
        return []
    grid_h, grid_w = math.ceil(h / cell_px), math.ceil(w / cell_px)
    padded = np.zeros((grid_h * cell_px, grid_w * cell_px), dtype=np.int32)
    padded[:h, :w] = uncovered
    cells = padded.reshape(grid_h, cell_px, grid_w, cell_px).sum(axis=(1, 3))
    hot = cells >= max(1, int(cell_px * cell_px * min_ink_fraction))
    seen = np.zeros_like(hot, dtype=bool)
    regions = []
    for row in range(grid_h):
        for col in range(grid_w):
            if not hot[row, col] or seen[row, col]:
                continue
            stack, group = [(row, col)], []
            seen[row, col] = True
            while stack:
                r, c = stack.pop()
                group.append((r, c))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < grid_h and 0 <= nc < grid_w and hot[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
            rows = np.array([p[0] for p in group])
            cols = np.array([p[1] for p in group])
            ink_px = int(cells[rows, cols].sum())
            if ink_px < min_region_ink:
                continue
            bbox = _tight_bbox(uncovered, rows, cols, cell_px, offset)
            if bbox is None:
                continue
            regions.append({"bbox": bbox, "ink_px": ink_px})
    regions.sort(key=lambda r: r["ink_px"], reverse=True)
    return regions[:max_regions]


def _tight_bbox(uncovered, rows, cols, cell_px, offset):
    """The smallest full-page box holding this group's ink, and nothing else (ported)."""
    off_x, off_y = offset
    r0, r1 = int(rows.min()), int(rows.max()) + 1
    c0, c1 = int(cols.min()), int(cols.max()) + 1
    owned = np.zeros((r1 - r0, c1 - c0), dtype=bool)
    owned[rows - r0, cols - c0] = True
    owned = np.kron(owned, np.ones((cell_px, cell_px), dtype=bool))
    y0, x0 = r0 * cell_px, c0 * cell_px
    patch = uncovered[y0:y0 + owned.shape[0], x0:x0 + owned.shape[1]]
    ys, xs = np.nonzero(patch & owned[:patch.shape[0], :patch.shape[1]])
    if ys.size == 0:
        return None
    return [int(off_x + x0 + xs.min()), int(off_y + y0 + ys.min()),
            int(off_x + x0 + xs.max() + 1), int(off_y + y0 + ys.max() + 1)]

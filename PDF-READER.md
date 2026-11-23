# Reading NYPD's PDFs where the spreadsheet is lost

For many weeks only NYPD's PDF survives: the spreadsheet was never saved, or the copy we have is
damaged. This reader turns those PDFs into the same rows as `data/`, and checks every number
before anyone uses it. It covers four reports: precinct CompStat, citywide CompStat, Department
of Correction CompStat, and the NYCHA Housing Bureau report.

**Phase 1 (this code) writes nothing to `data/`.** `read-pdfs --dry-run` writes review files to a
folder outside `data/`. Phase 2, writing the PDF-only weeks into `data/`, waits until the
spreadsheet re-filing of issue #9 is done, because that changes which weeks are PDF-only.

**Last revised:** 2026-11-23.

## The rules

- **No OCR.** No Tesseract and no other text-recognition engine is used anywhere. Every number
  comes from the PDF's own text layer. The page picture is used only to check those numbers.
- **Never fix.** A number is kept exactly as printed. Where the PDF disagrees with itself, or the
  two readings disagree, the row is flagged for a person. Nothing is corrected.
- **Refuse damaged files whole.** A PDF that is cut off is not read at all, not even its first
  pages.
- **Keep where every number came from:** its PDF, page, and box on the page.

The method is BetaNYC/New-York-City-Budget's
[`PDF-RECIPE.md`](https://github.com/BetaNYC/New-York-City-Budget/blob/main/PDF-RECIPE.md),
adapted to NYPD's pages.

## How it reads a page

1. **The guard** (`pdfread/guard.py`). The file must start with `%PDF-`, end with `%%EOF`, have a
   `startxref` that points at a cross-reference, and open in pypdf's strict mode with every
   page's content readable. Otherwise it is refused, with the reason. The page must have a text
   layer; a scanned page is refused, never read by OCR.
2. **The vector grid: the main reading.** NYPD draws its tables' rules in the PDF. In CompStat,
   the rules around the TOTAL row and under the Historical Perspective are drawn one segment per
   column, so the segment ends are the column edges. NYCHA tables are fully ruled, and
   pdfplumber's lines strategy finds their cells. Each character goes to the cell that holds its
   centre (the budget reader's rule).
3. **The image grid: the second reading.** The page is rendered by pdfium at 300 dpi. From the
   picture alone, the reader finds the text rows (bands of inked pixel rows once rules are taken
   out) and the columns (CompStat: the blank strips that run down every row, each column named by
   the header label nearest its right edge; NYCHA: the drawn rules, found as long thin runs of
   ink). The characters inside each image cell must read the same as the vector cell.
4. **NYPD's own arithmetic.** Every printed percent change must follow from its two counts.
   CompStat's TOTAL must equal the sum of the seven major crimes, in every count column, and the
   Historical Perspective's right-hand label must repeat its left-hand one. NYCHA's Total Index
   must equal the sum of the seven major crimes, this year and last.
5. **Ink.** Every character a number is read from must have ink under it (text the page does not
   show is caught here). Every inked pixel in a table must be explained by a character or a drawn
   rule; ink that is not is something printed that the text layer does not hold.
6. **Structure.** Every row label must be one NYPD has used, found once; the page's dates, years
   and long-range headers must agree with each other; the precinct in the title must match the
   file name.

A row that fails any check gets `check` = `flagged`, with the reasons in `check_notes`, and a line
in the review file.

### What the checks cannot prove

The image grid uses the text layer's characters, like the budget reader's. It proves that each
value sits in the right row and column, not that the text layer's glyph is the one drawn. A PDF
whose text layer says "3" where the page draws "8" would pass the image grid. The arithmetic
catches that for every count with a percent change beside it and for every total; the
long-range percents (2-, 16- and 33-year) have no such check.

## Layouts

Read off every PDF on disk on 2026-11-23. CompStat layouts are named by the rows the main table
prints; `+bases` means the long-range headers print their base year, as in "13 Year (2001)".

| Report | Layout | Rows | Years seen | Read? |
|---|---|---|---|---|
| CompStat (citywide) | 2008 | 7 major crimes | 2008-2009 | No: draws no column rules (4 files) |
| CompStat | `cs-2009` | 7 major crimes + TOTAL | 2009-2010 | Yes |
| CompStat | `cs-2011` | + Petit Larceny, Misd. Assault, Misd. Sex Crimes | 2011-2012 | Yes |
| CompStat | `cs-2012` | + Shooting Inc. | 2012-2013 | Yes |
| CompStat | `cs-2014a` | + Shooting Vic. | 2012-2014 | Yes |
| CompStat | `cs-2014` | + Transit, Housing | 2014-2019 | Yes |
| CompStat | `cs-2019` | UCR Rape*, Other Sex Crimes replace Misd. Sex Crimes | 2019-2020 | Yes |
| CompStat | `cs-2020` | + Hate Crimes | 2020-2024 | Yes |
| CompStat | `cs-2024` | + Retail Theft, Traffic Fatalities | 2024-2026 | Yes; the spreadsheets' layout |
| CompStat | week ending 2021-08-15 | | 2021 | No: that week's PDFs draw no column rules |
| Correction CompStat | `+hist1` | Historical Perspective is one year, no rule | 2014-2015 | Main table yes; Historical block not read, and the review file says so |
| NYCHA | `hb-2014` | 36 pages: PSA 1-9, Staten Island, Rockaway, Viper, Housing Authority | 2014-2026 | Yes |
| NYCHA | 2014 per-PSA tables, 2014-2015 "% CH" variant, a 34-page 2023 copy | | | No (4 files) |

Odd pages the reader handles, each found in the archive: named precincts (Central Park, Midtown
Precinct North and South); a "%" header with no "Chg" (2019 citywide); "% Chg" printed 3 pt from
its year; glyphs whose boxes touch across columns ("1" and "200.00"); a row label printed on its
own line a few points off its numbers (flagged); "2 Year" printed above the header line (2012
citywide); NYCHA pages that draw no rule between developments (each row's label is read at its
own height) and a few with a header rule missing (flagged). Many NYPD PDFs declare an upside-down
page box (`[0 0 612 -792]`), for which pdfplumber's `top` is 1,584 pt off; positions are taken
from the PDF's own coordinates instead (`geom.origin`).

## Output

`read-pdfs --dry-run` writes to `--out` (default `pdf-dry-run/`, git-ignored; it refuses any
folder inside `data/`):

- `rows/<kind>/<week_end>.csv`: the rows phase 2 would write. The `data/` columns, then
  `pdf_sha256`, `page`, `bbox` (x0 top x1 bottom in points from the page's top-left corner),
  `check` (`ok` or `flagged`), `check_notes`, `layout`, and `printed` (the cell's text as
  printed, such as `1,407` or `***.*`). `source_file` is the PDF's path in `raw/`.
  CompStat rows have the same keys and values as the spreadsheet reader's. NYCHA rows use
  `sheet` = the spreadsheet sheet the page prints, `section` = the PSA cell in force, `geography`
  = the row's own label and `precinct` = the PCT in force; they will follow the spreadsheet
  reader's NYCHA shape once that is merged (PR #32).
- `review-<kind>.csv`: one line per flagged printed row or page, with `page`, `bbox`, the
  checks that failed, what each found, the row as printed, and empty `decision` and `note`
  columns for a person.
- `refused-<kind>.csv`: every PDF not read, and why.
- `summary.json`: counts per kind and layout.

### Deciding a review line

The decisions are the budget recipe's:

| Decision | Meaning |
|---|---|
| `accepted_as_printed` | The row is right as the PDF prints it |
| `source_error` | The PDF itself is wrong (a total that does not add up); keep it as printed and flag it |
| `parser_bug` | We read it wrong; the right value goes in the note, and the code gets fixed |
| `needs_info` | Can't tell yet |

Open the PDF at `page` and look at `bbox`. Phase 2 decides where decisions are kept.

## Validation

`uv run --extra pdf nypd-archive read-pdfs --series compstat housing --validate` reads every
week where both the PDF and the spreadsheet survive, and compares every number. The spreadsheet
reader (`parse.py`) is the truth for CompStat. For NYCHA, whose numbers `parse.py` does not read
yet, the truth is a cell-for-cell read of the spreadsheet with openpyxl, matched to the PDF row
by row (`validate.housing_xlsx_grid`). Percents are compared at the precision printed: the
spreadsheet holds -14.814..., the PDF prints -14.8.

| Series | Layout | Weeks | Cells compared | Cells matched | Mismatch, unflagged row | Mismatch, flagged row | Rows flagged |
|---|---|---:|---:|---:|---:|---:|---:|
| precinct | cs-2024 | 728 | 153,608 | 153,608 | 0 | 0 | 0 |
| citywide | cs-2024 | 16 | 3,376 | 3,376 | 0 | 0 | 0 |
| correction | cs-2024 | 11 | 2,321 | 2,321 | 0 | 0 | 0 |
| housing | hb-2014 | 15 | 484,785 | 484,785 | 0 | 0 | 13 |
| housing | refused:layout | 4 files | | | | | |
| housing | refused:truncated | 3 files | | | | | |

Run 2026-11-23. "Weeks" counts PDFs with a spreadsheet for the same week. "Rows flagged" are
printed rows the reader sent to review even though they matched (cost, not error): the 13 NYCHA
rows are in tables whose header rule NYPD did not draw. Every surviving CompStat spreadsheet is
in the `cs-2024` layout, so the older CompStat layouts are checked only by the second opinion
below. Refused files are counted in files, not weeks.

The spreadsheets that survive are all in the newest CompStat layout. To check the older layouts,
precinct PDFs are also compared with Tim McDermott's weekly CSVs (`data/compstat_mcdermott/`,
2015-2026). Those numbers were not read by this repo, so this is a second opinion, not proof.
Differences are sorted by cause: `his_blank` (his file has no value), `his_label_shift` (his
value is the one the PDF prints for another crime in the same column; his columns were shifted
when NYPD added a row), `rounding_tie` (an exact half, such as 31.25, that NYPD prints as 31.2
and his file as 31.3), `pdf_blank` (the PDF prints no percent where he has one).

| Layout | Weeks | Numbers both hold | matched | his_blank | his_label_shift | rounding_tie | flagged_row | pdf_blank | unexplained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| cs-2014 | 5,812 | 616,164 | 613,822 | 1,850 | 11 | 464 | 0 | 0 | 17 |
| cs-2014+bases | 1,692 | 165,753 | 165,753 | 0 | 0 | 0 | 0 | 0 | 0 |
| cs-2019 | 149 | 18,774 | 17,092 | 275 | 1,250 | 14 | 143 | 0 | 0 |
| cs-2020 | 1,065 | 151,539 | 147,281 | 625 | 3,254 | 165 | 0 | 0 | 214 |
| cs-2024 | 1,465 | 224,145 | 168,014 | 4,205 | 51,727 | 199 | 0 | 0 | 0 |

Run 2026-11-23. Of the 231 `unexplained` numbers, 228 fall in four weeks (ending 2018-12-30,
2022-01-02, 2022-09-25 and 2023-04-09) where many numbers of a page differ a little, which looks
like a different edition of NYPD's file; the other 3 are single cells, such as Petit Larceny
printed 1,881 where his file has 1181. On every one of these rows the PDF's own percent and total
checks pass.

## What phase 2 would read

A dry run over every PDF on disk whose (file, week) has no spreadsheet, 2026-11-23. These counts
change when issue #9's re-filing lands, which is why phase 2 waits.

| Report | PDF-only files | Read | Refused | Files with a review item | Review items |
|---|---:|---:|---:|---:|---:|
| Precinct CompStat | 14,672 | 14,671 (502 weeks) | 1 (week ending 2021-08-15, no drawn columns) | 16 | 240 |
| Citywide CompStat | 537 | 532 | 5 (2008-2009 layout) | 11 | 58 |
| Correction CompStat | 146 | 146 | 0 | 47 | 47 (all `not_read`: the one-year Historical block) |
| NYCHA Housing Bureau | 85 | 78 | 7 (3 cut off at 1 MiB, 4 other layouts) | 2 | 70 |

Of the 14,671 precinct-weeks, 4,827 are in neither a spreadsheet nor Tim McDermott's files. Of
the 78 NYCHA weeks read, 63 have no spreadsheet; the other 15 were filed under a Wayback capture
date, so the PDF-only filter could not see that their spreadsheet survives.

## Running it

```sh
uv sync --extra pdf                                   # pdfplumber, pypdfium2, opencv, numpy
uv run nypd-archive read-pdfs --series compstat housing --dry-run
uv run nypd-archive read-pdfs --kinds correction --dry-run --limit 20 --out /tmp/pdf-check
uv run nypd-archive read-pdfs --series compstat housing --validate
uv run pytest tests/test_pdfread.py
```

`--all-weeks` also reads weeks whose spreadsheet survives. A full dry run of all four reports
takes about 10 minutes with 10 processes.

## Code

| File | What it does | Borrowed from New-York-City-Budget |
|---|---|---|
| `pdfread/guard.py` | Refuses damaged PDFs | |
| `pdfread/geom.py` | Text layer, lines, drawn rules | `code/schedc/vector.py`: `chars_to_text`, `origin`/`shift` (reworked for upside-down page boxes), `_dark`; `code/schedc_cv/ink.py`: `rule_shapes` |
| `pdfread/image.py` | Renders the page; image rows, columns and rules; ink checks | `code/schedc_cv/ink.py` and Christian Moscardi's `find_uncovered_regions`/`_tight_bbox` (from BetaNYC/nyc-executive-orders, MIT); the rule opening of `code/ocr/grid.py`; `code/ocr/render.py`'s pdfium render at 300 dpi |
| `pdfread/compstat.py` | Precinct, citywide and Correction CompStat | The centre rule of `code/schedc/vector.py`; the vector-against-image row comparison of `code/schedc_cv/imagegrid.py` |
| `pdfread/housing.py` | NYCHA Housing Bureau report | pdfplumber lines-strategy lattice and centre rule, as `code/schedc/vector.py` |
| `pdfread/run.py` | Picks PDF-only weeks, runs in parallel, writes review files | |
| `pdfread/validate.py` | The validation above | |

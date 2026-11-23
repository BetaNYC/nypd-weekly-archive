"""The checked PDF reader (src/nypd_weekly_archive/pdfread/), on real NYPD PDFs in tests/fixtures/pdf/.

Each fixture folder is the report's week; the files keep NYPD's names, which the reader uses.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("pdfplumber")
pytest.importorskip("pypdfium2")
pytest.importorskip("cv2")

from nypd_weekly_archive.cli import main  # noqa: E402
from nypd_weekly_archive.parse import LayoutError, parse_compstat  # noqa: E402
from nypd_weekly_archive.pdfread.common import PDF_FIELDS, parse_count, parse_pct, pct_agrees  # noqa: E402
from nypd_weekly_archive.pdfread.compstat import read_compstat_pdf  # noqa: E402
from nypd_weekly_archive.pdfread.guard import MIB, PdfRefused, check_pdf  # noqa: E402
from nypd_weekly_archive.pdfread.housing import read_housing_pdf  # noqa: E402
from nypd_weekly_archive.pdfread.run import dry_run  # noqa: E402
from nypd_weekly_archive.pdfread.validate import _hb_same, _same, housing_xlsx_grid  # noqa: E402

FX = Path(__file__).parent / "fixtures" / "pdf"


def _by_key(rows):
    return {(r["section"], r["measure"], r["period"], str(r["year"])): r for r in rows}


# ------------------------------------------------------------------- guard


def test_whole_pdf_passes_the_guard():
    info = check_pdf(FX / "2026-08-23" / "cs-en-us-048pct.pdf")
    assert info.pages == 1 and len(info.sha256) == 64


def test_cut_off_pdf_is_refused(tmp_path):
    data = (FX / "2026-08-23" / "cs-en-us-048pct.pdf").read_bytes()
    cut = tmp_path / "cs-en-us-048pct.pdf"
    cut.write_bytes(data[: len(data) // 2])
    with pytest.raises(PdfRefused) as exc:
        check_pdf(cut)
    assert exc.value.reason == "truncated"
    # Cut off at exactly 1 MiB, as three NYCHA downloads were: refused, and the size is named.
    cut.write_bytes((data * 8)[:MIB])
    with pytest.raises(PdfRefused) as exc:
        check_pdf(cut)
    assert exc.value.reason == "truncated" and "exactly 1 MiB" in exc.value.detail


def test_reader_refuses_a_cut_off_pdf_before_reading_any_of_it(tmp_path):
    data = (FX / "2026-02-01" / "hb_eagle_report.pdf").read_bytes()
    cut = tmp_path / "hb_eagle_report.pdf"
    cut.write_bytes(data[:MIB])
    with pytest.raises(PdfRefused):
        read_housing_pdf(cut)


def test_not_a_pdf_is_refused(tmp_path):
    p = tmp_path / "cs-en-us-city.pdf"
    p.write_bytes(b"<html>not found</html>")
    with pytest.raises(PdfRefused) as exc:
        check_pdf(p)
    assert exc.value.reason == "not_a_pdf"


# ------------------------------------------------------------------- numbers


def test_number_parsing():
    assert parse_count("1,407") == (1407, True)
    assert parse_count("") == (None, True)
    assert parse_count("12a") == (None, False)
    assert parse_pct("***.*") == (None, True)
    assert parse_pct("/0") == (None, True)
    assert parse_pct("1,100.0") == (1100.0, True)
    assert parse_pct("-100.0%") == (-100.0, True)


def test_percent_check():
    assert pct_agrees("-40.43", 28, 47) is True       # (28 - 47) / 47 = -40.425...
    assert pct_agrees("-40.4", 28, 47) is True
    assert pct_agrees("-40.5", 28, 47) is False
    assert pct_agrees("***.*", 3, 0) is True          # no percent from a zero
    assert pct_agrees("50.0", 3, 0) is False
    assert pct_agrees("31.2", 21, 16) is True         # 31.25: NYPD may round a half either way
    assert pct_agrees("31.3", 21, 16) is True
    assert pct_agrees("1.0", None, 4) is None         # a blank count cannot be checked


# ------------------------------------------------------------------- CompStat


def test_2024_layout_matches_the_spreadsheet_number_for_number():
    res = read_compstat_pdf(FX / "2026-08-23" / "cs-en-us-048pct.pdf", "raw/compstat/2026-08-23/cs-en-us-048pct.pdf")
    truth = {(r.section, r.measure, r.period, str(r.year)): r.value
             for r in parse_compstat(FX / "2026-08-23" / "cs-en-us-048pct.xlsx")}
    got = _by_key(res.rows)
    assert res.layout == "cs-2024" and res.week_end == "2026-08-23"
    assert set(got) == set(truth)
    assert all(_same(got[k]["value"], truth[k], str(got[k]["printed"])) for k in truth)
    assert not res.flags and {r["check"] for r in res.rows} == {"ok"}
    r = got[("Crime Complaints", "TOTAL", "ytd", "2026")]
    assert r["value"] == 1407 and r["printed"] == "1,407" and r["page"] == 1
    x0, top, x1, bottom = (float(v) for v in str(r["bbox"]).split())
    assert x0 < x1 and top < bottom and 300 < x1 < 360
    assert set(res.rows[0]) == set(PDF_FIELDS)


def test_2014_layout_with_printed_base_years():
    res = read_compstat_pdf(FX / "2014-06-29" / "cs122pct.pdf")
    got = _by_key(res.rows)
    assert res.layout == "cs-2014+bases" and not res.flags
    assert res.rows[0]["geography"] == "122nd Precinct" and res.rows[0]["precinct"] == 122
    assert got[("Crime Complaints", "TOTAL", "ytd", "2014")]["value"] == 326
    assert got[("Crime Complaints", "Misd. Sex Crimes", "28d", "2013")]["value"] == 4
    assert got[("Crime Complaints", "Murder", "ytd_pct_change_vs_2001", "2014")]["value"] == -100.0
    assert got[("Historical Perspective", "G.L.A.", "calendar_year", "1990")]["value"] == 4120


def test_digits_whose_boxes_touch_stay_in_their_columns():
    # Correction, week ending 2026-05-31, TOTAL: "3 1200.00" is a prior count of 1 and +200.00%.
    res = read_compstat_pdf(FX / "2026-05-31" / "cs-en-us-Correction.pdf")
    got = _by_key(res.rows)
    assert got[("Crime Complaints", "TOTAL", "wtd", "2025")]["value"] == 1
    assert res.rows[0]["geography"] == "Department of Correction Citywide"
    assert not res.flags


def test_bare_percent_header_and_named_layout():
    res = read_compstat_pdf(FX / "2019-09-22" / "cs-en-us-city.pdf")
    assert res.layout == "cs-2019" and not res.flags


def test_one_year_historical_block_is_not_read_and_says_so():
    res = read_compstat_pdf(FX / "2014-08-17" / "csCorrection.pdf")
    assert res.layout.endswith("+hist1")
    assert not [r for r in res.rows if r["section"] == "Historical Perspective"]
    assert [f.checks for f in res.flags] == [["not_read"]]


def test_a_total_nypd_printed_wrong_is_flagged_not_fixed():
    # 83rd Precinct, week ending 2021-03-28: the 2020 Historical TOTAL is printed 1,667; its
    # seven crimes add to 1,668. Every row of that table is flagged; the 1,667 is kept.
    res = read_compstat_pdf(FX / "2021-03-28" / "cs-en-us-083pct.pdf")
    flagged = {f.row_label for f in res.flags if "total_mismatch" in f.checks}
    assert "TOTAL" in flagged and len(flagged) == 8
    got = _by_key(res.rows)
    tot = got[("Historical Perspective", "TOTAL", "calendar_year", "2020")]
    assert tot["value"] == 1667 and tot["check"] == "flagged" and "1668" in str(tot["check_notes"])


def test_a_page_without_drawn_columns_is_refused():
    with pytest.raises(LayoutError, match="no segmented rule|not found"):
        read_compstat_pdf(FX / "2008-09-13" / "cscity.pdf")


# ------------------------------------------------------------------- NYCHA


def test_nycha_matches_the_spreadsheet_cell_for_cell():
    res = read_housing_pdf(FX / "2026-02-01" / "hb_eagle_report.pdf")
    assert res.week_end == "2026-02-01" and res.layout == "hb-2014"
    sheets = housing_xlsx_grid(FX / "2026-02-01" / "hb_eagle_report.xlsx")
    assert len(sheets) == 35
    cells = matched = 0
    for si, xrows in enumerate(sheets):
        prows = [(lab, texts) for pno, lab, texts, _ in res.grid if pno == si + 2]
        assert len(prows) == len(xrows), f"sheet {si}"
        pk = Counter()
        xk = Counter()
        pmap, xmap = {}, {}
        for lab, texts in prows:
            key = " ".join([x for x in lab if x][-1].split())
            pk[key] += 1
            pmap[(key, pk[key])] = texts
        for lab, vals in xrows:
            key = " ".join(lab[-1].split())
            xk[key] += 1
            xmap[(key, xk[key])] = vals
        assert set(pmap) == set(xmap), f"sheet {si}"
        for k, texts in pmap.items():
            for t, x in zip(texts, xmap[k], strict=True):
                cells += 1
                matched += _hb_same(t, x)
    assert cells > 30_000 and matched == cells
    tidy = [r for r in res.rows if r["sheet"] == "WTD" and r["geography"] == "CAREY"]
    assert {r["measure"] for r in tidy} == {"Murder", "Rape", "Robbery", "Fel. Assault", "Burglary", "Gr. Larceny",
                                             "GLA", "Shooting Inc.", "Total Index"}
    assert {r["section"] for r in tidy} == {"PSA 1"} and {r["precinct"] for r in tidy} == {60}


# ------------------------------------------------------------------- dry run and CLI


def test_dry_run_never_writes_into_data(tmp_path):
    (tmp_path / "data").mkdir()
    with pytest.raises(ValueError, match="inside data"):
        dry_run(tmp_path, [], ["precinct"], tmp_path / "data" / "x")


def test_dry_run_writes_review_files(tmp_path):
    raw = tmp_path / "raw" / "compstat" / "2021-03-28"
    raw.mkdir(parents=True)
    (raw / "cs-en-us-083pct.pdf").write_bytes((FX / "2021-03-28" / "cs-en-us-083pct.pdf").read_bytes())
    ledger = [{"series": "compstat", "url": "https://x/cs-en-us-083pct.pdf",
               "path": "raw/compstat/2021-03-28/cs-en-us-083pct.pdf", "week_end": "2021-03-28", "date_source": "pdf"}]
    summary = dry_run(tmp_path, ledger, ["precinct"], tmp_path / "out", workers=1)
    assert summary["precinct"]["read"] == 1 and summary["precinct"]["review_items"] == 8
    review = (tmp_path / "out" / "review-precinct.csv").read_text().splitlines()
    assert review[0].startswith("review_id,kind,week_end,source_file,pdf_sha256,page,bbox")
    assert len(review) == 9
    assert (tmp_path / "out" / "rows" / "precinct" / "2021-03-28.csv").exists()
    assert not (tmp_path / "data").exists()


def test_cli_refuses_to_write_data(tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "captures.csv").write_text("series,url,path,week_end,date_source\n")
    assert main(["read-pdfs", "--root", str(tmp_path), "--series", "compstat"]) == 2
    assert "--dry-run" in capsys.readouterr().err

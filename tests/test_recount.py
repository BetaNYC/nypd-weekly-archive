"""Recounting NYPD's weekly numbers from NYC Open Data (recount.py). No network: MockTransport."""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

import httpx
import pytest

from nypd_weekly_archive import recount as rc
from nypd_weekly_archive.net import USER_AGENT, Fetcher

from .conftest import make_fetcher

D = dt.date


def slow_fetcher(handler) -> Fetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT})
    clock = iter(range(0, 10**9, 10))
    return Fetcher(min_interval=2.0, client=client, sleep=lambda s: None, clock=lambda: float(next(clock)))


def write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def row(geo: str, pct: str, measure: str, value: int, period: str = "wtd", year: int = 2026,
        start: str = "2026-09-21", end: str = "2026-09-27", section: str = "Crime Complaints",
        sheet: str = "CompStat") -> dict:
    return {"series": "compstat", "source_file": "x", "geography": geo, "precinct": pct, "week_start": start,
            "week_end": end, "sheet": sheet, "section": section, "measure": measure, "period": period,
            "year": year, "value": value, "unit": "count"}


def test_soql_url_is_readable() -> None:
    url = rc.soql_url("5uac-w243", select="addr_pct_cd,count(*) AS n",
                      where="rpt_dt between '2026-09-21' and '2026-09-27'")
    assert url == ("https://data.cityofnewyork.us/resource/5uac-w243.csv?$select=addr_pct_cd,count(*)%20AS%20n"
                   "&$where=rpt_dt%20between%20'2026-09-21'%20and%20'2026-09-27'")


def test_open_data_needs_two_seconds() -> None:
    with pytest.raises(ValueError):
        rc.OpenData(make_fetcher(lambda r: httpx.Response(200)))


def test_empty_answer_is_an_error_not_zeros(tmp_path: Path) -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, text='"addr_pct_cd","n"\n')

    od = rc.OpenData(slow_fetcher(handler), cache=tmp_path, token="tok")
    with pytest.raises(rc.RecountError, match="no rows"):
        od.rows("5uac-w243", select="addr_pct_cd,count(*) AS n")
    assert seen[0].headers["X-App-Token"] == "tok"
    assert not list(tmp_path.iterdir())  # the empty answer is not cached


def test_answers_are_cached(tmp_path: Path) -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, text='"n"\n"5"\n')

    od = rc.OpenData(slow_fetcher(handler), cache=tmp_path)
    assert od.rows("x", select="count(*) AS n")[0] == [{"n": "5"}]
    assert od.rows("x", select="count(*) AS n")[0] == [{"n": "5"}]
    assert len(calls) == 1 and od.sent == 1


def test_error_body_raises(tmp_path: Path) -> None:
    od = rc.OpenData(slow_fetcher(lambda r: httpx.Response(200, text='{"message":"bad"}')))
    with pytest.raises(rc.RecountError, match="bad"):
        od.rows("x", select="count(*)")


def test_compstat_cells_take_this_years_week_to_date(tmp_path: Path) -> None:
    write(tmp_path / "data" / "compstat" / "2026-09-27.csv", [
        row("1st Precinct", "1", "Robbery", 3),
        row("1st Precinct", "1", "Robbery", 9, year=2025),
        row("1st Precinct", "1", "Robbery", 40, period="28d"),
        row("1st Precinct", "1", "UCR Rape*", 2),
        row("1st Precinct", "1", "Rape", 1),
        row("1st Precinct", "1", "Misd. Assault", 7),
        row("Citywide", "", "Murder", 6),
        row("Department of Correction Citywide", "", "Fel. Assault", 4),
        row("Brooklyn North", "", "Murder", 2),
    ])
    cells = rc.compstat_cells(tmp_path, "ours")
    wk = rc.Week(D(2026, 9, 21), D(2026, 9, 27))
    assert cells == {wk: {("1", "Robbery"): 3, ("1", "Rape"): 1, ("Citywide", "Murder"): 6,
                          ("DOC", "Fel. Assault"): 4}}


def test_complaint_counts_and_missing_is_zero() -> None:
    rows = [{"addr_pct_cd": "1", "jurisdiction_code": "0", "ky_cd": "105", "n": "2"},
            {"addr_pct_cd": "1", "jurisdiction_code": "2", "ky_cd": "105", "n": "1"},
            {"addr_pct_cd": "114", "jurisdiction_code": "72", "ky_cd": "106", "n": "4"},
            {"addr_pct_cd": "", "jurisdiction_code": "0", "ky_cd": "101", "n": "1"}]
    counts = rc.complaint_counts(rows)
    assert counts[("1", "Robbery")] == 3
    assert counts[("DOC", "Fel. Assault")] == 4
    assert counts[("Citywide", "Murder")] == 1  # no precinct, still citywide
    wk = rc.Week(D(2026, 9, 21), D(2026, 9, 27))
    out = rc.compare_complaints({("1", "Robbery"): 3, ("5", "Murder"): 1, ("Citywide", "Robbery"): 3,
                                 ("DOC", "Fel. Assault"): 4}, counts, "ours", wk)
    by = {(c.check, c.geography, c.measure): (c.report, c.open_data) for c in out}
    assert by[("compstat", "1", "Robbery")] == (3, 3)
    assert by[("compstat", "5", "Murder")] == (1, 0)
    assert by[("citywide", "Citywide", "Robbery")] == (3, 3)
    assert by[("correction", "DOC", "Fel. Assault")] == (4, 4)


def test_unknown_offense_code_stops() -> None:
    with pytest.raises(rc.RecountError):
        rc.complaint_counts([{"addr_pct_cd": "1", "jurisdiction_code": "0", "ky_cd": "999", "n": "1"}])


def test_which_complaint_dataset() -> None:
    lo, hi, hist = D(2026, 1, 1), D(2026, 9, 30), D(2025, 12, 31)
    assert rc.complaint_dataset(rc.Week(D(2026, 9, 21), D(2026, 9, 27)), lo, hi, hist) == rc.COMPLAINTS_CURRENT
    assert rc.complaint_dataset(rc.Week(D(2026, 9, 28), D(2026, 10, 4)), lo, hi, hist) is None
    assert rc.complaint_dataset(rc.Week(D(2017, 7, 17), D(2017, 7, 23)), lo, hi, hist) == rc.COMPLAINTS_HISTORIC
    assert rc.complaint_dataset(rc.Week(D(2025, 12, 29), D(2026, 1, 4)), lo, hi, hist) is None


def test_housing_skips_names_open_data_never_uses() -> None:
    wk = rc.Week(D(2026, 5, 25), D(2026, 5, 31))
    ours = {("ADAMS", "Robbery"): 1, ("ADAMS", "GLA"): 0, ("NOWHERE", "Robbery"): 2}
    rows = [{"hadevelopt": "adams ", "ky_cd": "105", "n": "1"}]
    out = rc.compare_housing(ours, rows, {"ADAMS"}, wk)
    assert [(c.geography, c.measure, c.report, c.open_data) for c in out] == [
        ("ADAMS", "GLA", 0, 0), ("ADAMS", "Robbery", 1, 1)]


def test_windows() -> None:
    w = rc.windows(D(2026, 11, 15), D(2026, 11, 9))
    assert w["wtd"] == (D(2026, 11, 9), D(2026, 11, 15))
    assert w["28d"] == (D(2026, 10, 19), D(2026, 11, 15))
    assert w["ytd"] == (D(2026, 1, 1), D(2026, 11, 15))
    assert w["7d"] == w["wtd"]


SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
HOLE = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]


def gj(features: list[tuple[str, list]]) -> dict:
    return {"features": [{"properties": {"precinct": p}, "geometry": {"type": "MultiPolygon", "coordinates": c}}
                         for p, c in features]}


def test_point_in_precinct_with_hole() -> None:
    many = [(str(i), [[[[100 + i, 0], [101 + i, 0], [101 + i, 1], [100 + i, 1], [100 + i, 0]]]]) for i in range(70)]
    areas = rc.precinct_areas(gj([("1", [[SQUARE, HOLE]]), *many]))
    assert rc.place(areas, lat=2, lon=2) == "1"
    assert rc.place(areas, lat=5, lon=5) is None  # in the hole
    assert rc.place(areas, lat=50, lon=50) is None


def test_too_few_precincts_stops() -> None:
    with pytest.raises(rc.RecountError):
        rc.precinct_areas(gj([("1", [[SQUARE]])]))


def test_qol_311_mapping() -> None:
    rows = [{"police_precinct": "Precinct 1", "complaint_type": "Illegal Parking",
             "descriptor": "Double Parked Blocking Traffic", "n": "7"},
            {"police_precinct": "Precinct 1", "complaint_type": "Illegal Parking",
             "descriptor": "Blocked Hydrant", "n": "24"},
            {"police_precinct": "Precinct 1", "complaint_type": "Noise - Commercial", "descriptor": "x", "n": "5"},
            {"police_precinct": "Unspecified", "complaint_type": "Drinking", "descriptor": "x", "n": "9"}]
    c = rc.qol_counts(rows)
    assert c[("1", "Double Parking")] == 7
    assert c[("1", "Illegal Parking#")] == 24
    assert c[("1", "Noise - Business Related")] == 5
    assert c[("1", "Total Noise")] == 5
    assert c[("1", "Total 311 Calls")] == 36
    assert ("Unspecified", "Drinking") not in c


def test_summary_counts_exact_matches() -> None:
    wk = D(2026, 9, 27)
    cs = [rc.Compared("compstat", "ours", wk, "wtd", "1", "Robbery", 3, 3),
          rc.Compared("compstat", "ours", wk, "wtd", "1", "Murder", 0, 1)]
    (s,) = rc.summarize(cs, {("compstat", wk): "u"}, {("compstat", wk): "5uac-w243"})
    assert (s["compared"], s["exact"], s["report_total"], s["open_data_total"], s["query"]) == (2, 1, "3", "4", "u")


def test_crash_run_compares_citywide_and_precincts(tmp_path: Path) -> None:
    ts = tmp_path / "data" / "trafficstat" / "2026-01-11.csv"
    write(ts, [row("Citywide", "", "Collisions", 5, start="2026-01-05", end="2026-01-11", section="Collisions",
                   sheet="Page1"),
               row("001", "1", "Collisions", 2, start="2026-01-05", end="2026-01-11", section="Collisions",
                   sheet="Page1")])
    write(tmp_path / "data" / "fatalities" / "2026-01-11.csv",
          [{**row("Citywide", "", "Traffic Fatalities", 1, period="ytd", start="", end="2026-01-11",
                  section="Traffic Fatalities", sheet="PAPD")}])
    many = [(str(i), [[[[100 + i, 0], [101 + i, 0], [101 + i, 1], [100 + i, 1], [100 + i, 0]]]]) for i in range(2, 72)]
    geo = gj([("1", [[SQUARE]]), *many])

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "geojson" in url:
            return httpx.Response(200, json=geo)
        if "group" in url:
            return httpx.Response(200, text='"crash_date","crashes","injured","killed"\n'
                                            '"2026-01-06T00:00:00.000","4","1","1"\n'
                                            '"2026-01-11T00:00:00.000","2","0","0"\n')
        return httpx.Response(200, text='"collision_id","crash_date","latitude","longitude",'
                                        '"number_of_persons_injured","number_of_persons_killed"\n'
                                        '"1","2026-01-06T00:00:00.000","2","2","1","1"\n'
                                        '"2","2026-01-07T00:00:00.000","","","0","0"\n')

    od = rc.OpenData(slow_fetcher(handler))
    compared, summary, info = rc.run_crashes(od, tmp_path, per_precinct_year=2026)
    got = {(c.check, c.geography, c.period): (c.report, c.open_data) for c in compared}
    assert got[("trafficstat", "Citywide", "wtd")] == (5, 6)
    assert got[("trafficstat_precinct", "1", "wtd")] == (2, 1)
    assert got[("fatalities", "Citywide", "ytd")] == (1, 1)
    assert info["points"] == 2 and info["placed"] == 1


def test_chunks_end_on_the_window_end() -> None:
    assert rc.chunks(D(2026, 1, 1), D(2026, 3, 10)) == [
        (D(2026, 1, 1), D(2026, 1, 13)), (D(2026, 1, 14), D(2026, 2, 10)), (D(2026, 2, 11), D(2026, 3, 10))]
    assert rc.chunks(D(2026, 9, 28), D(2026, 10, 25)) == [(D(2026, 9, 28), D(2026, 10, 25))]


def test_weeks_sort() -> None:
    a, b = rc.Week(D(2017, 7, 17), D(2017, 7, 23)), rc.Week(D(2016, 7, 18), D(2016, 7, 24))
    assert sorted([a, b]) == [b, a]

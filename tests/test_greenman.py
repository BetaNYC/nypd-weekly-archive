"""Josh Greenman's parsed CompStat weeks -> data/compstat_greenman/."""

from __future__ import annotations

import csv
import json
import subprocess
from pathlib import Path

import pytest

from nypd_weekly_archive.greenman import OUT, GreenmanError, compare, import_greenman, rows_for_week

BLOCK = {"week_to_date": {"current_year": 0, "prior_year": 4.0, "pct_change": -100},
         "twenty_eight_day": {"current_year": 10, "prior_year": 10, "pct_change": 0},
         "year_to_date": {"current_year": 120, "prior_year": 138, "pct_change": -13.04},
         "historical": {"2_yr_pct": -6.97, "14_yr_pct": 53.84, "31_yr_pct": None}}


def geo(name: str, start: str = "11/9/2026", end: str = "11/15/2026") -> dict:
    return {"source": name, "report_period": {"raw": f"{start}  Through  {end}", "week_start": start, "week_end": end},
            "seven_major_felonies": {"Robbery": BLOCK}, "total_seven_major": BLOCK,
            "additional_stats": {"Traffic Fatalities": {"week_to_date": {"current_year": 1, "prior_year": 0}}}}


def test_rows_map_his_fields_to_ours() -> None:
    rows, stale = rows_for_week({"citywide": geo("Citywide"), "1st Precinct": geo("1st Precinct"),
                                 "Bronx": geo("Bronx", "5/11/2026", "5/17/2026")}, "greenman/data/archive/x.json")
    assert stale == ["Bronx"]
    by = {(r["geography"], r["section"], r["measure"], r["period"], r["year"]): r["value"] for r in rows}
    assert by[("1st Precinct", "Crime Complaints", "Robbery", "wtd", 2025)] == 4  # 4.0 -> 4
    assert by[("1st Precinct", "Crime Complaints", "Robbery", "ytd", 2026)] == 120
    assert by[("1st Precinct", "Crime Complaints", "Robbery", "ytd_pct_change_vs_2010", 2026)] == 53.84
    assert by[("1st Precinct", "Crime Complaints", "Robbery", "ytd_pct_change_vs_1993", 2026)] == ""
    assert by[("Citywide", "Crime Complaints", "TOTAL", "28d", 2026)] == 10
    assert by[("Citywide", "Traffic Statistics", "Traffic Fatalities", "wtd", 2026)] == 1
    assert not any(r["period"].startswith("pct") for r in rows)
    assert {r["precinct"] for r in rows} == {1, ""}


def test_unknown_report_year_stops() -> None:
    with pytest.raises(GreenmanError, match="no base years"):
        rows_for_week({"citywide": geo("Citywide", "1/5/2027", "1/11/2027")}, "x")


def test_import_writes_weeks_readme_and_compares(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    (clone / "data" / "archive").mkdir(parents=True)
    (clone / "data" / "archive" / "2026-11-15.json").write_text(
        json.dumps({"citywide": geo("Citywide"), "1st Precinct": geo("1st Precinct")}))
    git = ["git", "-C", str(clone), "-c", "user.name=t", "-c", "user.email=t@example.org"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "remote", "add", "origin", "https://github.com/joshgreenman1973/nypd-compstat-scraper"],
                   check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    repo = tmp_path / "repo"
    ours = repo / "data" / "compstat"
    ours.mkdir(parents=True)
    with (ours / "2026-11-15.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["geography", "section", "measure", "period", "year", "value"])
        w.writerow(["1st Precinct", "Crime Complaints", "Robbery", "ytd", "2026", "120"])
        w.writerow(["1st Precinct", "Crime Complaints", "Robbery", "ytd", "2025", "139"])  # differs

    assert import_greenman(repo, clone) == 0

    assert (repo / OUT / "2026-11-15.csv").exists()
    readme = (repo / OUT / "README.md").read_text()
    assert "https://github.com/joshgreenman1973/nypd-compstat-scraper" in readme and "permission" in readme
    assert compare(repo) == [{"week": "2026-11-15", "key": "1st Precinct|Crime Complaints|Robbery|ytd|2025",
                              "ours": "139", "greenman": "138"}]


def test_import_refuses_another_repo(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(clone), "remote", "add", "origin", "https://github.com/x/y"], check=True)
    with pytest.raises(GreenmanError):
        import_greenman(tmp_path / "repo", clone)

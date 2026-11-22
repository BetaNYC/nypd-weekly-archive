"""Tim McDermott's weekly CompStat CSVs -> data/compstat_mcdermott/."""

from __future__ import annotations

from pathlib import Path

from nypd_weekly_archive.mcdermott import OUT, import_mcdermott, place, rows_for_file
from nypd_weekly_archive.yearfiles import read_year_files

HEAD = ("Start_date,End_Date,Crime,Current,Prior,Ratio,28Day_Current,28Day_Prior,28Day_Ratio,YTD_Current,YTD_Prior,"
        "YTD_Ratio,2YR,16YR(2010),33YR(1993),Volume_Number,Number_number,Mayor,Commissioner,Precinct\n")


def line(crime: str, where: str, vals: str = "1,2,-50.0,3,4,-25.0,5,6,-16.7,1.0,2.0,***.*",
         start: str = "  1/5/2026  ", end: str = "1/11/2026", vol: str = "33", wk: str = "2") -> str:
    return f'{start},{end},{crime},{vals},{vol},{wk},Zohran K. Mamdani,"Jessica S. Tisch",{where}\n'


def test_counts_years_and_long_changes() -> None:
    data = (HEAD + line("Robbery", "1st Precinct") + line("TOTAL", "Staten Island", '"1,058",2,x,3,4,x,5,6,x,NA,2,3')
            ).encode()
    rows, notes = rows_for_file(data, "dathere/x/vol3302.csv", 33)
    by = {(r["geography"], r["measure"], r["period"], r["year"]): r["value"] for r in rows}
    assert by[("1st Precinct", "Robbery", "wtd", 2026)] == 1
    assert by[("1st Precinct", "Robbery", "wtd", 2025)] == 2
    assert by[("1st Precinct", "Robbery", "ytd", 2025)] == 6
    assert by[("1st Precinct", "Robbery", "ytd_pct_change_vs_2024", 2026)] == 1.0
    assert by[("1st Precinct", "Robbery", "ytd_pct_change_vs_1993", 2026)] == ""  # ***.*
    assert by[("Staten Island", "TOTAL", "wtd", 2026)] == 1058  # "1,058"
    assert by[("Staten Island", "TOTAL", "ytd_pct_change_vs_2024", 2026)] == ""  # NA
    assert {r["precinct"] for r in rows} == {1, ""}
    assert not notes


def test_printed_volume_sets_the_year_not_the_file_name() -> None:
    data = (HEAD + line("Robbery", "1st Precinct", start="12/28/2015", end="1/3/2016", vol="23", wk="53")).encode()
    rows, notes = rows_for_file(data, "x/vol2253.csv", 22)
    assert {r["year"] for r in rows if r["period"] == "wtd"} == {2016, 2015}
    assert notes["year_from_printed_volume_not_file_name"] == 1


def test_2015_16_long_changes_have_no_base_year() -> None:
    rows, _ = rows_for_file((HEAD + line("Robbery", "1st Precinct", vol="22", end="1/11/2015")).encode(), "x", 22)
    assert {r["period"] for r in rows if r["unit"] == "percent"} == {
        "ytd_pct_change_vs_2013", "ytd_pct_change_mid", "ytd_pct_change_long"}


def test_old_labels_after_2022_are_relabelled() -> None:
    crimes = ["Murder", "Misd. Sex Crimes", "Shooting Vic.", "Shooting Inc."]
    data = (HEAD + "".join(line(c, "1st Precinct", vol="29", end="1/9/2022") for c in crimes)).encode()
    rows, notes = rows_for_file(data, "x", 29)
    got = {(r["measure"], r["sheet"]) for r in rows}
    assert ("UCR Rape*", "CompStat (relabelled by BetaNYC)") in got
    assert ("Other Sex Crimes", "CompStat (relabelled by BetaNYC)") in got
    assert ("Shooting Vic.", "CompStat (relabelled by BetaNYC)") in got
    assert ("Murder", "CompStat") in got
    assert notes["relabelled_row"] == 3
    # New layout (has UCR Rape*): nothing is moved.
    new = ["Murder", "UCR Rape*", "Shooting Vic."]
    data = (HEAD + "".join(line(c, "1st Precinct", vol="29") for c in new)).encode()
    assert not rows_for_file(data, "x", 29)[1]["relabelled_row"]


def test_place_carried_down_but_not_into_an_unnamed_block() -> None:
    data = (HEAD + line("Murder", "70th Precinct") + line("Rape", "") + line("Murder", "") + line("Rape", "")
            + line("Murder", "Midtown Precinct South") + line("Rape", "")).encode()
    rows, notes = rows_for_file(data, "x", 33)
    got = {(r["geography"], r["precinct"], r["measure"]) for r in rows}
    assert got == {("70th Precinct", 70, "Murder"), ("70th Precinct", 70, "Rape"),
                   ("Midtown Precinct South", 14, "Murder"), ("Midtown Precinct South", 14, "Rape")}
    assert notes["dropped_no_crime_or_place"] == 2


def test_place_rejects_names_that_are_not_places() -> None:
    assert place("William J. Bratton") is None
    assert place("Precinct") is None
    assert place("Bronx North") == ("Bronx North", "")


def test_import_keeps_the_fuller_copy_and_writes_readme(tmp_path: Path) -> None:
    a, b = tmp_path / "a" / "2026", tmp_path / "b" / "2026"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "vol3302.csv").write_text(HEAD + line("Robbery", "1st Precinct"))
    (b / "vol3302.csv").write_text(HEAD + line("Robbery", "1st Precinct") + line("Murder", "5th Precinct"))
    (b / "vol33all.csv").write_text(HEAD + line("Robbery", "1st Precinct"))  # combined files are ignored
    stats = import_mcdermott(tmp_path / "repo", [("a", tmp_path / "a"), ("b", tmp_path / "b")])
    assert stats["weeks"] == 1
    assert sorted(p.name for p in (tmp_path / "repo" / OUT).iterdir()) == ["2026.csv.gz", "README.md"]
    weeks = read_year_files(tmp_path / "repo" / OUT)
    assert list(weeks) == ["2026-01-11"]
    assert {r["source_file"] for r in weeks["2026-01-11"]} == {"b/2026/vol3302.csv"}
    assert "Thank you, Tim" in (tmp_path / "repo" / OUT / "README.md").read_text()

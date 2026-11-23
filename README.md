# NYPD weekly archive

NYPD posts four sets of weekly statistics on nyc.gov as Excel and PDF files. Each week's files replace the previous week's at the same address, and nyc.gov keeps no back issues. None of these four is on NYC Open Data. Unless someone saves a copy every week, the history is gone.

This repository saves a copy every week and turns the spreadsheets into tidy CSV files.

**Website: <https://betanyc.github.io/nypd-weekly-archive/>** explains the project, and has one page per series (TrafficStat, CompStat, traffic fatalities, quality of life) charting what we saved for each precinct, week by week. Link to one precinct with, for example, `compstat.html?precinct=034#coverage`. Older links (`?precinct=034#coverage` on the home page) forward to the TrafficStat page.

## What it captures

| Series | NYPD page | Files | What is in it |
|---|---|---|---|
| `trafficstat` | [TrafficStat](https://www.nyc.gov/site/nypd/stats/traffic-data/traffic-data-trafficstat.page) | Citywide, 8 patrol boroughs, 78 precincts | Collisions, injuries and deaths by mode; DWI and VTL 511 arrests; parking summonses (double parking, bus stop, bus lane, crosswalk, bike lane); hazardous-violation summonses; moving summonses by vehicle type, including e-bikes, stand-up scooters and mopeds. Week to date, 28 days and year to date, each beside the same period a year earlier. |
| `fatalities` | [Traffic Fatalities](https://www.nyc.gov/site/nypd/stats/traffic-data/traffic-fatalities.page) | One per patrol borough, plus Port Authority Police | Traffic deaths by precinct: 7 days, 28 days and year to date, against the prior year. Each file also repeats the citywide total. |
| `compstat` | [Borough and precinct crime statistics](https://www.nyc.gov/site/nypd/stats/crime-statistics/borough-and-precinct-crime-stats.page), and [Citywide crime statistics](https://www.nyc.gov/site/nypd/stats/crime-statistics/citywide-crime-stats.page) for the citywide and Department of Correction sheets | 78 precincts, 8 patrol boroughs, citywide, Department of Correction | Crime complaints, shootings, hate crimes and traffic fatalities, with NYPD's 2-year and long-range percent changes and a calendar-year history (1990, 1993, 1998, 2001, last year). |
| `qteam` | [Quality of Life statistics](https://www.nyc.gov/site/nypd/stats/quality-of-life-stats/quality-of-life-stats.page) | Citywide and 78 precincts | 911 and 311 calls for service: noise, vehicle-related calls (illegal parking, blocked driveway, double parking, abandoned vehicles, dangerous driving) and disorderly behavior. This series runs a few weeks behind the others. |
| `housing` | [Citywide crime statistics](https://www.nyc.gov/site/nypd/stats/crime-statistics/citywide-crime-stats.page) | One file (`hb_eagle_report`), 35 sheets | NYPD Housing Bureau weekly report: the seven major felonies for each NYCHA development, by Police Service Area and precinct, week to date, 28 days and year to date against the prior year. Read into `data/housing/` since November 23, 2026; see "NYCHA Housing Bureau report" below. |

The list of files is read from NYPD's own pages on every run, never built from a pattern: the four pages above, the [precinct directory](https://www.nyc.gov/site/nypd/bureaus/patrol/precincts-landing.page), and each precinct page it links. TrafficStat and Q-Team precinct files are linked only from the precinct pages. `manifest.csv` holds the list from the latest run. If a page cannot be fetched, or a series loses more than 10 percent of its files from one run to the next, the run stops and says so.

Every week the archive also saves the crash points on the map of NYPD's [TrafficStat dashboard](https://trafficsafetyforum.nypdonline.org/), which the dashboard shows only for the current week. See "Dashboard maps" below.

The Traffic Fatalities page also links `cityacc-en-us.xlsx`, a monthly collision report last updated in January 2024. It is not weekly and is not captured.

## Where things are

- The original Excel and PDF files, exactly as NYPD published them, are not in git. They are attached to this repository's GitHub Releases as zip files (see "Where the originals are" below). While a run is working, they sit in `raw/<series>/<week ending>/`; the date is the last day of the report week, read from the spreadsheet, and a PDF takes the date of the spreadsheet with the same name. If NYPD changes a file again within the same week, both versions are kept; the later one gets part of its checksum in its name.
- `captures.csv` is the ledger, with one row per file saved. Each row has its address, SHA-256, size, the `Last-Modified` time nyc.gov reported, the week-ending date and where that date came from: `xlsx` (read from the spreadsheet), `sibling_xlsx` (a PDF dated by its spreadsheet), `fetch_date` or `fetch_date_layout_error` (the download date, because the file could not be read), `wayback_capture_date`, `pdf` (a PDF dated by its own cover line) or `github_folder_date`. The `source` column says whether the copy came from nyc.gov, the Internet Archive (`wayback`) or another GitHub archive (`github:<owner>/<repo>`, with `source_ref` linking the exact file and commit there), and `release_tag` and `release_asset` say which release and zip hold the original.
- `moves.csv` lists every file that `redate` moved from one folder in `raw/` to another, one row per file: `moved_on`, `series`, `sha256`, `old_path`, `new_path`, `release_tag`, `release_asset` and `reason`. The zips on GitHub Releases keep the old path, so this is how to find a moved file in them. See "Files that moved" below.
- `backfills.csv` does the same for backfill files that are not weekly captures (dashboard responses, Internet Archive listings).
- `data/<series>/<week ending>.csv` is the tidy version, one row per number. Each week is its own file so that no single file grows past what GitHub accepts. Join the files for a series to get its history.
- `data/housing/<week ending>.csv` holds the NYCHA Housing Bureau report, about 21,500 numbers a week: every development, precinct, Police Service Area and citywide total NYPD prints. See "NYCHA Housing Bureau report" below.
- `data/compstat_mcdermott/<year>.csv.gz` holds CompStat numbers from January 2015 to February 2026, from weekly CSVs made by Tim McDermott, a BetaNYC volunteer. `data/compstat_greenman/<year>.csv.gz` holds CompStat numbers from March 2026 on, parsed by Josh Greenman's [nypd-compstat-scraper](https://github.com/joshgreenman1973/nypd-compstat-scraper), used with his permission. Both are kept apart from `data/compstat/` because this repo did not read them from NYPD's files. Each is one gzip-compressed CSV per year, in the same columns as `data/`, so that millions of rows fit in a few megabytes; read one with `pandas.read_csv("2020.csv.gz")` or `gzip -dc 2020.csv.gz`. See "CompStat backfill" below.
- `data/trafficstat_dashboard_map/<week ending>.csv` holds the TrafficStat dashboard's map points for that week, one row per crash. See "Dashboard maps" below.
- `data/anomalies.csv` lists places where an NYPD file disagrees with itself but could still be read. The first one: in the Staten Island fatalities file for the week ending November 15, 2026, the citywide "CHANGE" cell is typed in as -2 while the two counts beside it are 3 and 1. The counts are kept as published.
- `docs/` is the GitHub Pages site (see "Website" below). `docs/data/<series>-coverage.json` (one per series) is rebuilt from `captures.csv` and `data/` on every scheduled run.
- `data/stale_files.csv` lists files whose newest week is at least 7 days behind the newest week in their series. nyc.gov can keep serving an old file with a normal response (the Bronx patrol borough file from before the 2026 north/south split is one), so the week printed inside the file is what counts, not the server's answer.

### Columns in `data/`

| Column | Meaning |
|---|---|
| `series` | `trafficstat`, `fatalities`, `compstat`, `qteam` or `housing` |
| `source_file` | The file in `raw/` this row came from |
| `geography` | NYPD's label, such as `Citywide`, a patrol borough, `034`, `34th Precinct`, or a public-housing development |
| `precinct` | Precinct number, when the row is for one precinct |
| `week_start`, `week_end` | The report week. The fatalities files give only the end date. |
| `sheet`, `section`, `measure` | NYPD's labels, unchanged, footnote marks included (`Total Parkers*`, `Motor Vehicle+`) |
| `period` | `wtd` (week to date), `7d`, `28d`, `ytd`, `calendar_year`, or `ytd_pct_change_vs_<year>` |
| `year` | The year the value belongs to. NYPD prints each figure beside the same period a year earlier; that earlier figure is its own row. |
| `value` | The number. Empty where NYPD left the cell blank. |
| `unit` | `count`, or `percent` for CompStat's long-range changes |

NYPD's own "Change" and "% +/-" columns are not carried over, because they can be recomputed from the two years. The parser does check every "Change" against the two counts, which catches a shifted column. CompStat's 2-year and long-range percent changes are kept, because the counts behind them are not in the file.

What the parser accepts, besides the plain layout, all seen in this week's files: tables that start in column B instead of A; a fatalities workbook (Port Authority Police) that carries eight undated tabs of 2025 figures next to its current tab, where only the tab with a "through" date line is read; a sheet named `Table 1` instead of `Sheet1`; blank cells in CompStat's history for precincts that did not exist in 1990; `/0` where a percent cannot be computed; and the note in the 105th, 113th and 116th Precinct TrafficStat files that their figures were adjusted for the 116th Precinct, created in December 2024.

TrafficStat has two sheets that overlap. `Page1` groups e-bikes, scooters, mopeds and motorcycles as "Motorized Two-Wheeler+"; `Page2` breaks them out. Use one sheet at a time when adding things up.

### NYCHA Housing Bureau report

As of November 23, 2026, `parse_housing` reads every Housing Bureau spreadsheet on disk: 44 report weeks, from the week ending August 30, 2015 to the week ending November 15, 2026. All 44 have the same 35 sheets in the same order, with the same headers. Only the sheet names changed: files up to May 2025 number every sheet (`WTD_1`, `28 Days_2`, ... `HA YTD_35`), later ones do not (`WTD`, `28 Days`, ... `HA YTD`). Both name lists are accepted exactly; anything else stops the parse.

Eighteen of the older files declare each sheet's size as "A1" while holding a full table. The spreadsheet library trusts that in read-only mode and sees one row, which is why these files looked empty before. The parser resets the size before reading.

The 35 sheets, each with the seven major felonies, "Shooting Inc." and "Total Index" (the seven felonies added up; shootings are not in it), for the report year and the year before:

- **Nine PSA sheets per period** (`WTD`, `28 Days`, `YTD`, `WTD_2` ... `YTD_9`), one per Police Service Area, and **`SI` and `RK`** (Staten Island and Rockaway), which stack all three periods in one sheet. Columns C to E are PSA, PCT and Development. NYPD merges each PSA and precinct cell down over the rows it covers; the parser carries the label down the same way.
- **`Viper WTD`, `Viper 28 Days`, `Viper YTD`**: developments covered by VIPER cameras, with NYPD's Viper number, and a totals row.
- **`HA WTD`, `HA 28 Days`, `HA YTD`**: one row per PSA and per Patrol Services Bureau (PSB) housing unit, then "HB Covered", "PSB Covered" and "HA City Wide".

How the rows are filled:

| Column | Housing Bureau rows |
|---|---|
| `sheet` | NYPD's sheet name |
| `geography` | The row's own label, as printed: a development (`CAREY`), or a totals label (`HBB 060 PCT Totals`, `PSA 1 Totals`, `HA City Wide`) |
| `precinct` | The precinct, for a development or a precinct-totals row |
| `section` | The PSA group in column C (`PSA 1`, `PSA 1 Satellite`, `PSA 1 PSB`, `STATEN ISLAND`) for rows inside it; `Viper <n>` on the Viper sheets; on the HA sheets, the `PSA n` row that opens a block, for the rows down to its `Total`; empty for totals that span groups |
| `measure` | `Murder`, `Rape`, `Robbery`, `Fel. Assault`, `Burglary`, `Gr. Larceny`, `GLA`, `Shooting Inc.`, `Total Index` |
| `period` | `wtd`, `28d` or `ytd`, from the sheet's title, checked against its date line |

`section` is what tells apart a label NYPD uses twice on one sheet. On `WTD`, "PSA 1 Totals" with section `PSA 1` is the PSA 1 group alone, and "PSA 1 Totals" with an empty section is PSA 1 with its Satellite.

The sheets total each other several ways, and the PSA, Viper and HA sheets repeat the same developments. Pick one level and one sheet type before adding anything up. The parser checks every total NYPD prints against the rows it totals (developments to precinct, precinct to group, groups to PSA, Viper rows to their total, HA blocks and citywide rows), every "Total Index" against its seven felonies, and every "CH %" against its two counts. All of them held in all 44 files.

Reshaped: one number per row; the "CH %" columns are checked, then dropped; the PSA column of the Viper sheets (`6`, `SI`) is checked, then dropped, since the precinct is kept and the PSA sheets give each precinct's PSA.

What is not read yet:

- **25 spreadsheets were filed under the wrong week until November 23, 2026.** They were saved before the parser existed, so `captures.csv` filed them under the date the Internet Archive saved them (`fetch_date_layout_error`). `redate` moved them to their report weeks on November 23, 2026, and they are now in `data/housing/`; each move is a row in `moves.csv`. One of them, saved by the archive in May 2025 under the old address, is the report for the week ending May 7, 2017.
- **PDF-only weeks.** By the cover line each PDF prints ("Compstat Period Ending Aug 30, 2015"), 83 of the 86 PDFs on disk are datable, and 53 of those weeks have no spreadsheet. Three PDFs (saved 2021-01-19, 2024-03-05 and 2024-09-08) are cut off at exactly 1 MiB and do not open. The PDFs are not read; see "Spreadsheets, not PDFs" below.

## Where the originals are

Each GitHub Release holds zip files of originals:

| Release | What it holds |
|---|---|
| `week-<week ending>`, e.g. `week-2026-11-15` | Files captured from nyc.gov for that report week. One zip per series per run, named `<series>_<week ending>_<run time>.zip`. A run that catches two weeks (Q-Team runs behind) adds to two releases. The same release holds `trafficstat-dashboard-map_<week ending>_<run time>.zip`, the dashboard map answers behind `data/trafficstat_dashboard_map/`. |
| `fetched-<date>` | Files from nyc.gov whose week could not be read, filed by download date |
| `backfill-dashboard-2026-11-21` | The TrafficStat dashboard responses behind `data/trafficstat_dashboard_weekly.csv` |
| `backfill-wayback-2026-11-21` | Copies downloaded from the Internet Archive, and the archive's listings |
| `backfill-github-<date>` | CompStat PDFs from 2014 to 2016, copied from [ajschumacher/nypd](https://github.com/ajschumacher/nypd). One zip per year. |

Inside a zip, each file sits at the path it had when the zip was made, which is the path in `captures.csv` unless the file has moved since (for example `raw/trafficstat/2026-11-15/ts-en-us-034.xlsx`). GitHub accepts release files up to 2 GiB each; a week of the largest series is about 50 MB.

To get the original behind any row of `data/`, take its `source_file`, find that path in `captures.csv`, and download the zip named in `release_asset` from the release in `release_tag`:

```sh
grep 'raw/trafficstat/2026-11-15/ts-en-us-034.xlsx' captures.csv   # gives release_tag and release_asset
gh release download week-2026-11-15 --pattern 'trafficstat_2026-11-15_*.zip' --dir originals
unzip originals/trafficstat_2026-11-15_*.zip raw/trafficstat/2026-11-15/ts-en-us-034.xlsx
shasum -a 256 raw/trafficstat/2026-11-15/ts-en-us-034.xlsx           # matches the sha256 column
```

### Files that moved

A file saved before the parser could read it is filed under the date it was downloaded (`fetch_date_layout_error`). Once the parser learns its layout, `redate` moves it to its report week and changes its path in `captures.csv`. The zip on Releases is not rewritten, so it still holds the file under its old path. `moves.csv` records each move: the old path, the new path, the checksum, and the release and zip that hold the file. A file that moved twice has two rows, in order.

To bring a copy of `raw/` (or files unpacked from a release zip) up to date, run:

```sh
python scripts/apply_moves.py --root <folder that holds raw/>           # shows what it would do
python scripts/apply_moves.py --root <folder that holds raw/> --apply   # moves the files
```

It moves a file only when its checksum matches the row, skips rows already done, and reports files it cannot find. It never deletes or overwrites a file: if the new path already holds a different file, or a file's checksum does not match, it leaves both alone, says so, and exits with status 1.

Each run uploads its zips before it commits `captures.csv`. If the upload fails, nothing is committed and the next run captures the same files again. `uv run nypd-archive package` writes the zips to `dist/` and lists them in `dist/upload-plan.tsv`; `scripts/upload-release-assets.sh` creates any missing release and uploads them. Rebuilding `data/` needs the originals on disk, so `build` leaves alone any week whose files are not there.

## When NYPD updates

Read from the `Last-Modified` times nyc.gov reported for all 528 files on 2026-11-21, for the week ending Sunday, November 15, 2026:

| Files | Last changed (UTC) |
|---|---|
| Fatalities spreadsheets | Monday, about 15:30 |
| CompStat spreadsheets | Monday, about 17:30 |
| TrafficStat spreadsheets | Tuesday, about 16:40 |
| PDFs | Mostly within an hour of their spreadsheet; some replaced again up to Wednesday about 22:00 |
| Q-Team spreadsheets | Tuesday, October 27: about three weeks behind the others |

The scheduled job runs Thursday and Saturday at 14:23 UTC. Thursday comes after the last Wednesday replacements; Saturday is a second chance for anything late and for a failed Thursday run. Both are before the Monday overwrite. One week of timestamps is a small sample, so check `captures.csv` after a few weeks and move the schedule if NYPD's timing shifts.

## Running it

Needs [uv](https://docs.astral.sh/uv/) and Python 3.11 to 3.14.

```sh
uv run nypd-archive run          # discover, capture, rebuild data/
uv run nypd-archive discover     # only refresh manifest.csv
uv run nypd-archive capture      # only download what changed
uv run nypd-archive build        # only rebuild data/ from raw/
uv run nypd-archive site         # only rebuild docs/data/ for the website
uv run nypd-archive dashboard-map  # save this week's TrafficStat dashboard map points
uv run pytest
```

Useful options: `--series trafficstat fatalities` to limit a run, `--skip-pdf` to save only spreadsheets, `--allow-shrink` after you have checked that NYPD really did drop files.

Requests go one at a time, at least one second apart, with a descriptive user agent. Failed requests are retried with capped backoff, and `Retry-After` is honored. Each request sends `If-Modified-Since`, so unchanged files cost nyc.gov a 304 and nothing more. A run that finds nothing new changes nothing.

Files are identified by their first bytes, never by the `Content-Type` header: a spreadsheet must start like a ZIP file and a PDF with `%PDF-`. Anything else (an error page served with a 200, say) is reported as a failed download and not saved.

The exit code is 1 if any file failed to download or did not match the layout the parser knows. Files that do not parse are still saved; only `data/` is held back. An unfamiliar row label, section, sheet or header stops the parser, so a change at NYPD shows up as a red run instead of numbers quietly going missing.

After fixing the parser for a file it rejected, `uv run nypd-archive redate` re-reads the files that were filed under their download date, moves them to their report week, and rebuilds `data/`. Each move (a PDF that follows its spreadsheet included) is appended to `moves.csv` as it happens. A new path is never one another row of `captures.csv` already uses, even when that file is not on disk; the file gets part of its checksum in its name instead. Commit `moves.csv` with `captures.csv`, then run `scripts/apply_moves.py` on any other copy of `raw/` (see "Files that moved").

## Dashboard maps

The map on NYPD's [TrafficStat dashboard](https://trafficsafetyforum.nypdonline.org/) shows the crashes behind each number in its table as points. It shows them only for this week, the last 28 days and the year to date, plus the same stretch a year earlier, so a week that is not saved is gone. `uv run nypd-archive dashboard-map` saves the current week, and a scheduled job (`.github/workflows/dashboard-map.yml`) runs it Thursday and Saturday at 16:23 UTC, after the weekly capture. If that week is already saved, it stops after one request.

For each precinct it asks for the precinct's table, then for the points of each week-to-date measure whose count is not zero. For one precinct (075, week ending November 15, 2026) that was 15 requests; a full run is about a thousand, at least 2 seconds apart. As with the dashboard backfill, the requests are the ones the dashboard sends from a browser, observed on 2026-11-22 and listed in `src/nypd_weekly_archive/dashboard_map.py`, and the run stops at the first answer that does not match. It also stops if a precinct has 5,000 points or more (TrafficStat maps no more than that), and if the dashboard moves to a new week during the run. The points' counts are checked against the table. Fewer points than the table is logged; more points than the table is logged and makes the run exit 1. Either way the week is kept.

Raw answers go to `raw/trafficstat_dashboard_map/<week ending>/responses.jsonl.gz` and into that week's release. `data/trafficstat_dashboard_map/<week ending>.csv` has one row per incident:

| Column | Meaning |
|---|---|
| `site` | `trafficstat` (or `compstat`, below) |
| `week_start`, `week_end` | The dashboard's report week |
| `precinct` | The precinct NYPD counts the incident in |
| `record_id`, `window`, `measure` | The dashboard's cell, such as `WTD_COLLISIONS_Col E-bike`: window `WTD`, measure `COLLISIONS_Col E-bike` |
| `point` | Which point in the answer. Incidents at the same spot are merged into one point. |
| `latitude`, `longitude` | As NYPD gives them, 7 decimals |
| `point_count` | The count at that point. For fatality measures it counts deaths, so one crash can have a count of 2. |
| `incident` | Which incident at that point |
| `label` | NYPD's label. For crashes, a crash type such as `RIGHT ANGLE`, `LEFT TURN (OPP DIR)`, `OTHER`, `UNKNOWN`; NYPD does not say what field this is. |
| `extra_labels` | Any further labels on the incident, joined with `; ` |
| `date`, `hour` | As NYPD shows them. `hour` is 0 to 23. Most crashes show 12AM (hour 0), which most likely means no hour was given, not midnight. |
| `fetched_on` | The day the answer was saved |

Things to know:

- The crash points are the same crashes as NYC Open Data's [Motor Vehicle Collisions - Crashes](https://data.cityofnewyork.us/d/h9gi-nx95), at the same coordinates: in a test of one precinct and one week, 36 of 43 matched within 2 meters on the same day. The map adds NYPD's precinct, its mode groups, the crash-type label and its own death count. It has no collision id, no street names and no injury counts by role. Some dates are a day or two later than Open Data's.
- An incident appears once for each measure it counts in: under `Collisions`, again under `Injury Collisions`, again under each mode. "Motor Vehicle+" and "Motorized Two-Wheeler+" are groups of the other modes. Pick one measure, or the modes without the groups, before counting.

Options:

- `--windows YTD SPLYYTD` pulls other windows instead of the week, into `<week ending>_YTD-SPLYYTD.csv`. Last year's year to date grows until December 31 and is then gone.
- `--geos 075` limits a run to some precincts, for testing. Its files are named `<week ending>_only-075`, so it never stands in for a full week.
- `--out <folder>` writes `raw/` and `data/` under that folder instead of this repository.
- `--site compstat` reads the map on [CompStat 2.0](https://compstat.nypdonline.org/) instead, the same way: 19 crime measures, with a label such as the robbery type. CompStat points are kept on a local machine only, never in this repository or its releases, so this needs `--out` with a folder outside the repository, and the command refuses anything else. There is no scheduled job for it here.

## Backfills

Two one-off commands recover weeks from before this archive started.

### TrafficStat dashboard

`uv run nypd-archive backfill-dashboard` reads NYPD's [TrafficStat dashboard](https://trafficsafetyforum.nypdonline.org/). Its weekly timeline covers the 53 weeks ending with the current report week, and a "same period last year" view covers the 53 weeks before that, for Citywide and every precinct. It has collisions, injuries and fatalities by mode (30 measures). It has no summonses or arrests: those exist only in the weekly files.

The dashboard publishes no documentation for this. The command sends exactly the requests the dashboard sends from a browser, as observed on 2026-11-21 (listed in `src/nypd_weekly_archive/dashboard.py`), and stops at the first response that does not match. Each run saves every response to `raw/trafficstat_dashboard/<date>/responses.jsonl.gz` (published in the release `backfill-dashboard-<date>`) and rebuilds `data/trafficstat_dashboard_weekly.csv`. Requests go at least 2 seconds apart; a full pull is about 4,800 requests.

Things to know about that file:

- The dashboard leaves out weeks with a count of zero. Inside the 53 weeks a timeline covers, a missing week is written as 0 with `imputed` set to 1.
- `window` is `WTD` for the current 53 weeks and `SPLYWTD` for the year before. The earlier window lines up by calendar date, not by weekday: when the current week ends Sunday, November 15, 2026, the earlier one ends Saturday, November 15, 2025. The two windows therefore do not join into one even series: the first week of the current window (ending Sunday, November 16, 2025) and the last week of the earlier one (ending Saturday, November 15, 2025) share six days.
- The window moves forward every week and older weeks drop off, so a run saves what is visible on that day and no more.

### Internet Archive copies

`uv run nypd-archive backfill-wayback --series trafficstat --limit 5` lists the Internet Archive's copies of a series through the [Wayback CDX server](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md) and downloads each distinct version in its original form. Every download is checked against the archive's own checksum before it is kept. Spreadsheets are dated from their contents like any other capture. CompStat PDFs are dated by their own cover line (`pdf`). Other PDFs are filed under the date the archive captured them, marked `wayback_capture_date` in the ledger, because that is all that is known about them. Ledger rows from this command have `source` set to `wayback` and the archive's capture timestamp, and the files go to the release `backfill-wayback-<date>`. Requests go at least 3 seconds apart, with a pause of at least a minute after a 429 or any 5xx, and five failed downloads in a row stop the run; running it again picks up where it stopped. It searches both of NYPD's download folders: `/assets/nypd/downloads/` (May 2017 on) and the older `/html/nypd/downloads/` on www.nyc.gov and www1.nyc.gov, including the old CompStat names `cscity.pdf` and `csCorrection.pdf`. `--names` limits a run to named files (no extension, any case) and asks the archive for those exact addresses, which is far lighter than listing a whole folder: `--series compstat housing --names cs-en-us-city cs-en-us-correction cscity cscorrection hb_eagle_report`. TrafficStat copies start in August 2024, which is when NYPD began posting the precinct files: the earliest saved file is from August 9, 2024, and the Wayback Machine's copies of the 34th Precinct page have no TrafficStat link through July 25, 2024 and one from September 17, 2024. Of the 120 report weeks from August 2024 to November 15, 2026, the typical precinct has some copy of 13 and a readable spreadsheet for 8.

TrafficStat files from 2024 use an older layout (sheets named `Page1_1` and `Page2_2`, no geography in the title block, and an extra "RMP Collisions" section). The parser does not read it yet. Those files are kept, marked `fetch_date_layout_error`, and `redate` will file them by week once it does.

### CompStat backfill

Three other people already kept CompStat history, and this archive builds on their work. Thank you to all three.

**2014 to 2016: [ajschumacher/nypd](https://github.com/ajschumacher/nypd).** Aaron Schumacher saved NYPD's CompStat PDFs from nyc.gov every Tuesday from January 2014, as part of a 2014 PDF Liberation hackathon. `uv run nypd-archive backfill-github --clone <clone of that repo>` copies the CompStat files from a local clone into this archive. Each PDF is dated by its own cover line ("Report Covering the Week … Through …"). Ledger rows have `source` set to `github:ajschumacher/nypd` and `source_ref` linking the file at the commit it came from. The result: 12,670 distinct PDFs covering 147 of the 153 report weeks from the week ending January 5, 2014 to the week ending December 4, 2016, for every precinct, patrol borough, citywide and the Department of Correction. Six single weeks are missing, in 2015 and 2016. The repo's later folders hold no CompStat files, because NYPD moved them to a new address. These are PDFs only; their numbers are not read yet (issue #10).

**2015 to February 2026: Tim McDermott.** Tim, a BetaNYC volunteer, turned NYPD's weekly CompStat reports into one CSV per week and published them on data.beta.nyc, BetaNYC's old data portal. When BetaNYC retired that portal in 2024, datHere re-hosted the files on [data.dathere.com](https://data.dathere.com/organization/betanyc) and kept a copy of the old site in Google Drive. `uv run nypd-archive import-mcdermott --source dathere=<folder> --source databeta-drive=<folder>` converts them into `data/compstat_mcdermott/`, in the same columns as `data/compstat/`. The result: 531 weeks, from the week ending January 4, 2015 to the week ending February 15, 2026, for every precinct and patrol borough. His files have no citywide totals. Where his weeks overlap the 2014 to 2016 PDFs above, all 336,066 numbers checked match NYPD's PDFs. A few repairs are made and logged, the largest for 2022 and 2023 weeks whose Drive copies carry last year's row labels; `data/compstat_mcdermott/README.md` and `src/nypd_weekly_archive/mcdermott.py` list them.

**2026: [joshgreenman1973/nypd-compstat-scraper](https://github.com/joshgreenman1973/nypd-compstat-scraper).** Josh Greenman's scraper reads NYPD's weekly CompStat spreadsheets into JSON, from March 2026. Josh gave BetaNYC permission to use his data with a link to his repo. `uv run nypd-archive import-greenman --clone <clone of that repo>` converts his weekly files into `data/compstat_greenman/`, in the same columns as `data/compstat/`, and then compares the weeks both have. For the week ending November 15, 2026, all 14,193 numbers that appear in both match. His files repeat the retired Bronx patrol borough with its last week (May 17, 2026) after NYPD split it; those repeats are dropped. `data/compstat_greenman/README.md` names the commit the data came from.

The Internet Archive also holds NYPD's own CompStat files from May 2017 on. `uv run nypd-archive backfill-wayback --series compstat` fetched them on November 22, 2026 (issue #9): 6,959 files, 2,166 spreadsheets and 4,793 PDFs, for weeks from May 2017 to November 2026. 890 spreadsheets were read then. 1,276 were in an older NYPD layout (see "Older CompStat layout" below); they are read since November 23, 2026.

On November 23, 2026 the same command, with the old folder and old names, added 197 more files for the citywide and Department of Correction CompStat reports and the NYCHA Housing Bureau report (`hb_eagle_report`, series `housing`). The archive now holds 567 weeks of the citywide report (May 2008 to November 2026), 175 of the Correction report (July 2014 on) and 100 of the Housing Bureau report (September 2014 on). Housing PDFs are filed under estimated weeks from the capture date, although each prints its week on its cover (see "NYCHA Housing Bureau report" above). 25 older Housing Bureau spreadsheets name their sheet `WTD_1` rather than `WTD`, and 18 citywide and Correction spreadsheets use the older CompStat layout below. All are read since November 23, 2026.

#### Older CompStat layout

1,294 CompStat spreadsheets from the Internet Archive use an older layout: 1,237 precinct and patrol borough files for weeks from May 21, 2017 to September 29, 2024, and 34 citywide and 23 Department of Correction files for weeks from August 30, 2015 to December 31, 2023. All 1,294 name their one sheet `CompStat_1`, not `CompStat` (kept in the `sheet` column), and 998 of them declare the sheet's size as "A1" while holding a full table, the same quirk as the older Housing Bureau files; the reader ignores the declared size. The rows changed over those years:

- up to the week ending August 18, 2019 (503 files), "Misd. Sex Crimes" where later files have "UCR Rape*" and "Other Sex Crimes", and the table ends with "All figures are subject to further analysis and revision. All degrees of rape are included in the rape category." instead of "Figures are preliminary and subject to further analysis and revision.";
- "Hate Crimes" from the week ending October 11, 2020, and "Retail Theft" and the "Traffic Statistics" section from the week ending September 15, 2024.

On November 23, 2026 `redate` filed all 1,294 under their report weeks, which added 133 weeks to `data/compstat/`; `moves.csv` lists each move. As a check, 25 of them (22 weeks, from 2015 to 2024: precincts, citywide and Correction) were compared with NYPD's PDF for the same week, using the PDF's own text: all 3,988 numbers compared match.

## Website

`docs/` is a static site served by GitHub Pages from the `main` branch's `/docs` folder, with no build step. It uses BetaNYC's Pages template: `docs/assets/site.css`, `docs/assets/nav.js` (the phone menu) and `docs/assets/share.js` are copied from it unchanged, so they can be updated together across BetaNYC sites. This site's own files are `docs/assets/archive.css` and `docs/assets/coverage.js`, the chart used by the seven series pages (`trafficstat.html`, `compstat.html`, `fatalities.html`, `quality-of-life.html`, `citywide-compstat.html`, `correction-compstat.html`, `nycha-housing.html`). Each page shows the same counts as a table under the chart. `about.html` says who built the site, how we use AI, licensing, contributing and how to support the work; every page's footer links it and carries a short note on AI use.

Each chart reads `docs/data/<series>-coverage.json`, written by `uv run nypd-archive site`. Each precinct-week gets the best copy the archive holds: numbers in CSV, a spreadsheet in an older layout the parser does not read yet, a PDF only, or nothing. For CompStat, "numbers in CSV" also counts `data/compstat_mcdermott/` and `data/compstat_greenman/`, and the file says how many precinct-weeks come only from those. Traffic fatality files cover a patrol borough each, so that chart is built from the precinct rows in `data/fatalities/`. Citywide CompStat, the Department of Correction's CompStat and the NYCHA Housing Bureau report are one file a week for the whole city, so each of their charts has a single row (`SINGLES` in `site_data.py`); citywide and Correction weeks count as "numbers in CSV" when their rows are in `data/compstat/`, and Housing Bureau weeks when their rows are in `data/housing/`.

A file from the Internet Archive that cannot be dated by its contents has only the date it was saved, so its week is estimated as the last Sunday at least two days before that date. Each coverage file's `estimate_check` tests that rule on the files that carry both dates; the series page prints the result (for TrafficStat on 2026-11-22: right for 450 of 571 spreadsheets). Estimated weeks are flagged in the file and in the chart's tooltip.

Picking a precinct, from the list, the chart or the table, opens its weeks in their own section under the chart and scrolls there. A report with a single row opens that section from the start. A chosen precinct is kept in the page address (`?precinct=034`), and the **Copy link** button copies it, so any view can be shared. The top menu has more links than the template's, so `archive.css` switches to the Menu button below 1100px instead of the template's 782px. Social sites show `docs/assets/og-image.png`, a 1200 x 630 screenshot of `docs/assets/social-card.html`; retake it when the chart changes a lot.

## Saving new captures to the Internet Archive

`--save-to-wayback` asks the Internet Archive's [Save Page Now](https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA) to capture each newly saved file's nyc.gov address. It is off by default. It sends anonymous requests 21 seconds apart, at most 150 a run, and stops at the first 429, to stay inside the anonymous limits in that document (3 a minute, 200 a day). A full week is about 500 files, so one run covers part of a week.

## Spreadsheets, not PDFs

The data comes from the Excel files. The PDFs are kept as published but not read: they print the same tables, and the spreadsheets hold the numbers as cells, so there is no table to recover from a page image.

The exception is the Housing Bureau report, where 53 weeks survive only as a PDF. Plan (not started, November 23, 2026), following the New-York-City-Budget repo's `PDF-RECIPE.md`: the PDFs have a text layer (about 1,100 to 1,250 words a page), so the text layer with its word positions is the main reading, checked cell by cell against a second reading from the page image by computer vision, not by an OCR engine. Every row keeps its page and bounding box. The checks are the ones the spreadsheet parser already runs: every printed total against its rows, "Total Index" against the seven felonies, and "CH %" against the two counts. The 30 weeks that have both a PDF and a spreadsheet test the reader before it is trusted on the rest. Anything that does not check out goes to a person to review; nothing is corrected. From March 2015 on, the PDFs print the same table as the spreadsheets. The three from August 2014 to January 2015 use older layouts (no PCT column, "% CH", and "BOROUGH:" headings), and one from June 2023 is a 34-page report with an arrests page, so those need their own handling.

## Caveats

- NYPD calls these figures preliminary and subject to change. A later report can revise a number this archive already holds, and the archive keeps both.
- Collision counts from the dashboard backfill and from the weekly files are both NYPD's, but they come from different products. Check that they agree for the weeks they share before joining them.
- These are counts NYPD chose to publish, aggregated by precinct and borough. There are no incident records here.
- BetaNYC modified this data, so it is not an official record. The spreadsheets are reshaped from NYPD's report layout into one number per row; NYPD's "Change" and short-range "% +/-" columns, and the Housing Bureau's "CH %", are dropped; the Port Authority Police row that the PAPD file prints twice is kept once; and in the dashboard backfill, weeks NYPD left out are filled in as zero. The original files in the releases are NYPD's, unmodified. For the official figures, go to the NYPD pages linked above.
- Week-ending dates come from the files. If NYPD posts a file with the wrong dates in it, so does this archive.

## Source

All data is published by the [New York City Police Department](https://www.nyc.gov/site/nypd/stats/stats.page) on nyc.gov. The code is MIT licensed; see `LICENSE`.

Built by [BetaNYC](https://beta.nyc).

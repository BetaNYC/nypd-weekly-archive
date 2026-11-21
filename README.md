# NYPD weekly archive

NYPD posts four sets of weekly statistics on nyc.gov as Excel and PDF files. Each week's files replace the previous week's at the same address, and nyc.gov keeps no back issues. None of these four is on NYC Open Data. Unless someone saves a copy every week, the history is gone.

This repository saves a copy every week and turns the spreadsheets into tidy CSV files.

## What it captures

| Series | NYPD page | Files | What is in it |
|---|---|---|---|
| `trafficstat` | [TrafficStat](https://www.nyc.gov/site/nypd/stats/traffic-data/traffic-data-trafficstat.page) | Citywide, 8 patrol boroughs, 78 precincts | Collisions, injuries and deaths by mode; DWI and VTL 511 arrests; parking summonses (double parking, bus stop, bus lane, crosswalk, bike lane); hazardous-violation summonses; moving summonses by vehicle type, including e-bikes, stand-up scooters and mopeds. Week to date, 28 days and year to date, each beside the same period a year earlier. |
| `fatalities` | [Traffic Fatalities](https://www.nyc.gov/site/nypd/stats/traffic-data/traffic-fatalities.page) | One per patrol borough, plus Port Authority Police | Traffic deaths by precinct: 7 days, 28 days and year to date, against the prior year. Each file also repeats the citywide total. |
| `compstat` | [Borough and precinct crime statistics](https://www.nyc.gov/site/nypd/stats/crime-statistics/borough-and-precinct-crime-stats.page) | 78 precincts, 8 patrol boroughs | Crime complaints, shootings, hate crimes and traffic fatalities, with NYPD's 2-year and long-range percent changes and a calendar-year history (1990, 1993, 1998, 2001, last year). |
| `qteam` | [Quality of Life statistics](https://www.nyc.gov/site/nypd/stats/quality-of-life-stats/quality-of-life-stats.page) | Citywide and 78 precincts | 911 and 311 calls for service: noise, vehicle-related calls (illegal parking, blocked driveway, double parking, abandoned vehicles, dangerous driving) and disorderly behavior. This series runs a few weeks behind the others. |

The list of files is read from NYPD's own pages on every run, never built from a pattern: the four pages above, the [precinct directory](https://www.nyc.gov/site/nypd/bureaus/patrol/precincts-landing.page), and each precinct page it links. TrafficStat and Q-Team precinct files are linked only from the precinct pages. `manifest.csv` holds the list from the latest run. If a page cannot be fetched, or a series loses more than 10 percent of its files from one run to the next, the run stops and says so.

The Traffic Fatalities page also links `cityacc-en-us.xlsx`, a monthly collision report last updated in January 2024. It is not weekly and is not captured.

## Where things are

- `raw/<series>/<week ending>/` holds each file exactly as NYPD published it, Excel and PDF. The date is the last day of the report week, read from the spreadsheet. A PDF takes the date of the spreadsheet with the same name. If NYPD changes a file again within the same week, both versions are kept; the later one gets part of its checksum in its name.
- `captures.csv` is the ledger: one row per file saved, with its address, SHA-256, size, the `Last-Modified` time nyc.gov reported, the week-ending date and where that date came from (`xlsx`, `sibling_xlsx`, or `fetch_date` when it could not be read from the file).
- `data/<series>/<week ending>.csv` is the tidy version, one row per number. Each week is its own file so that no single file grows past what GitHub accepts. Join the files for a series to get its history.
- `data/anomalies.csv` lists places where an NYPD file disagrees with itself but could still be read. The first one: in the Staten Island fatalities file for the week ending November 15, 2026, the citywide "CHANGE" cell is typed in as -2 while the two counts beside it are 3 and 1. The counts are kept as published.
- `data/stale_files.csv` lists files whose newest week is at least 7 days behind the newest week in their series. nyc.gov can keep serving an old file with a normal response (the Bronx patrol borough file from before the 2026 north/south split is one), so the week printed inside the file is what counts, not the server's answer.

### Columns in `data/`

| Column | Meaning |
|---|---|
| `series` | `trafficstat`, `fatalities`, `compstat` or `qteam` |
| `source_file` | The file in `raw/` this row came from |
| `geography` | NYPD's label: `Citywide`, a patrol borough, `034`, `34th Precinct` |
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
uv run pytest
```

Useful options: `--series trafficstat fatalities` to limit a run, `--skip-pdf` to save only spreadsheets, `--allow-shrink` after you have checked that NYPD really did drop files.

Requests go one at a time, at least one second apart, with a descriptive user agent. Failed requests are retried with capped backoff, and `Retry-After` is honored. Each request sends `If-Modified-Since`, so unchanged files cost nyc.gov a 304 and nothing more. A run that finds nothing new changes nothing.

Files are identified by their first bytes, never by the `Content-Type` header: a spreadsheet must start like a ZIP file and a PDF with `%PDF-`. Anything else (an error page served with a 200, say) is reported as a failed download and not saved.

The exit code is 1 if any file failed to download or did not match the layout the parser knows. Files that do not parse are still saved; only `data/` is held back. The parser is strict on purpose: an unfamiliar row label, section, sheet or header stops it, so a change at NYPD shows up as a red run instead of numbers quietly going missing.

After fixing the parser for a file it rejected, `uv run nypd-archive redate` re-reads the files that were filed under their download date, moves them to their report week, and rebuilds `data/`.

## Backfills

Two one-off commands recover weeks from before this archive started.

### TrafficStat dashboard

`uv run nypd-archive backfill-dashboard` reads NYPD's [TrafficStat dashboard](https://trafficsafetyforum.nypdonline.org/). Its weekly timeline covers the 53 weeks ending with the current report week, and a "same period last year" view covers the 53 weeks before that, for Citywide and every precinct. It has collisions, injuries and fatalities by mode (30 measures). It has no summonses or arrests: those exist only in the weekly files.

The dashboard publishes no documentation for this. The command sends exactly the requests the dashboard sends from a browser, as observed on 2026-11-21 (listed in `src/nypd_weekly_archive/dashboard.py`), and stops at the first response that does not match. Each run saves every response to `raw/trafficstat_dashboard/<date>/responses.jsonl.gz` and rebuilds `data/trafficstat_dashboard_weekly.csv`. Requests go at least 2 seconds apart; a full pull is about 4,800 requests.

Things to know about that file:

- The dashboard leaves out weeks with a count of zero. Inside the 53 weeks a timeline covers, a missing week is written as 0 with `imputed` set to 1.
- `window` is `WTD` for the current 53 weeks and `SPLYWTD` for the year before. The earlier window lines up by calendar date, not by weekday: when the current week ends Sunday, November 15, 2026, the earlier one ends Saturday, November 15, 2025. The two windows overlap by a day at the seam and do not join into one even series.
- The window moves forward every week and older weeks drop off, so a run saves what is visible on that day and no more.

### Internet Archive copies

`uv run nypd-archive backfill-wayback --series trafficstat --limit 5` lists the Internet Archive's copies of a series through the [Wayback CDX server](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md) and downloads each distinct version in its original form. Every download is checked against the archive's own checksum before it is kept. Spreadsheets are dated from their contents like any other capture. PDFs are filed under the date the archive captured them, marked `wayback_capture_date` in the ledger, because that is all that is known about them. Ledger rows from this command have `source` set to `wayback` and the archive's capture timestamp. Requests go at least 3 seconds apart, with a pause of at least a minute after a 429 or 503. Coverage is uneven: TrafficStat copies start in August 2024 and most precincts have fewer than a dozen.

## Saving new captures to the Internet Archive

`--save-to-wayback` asks the Internet Archive's [Save Page Now](https://docs.google.com/document/d/1Nsv52MvSjbLb2PCpHlat0gkzw0EvtSgpKHu4mk0MnrA) to capture each newly saved file's nyc.gov address. It is off by default. It sends anonymous requests 21 seconds apart, at most 150 a run, and stops at the first 429, to stay inside the anonymous limits in that document (3 a minute, 200 a day). A full week is about 500 files, so one run covers part of a week.

## Spreadsheets, not PDFs

The data comes from the Excel files. The PDFs are kept as published but not read: they print the same tables, and the spreadsheets hold the numbers as cells, so there is no table to recover from a page image.

## Caveats

- NYPD calls these figures preliminary and subject to change. A later report can revise a number this archive already holds, and the archive keeps both.
- Collision counts from the dashboard backfill and from the weekly files are both NYPD's, but they come from different products. Check that they agree for the weeks they share before joining them.
- These are counts NYPD chose to publish, aggregated by precinct and borough. There are no incident records here.
- This is not an official record. BetaNYC changed the shape of the data (from report layout to one number per row) and changed none of the numbers. For the official figures, go to the NYPD pages linked above.
- Week-ending dates come from the files. If NYPD posts a file with the wrong dates in it, so does this archive.

## Source

All data is published by the [New York City Police Department](https://www.nyc.gov/site/nypd/stats/stats.page) on nyc.gov. The code is MIT licensed; see `LICENSE`.

Built by [BetaNYC](https://beta.nyc).

# What's changed

This page lists the changes to BetaNYC's NYPD Weekly Archive that a reader would notice: new pages, new reports and earlier years of data, data fixes, and corrections when we published something wrong. Newest first. Every change to the published data or to where the files are kept gets an entry. The routine weekly saves are not listed.

- **History begins:** 2026-11-21, the day the archive started. Nothing before it is missing.
- **Data refresh:** Every Thursday and Saturday, from NYPD's pages on nyc.gov. NYPD replaces its files each Monday and Tuesday.

To report a bug in this site or ask for a new feature, [open an issue on GitHub](https://github.com/BetaNYC/nypd-weekly-archive/issues) or write to [lab@beta.nyc](mailto:lab@beta.nyc).

## 2026-11-23: TrafficStat for precinct 78, week ending August 30, 2026

- **Tags:** Data
- **What changed:** We checked the Internet Archive again for every TrafficStat file it holds. We already had all but 5. One fills a gap: precinct 78's spreadsheet for the week ending August 30, 2026, which adds 414 numbers. A second is a PDF, which we do not read yet. The other 3 did not match the Internet Archive's own checksum, so we left them out. The originals are in the releases.
- **More detail:** [TrafficStat](docs/trafficstat.html) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/39)

## 2026-11-23: TrafficStat for the week ending November 15, 2026 had a blank precinct column

- **Tags:** Data, Correction
- **What changed:** In the TrafficStat data for the week ending November 15, 2026, the `precinct` column was blank on all 32,292 precinct rows. NYPD posted its files twice that day, and our reader did not expect the file names of the second copy. The site showed those precinct-weeks as not read yet. The column is now filled in. No other number changed.
- **If you used the old numbers:** The counts were right; only the `precinct` column was blank. If you downloaded `data/trafficstat/2026-11-15.csv` before this fix, download it again, or use its `geography` column, which names the precinct.
- **Who it affects:** Anyone who used the precinct column in that week's TrafficStat data.
- **More detail:** [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/36)

## 2026-11-23: TrafficStat from August to December 2024

- **Tags:** Data
- **What changed:** 27 TrafficStat spreadsheets from 2024, copies from the Internet Archive, used a layout we could not read. Now we can. This adds 12 weeks of TrafficStat, from the week ending August 4 to the week ending December 15, 2024. The 27 files were moved to folders named for the week they report, and `moves.csv` lists each move. For 12 of the files, NYPD's PDF of the same report was saved the same day; all 4,980 numbers in them match.
- **More detail:** [TrafficStat](docs/trafficstat.html) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/35)

## 2026-11-23: Older CompStat spreadsheets read, and files refiled by week

- **Tags:** Data
- **What changed:** 1,294 CompStat spreadsheets from the Internet Archive, for precincts, patrol boroughs, citywide and the Department of Correction, used an older layout we could not read. Now we can. This adds 133 weeks to the CompStat data and 25 weeks to the NYCHA data. We also moved 1,319 original files from folders named for the day we downloaded them to folders named for the week they report. The releases keep the old paths, so `moves.csv` lists every move, old path and new path, to help you find a file. We checked 3,988 numbers against NYPD's PDFs for the same weeks, and all of them matched.
- **More detail:** [moves.csv](moves.csv) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/34)

## 2026-11-23: Numbers from the NYCHA Housing Bureau report

- **Tags:** Data
- **What changed:** We now read the numbers in NYPD's weekly NYCHA Housing Bureau report, from all 35 of its sheets: each Police Service Area, Staten Island and Rockaway, the Viper sheets and the citywide housing totals. Many older files say each sheet holds a single cell when it holds a full table, which is why they looked empty before; we now read the whole sheet. The data has 19 weeks so far, in `data/housing/`.
- **More detail:** [NYCHA Housing Bureau report](docs/nycha-housing.html) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/32)

## 2026-11-23: Citywide CompStat for the week ending November 9, 2025

- **Tags:** Data
- **What changed:** Two files we downloaded from the Internet Archive had been turned away because they seemed to fail our checksum check. The Internet Archive sends some files still compressed, and its checksum is for the compressed bytes, so good copies looked damaged. We now check the bytes as they arrive. One of the two was new to us: citywide CompStat for the week ending November 9, 2025, which adds 211 numbers. Its original file is in the releases.
- **More detail:** [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/31) · [Citywide CompStat](docs/citywide-compstat.html)

## 2026-11-23: The About page compares our weekly copies with NYC Open Data

- **Tags:** Site
- **What changed:** A new section on the About page lists, for each of the seven weekly reports, what NYPD's 2026 list of data on NYC Open Data says about it, what we found there, and how far back our copies go. It also shows how to recount a week of CompStat or TrafficStat from NYC Open Data yourself, and what we got when we tried.
- **More detail:** [Our weekly copies and NYC Open Data](docs/about.html#open-data) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/33)

## 2026-11-23: The original files from the Internet Archive are now in the releases

- **Tags:** Data
- **What changed:** The 7,156 original NYPD files we downloaded from the Internet Archive, which were listed in the archive but not yet attached anywhere, are now in the repository's releases as zip files. You can check any number from those years against the file it came from.
- **More detail:** [Releases](https://github.com/BetaNYC/nypd-weekly-archive/releases) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/30)

## 2026-11-23: The About page gave an email address that does not exist

- **Tags:** Site, Correction
- **What changed:** For about an hour on the morning of November 23, 2026, the About page asked readers to send questions about how we use AI to hello@beta.nyc. That address does not exist. The page now gives lab@beta.nyc.
- **If you used the old numbers:** No numbers were affected. If you wrote to hello@beta.nyc, your message did not reach us. Please send it again to lab@beta.nyc.
- **Who it affects:** Anyone who wrote to the address on the About page that morning.
- **More detail:** [How we use AI](docs/about.html#ai) · [The fix](https://github.com/BetaNYC/nypd-weekly-archive/commit/3b0d457)

## 2026-11-23: Internet Archive copies of CompStat from 2017, and citywide reports from 2008

- **Tags:** Data
- **What changed:** We added the precinct CompStat files the Internet Archive saved between May 2017 and November 2026, and its older copies of citywide CompStat, the Department of Correction's CompStat and the NYCHA Housing Bureau report, some from as early as 2008. The archive now holds 567 weeks of citywide CompStat, 175 weeks for the Department of Correction and 100 weeks of the NYCHA report. Some older spreadsheets are saved but their numbers are not read yet.
- **More detail:** [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/16) · [Citywide CompStat](docs/citywide-compstat.html)

## 2026-11-23: Pages for three more weekly reports, and an About page

- **Tags:** Site
- **What changed:** Citywide CompStat, Department of Correction CompStat and the NYCHA Housing Bureau report each have a page in the same shape as the other four reports, with a week-by-week chart and a table. A new About page says who built the archive, how we use AI and how to contribute. When you choose a precinct, it now gets its own section under the chart.
- **More detail:** [About](docs/about.html) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/26)

## 2026-11-23: Citywide and Department of Correction numbers added for November 15

- **Tags:** Data
- **What changed:** The CompStat data for the week ending November 15, 2026 left out the citywide and Department of Correction sheets, though both files were saved. Their 211 numbers each are now in the data. The precinct numbers did not change.
- **More detail:** [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/27)

## 2026-11-23: TrafficStat dashboard crash points saved for the week ending November 15

- **Tags:** Data
- **What changed:** The first weekly save of the TrafficStat dashboard's crash points finished, but its result was lost when another job saved at the same time. The 4,063 crash points for the week ending November 15, 2026 are now in the data, and both weekly jobs now try again instead of losing their work.
- **More detail:** [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/29)

## 2026-11-22: The archive saves three more weekly reports

- **Tags:** Data
- **What changed:** Each week the archive now also saves NYPD's citywide CompStat, the Department of Correction's CompStat and the NYCHA Housing Bureau weekly report, from NYPD's citywide crime statistics page. The two CompStat files are read into the data like the precinct files. The NYCHA report is saved and dated each week, but its numbers are not read yet.
- **More detail:** [Citywide crime statistics on nyc.gov](https://www.nyc.gov/site/nypd/stats/crime-statistics/citywide-crime-stats.page) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/22)

## 2026-11-22: The archive saves the TrafficStat dashboard's crash points every week

- **Tags:** Data
- **What changed:** NYPD's TrafficStat dashboard maps each traffic crash, but only for the current week. Each week the archive now saves those points, one row per crash, with its precinct, location, date and hour.
- **More detail:** [TrafficStat dashboard](https://trafficsafetyforum.nypdonline.org/) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/18)

## 2026-11-22: One page for each report

- **Tags:** Site
- **What changed:** TrafficStat, CompStat, traffic fatalities and quality of life each have their own page, with key numbers, the precinct-by-week chart and the same facts as a table. Old links to a precinct on the home page now open the TrafficStat page.
- **More detail:** [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/15)

## 2026-11-22: A Menu button on phones, and accessibility fixes

- **Tags:** Site
- **What changed:** On a narrow screen, the site's links now sit behind a Menu button. The chart can be scrolled with the keyboard, and the pages have the landmarks and labels that screen readers use to move around.
- **More detail:** [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/13)

## 2026-11-22: CompStat history from 2014, from three earlier archives

- **Tags:** Data
- **What changed:** Others saved CompStat before we did. We added Aaron Schumacher's copies of NYPD's CompStat PDFs from 2014 to 2016, Tim McDermott's weekly CSV files from 2015 to February 2026, and Josh Greenman's numbers from March 2026 on, used with his permission. With our own weekly saves, CompStat now runs from January 2014.
- **More detail:** [Josh Greenman's nypd-compstat-scraper](https://github.com/joshgreenman1973/nypd-compstat-scraper) · [Aaron Schumacher's archive](https://github.com/ajschumacher/nypd) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/12)

## 2026-11-22: The website opens

- **Tags:** Site
- **What changed:** The archive has a website. It explains the project and charts which weeks we saved for each precinct.
- **More detail:** [The website](https://betanyc.github.io/nypd-weekly-archive/) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/6)

## 2026-11-22: TrafficStat back to December 2024, from the Internet Archive

- **Tags:** Data
- **What changed:** We added 1,508 TrafficStat files the Internet Archive saved, each checked against the Internet Archive's own fingerprint of the file. TrafficStat now covers 77 weeks, from December 2024 to the week ending November 15, 2026, though many of the older weeks hold only a few precincts.
- **More detail:** [The files](https://github.com/BetaNYC/nypd-weekly-archive/releases/tag/backfill-wayback-2026-11-21) · [Pull request](https://github.com/BetaNYC/nypd-weekly-archive/pull/5)

## 2026-11-21: Two years of weekly collision counts from the TrafficStat dashboard

- **Tags:** Data
- **What changed:** NYPD's TrafficStat dashboard shows weekly counts of collisions, injuries and deaths for the past year and the year before. We saved them once, for the whole city and every precinct: 106 weeks, from November 16, 2024 to November 15, 2026.
- **More detail:** [The commit](https://github.com/BetaNYC/nypd-weekly-archive/commit/7dc0e29)

## 2026-11-21: The archive starts saving NYPD's weekly files

- **Tags:** Data
- **What changed:** Each week the archive saves NYPD's TrafficStat, traffic fatalities, CompStat and quality of life files from nyc.gov before they are replaced, and turns the spreadsheets into CSV files. The first save held 528 files. The original files are kept in the repository's releases.
- **More detail:** [The first save](https://github.com/BetaNYC/nypd-weekly-archive/commit/7e891dd) · [Originals in releases](https://github.com/BetaNYC/nypd-weekly-archive/commit/5878948)

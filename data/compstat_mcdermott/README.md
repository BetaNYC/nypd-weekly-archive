# CompStat weeks from Tim McDermott

These 531 weeks (2015-01-04 to 2026-02-15) are NYPD's weekly CompStat
numbers as Tim McDermott, a BetaNYC volunteer, turned NYPD's weekly reports
into one CSV per week for many years and published them on data.beta.nyc.
Thank you, Tim.

- One gzip-compressed CSV per year, `<year>.csv.gz`, weeks in order. Read it
  with `pandas.read_csv("2020.csv.gz")` or `gzip -dc 2020.csv.gz`.

- After BetaNYC retired data.beta.nyc in 2024, datHere re-hosted the files
  on https://data.dathere.com/organization/betanyc and kept a copy of the
  old site in Google Drive. BetaNYC's copy of that Drive folder is in the
  shared drive Working Group Documents > data-tool-preservation.
- `source_file` names the copy each week came from: `dathere/...` or
  `databeta-drive/...`, then the dataset folder and Tim's file name.
- Same columns as `data/compstat/`, but these numbers were not read from
  NYPD's files by this repo. Where both exist, `data/compstat/` is ours.
- Rows with `sheet` "CompStat (relabelled by BetaNYC)" were moved to the
  crime name NYPD used that year; see `src/nypd_weekly_archive/mcdermott.py`.
- Tim's files list precincts and patrol boroughs, not citywide totals. His
  weeks before July 2015 have no TOTAL row.
- Made by `uv run nypd-archive import-mcdermott --source dathere=<folder>
  --source databeta-drive=<folder>`.

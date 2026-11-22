# CompStat weeks from Josh Greenman's scraper

These 38 weekly files (2026-03-01 to 2026-11-15) are Josh Greenman's parsed
CompStat numbers, from [joshgreenman1973/nypd-compstat-scraper](https://github.com/joshgreenman1973/nypd-compstat-scraper),
used with his permission. Thank you, Josh.

- Taken from commit [`5bc45bd847e491bd2f88961e163f12e879428356`](https://github.com/joshgreenman1973/nypd-compstat-scraper/tree/5bc45bd847e491bd2f88961e163f12e879428356).
- `source_file` on each row is the file's path in that repo, under `greenman/`:
  `greenman/data/archive/2026-03-01.json` is
  `https://github.com/joshgreenman1973/nypd-compstat-scraper/blob/5bc45bd847e491bd2f88961e163f12e879428356/data/archive/2026-03-01.json`.
- Same columns as `data/compstat/`, but these numbers were not read from
  NYPD's files by this repo. Where both exist, `data/compstat/` is ours.
- Made by `uv run nypd-archive import-greenman --clone <clone of his repo>`.
  See `src/nypd_weekly_archive/greenman.py` for how his fields map to ours.

"""Package original files for GitHub Releases.

Git keeps the tidy CSVs and the ledgers; the original xlsx, pdf and JSON
files go into zips attached to GitHub Releases, so the repository does not
grow by tens of megabytes a week.

Which release (tag):
- nyc.gov captures dated from a spreadsheet: week-<week ending>, e.g.
  week-2026-11-15. A run that catches two weeks (Q-Team lags) fills two.
- nyc.gov captures that could not be dated: fetched-<download date>.
- Internet Archive copies: backfill-wayback-<download date>.
- Copies from another GitHub archive (github_backfill.py):
  backfill-github-<import date>, one zip per source and year.
- Dashboard responses (raw/trafficstat_dashboard/<date>/):
  backfill-dashboard-<date>. Weekly dashboard map answers
  (raw/trafficstat_dashboard_map/<week end>/): week-<week end>. CDX listings (raw/wayback_cdx/<date>/):
  backfill-wayback-<date>.

Which asset: one zip per series per release per packaging run, named
<series>_<label>_<UTC stamp>.zip. Per series, so a reader can fetch one
series without the rest; the stamp keeps a later run in the same week from
replacing an earlier run's zip, because in CI the earlier files are no
longer on disk to re-zip. A week of the largest series (Q-Team, about 50 MB)
is far under GitHub's limit: "Each file included in a release must be under
2 GiB", and "Up to 1000 release assets may be associated with a single
release" (https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases,
read 2026-11-21).

Inside each zip a file sits at its ledger path (raw/<series>/<week>/<name>),
and the ledger row records release_tag and release_asset, so any CSV row
traces to its original: source_file -> captures.csv -> release asset -> path.

package() only writes zips into dist/ and appends to a plan,
dist/upload-plan.tsv (tag<TAB>file). scripts/upload-release-assets.sh does the uploading, in CI
and by hand. Running package() again with nothing new does nothing.
"""

from __future__ import annotations

import csv
import hashlib
import zipfile
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .capture import read_ledger, write_ledger
from .log import log

BACKFILL_FIELDS = ["path", "sha256", "bytes", "release_tag", "release_asset"]


class PackageError(RuntimeError):
    pass


def tag_for(row: dict[str, str]) -> tuple[str, str]:
    """(release tag, label used in the asset name) for a ledger row."""
    day = row["fetched_at"][:10]
    if row.get("source") == "wayback":
        # The day is in the label too: two download days must not share a zip name.
        return f"backfill-wayback-{day}", f"wayback-{day}"
    if (src := row.get("source", "")).startswith("github:"):
        # One zip per year, so no asset nears GitHub's 2 GiB limit.
        owner = src.removeprefix("github:").split("/")[0]
        return f"backfill-github-{day}", f"{owner}-{row['week_end'][:4]}"
    if row["date_source"] in ("xlsx", "sibling_xlsx"):
        return f"week-{row['week_end']}", row["week_end"]
    return f"fetched-{day}", f"fetched-{day}"


def _zip(dest: Path, repo: Path, paths: list[str]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".zip.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(paths):
            z.write(repo / p, arcname=p)
    tmp.replace(dest)


def read_backfills(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_backfills(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BACKFILL_FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["path"]))


def package(repo: Path, dist: Path, now: datetime | None = None) -> list[tuple[str, Path]]:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%MZ")
    plan: list[tuple[str, Path]] = []

    # 1. Captures in captures.csv that have no release yet.
    ledger_path = repo / "captures.csv"
    ledger = read_ledger(ledger_path)
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for r in ledger:
        if not r["release_tag"]:
            tag, label = tag_for(r)
            groups[(tag, r["series"], label)].append(r)
    missing = [r["path"] for rows in groups.values() for r in rows if not (repo / r["path"]).exists()]
    if missing:
        raise PackageError(f"{len(missing)} unreleased captures are not on disk, e.g. {missing[:3]}")
    names = Counter(f"{series}_{label}" for _, series, label in groups)
    if clash := [n for n, c in names.items() if c > 1]:
        # Two groups would write the same zip, and the second would overwrite the first.
        raise PackageError(f"two release groups share an asset name: {clash}")
    for (tag, series, label), rows in sorted(groups.items()):
        asset = f"{series}_{label}_{stamp}.zip"
        _zip(dist / asset, repo, [r["path"] for r in rows])
        for r in rows:
            r["release_tag"], r["release_asset"] = tag, asset
        plan.append((tag, dist / asset))
        log("packaged", tag=tag, asset=asset, files=len(rows))
    if groups:
        write_ledger(ledger_path, ledger)

    # 2. Files that are not captures: dashboard responses and map answers, CDX listings.
    bf_path = repo / "backfills.csv"
    backfills = read_backfills(bf_path)
    known = {b["path"] for b in backfills}
    found: dict[tuple[str, str], list[Path]] = defaultdict(list)
    raw = repo / "raw"
    for f in sorted(raw.glob("trafficstat_dashboard/*/responses.jsonl.gz")):
        found[(f"backfill-dashboard-{f.parent.name}", f"trafficstat-dashboard_{f.parent.name}")].append(f)
    for f in sorted(raw.glob("trafficstat_dashboard_map/????-??-??/responses.jsonl.gz")):
        found[(f"week-{f.parent.name}", f"trafficstat-dashboard-map_{f.parent.name}")].append(f)
    for f in sorted(raw.glob("wayback_cdx/*/*.json")):
        found[(f"backfill-wayback-{f.parent.name}", f"wayback-cdx_{f.parent.name}")].append(f)
    for (tag, name), files in sorted(found.items()):
        new = [f for f in files if f.relative_to(repo).as_posix() not in known]
        if not new:
            continue
        asset = f"{name}_{stamp}.zip"
        rels = [f.relative_to(repo).as_posix() for f in new]
        _zip(dist / asset, repo, rels)
        for f, rel in zip(new, rels, strict=True):
            data = f.read_bytes()
            backfills.append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": str(len(data)),
                              "release_tag": tag, "release_asset": asset})
        plan.append((tag, dist / asset))
        log("packaged", tag=tag, asset=asset, files=len(new))
    if found:
        write_backfills(bf_path, backfills)

    # Appended, not overwritten: a plan not yet uploaded must survive a second
    # packaging run. The upload script moves it aside once every line succeeds.
    dist.mkdir(parents=True, exist_ok=True)
    plan_path = dist / "upload-plan.tsv"
    lines = plan_path.read_text().splitlines() if plan_path.exists() else []
    for tag, path in plan:
        line = f"{tag}\t{path.relative_to(repo).as_posix() if path.is_relative_to(repo) else path}"
        if line not in lines:
            lines.append(line)
    plan_path.write_text("".join(ln + "\n" for ln in lines))
    log("package_done", assets=len(plan))
    return plan

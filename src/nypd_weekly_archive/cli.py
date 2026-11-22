"""Command line.

Weekly: discover, capture, build, or run (all three); redate after a parser fix.
package zips originals not yet released into dist/ for GitHub Releases.
One-off backfills: backfill-dashboard, backfill-wayback, backfill-github.
import-greenman converts Josh Greenman's parsed CompStat weeks into data/compstat_greenman/.
import-mcdermott converts Tim McDermott's weekly CompStat CSVs into data/compstat_mcdermott/.
site rewrites docs/data/ for the GitHub Pages site from captures.csv and data/.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .build import build
from .capture import capture, redate
from .discover import SERIES, DiscoveryError, check_shrink, discover, read_manifest, write_manifest
from .log import log
from .net import Fetcher
from .wayback import save_to_wayback


def _paths(root: Path) -> dict[str, Path]:
    return {
        "manifest": root / "manifest.csv",
        "ledger": root / "captures.csv",
        "raw": root / "raw",
        "data": root / "data",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nypd-archive", description=__doc__)
    ap.add_argument(
        "command",
        choices=["discover", "capture", "build", "run", "redate", "package", "site",
                 "backfill-dashboard", "backfill-wayback", "backfill-github", "import-greenman", "import-mcdermott"],
    )
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="repository root (default: cwd)")
    ap.add_argument("--series", nargs="+", choices=sorted(SERIES), default=sorted(SERIES))
    ap.add_argument("--skip-pdf", action="store_true", help="capture xlsx only")
    ap.add_argument("--allow-shrink", action="store_true", help="accept a manifest >10%% smaller than last run")
    ap.add_argument("--save-to-wayback", action="store_true",
                    help="also submit each newly captured file URL to the Internet Archive (off by default)")
    ap.add_argument("--min-interval", type=float, default=1.0, help="seconds between requests (>= 1)")
    ap.add_argument("--geos", nargs="+", help="backfill-dashboard: only these geographies (e.g. 075 114)")
    ap.add_argument("--limit", type=int, help="backfill-wayback/-github: take at most this many files")
    ap.add_argument("--source", action="append", default=[], metavar="LABEL=FOLDER",
                    help="import-mcdermott: a folder of Tim's vol*.csv files; repeat, best first")
    ap.add_argument("--clone", type=Path, help="backfill-github, import-greenman: local clone of the source repo")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    if not (root / "pyproject.toml").exists():
        print(f"error: {root} does not look like the nypd-weekly-archive repo (no pyproject.toml)", file=sys.stderr)
        return 2
    p = _paths(root)
    status = 0
    log("start", command=args.command, series=args.series, root=str(root))

    if args.command == "package":
        from .release import PackageError, package

        try:
            plan = package(root, root / "dist")
        except PackageError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        log("finish", status=0, assets=len(plan))
        return 0

    if args.command == "site":
        from .site_data import OUT, build_site_data

        out = build_site_data(root)
        log("finish", status=0, file=str(OUT), precincts=len(out["precincts"]), weeks=len(out["weeks"]),
            **out["totals"])
        return 0

    if args.command == "backfill-dashboard":
        from .dashboard import ShapeError, backfill, build_dashboard

        try:
            backfill(Fetcher(min_interval=max(2.0, args.min_interval)), p["raw"], geos=args.geos)
            build_dashboard(p["raw"], p["data"] / "trafficstat_dashboard_weekly.csv")
        except ShapeError as exc:
            log("dashboard_shape_changed", error=str(exc))
            print(f"error: {exc}", file=sys.stderr)
            return 1
        log("finish", status=0)
        return 0

    if args.command == "backfill-wayback":
        from .wayback_backfill import backfill_wayback

        status = backfill_wayback(
            Fetcher(min_interval=max(3.0, args.min_interval), retry_floor=60.0),
            root, args.series, limit=args.limit,
        )
        if build(root, p["ledger"], p["data"]):
            status = 1
        log("finish", status=status)
        return status

    if args.command == "backfill-github":
        from .github_backfill import SourceError, backfill_github

        if not args.clone:
            print("error: backfill-github needs --clone <path>", file=sys.stderr)
            return 2
        try:
            status = backfill_github(root, args.clone.resolve(), limit=args.limit)
        except SourceError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if build(root, p["ledger"], p["data"]):
            status = 1
        log("finish", status=status)
        return status

    if args.command == "import-mcdermott":
        from .mcdermott import import_mcdermott

        pairs = [x.split("=", 1) for x in args.source]
        if not pairs or any(len(x) != 2 or not Path(x[1]).is_dir() for x in pairs):
            print("error: import-mcdermott needs --source LABEL=FOLDER (an existing folder), best first",
                  file=sys.stderr)
            return 2
        stats = import_mcdermott(root, [(label, Path(folder).resolve()) for label, folder in pairs])
        log("finish", status=0, weeks=stats["weeks"])
        return 0

    if args.command == "import-greenman":
        from .greenman import GreenmanError, compare, import_greenman

        if not args.clone:
            print("error: import-greenman needs --clone <path>", file=sys.stderr)
            return 2
        try:
            import_greenman(root, args.clone.resolve())
        except GreenmanError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        diffs = compare(root)
        for d in diffs:
            log("greenman_differs", **d)
        log("finish", status=1 if diffs else 0, differences=len(diffs))
        return 1 if diffs else 0

    fetcher = Fetcher(min_interval=args.min_interval) if args.command in ("discover", "capture", "run") else None

    manifest = read_manifest(p["manifest"])
    if args.command in ("discover", "run"):
        assert fetcher is not None
        try:
            new = discover(fetcher, args.series)
            old = [r for r in manifest if r.series in args.series]
            if not args.allow_shrink:
                check_shrink(old, new)
        except DiscoveryError as exc:
            log("discovery_failed", error=str(exc))
            print(f"error: {exc}", file=sys.stderr)
            return 2
        manifest = [r for r in manifest if r.series not in args.series] + new
        manifest.sort()
        write_manifest(p["manifest"], manifest)

    if args.command in ("capture", "run"):
        assert fetcher is not None
        todo = [m for m in manifest if m.series in args.series and not (args.skip_pdf and m.kind == "pdf")]
        if not todo:
            print("error: manifest is empty; run `discover` first", file=sys.stderr)
            return 2
        res = capture(fetcher, todo, p["raw"], p["ledger"])
        log("capture_done", new=len(res.new), unchanged=res.unchanged,
            failed=len(res.failed), layout_errors=len(res.layout_errors))
        if res.failed or res.layout_errors:
            status = 1
        if args.save_to_wayback and res.new:
            stats = save_to_wayback([r["url"] for r in res.new])
            log("wayback_done", **stats)

    if args.command == "redate":
        still = redate(root, p["ledger"])
        for e in still:
            log("still_unparsed", error=e)
        if still:
            status = 1

    if args.command in ("build", "run", "redate"):
        errors = build(root, p["ledger"], p["data"])
        if errors:
            status = 1

    log("finish", status=status)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

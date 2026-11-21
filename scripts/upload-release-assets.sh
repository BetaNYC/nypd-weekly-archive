#!/usr/bin/env bash
# Upload the zips listed in dist/upload-plan.tsv (written by `nypd-archive
# package`) to GitHub Releases. Used by .github/workflows/capture.yml and by
# hand. Needs gh authenticated (in Actions: GH_TOKEN=${{ secrets.GITHUB_TOKEN }}).
#
# gh flags, from the manual (https://cli.github.com/manual/, read 2026-11-21):
# - `gh release view <tag>`: view a release. gh exits 1 when a command fails
#   for any reason (`gh help exit-codes`), so a missing release is a non-zero exit.
# - `gh release create <tag> --title --notes`: "If a matching git tag does not
#   yet exist, one will automatically get created from the latest state of the
#   default branch."
# - `gh release upload <tag> <files> --clobber`: "Delete and re-upload existing
#   assets of the same name." Asset names carry a per-run stamp, so this only
#   matters when the same run is retried; it makes a retry idempotent.
set -euo pipefail

plan="${1:-dist/upload-plan.tsv}"
if [[ ! -s "$plan" ]]; then
  echo "upload-release-assets: nothing to upload ($plan empty or absent)"
  exit 0
fi
command -v gh >/dev/null || { echo "upload-release-assets: gh not found" >&2; exit 2; }

while IFS=$'\t' read -r tag file; do
  [[ -z "$tag" ]] && continue
  [[ -f "$file" ]] || { echo "upload-release-assets: $file missing" >&2; exit 1; }
  if ! gh release view "$tag" >/dev/null 2>&1; then
    case "$tag" in
      week-*) notes="Original NYPD files captured from nyc.gov for the report week ending ${tag#week-}. Published by the NYPD; unmodified. See captures.csv for each file's source address and checksum." ;;
      backfill-*) notes="One-off backfill of earlier weeks (${tag}). See README.md, Backfills." ;;
      *) notes="Original files (${tag}). See captures.csv." ;;
    esac
    gh release create "$tag" --title "$tag" --notes "$notes" --latest=false
  fi
  gh release upload "$tag" "$file" --clobber
  echo "upload-release-assets: $tag <- $file"
done < "$plan"

mv "$plan" "${plan%.tsv}.uploaded-$(date -u +%Y%m%dT%H%M%SZ).tsv"

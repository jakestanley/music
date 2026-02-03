#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts.core.env import load_env
from scripts.report.html_report import generate_report, write_html_report
from scripts.spotdl.manifest import parse_manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an HTML report of playlist download status.")
    parser.add_argument("--manifest", default="manifest.json", help="Path to manifest JSON (default: manifest.json).")
    parser.add_argument("--db", default="state/music.sqlite3", help="Path to SQLite state DB (default: state/music.sqlite3).")
    parser.add_argument(
        "--playlist-json-name",
        default="playlist.json",
        help="Filename under each root for scraped playlist JSON (default: playlist.json).",
    )
    parser.add_argument(
        "--download-name",
        default="playlist.download.spotdl",
        help="Filename under each root for spotdl song list (default: playlist.download.spotdl).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path. Default writes to reports/report-<timestamp>.html",
    )
    return parser.parse_args()


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))

    args = _parse_args()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    manifest_entries = parse_manifest(str(manifest_path))

    if args.output:
        output_path = Path(args.output)
    else:
        reports_dir = repo_root / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        output_path = reports_dir / f"report-{Path(args.manifest).stem}.html"

    report = generate_report(
        manifest_entries=manifest_entries,
        manifest_path=manifest_path,
        playlist_json_name=args.playlist_json_name,
        download_name=args.download_name,
        db_path=Path(args.db),
    )
    write_html_report(report, output_path)
    print(f"Report written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

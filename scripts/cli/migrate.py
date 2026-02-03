#!/usr/bin/env python3

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.spotdl.manifest import parse_manifest
from scripts.state.db import ensure_db
from scripts.state.sync import sync_entries_from_legacy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-off migration from legacy playlist/log files into SQLite state DB."
    )
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--db", default="state/music.sqlite3")
    parser.add_argument("--drop-existing", action="store_true", help="Delete existing DB file first.")
    parser.add_argument("--download-name", default="playlist.download.spotdl")
    parser.add_argument("--playlist-json-name", default="playlist.json")
    parser.add_argument("--spotdl-errors-name", default="spotdl.errors.json")
    parser.add_argument("--fallback-errors-name", default="ytdlp_fallback.errors.jsonl")
    parser.add_argument("--fallback-success-name", default="ytdlp_fallback.success.jsonl")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    db_path = Path(args.db)
    if args.drop_existing and db_path.exists():
        db_path.unlink()

    entries = parse_manifest(str(manifest_path))
    with ensure_db(db_path) as conn:
        summary = sync_entries_from_legacy(
            conn,
            entries,
            download_name=args.download_name,
            playlist_json_name=args.playlist_json_name,
            spotdl_errors_name=args.spotdl_errors_name,
            fallback_errors_name=args.fallback_errors_name,
            fallback_success_name=args.fallback_success_name,
            action_type="migrate",
        )

    print(
        f"Migration complete: {db_path} "
        f"(playlists={summary['playlists']}, tracks={summary['tracks']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


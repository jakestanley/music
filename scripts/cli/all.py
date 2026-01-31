#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import List, Sequence, Tuple

from scripts.core.env import load_env
from scripts.report.html_report import generate_report, write_html_report
from scripts.spotdl.manifest import parse_manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run scraper, convert, spotdl, ytdlp fallback, demucs, then write an HTML report."
    )
    parser.add_argument("--manifest", default="manifest.json", help="Path to manifest JSON (default: manifest.json).")
    parser.add_argument(
        "--playlist-json-name",
        default="playlist.json",
        help="Filename under each root for scraped playlist JSON (default: playlist.json).",
    )
    parser.add_argument(
        "--demucs-mode",
        choices=["both", "4", "2", "skip"],
        default="both",
        help="Demucs stems to generate: both, 4, 2, or skip (default: both).",
    )
    parser.add_argument(
        "--select",
        action="store_true",
        help="Interactively choose a single playlist from the manifest instead of running all.",
    )
    parser.add_argument("--skip-fallback", action="store_true", help="Skip yt-dlp fallback step.")
    parser.add_argument("--report", default=None, help="Custom path for HTML report output.")
    return parser.parse_args()


def run_step(name: str, cmd: Sequence[str]) -> int:
    printable = " ".join(cmd)
    print(f"\n==> {name}\n{printable}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"{name} failed with exit code {result.returncode}", file=sys.stderr)
    return result.returncode


def _unique_roots(entries: List[Tuple[str, str]]) -> List[str]:
    seen = set()
    roots: List[str] = []
    for _, root in entries:
        if root not in seen:
            roots.append(root)
            seen.add(root)
    return roots


def _select_entry(entries: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    print("Select a playlist to run:")
    for idx, (url, root) in enumerate(entries, start=1):
        print(f"{idx}) {root} - {url}")

    while True:
        choice = input("Enter a number (or press Enter to cancel): ").strip()
        if choice == "":
            print("No selection made; exiting.")
            raise SystemExit(1)
        try:
            index = int(choice)
        except ValueError:
            print("Please enter a valid number.")
            continue
        if 1 <= index <= len(entries):
            return [entries[index - 1]]
        print(f"Enter a number between 1 and {len(entries)}.")


def _write_temp_manifest(entries: List[Tuple[str, str]]) -> Path:
    payload = [{"playlist_url": url, "root": root} for url, root in entries]
    tmp = tempfile.NamedTemporaryFile("w", delete=False, prefix="manifest-select-", suffix=".json")
    with tmp:
        json.dump(payload, tmp, indent=2)
        tmp.flush()
    return Path(tmp.name)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))

    args = _parse_args()
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    entries = parse_manifest(str(manifest_path))
    manifest_for_commands = manifest_path
    temp_manifest: Path | None = None

    if args.select:
        entries = _select_entry(entries)
        temp_manifest = _write_temp_manifest(entries)
        manifest_for_commands = temp_manifest

    roots = _unique_roots(entries)

    exit_codes: List[int] = []
    py = sys.executable

    exit_codes.append(
        run_step(
            "Scrape playlists + convert",
            [py, "-m", "scripts.cli.scraper", "--manifest", str(manifest_for_commands), "--no-report"],
        )
    )
    spotdl_cmd = [py, "-m", "scripts.cli.spotdl", "--manifest", str(manifest_for_commands)]
    if args.skip_fallback:
        spotdl_cmd.append("--skip-fallback")
    exit_codes.append(run_step("spotdl download/sync", spotdl_cmd))

    if args.demucs_mode != "skip":
        demucs_cmd = [py, "-m", "scripts.cli.demucs"] + roots + [args.demucs_mode]
        exit_codes.append(run_step("Demucs stems", demucs_cmd))

    timestamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    if args.report:
        report_path = Path(args.report)
    else:
        report_path = repo_root / "reports" / f"report-{timestamp}.html"

    report_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = generate_report(
            manifest_entries=entries,
            manifest_path=manifest_path,
            playlist_json_name=args.playlist_json_name,
        )
        write_html_report(report, report_path)
        latest_path = report_path.parent / "latest.html"
        shutil.copyfile(report_path, latest_path)
        print(f"\nReport written to {report_path}")
        print(f"Latest report copy: {latest_path}")
    finally:
        if temp_manifest:
            try:
                temp_manifest.unlink()
            except FileNotFoundError:
                pass

    return 0 if all(code == 0 for code in exit_codes) else 1


if __name__ == "__main__":
    raise SystemExit(main())

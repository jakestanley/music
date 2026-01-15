#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ManifestEntry:
    playlist_url: str
    root: Path


def load_manifest(path: Path) -> list[ManifestEntry]:
    if not path.is_file():
        raise FileNotFoundError(f"Manifest file not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data: Any = json.load(handle)

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Manifest must be a JSON array (or a single object).")

    entries: list[ManifestEntry] = []
    for index, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Manifest entry {index} must be an object.")

        url = (
            entry.get("playlist_url")
            or entry.get("playlistUrl")
            or entry.get("playlistURL")
            or entry.get("url")
        )
        root = (
            entry.get("root")
            or entry.get("path")
            or entry.get("target_dir")
            or entry.get("targetDir")
        )

        url = url.strip() if isinstance(url, str) else ""
        root = root.strip() if isinstance(root, str) else ""

        if not url:
            raise ValueError(f"Manifest entry {index} is missing a playlist URL.")
        if not root:
            raise ValueError(f"Manifest entry {index} is missing a root path.")

        entries.append(ManifestEntry(playlist_url=url, root=Path(root)))

    if not entries:
        raise ValueError(f"Manifest file contains no playlist entries: {path}")

    return entries


def run_spotifyscraper_playlist(playlist_url: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "spotifyscraper",
        "playlist",
        "--output",
        str(output_path),
        "--pretty",
        playlist_url,
    ]
    print(f"Running: {shlex.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode == 0:
        return

    message = (result.stdout or "") + "\n" + (result.stderr or "")
    if "Unexpected error: 'list' object has no attribute 'get'" in message:
        print(
            "WARNING: spotifyscraper hit a known transient error; continuing with manifest.",
            file=sys.stderr,
        )
        return

    raise subprocess.CalledProcessError(
        result.returncode, cmd, output=result.stdout, stderr=result.stderr
    )

def format_json_file(path: Path) -> None:
    try:
        with path.open(encoding="utf-8") as handle:
            data: Any = json.load(handle)
    except FileNotFoundError:
        return
    except Exception as exc:
        print(f"WARNING: failed to parse JSON for formatting: {path} ({exc})", file=sys.stderr)
        return

    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except Exception as exc:
        print(f"WARNING: failed to write formatted JSON: {path} ({exc})", file=sys.stderr)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate per-playlist JSON files from a manifest using spotifyscraper."
    )
    parser.add_argument(
        "--manifest",
        default="manifest.json",
        help="Path to the manifest JSON file (default: manifest.json).",
    )
    parser.add_argument(
        "--output-name",
        default="playlist.json",
        help="Output filename to write under each playlist root (default: playlist.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    try:
        entries = load_manifest(Path(args.manifest))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    for entry in entries:
        if not entry.root.is_dir():
            print(f"ERROR: Root directory not found: {entry.root}", file=sys.stderr)
            return 1

        output_path = (entry.root / args.output_name).resolve()
        if output_path.exists():
            age_seconds = time.time() - output_path.stat().st_mtime
            if age_seconds < 60:
                print(
                    f"Skipping (fresh): {output_path} ({int(age_seconds)}s old)",
                    file=sys.stderr,
                )
                format_json_file(output_path)
                continue

        try:
            run_spotifyscraper_playlist(entry.playlist_url, output_path)
        except FileNotFoundError:
            print(
                "ERROR: spotifyscraper not found on PATH. Install it and try again.",
                file=sys.stderr,
            )
            return 127
        except subprocess.CalledProcessError as exc:
            return exc.returncode

        format_json_file(output_path)
        time.sleep(5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

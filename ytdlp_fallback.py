#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ManifestEntry:
    playlist_url: str
    root: Path


def log(message: str) -> None:
    print(message, file=sys.stderr)


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


_SPOTIFY_TRACK_URL_RE = re.compile(r"https?://open\.spotify\.com/track/([A-Za-z0-9]+)")


def extract_spotify_track_ids(value: Any) -> set[str]:
    ids: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for vv in v.values():
                walk(vv)
            return
        if isinstance(v, list):
            for vv in v:
                walk(vv)
            return
        if isinstance(v, str):
            for m in _SPOTIFY_TRACK_URL_RE.finditer(v):
                ids.add(m.group(1))
            return

    walk(value)
    return ids


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)

def load_errors_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def load_spotdl_download_file(path: Path) -> list[dict[str, Any]]:
    data = load_json(path)
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError(f"Expected a JSON list of song objects: {path}")
    return data  # type: ignore[return-value]


def song_query(song: dict[str, Any]) -> str:
    name = song.get("name")
    if not isinstance(name, str) or not name.strip():
        name = "unknown title"

    artists: list[str] = []
    raw_artists = song.get("artists")
    if isinstance(raw_artists, list):
        for a in raw_artists:
            if isinstance(a, str) and a.strip():
                artists.append(a.strip())
    if not artists:
        artist = song.get("artist")
        if isinstance(artist, str) and artist.strip():
            artists = [artist.strip()]

    artist_part = artists[0] if artists else "unknown artist"
    return f"{artist_part} - {name}"


def run_ytdlp_search_download(
    query: str, output_dir: Path, audio_format: str, dry_run: bool
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--default-search",
        "ytsearch1",
        "-x",
        "--audio-format",
        audio_format,
        "-o",
        str(output_dir / "%(title)s [%(id)s].%(ext)s"),
        query,
    ]
    log(f"Running: {shlex.join(cmd)}")
    if dry_run:
        return 0
    result = subprocess.run(cmd)
    return result.returncode


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fallback downloader: uses yt-dlp search to download songs that spotdl failed."
    )
    parser.add_argument(
        "--manifest",
        default="manifest.json",
        help="Path to the manifest JSON file (default: manifest.json).",
    )
    parser.add_argument(
        "--errors-name",
        default="spotdl.errors.json",
        help="Filename under each playlist root written by spotdl --save-errors (default: spotdl.errors.json).",
    )
    parser.add_argument(
        "--download-name",
        default="playlist.download.spotdl",
        help="Song-list .spotdl filename under each root (default: playlist.download.spotdl).",
    )
    parser.add_argument(
        "--audio-format",
        default="mp3",
        help="Audio format for yt-dlp extraction (default: mp3).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands but do not execute yt-dlp.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    try:
        entries = load_manifest(Path(args.manifest))
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 2

    overall_exit = 0
    for entry in entries:
        root = entry.root
        if not root.is_dir():
            log(f"ERROR: Root directory not found: {root}")
            overall_exit = 1
            continue

        errors_path = (root / args.errors_name).resolve()
        download_path = (root / args.download_name).resolve()
        output_dir = (root / "unprocessed").resolve()

        if not errors_path.is_file():
            log(f"Skipping: no errors file: {errors_path}")
            continue
        if not download_path.is_file():
            log(f"ERROR: missing download list: {download_path}")
            overall_exit = 1
            continue

        try:
            errors_payload = load_errors_payload(errors_path)
        except Exception as exc:
            log(f"ERROR: failed to read errors file {errors_path}: {exc}")
            overall_exit = 1
            continue

        failed_ids = extract_spotify_track_ids(errors_payload)
        if not failed_ids:
            log(f"Skipping: no Spotify track ids found in {errors_path}")
            continue

        try:
            songs = load_spotdl_download_file(download_path)
        except Exception as exc:
            log(f"ERROR: failed to read download list {download_path}: {exc}")
            overall_exit = 1
            continue

        by_id: dict[str, dict[str, Any]] = {}
        for song in songs:
            song_id = song.get("song_id")
            if isinstance(song_id, str) and song_id.strip():
                by_id[song_id.strip()] = song

        targets = [by_id[sid] for sid in failed_ids if sid in by_id]
        if not targets:
            log(f"Skipping: none of the failed ids matched songs in {download_path}")
            continue

        log(f"Fallback: {root} ({len(targets)} songs)")
        for song in targets:
            query = song_query(song)
            code = run_ytdlp_search_download(
                query=query,
                output_dir=output_dir,
                audio_format=args.audio_format,
                dry_run=args.dry_run,
            )
            if code != 0:
                overall_exit = 1

    return overall_exit


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

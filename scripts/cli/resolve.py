#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.core.env import load_env
from scripts.report.html_report import (
    classify_tracks,
    list_audio_files,
    load_fallback_errors,
    load_fallback_success,
    load_spotdl_errors,
    load_tracks,
)
from scripts.spotdl.manifest import parse_manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Interactively resolve missing tracks with manual YouTube URLs. "
            "Only prompts for tracks where spotdl and automated fallback both failed."
        )
    )
    parser.add_argument("--manifest")
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--errors-name", default="spotdl.errors.json")
    parser.add_argument("--download-name", default="playlist.download.spotdl")
    parser.add_argument("--audio-format", default="mp3")
    parser.add_argument("--log-name", default="ytdlp_fallback.errors.jsonl")
    parser.add_argument("--success-log-name", default="ytdlp_fallback.success.jsonl")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("root", nargs="?")
    args = parser.parse_args()
    if args.select and not args.manifest:
        raise SystemExit("--select requires --manifest")
    if not args.manifest and not args.root:
        raise SystemExit("Provide either --manifest or a playlist root path.")
    if args.manifest and args.root:
        raise SystemExit("Cannot mix --manifest with a positional root.")
    return args


def _select_entry(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    print("Select a playlist to resolve:")
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


def _load_spotdl_download_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError(f"Expected a JSON list of song objects: {path}")
    return data  # type: ignore[return-value]


def _song_query(song: dict[str, Any]) -> str:
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


def _run_ytdlp_url_download(
    youtube_url: str, output_dir: Path, audio_format: str, dry_run: bool
) -> tuple[int, list[str], str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--print",
        "after_move:filepath",
        "-x",
        "--audio-format",
        audio_format,
        "-o",
        str(output_dir / "%(title)s [%(id)s].%(ext)s"),
        youtube_url,
    ]
    print(f"Running: {shlex.join(cmd)}", file=sys.stderr)
    if dry_run:
        return 0, cmd, "", ""
    result = subprocess.run(cmd, text=True, capture_output=True)
    return result.returncode, cmd, result.stdout or "", result.stderr or ""


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
        handle.write("\n")


def _extract_existing_filepaths(text: str) -> list[str]:
    paths: list[str] = []
    for line in (text or "").splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            p = Path(candidate)
        except Exception:
            continue
        if p.is_file():
            paths.append(str(p))
    return paths


def _load_success_index(path: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    if not path.is_file():
        return index

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        track_id = rec.get("spotify_track_id")
        if not isinstance(track_id, str) or not track_id.strip():
            continue
        filepaths = rec.get("filepaths")
        if not isinstance(filepaths, list):
            continue
        existing = [fp for fp in filepaths if isinstance(fp, str) and Path(fp).is_file()]
        if existing:
            index[track_id.strip()] = existing
    return index


def _load_auto_fallback_fail_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.is_file():
        return ids
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("source") == "manual_url_fallback":
            continue
        sid = rec.get("spotify_track_id")
        if isinstance(sid, str) and sid.strip():
            ids.add(sid.strip())
    return ids


def _manual_prompt(song: dict[str, Any]) -> str | None:
    query = _song_query(song)
    print("")
    print(f"Missing: {query}", file=sys.stderr)
    spotify_track_url = song.get("url")
    if isinstance(spotify_track_url, str) and spotify_track_url.strip():
        print(f"Spotify: {spotify_track_url.strip()}", file=sys.stderr)
    try:
        value = input("YouTube URL (press Enter to skip): ").strip()
    except EOFError:
        return None
    if not value:
        return None
    return value


def _run_manual_resolution(entries: list[tuple[str, str]], args: argparse.Namespace) -> int:
    overall_exit = 0
    for _, root in entries:
        root_path = Path(root)
        if not root_path.is_dir():
            print(f"ERROR: Root directory not found: {root_path}", file=sys.stderr)
            overall_exit = 1
            continue

        download_path = (root_path / args.download_name).resolve()
        output_dir = (root_path / "unprocessed").resolve()
        success_log_path = (root_path / args.success_log_name).resolve()
        fallback_log_path = (root_path / args.log_name).resolve()

        tracks = load_tracks(root_path, args.download_name, "playlist.json")
        if not tracks:
            print(f"Skipping manual resolution (no tracks found): {root_path}", file=sys.stderr)
            continue

        spotdl_fail_ids = load_spotdl_errors(root_path, args.errors_name)
        fallback_fail_ids = load_fallback_errors(root_path, args.log_name)
        auto_fallback_fail_ids = _load_auto_fallback_fail_ids(fallback_log_path)
        fallback_success = load_fallback_success(root_path, args.success_log_name)
        unprocessed_files = list_audio_files(root_path / "unprocessed")
        results = classify_tracks(tracks, unprocessed_files, spotdl_fail_ids, fallback_fail_ids, fallback_success)

        missing_by_id: dict[str, Any] = {}
        for r in results:
            if (
                r.track.track_id
                and r.status != "downloaded"
                and r.track.track_id in spotdl_fail_ids
                and r.track.track_id in auto_fallback_fail_ids
            ):
                missing_by_id[r.track.track_id] = {
                    "song_id": r.track.track_id,
                    "name": r.track.name,
                    "artists": r.track.artists,
                    "url": r.track.url,
                }

        if not missing_by_id:
            print(f"Manual resolution: nothing pending for {root_path}", file=sys.stderr)
            continue

        if download_path.is_file():
            try:
                songs = _load_spotdl_download_file(download_path)
                for song in songs:
                    song_id = song.get("song_id")
                    if isinstance(song_id, str) and song_id in missing_by_id:
                        missing_by_id[song_id] = song
            except Exception as exc:
                print(f"WARNING: failed to read {download_path}: {exc}", file=sys.stderr)

        success_index = _load_success_index(success_log_path)
        pending = [song for sid, song in missing_by_id.items() if sid not in success_index]
        if not pending:
            print(f"Manual resolution: all pending tracks already downloaded for {root_path}", file=sys.stderr)
            continue

        print(f"Manual resolution: {root_path} ({len(pending)} songs)", file=sys.stderr)

        for song in pending:
            spotify_track_id = song.get("song_id")
            if isinstance(spotify_track_id, str) and spotify_track_id in success_index:
                continue

            youtube_url = _manual_prompt(song)
            if youtube_url is None:
                continue

            code, cmd, stdout, stderr = _run_ytdlp_url_download(
                youtube_url=youtube_url,
                output_dir=output_dir,
                audio_format=args.audio_format,
                dry_run=args.dry_run,
            )
            query = _song_query(song)
            if code != 0:
                if not args.dry_run:
                    try:
                        _append_jsonl(
                            fallback_log_path,
                            {
                                "timestamp": _utc_now_iso(),
                                "spotify_track_id": spotify_track_id,
                                "spotify_track_url": song.get("url"),
                                "query": query,
                                "youtube_url": youtube_url,
                                "source": "manual_url_fallback",
                                "yt_dlp_command": cmd,
                                "exit_code": code,
                                "stderr_tail": _tail(stderr, 4000),
                                "stdout_tail": _tail(stdout, 2000),
                            },
                        )
                    except Exception as exc:
                        print(f"ERROR: failed to write fallback log {fallback_log_path}: {exc}", file=sys.stderr)
                print(f"ERROR: manual yt-dlp failed for: {query}", file=sys.stderr)
                overall_exit = 1
                continue

            if not args.dry_run:
                filepaths = _extract_existing_filepaths(stdout)
                if not filepaths:
                    print(
                        f"WARNING: manual resolution succeeded but no output filepath was detected for: {query}",
                        file=sys.stderr,
                    )
                try:
                    _append_jsonl(
                        success_log_path,
                        {
                            "timestamp": _utc_now_iso(),
                            "spotify_track_id": spotify_track_id,
                            "spotify_track_url": song.get("url"),
                            "query": query,
                            "youtube_url": youtube_url,
                            "source": "manual_url_fallback",
                            "yt_dlp_command": cmd,
                            "filepaths": filepaths,
                        },
                    )
                    if isinstance(spotify_track_id, str) and spotify_track_id.strip():
                        success_index[spotify_track_id.strip()] = filepaths
                except Exception as exc:
                    print(f"ERROR: failed to write success log {success_log_path}: {exc}", file=sys.stderr)
                    overall_exit = 1
                    continue

            print(f"Downloaded via manual URL: {query}", file=sys.stderr)

    return overall_exit


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))
    args = _parse_args()

    if args.manifest:
        if not os.path.isfile(args.manifest):
            raise SystemExit(f"Manifest file not found: {args.manifest}")
        entries = parse_manifest(args.manifest)
        if not entries:
            raise SystemExit(f"Manifest file contains no playlist entries: {args.manifest}")
        if args.select:
            entries = _select_entry(entries)
    else:
        entries = [("", args.root)]

    return _run_manual_resolution(entries, args)


if __name__ == "__main__":
    raise SystemExit(main())

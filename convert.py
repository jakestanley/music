#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def find_playlist_json(root: Path, filename: str) -> Path | None:
    direct = root / filename
    if direct.is_file():
        return direct

    candidates = [p for p in root.rglob(filename) if p.is_file()]
    if not candidates:
        return None

    candidates.sort(key=lambda p: (len(p.relative_to(root).parts), p.as_posix()))
    return candidates[0]


_TRACK_URI_RE = re.compile(r"^spotify:track:([A-Za-z0-9]+)$")


def track_id_from_track(track: dict[str, Any]) -> str:
    track_id = track.get("id")
    if isinstance(track_id, str) and track_id.strip():
        return track_id.strip()

    uri = track.get("uri")
    if isinstance(uri, str):
        m = _TRACK_URI_RE.match(uri.strip())
        if m:
            return m.group(1)

    raise ValueError("Track is missing an id/uri usable as a Spotify track id.")


def artist_names_from_track(track: dict[str, Any]) -> list[str]:
    artists = track.get("artists")
    names: list[str] = []

    if isinstance(artists, list):
        for artist in artists:
            if isinstance(artist, dict):
                name = artist.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            elif isinstance(artist, str) and artist.strip():
                names.append(artist.strip())

    if not names:
        return ["Unknown Artist"]

    return names


def load_playlist(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data: Any = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Playlist JSON must be an object: {path}")
    return data


def build_spotdl_sync_payload(
    playlist_url: str, playlist: dict[str, Any]
) -> dict[str, Any]:
    playlist_name = playlist.get("name")
    if not isinstance(playlist_name, str) or not playlist_name.strip():
        playlist_name = "Unknown Playlist"

    tracks = playlist.get("tracks")
    if not isinstance(tracks, list):
        raise ValueError("playlist.json missing 'tracks' array.")

    list_length = playlist.get("track_count")
    if not isinstance(list_length, int) or list_length <= 0:
        list_length = len(tracks)

    songs: list[dict[str, Any]] = []
    for idx, track in enumerate(tracks, start=1):
        if not isinstance(track, dict):
            continue

        track_name = track.get("name")
        if not isinstance(track_name, str) or not track_name.strip():
            track_name = "Unknown Title"

        duration_ms = track.get("duration_ms")
        duration_seconds = 0
        if isinstance(duration_ms, int) and duration_ms >= 0:
            duration_seconds = int(duration_ms / 1000)

        track_id = track_id_from_track(track)
        artists = artist_names_from_track(track)

        songs.append(
            {
                "name": track_name,
                "artists": artists,
                "artist": artists[0],
                "genres": [],
                "disc_number": 1,
                "disc_count": 1,
                "album_name": "",
                "album_artist": "",
                "duration": duration_seconds,
                "year": "",
                "date": "",
                "track_number": 0,
                "tracks_count": 0,
                "song_id": track_id,
                "explicit": False,
                "publisher": "",
                "url": f"https://open.spotify.com/track/{track_id}",
                "isrc": "",
                "cover_url": "",
                "copyright_text": "",
                "download_url": None,
                "lyrics": None,
                "popularity": 0,
                "album_id": "",
                "list_name": playlist_name,
                "list_url": playlist_url,
                "list_position": idx,
                "list_length": list_length,
                "artist_id": "",
                "album_type": "",
            }
        )

    return {"type": "sync", "query": [playlist_url], "songs": songs}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp_path.replace(path)


def write_json_list(path: Path, items: list[dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp_path.replace(path)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert spotifyscraper playlist.json files into spotdl playlist.sync.spotdl files."
    )
    parser.add_argument(
        "--manifest",
        default="manifest.json",
        help="Path to the manifest JSON file (default: manifest.json).",
    )
    parser.add_argument(
        "--playlist-json-name",
        default="playlist.json",
        help="Filename to search for under each root (default: playlist.json).",
    )
    parser.add_argument(
        "--sync-name",
        default="playlist.sync.spotdl",
        help="Output filename to write under each root (default: playlist.sync.spotdl).",
    )
    parser.add_argument(
        "--download-name",
        default="playlist.download.spotdl",
        help="Output filename to write a song-list .spotdl under each root for `spotdl download` (default: playlist.download.spotdl).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    try:
        entries = load_manifest(Path(args.manifest))
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 2

    exit_code = 0
    total = len(entries)
    written = 0
    skipped = 0
    errored = 0

    log(f"Loaded {total} manifest entr{'y' if total == 1 else 'ies'} from {args.manifest}")
    for entry in entries:
        if not entry.root.is_dir():
            log(f"ERROR: Root directory not found: {entry.root}")
            exit_code = 1
            errored += 1
            continue

        playlist_path = find_playlist_json(entry.root, args.playlist_json_name)
        if playlist_path is None:
            log(f"ERROR: No {args.playlist_json_name} found under: {entry.root}")
            exit_code = 1
            errored += 1
            continue

        try:
            playlist = load_playlist(playlist_path)
            payload = build_spotdl_sync_payload(entry.playlist_url, playlist)
        except Exception as exc:
            log(f"ERROR: {entry.root}: {exc}")
            exit_code = 1
            errored += 1
            continue

        output_path = (entry.root / args.sync_name).resolve()
        try:
            track_count = len(payload.get("songs", [])) if isinstance(payload.get("songs"), list) else "?"
            log(f"Converting: {entry.root}")
            log(f"- Input:  {playlist_path}")
            log(f"- Output: {output_path} ({track_count} tracks)")
            write_json(output_path, payload)
            written += 1
        except Exception as exc:
            log(f"ERROR: failed to write {output_path}: {exc}")
            exit_code = 1
            errored += 1
            continue

        download_path = (entry.root / args.download_name).resolve()
        try:
            songs = payload.get("songs")
            if not isinstance(songs, list) or not all(isinstance(s, dict) for s in songs):
                raise ValueError("payload.songs is not a list of song objects")
            log(f"- Output: {download_path} (song list for `spotdl download`)")
            write_json_list(download_path, songs)  # type: ignore[arg-type]
        except Exception as exc:
            log(f"ERROR: failed to write {download_path}: {exc}")
            exit_code = 1
            errored += 1

    skipped_msg = f", skipped {skipped}" if skipped else ""
    log(f"Done: wrote {written}{skipped_msg}, errors {errored}.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

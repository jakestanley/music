#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from scripts.core.env import load_env


@dataclass(frozen=True)
class ManifestEntry:
    playlist_url: str
    root: Path


SPOTIFY_PLAYLIST_RE = re.compile(r"(playlist/|spotify:playlist:)([A-Za-z0-9]+)")


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


def _find_playlist_id(url: str) -> str:
    m = SPOTIFY_PLAYLIST_RE.search(url)
    if not m:
        raise ValueError(f"Could not parse playlist id from URL: {url}")
    return m.group(2)


def _client_credentials() -> Tuple[str, str]:
    client_id = os.environ.get("SPOTIFY_CLIENT_ID") or os.environ.get("SPOTDL_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or os.environ.get("SPOTDL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "Set SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET (or reuse SPOTDL_CLIENT_ID/SPOTDL_CLIENT_SECRET)."
        )
    return client_id, client_secret


def _get_access_token(client_id: str, client_secret: str) -> str:
    resp = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=10,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Spotify auth failed: HTTP {resp.status_code} {resp.text}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise SystemExit("Spotify auth response missing access_token")
    return str(token)


def _fetch_playlist_meta(playlist_id: str, token: str) -> Dict[str, Any]:
    resp = requests.get(
        f"https://api.spotify.com/v1/playlists/{playlist_id}",
        params={"fields": "name,tracks.total,external_urls"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Spotify playlist fetch failed: HTTP {resp.status_code} {resp.text}")
    return resp.json()


def _fetch_tracks(playlist_id: str, token: str) -> List[Dict[str, Any]]:
    tracks: List[Dict[str, Any]] = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    params = {
        "limit": 100,
        "offset": 0,
        "fields": "items(track(name,duration_ms,id,uri,is_local,artists(name),external_urls)),next",
    }
    while True:
        resp = requests.get(url, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        if resp.status_code != 200:
            raise SystemExit(f"Spotify tracks fetch failed: HTTP {resp.status_code} {resp.text}")
        payload = resp.json()
        items = payload.get("items") or []
        for item in items:
            track = item.get("track")
            if not isinstance(track, dict):
                continue
            if track.get("is_local") or track.get("id") is None:
                # Skip local/unavailable tracks since they can't be downloaded
                continue
            tracks.append(track)
        next_url = payload.get("next")
        if not next_url:
            break
        url = next_url
        params = {}  # next_url already contains query params
    return tracks


def _slim_track(track: Dict[str, Any]) -> Dict[str, Any]:
    artists = track.get("artists") or []
    artist_objs = []
    if isinstance(artists, list):
        for a in artists:
            if isinstance(a, dict) and isinstance(a.get("name"), str):
                artist_objs.append({"name": a["name"]})

    external_urls = track.get("external_urls")
    if isinstance(external_urls, dict) and isinstance(external_urls.get("spotify"), str):
        ext_urls = {"spotify": external_urls["spotify"]}
    else:
        ext_urls = {}

    return {
        "name": track.get("name") or "Unknown Title",
        "duration_ms": track.get("duration_ms") or 0,
        "id": track.get("id"),
        "uri": track.get("uri"),
        "artists": artist_objs,
        "external_urls": ext_urls,
    }


def fetch_playlist_json(playlist_url: str, output_path: Path, token: str) -> None:
    playlist_id = _find_playlist_id(playlist_url)
    meta = _fetch_playlist_meta(playlist_id, token)
    tracks_full = _fetch_tracks(playlist_id, token)
    tracks = [_slim_track(t) for t in tracks_full]

    payload = {
        "name": meta.get("name") or "Unknown Playlist",
        "playlist_url": playlist_url,
        "track_count": meta.get("tracks", {}).get("total") or len(tracks),
        "tracks": tracks,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate per-playlist JSON files from a manifest using the Spotify Web API (paginated, no 100-track cap)."
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

    repo_root = Path(__file__).resolve().parent
    load_env(str(repo_root / ".env"))

    try:
        client_id, client_secret = _client_credentials()
        token = _get_access_token(client_id, client_secret)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

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
            fetch_playlist_json(entry.playlist_url, output_path, token)
        except SystemExit as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"ERROR: failed to fetch playlist {entry.playlist_url}: {exc}", file=sys.stderr)
            return 1

        format_json_file(output_path)
        time.sleep(1)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

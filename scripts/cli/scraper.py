#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

from scripts.core.env import load_env
from scripts.spotdl.manifest import parse_manifest

SPOTIFY_PLAYLIST_RE = re.compile(r"(playlist/|spotify:playlist:)([A-Za-z0-9]+)")
_TRACK_URI_RE = re.compile(r"^spotify:track:([A-Za-z0-9]+)$")


def _print_usage(script_name: str) -> None:
    print(
        f"Usage: {script_name} [--manifest <MANIFEST_FILE>] [--playlist-json-name <NAME>] [--sync-name <NAME>] [--download-name <NAME>] [--report <HTML>] [--no-report]",
        file=sys.stderr,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--playlist-json-name", default="playlist.json")
    parser.add_argument("--sync-name", default="playlist.sync.spotdl")
    parser.add_argument("--download-name", default="playlist.download.spotdl")
    parser.add_argument("--report", default=None)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--regenerate", action="store_true")
    args, extra = parser.parse_known_args(argv)
    if extra:
        _print_usage(Path(sys.argv[0]).name)
        raise SystemExit(1)
    return args


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


def _find_playlist_id(url: str) -> str:
    m = SPOTIFY_PLAYLIST_RE.search(url)
    if not m:
        raise ValueError(f"Could not parse playlist id from URL: {url}")
    return m.group(2)


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
                continue
            tracks.append(track)
        next_url = payload.get("next")
        if not next_url:
            break
        url = next_url
        params = {}
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


def _fetch_playlist_json(playlist_url: str, output_path: Path, token: str) -> Dict[str, Any]:
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
    return payload


def _format_json_file(path: Path) -> None:
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


def _track_id_from_track(track: dict[str, Any]) -> str:
    track_id = track.get("id")
    if isinstance(track_id, str) and track_id.strip():
        return track_id.strip()

    uri = track.get("uri")
    if isinstance(uri, str):
        m = _TRACK_URI_RE.match(uri.strip())
        if m:
            return m.group(1)

    raise ValueError("Track is missing an id/uri usable as a Spotify track id.")


def _artist_names_from_track(track: dict[str, Any]) -> list[str]:
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


def _build_spotdl_sync_payload(playlist_url: str, playlist: dict[str, Any]) -> dict[str, Any]:
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

        track_id = _track_id_from_track(track)
        artists = _artist_names_from_track(track)

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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp_path.replace(path)


def _write_json_list(path: Path, items: list[dict[str, Any]]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(items, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    tmp_path.replace(path)


def _resolve_report_path(repo_root: Path, report_arg: str | None) -> Path:
    if report_arg:
        report_path = Path(report_arg)
        return report_path if report_path.is_absolute() else (repo_root / report_path)
    return repo_root / "reports" / "scraper.html"


def _write_scraper_report(
    entries: list[tuple[str, str]],
    playlist_json_name: str,
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    template_path = Path(__file__).resolve().parents[2] / "scripts" / "report" / "templates" / "scraper_report.html"
    html_template = template_path.read_text(encoding="utf-8")
    lines: list[str] = []

    for playlist_url, root in entries:
        root_path = Path(root).expanduser().resolve()
        playlist_path = root_path / playlist_json_name
        lines.append("<section class='playlist'>")
        lines.append("<details>")
        header_title = html.escape(str(root_path))
        header_url = html.escape(playlist_url)
        header_count = "0"
        if not playlist_path.is_file():
            lines.append(f"<summary><span class='root'>{header_title}</span></summary>")
            lines.append(f"<div class='muted'>{header_url}</div>")
            lines.append("<p class='muted'>Missing playlist.json</p>")
            lines.append("</details></section>")
            continue
        try:
            payload = json.loads(playlist_path.read_text(encoding="utf-8"))
        except Exception as exc:
            lines.append(f"<summary><span class='root'>{header_title}</span></summary>")
            lines.append(f"<div class='muted'>{header_url}</div>")
            lines.append(f"<p class='muted'>Failed to read playlist.json: {html.escape(str(exc))}</p>")
            lines.append("</details></section>")
            continue
        tracks = payload.get("tracks") if isinstance(payload, dict) else None
        if not isinstance(tracks, list):
            lines.append(f"<summary><span class='root'>{header_title}</span></summary>")
            lines.append(f"<div class='muted'>{header_url}</div>")
            lines.append("<p class='muted'>No track list found.</p>")
            lines.append("</details></section>")
            continue
        header_count = str(len(tracks))
        lines.append(
            f"<summary><span class='root'>{header_title}</span><span class='count'>{header_count} songs</span></summary>"
        )
        lines.append(f"<div class='muted'>{header_url}</div>")
        lines.append("<table><thead><tr><th>#</th><th>Artist</th><th>Title</th></tr></thead><tbody>")
        row_index = 0
        for track in tracks:
            if not isinstance(track, dict):
                continue
            row_index += 1
            title = track.get("name") or "Unknown Title"
            artists = track.get("artists") or []
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
                names = ["Unknown Artist"]
            artists_text = html.escape(", ".join(names))
            title_text = html.escape(str(title))
            lines.append(f"<tr><td>{row_index}</td><td>{artists_text}</td><td>{title_text}</td></tr>")
        lines.append("</tbody></table>")
        lines.append("</details></section>")

    html_text = html_template.replace("{{CONTENT}}", "\n".join(lines))
    output_path.write_text(html_text, encoding="utf-8")


def main(argv: list[str]) -> int:
    args = _parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"ERROR: Manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        entries = parse_manifest(str(manifest_path))
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not args.regenerate:
        try:
            client_id, client_secret = _client_credentials()
            token = _get_access_token(client_id, client_secret)
        except SystemExit as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2

        for playlist_url, root in entries:
            root_path = Path(root)
            if not root_path.is_dir():
                print(f"ERROR: Root directory not found: {root_path}", file=sys.stderr)
                return 1

            output_path = (root_path / args.playlist_json_name).resolve()
            if output_path.exists():
                age_seconds = time.time() - output_path.stat().st_mtime
                if age_seconds < 60:
                    print(f"Skipping (fresh): {output_path} ({int(age_seconds)}s old)", file=sys.stderr)
                    _format_json_file(output_path)
                    continue

            try:
                playlist_payload = _fetch_playlist_json(playlist_url, output_path, token)
            except SystemExit as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                return 1
            except Exception as exc:
                print(f"ERROR: failed to fetch playlist {playlist_url}: {exc}", file=sys.stderr)
                return 1

            _format_json_file(output_path)

            try:
                payload = _build_spotdl_sync_payload(playlist_url, playlist_payload)
            except Exception as exc:
                print(f"ERROR: {root_path}: {exc}", file=sys.stderr)
                return 1

            sync_path = (root_path / args.sync_name).resolve()
            download_path = (root_path / args.download_name).resolve()
            try:
                track_count = len(payload.get("songs", [])) if isinstance(payload.get("songs"), list) else "?"
                print(f"Converting: {root_path}", file=sys.stderr)
                print(f"- Input:  {output_path}", file=sys.stderr)
                print(f"- Output: {sync_path} ({track_count} tracks)", file=sys.stderr)
                _write_json(sync_path, payload)
            except Exception as exc:
                print(f"ERROR: failed to write {sync_path}: {exc}", file=sys.stderr)
                return 1

            try:
                songs = payload.get("songs")
                if not isinstance(songs, list) or not all(isinstance(s, dict) for s in songs):
                    raise ValueError("payload.songs is not a list of song objects")
                print(f"- Output: {download_path} (song list for `spotdl download`)", file=sys.stderr)
                _write_json_list(download_path, songs)
            except Exception as exc:
                print(f"ERROR: failed to write {download_path}: {exc}", file=sys.stderr)
                return 1

            time.sleep(1)

    if not args.no_report:
        report_path = _resolve_report_path(repo_root, args.report)
        _write_scraper_report(entries, args.playlist_json_name, report_path)
        print(f"Report written to {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

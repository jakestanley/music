import json
import os
import re
from typing import Dict, Iterable, List, Set


def extract_ids(value: object) -> Set[str]:
    ids: Set[str] = set()
    pat = re.compile(r"https?://open\.spotify\.com/track/([A-Za-z0-9]+)")

    def walk(v: object) -> None:
        if isinstance(v, dict):
            for vv in v.values():
                walk(vv)
        elif isinstance(v, list):
            for vv in v:
                walk(vv)
        elif isinstance(v, str):
            for m in pat.finditer(v):
                ids.add(m.group(1))

    walk(value)
    return ids


def song_query(song: Dict[str, object]) -> str:
    name = song.get("name")
    if not isinstance(name, str) or not name.strip():
        name = "unknown title"
    artists: List[str] = []
    raw_artists = song.get("artists")
    if isinstance(raw_artists, list):
        for artist in raw_artists:
            if isinstance(artist, str) and artist.strip():
                artists.append(artist.strip())
    if not artists:
        artist = song.get("artist")
        if isinstance(artist, str) and artist.strip():
            artists = [artist.strip()]
    artist_part = artists[0] if artists else "unknown artist"
    return f"{artist_part} - {name}"


def summarize_errors(errors_path: str, download_path: str) -> List[str]:
    if not os.path.isfile(errors_path):
        return []
    try:
        with open(errors_path, encoding="utf-8") as handle:
            errors_payload = json.load(handle)
    except Exception:
        return []

    failed_ids = extract_ids(errors_payload)
    if not failed_ids:
        return []

    name_by_id: Dict[str, str] = {}
    if download_path and os.path.isfile(download_path):
        try:
            with open(download_path, encoding="utf-8") as handle:
                songs = json.load(handle)
            if isinstance(songs, list):
                for song in songs:
                    if not isinstance(song, dict):
                        continue
                    song_id = song.get("song_id")
                    if isinstance(song_id, str) and song_id.strip():
                        name_by_id[song_id.strip()] = song_query(song)
        except Exception:
            pass

    lines: List[str] = []
    for sid in sorted(failed_ids):
        url = f"https://open.spotify.com/track/{sid}"
        name = name_by_id.get(sid)
        if name:
            lines.append(f"FAILED: {name} ({url})")
        else:
            lines.append(f"FAILED: {url}")
    return lines

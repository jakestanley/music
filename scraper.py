"""
Scrape a Spotify playlist and write/merge track metadata into state.json.

Usage:
    python scraper.py <playlist_url> [root_dir]

If root_dir is omitted, the directory is created under DEFAULT_PLAYLIST_DIRECTORY
(from .env) using the playlist name fetched from Spotify.

Example:
    python scraper.py https://open.spotify.com/playlist/4qqoIxMnK39DjLBvn8gTiP
    python scraper.py https://open.spotify.com/playlist/4qqoIxMnK39DjLBvn8gTiP \
        "/home/jake/Music/Playlists/BATW Rejects"
"""

import base64
import os
import re
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

from scripts.core import state as st

load_dotenv()


def _safe_dirname(name: str) -> str:
    """Convert a playlist name into a filesystem-safe directory name."""
    # Replace chars that are invalid on Windows (and thus the most restrictive common denominator)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    name = name.strip(". ")  # Windows disallows leading/trailing dots and spaces
    return name or "playlist"


def get_spotify_token(client_id: str, client_secret: str) -> str:
    encoded = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    response = requests.post(
        "https://accounts.spotify.com/api/token",
        headers={
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def get_playlist_name(playlist_id: str, token: str) -> str:
    response = requests.get(
        f"https://api.spotify.com/v1/playlists/{playlist_id}",
        params={"fields": "name"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    response.raise_for_status()
    return response.json().get("name", "Unknown Playlist")


def get_all_playlist_tracks(playlist_id: str, token: str) -> list[dict]:
    """Fetch all tracks from a playlist with pagination."""
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

    while url:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        for item in data["items"]:
            track = item["track"]
            if not track or track.get("is_local") or not track.get("id"):
                continue
            tracks.append({
                "id": track["id"],
                "name": track["name"],
                "artist": track["artists"][0]["name"],
                "duration_ms": track["duration_ms"],
            })

        url = data.get("next")

    return tracks


def _playlist_id_from_url(url: str) -> str:
    # https://open.spotify.com/playlist/<id>?...
    path = url.split("?")[0].rstrip("/")
    return path.split("/")[-1]


def main() -> int:
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(f"Usage: {sys.argv[0]} <playlist_url> [root_dir]", file=sys.stderr)
        return 1

    playlist_url = sys.argv[1]
    explicit_root = sys.argv[2] if len(sys.argv) == 3 else None

    client_id = os.environ.get("SPOTDL_CLIENT_ID", "")
    client_secret = os.environ.get("SPOTDL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        print("ERROR: SPOTDL_CLIENT_ID and SPOTDL_CLIENT_SECRET must be set", file=sys.stderr)
        return 1

    playlist_id = _playlist_id_from_url(playlist_url)

    print("Authenticating with Spotify...")
    token = get_spotify_token(client_id, client_secret)

    print("Fetching playlist metadata...")
    name = get_playlist_name(playlist_id, token)

    if explicit_root:
        root = Path(explicit_root).expanduser()
    else:
        default_dir = os.environ.get("DEFAULT_PLAYLIST_DIRECTORY", "")
        if not default_dir:
            print("ERROR: provide a root_dir or set DEFAULT_PLAYLIST_DIRECTORY in .env", file=sys.stderr)
            return 1
        root = Path(default_dir).expanduser() / _safe_dirname(name)
        print(f"Root: {root}")

    root.mkdir(parents=True, exist_ok=True)

    print(f"Fetching tracks for: {name}")
    tracks = get_all_playlist_tracks(playlist_id, token)
    print(f"Found {len(tracks)} tracks")

    playlist_state = st.load(root)
    playlist_state["playlist_url"] = playlist_url
    playlist_state["name"] = name
    st.merge_tracks(playlist_state, tracks)
    st.save(root, playlist_state)

    pending = len(st.tracks_by_status(playlist_state, "pending"))
    total = len(playlist_state["tracks"])
    print(f"State saved: {root / st.STATE_FILE} ({total} tracks, {pending} pending)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

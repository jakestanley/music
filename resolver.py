"""
Resolve pending tracks in state.json to YouTube URLs using ytmusicapi.

Usage:
    python resolver.py <root_dir>

Example:
    python resolver.py "/home/jake/Music/Playlists/BATW Rejects"
"""

import sys
from pathlib import Path

from ytmusicapi import YTMusic

from scripts.core import state as st

# Accept a match if duration is within this fraction of the Spotify duration.
_DURATION_TOLERANCE = 0.15


def _search_youtube(yt: YTMusic, artist: str, name: str, duration_ms: int) -> str | None:
    query = f"{artist} - {name}"
    try:
        results = yt.search(query, filter="songs", limit=5)
    except Exception as exc:
        print(f"  Search error for {query!r}: {exc}", file=sys.stderr)
        return None

    duration_s = duration_ms / 1000
    for result in results:
        video_id = result.get("videoId")
        if not video_id:
            continue
        # Validate duration if available.
        result_duration = result.get("duration_seconds")
        if result_duration and duration_s > 0:
            if abs(result_duration - duration_s) / duration_s > _DURATION_TOLERANCE:
                continue
        return f"https://www.youtube.com/watch?v={video_id}"

    return None


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <root_dir>", file=sys.stderr)
        return 1

    root = Path(sys.argv[1])
    playlist_state = st.load(root)
    if not playlist_state:
        print(f"ERROR: no state.json found in {root}", file=sys.stderr)
        return 1

    pending = st.tracks_by_status(playlist_state, "pending")
    if not pending:
        print("Nothing to resolve.")
        return 0

    print(f"Resolving {len(pending)} tracks via YouTube Music...")
    yt = YTMusic()

    resolved = 0
    failed = 0
    for track_id, track in pending:
        artist = track["artist"]
        name = track["name"]
        duration_ms = track.get("duration_ms", 0)
        print(f"  Searching: {artist} - {name}", end=" ", flush=True)

        url = _search_youtube(yt, artist, name, duration_ms)
        if url:
            st.update_track(playlist_state, track_id, status="resolved", youtube_url=url)
            print(f"-> {url}")
            resolved += 1
        else:
            print("-> no match found")
            failed += 1

        st.save(root, playlist_state)  # save after each track so progress isn't lost

    print(f"\nDone: {resolved} resolved, {failed} unmatched")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

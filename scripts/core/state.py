"""
Atomic read/write for per-playlist state.json.

Schema:
{
  "playlist_url": "https://open.spotify.com/playlist/...",
  "name": "Playlist Name",
  "tracks": {
    "<spotify_track_id>": {
      "name": "Song Name",
      "artist": "Artist Name",
      "duration_ms": 213000,
      "status": "pending" | "resolved" | "downloaded" | "stems_done",
      "youtube_url": "https://www.youtube.com/watch?v=...",  # set at resolved
      "file": "/path/to/file.mp3"                            # set at downloaded
    }
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_FILE = "state.json"


def load(root: Path) -> dict[str, Any]:
    path = root / STATE_FILE
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save(root: Path, state: dict[str, Any]) -> None:
    path = root / STATE_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


_METADATA_FIELDS = {"name", "artist", "album", "track_number", "duration_ms"}


def merge_tracks(state: dict[str, Any], tracks: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge scraped tracks into state. Metadata fields are always updated;
    progress fields (status, youtube_url, file, bpm, camelot_key) are preserved."""
    existing = state.setdefault("tracks", {})
    for track in tracks:
        track_id = track["id"]
        if track_id not in existing:
            existing[track_id] = {"status": "pending"}
        for field in _METADATA_FIELDS:
            if field in track:
                existing[track_id][field] = track[field]
    return state


def tracks_by_status(state: dict[str, Any], status: str) -> list[tuple[str, dict[str, Any]]]:
    """Return (track_id, track) pairs for all tracks at the given status."""
    return [
        (tid, t)
        for tid, t in state.get("tracks", {}).items()
        if t.get("status") == status
    ]


def update_track(state: dict[str, Any], track_id: str, **fields: Any) -> None:
    state["tracks"][track_id].update(fields)

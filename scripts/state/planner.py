from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Sequence


@dataclass(frozen=True)
class ManualResolutionTask:
    playlist_id: int
    root_path: str
    track_id: str
    title: str
    primary_artist: str
    spotify_url: str | None
    attempt_spotdl: int
    attempt_auto: int
    attempt_manual: int


def list_manual_resolution_tasks(
    conn: sqlite3.Connection, roots: Sequence[str] | None = None
) -> list[ManualResolutionTask]:
    where = "WHERE ts.status = 'manual_pending'"
    params: list[object] = []
    if roots:
        marks = ",".join("?" for _ in roots)
        where += f" AND p.root_path IN ({marks})"
        params.extend(roots)

    query = f"""
    SELECT
      p.id AS playlist_id,
      p.root_path AS root_path,
      t.id AS track_id,
      t.title AS title,
      COALESCE(t.primary_artist, 'Unknown Artist') AS primary_artist,
      t.spotify_url AS spotify_url,
      ts.attempt_spotdl AS attempt_spotdl,
      ts.attempt_auto AS attempt_auto,
      ts.attempt_manual AS attempt_manual
    FROM track_state ts
    JOIN playlists p ON p.id = ts.playlist_id
    JOIN tracks t ON t.id = ts.track_id
    {where}
    ORDER BY p.root_path, lower(COALESCE(t.primary_artist,'')), lower(t.title)
    """
    rows = conn.execute(query, params).fetchall()
    return [
        ManualResolutionTask(
            playlist_id=int(r["playlist_id"]),
            root_path=str(r["root_path"]),
            track_id=str(r["track_id"]),
            title=str(r["title"]),
            primary_artist=str(r["primary_artist"]),
            spotify_url=str(r["spotify_url"]) if r["spotify_url"] is not None else None,
            attempt_spotdl=int(r["attempt_spotdl"]),
            attempt_auto=int(r["attempt_auto"]),
            attempt_manual=int(r["attempt_manual"]),
        )
        for r in rows
    ]


# State Model

Source of truth is SQLite (`state/music.sqlite3`).

## Entities

- `playlists`: one row per playlist root.
- `tracks`: one row per track ID (Spotify or synthetic fallback ID).
- `playlist_tracks`: playlist membership + order.
- `track_state`: current lifecycle state per playlist+track.
- `actions`: append-only audit history of migration/runs.

## Status values

- `pending`
- `spotdl_ok`
- `spotdl_failed`
- `auto_ok`
- `auto_failed` (reserved; `manual_pending` is preferred operational state)
- `manual_pending`
- `manual_ok`
- `demucs_done`
- `ignored`

## Planner rules (current intent)

1. `auto_failed`/`manual_pending` tracks are not auto-retried.
2. Manual resolver prompts those tracks each run until success.
3. Manual skip keeps the track in `manual_pending`.
4. Any success stores a `resolved_path` and moves to `*_ok`.

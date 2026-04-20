# Agent Onboarding

This file is the authoritative context for AI agents working in this repository. Read it before making any changes.

## What this repo does

Downloads Spotify playlists as local MP3 files and generates Demucs stem separations. A five-step pipeline runs per playlist:

1. **scrape** — fetch playlist metadata from Spotify API → `state.json`
2. **resolve** — match each track to a YouTube URL via ytmusicapi → `state.json`
3. **download** — download each resolved URL as MP3 via yt-dlp → `unprocessed/`
4. **demucs** — split each MP3 into stems → `all/` (4-stem) and/or `vocals/` (2-stem)
5. **analyse** — measure BPM and Camelot key via aubio/keyfinder-cli → `state.json`

`batch.py` orchestrates all five steps for every playlist listed in `manifest.json`.

## Key scripts

| Script | Purpose |
|--------|---------|
| `scraper.py <url> <root>` | Scrape Spotify playlist → write/merge `state.json` |
| `resolver.py [--manual] <root>` | Resolve pending tracks to YouTube URLs |
| `downloader.py <root>` | Download resolved tracks as MP3 |
| `demucs.py [--api] <root> [4\|2\|both]` | Generate stems |
| `analyser.py <root>` | Analyse BPM and Camelot key for downloaded tracks |
| `batch.py [--demucs-api] [--demucs-mode ...] [--until <step>]` | Run the full pipeline |

## State model

Each playlist directory contains one `state.json`. It is the **only shared context** between steps — scripts must not assume any other shared state.

```json
{
  "playlist_url": "https://open.spotify.com/playlist/...",
  "name": "Playlist Name",
  "tracks": {
    "<spotify_track_id>": {
      "name": "Song Name",
      "artist": "Artist Name",
      "duration_ms": 213000,
      "status": "pending | resolved | downloaded | stems_done",
      "youtube_url": "https://www.youtube.com/watch?v=...",
      "file": "/absolute/path/to/unprocessed/Artist - Song.mp3",
      "bpm": "128.0",
      "camelot_key": "8A"
    }
  }
}
```

- `status` is a linear progression: `pending` → `resolved` → `downloaded` → `stems_done`
- `bpm` and `camelot_key` are set by `analyser.py` independently of `status`
- All writes go through `scripts/core/state.py` using atomic tmp-then-replace to avoid corruption

## `scripts/core/state.py` API

```python
st.load(root)                          # returns {} if no state.json
st.save(root, state)                   # atomic write
st.merge_tracks(state, tracks)         # add new tracks as 'pending', never overwrite
st.tracks_by_status(state, "pending")  # returns [(track_id, track), ...]
st.update_track(state, track_id, **fields)
```

## `batch.py` design

- **Venv bootstrap**: if not running inside `.venv`, re-execs under `.venv/bin/python` (creating and pip-installing if needed). Uses `is_relative_to()` — not `.resolve()` — to detect the venv, because `.venv/bin/python` is a symlink to the system Python and `.resolve()` would make them look identical.
- **Step ordering**: scrape → resolve → download → demucs → analyse. `--until <step>` stops after the named step.
- **Failure handling**: scrape failure skips the rest of that playlist (`continue`). All other step failures are recorded but processing continues. Failures are printed as a summary at the end.
- **UpSnap wake/sleep**: `batch.py` owns the demucs server wake (via `--demucs-api`) and issues a single sleep call after all playlists complete. Never put sleep logic in `demucs.py` subprocess calls — module-level globals reset per subprocess and cause race conditions.
- **Buffered output**: all subprocesses run with `PYTHONUNBUFFERED=1` so log lines stream live.
- **Env loading**: `load_env(str(_ROOT / ".env"))` is called explicitly in `batch.py` after the venv bootstrap block, because `load_dotenv()` without a path can fail in cron/subprocess contexts.

## Folder layout per playlist

```
<root>/
  state.json
  unprocessed/     # MP3s named "Artist - Title.mp3"
  all/             # 4-stem output
    Track Name/
      vocals.wav  drums.wav  bass.wav  other.wav
  vocals/          # 2-stem output
    Track Name/
      vocals.wav
```

Stem directory names are derived from the MP3 filename via `canonical_output_name()` in `scripts/cli/demucs.py`.

## manifest.json

```json
[
  {
    "playlist_url": "https://open.spotify.com/playlist/abc123",
    "root": "/home/jake/Music/Playlists/MyPlaylist"
  }
]
```

`batch.py` skips entries missing either `playlist_url`/`url` or `root`.

## Environment variables (`.env`)

| Variable | Used by | Notes |
|----------|---------|-------|
| `SPOTIFY_CLIENT_ID` | scraper | Spotify app credentials |
| `SPOTIFY_CLIENT_SECRET` | scraper | |
| `YOUTUBE_COOKIE_FILE` | downloader | Netscape-format cookies |
| `DEFAULT_PLAYLIST_DIRECTORY` | scraper | Fallback root when none given |
| `DEMUCS_API_URL` | demucs | |
| `DEMUCS_API_MODEL` | demucs | Default: `htdemucs` |
| `DEMUCS_API_CA_CERT` | demucs | Self-signed TLS CA |
| `DEMUCS_API_BATCH_SIZE` | demucs | Default: `10` |
| `DEMUCS_API_POLL_SECS` | demucs | Default: `5` |
| `DEMUCS_API_TIMEOUT_SECS` | demucs | Default: `3600` |
| `UPSNAP_URL` | batch, demucs | UpSnap API base URL |
| `UPSNAP_BEARER_TOKEN` | batch, demucs | |
| `UPSNAP_DEVICE_NAME` | batch, demucs | Device to wake/sleep |
| `UPSNAP_CA_CERT` | batch, demucs | Self-signed TLS CA |
| `UPSNAP_INSECURE_TLS` | batch, demucs | Set `true` to skip verification |
| `UPSNAP_STATUS_POLL_SECS` | batch, demucs | Default: `5` |
| `UPSNAP_STATUS_TIMEOUT_SECS` | batch, demucs | Default: `180` |

## Design principles

- **Idempotent steps**: every script checks what work remains before doing anything. Re-running is safe at any point.
- **Save after each track**: `resolver.py`, `downloader.py`, and `analyser.py` all call `st.save()` after each individual track so partial progress survives interruption.
- **No spotdl dependency**: Spotify→YouTube resolution is handled by ytmusicapi (with ±15% duration tolerance). Download is handled by venv yt-dlp with `--js-runtimes node` and `--cookies`.
- **Path safety**: `_safe_dirname()` in `scraper.py` strips characters illegal on Windows (`<>:"/\|?*`) so playlist directories are cross-platform.
- **Absolute file paths in state**: the `file` field always stores an absolute path so scripts work regardless of working directory.

## Known gotchas

- **Venv symlink detection**: `.venv/bin/python` resolves to the same inode as the system Python on some distros. Always use `is_relative_to(_VENV)` in the bootstrap check, never `.resolve()` comparison.
- **UpSnap wake/sleep ownership**: sleep must only be called once, by `batch.py`, after all playlists. If `demucs.py` subprocesses call sleep, the next playlist's wake races against the shutdown.
- **`load_dotenv()` in cron**: call `load_env(path)` with an explicit path — the no-argument form looks up frames to find the caller's directory and breaks in subprocess/cron contexts.
- **`PYTHONUNBUFFERED=1`**: without this, subprocess output is buffered and doesn't appear until the process exits.

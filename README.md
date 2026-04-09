# Music Tooling

Helper scripts for downloading Spotify playlists as MP3s and generating Demucs stems.

## Quick start

```bash
# 0) Set up venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 1) Copy and fill in credentials
cp .env.example .env

# 2) Scrape playlist metadata → state.json
python scraper.py <playlist_url> <root_dir>

# 3) Resolve Spotify tracks to YouTube URLs
python resolver.py <root_dir>
# Add --manual to be prompted for any tracks ytmusicapi couldn't match

# 4) Download to unprocessed/*.mp3
python downloader.py <root_dir>

# 5) Split stems with Demucs (optional)
python demucs.py <root_dir> both
```

## Prerequisites

- Python 3.10+
- `node` (v20+) on your `PATH` — yt-dlp uses it to solve YouTube's JS signature challenge
- `demucs` for stem separation
- Spotify API credentials: `SPOTDL_CLIENT_ID` and `SPOTDL_CLIENT_SECRET` (create an app at https://developer.spotify.com/dashboard)
- YouTube cookies: `SPOTDL_COOKIE_FILE` must point to a Netscape-format cookies file exported from a browser logged into YouTube (e.g. via the **Get cookies.txt LOCALLY** extension)

## State

Each playlist root contains a `state.json` that is the single shared context between pipeline steps:

```json
{
  "playlist_url": "https://open.spotify.com/playlist/...",
  "name": "Playlist Name",
  "tracks": {
    "<spotify_track_id>": {
      "name": "Song Name",
      "artist": "Artist Name",
      "duration_ms": 213000,
      "status": "downloaded",
      "youtube_url": "https://www.youtube.com/watch?v=...",
      "file": "/path/to/unprocessed/Artist - Song.mp3"
    }
  }
}
```

Status progression: `pending` → `resolved` → `downloaded` → `stems_done`

Each step is idempotent — re-running skips tracks already at or past its status.

## Folder structure

```
<root>/
  state.json          # shared pipeline state
  unprocessed/        # downloaded MP3s
  all/                # 4-stem Demucs output
    Track Name/
      vocals.wav
      drums.wav
      bass.wav
      other.wav
  vocals/             # 2-stem Demucs output
    Track Name/
      vocals.wav
```

## Scheduling with cron

`batch.py` bootstraps the venv automatically, so the system `python3` is enough:

```
0 3 * * * python3 /home/jake/Music/batch.py >> /home/jake/Music/logs/batch.log 2>&1
```

Add with `crontab -e`. Adjust the schedule (`0 3 * * *` = 3 AM daily) to taste.
`--demucs-mode skip` is useful if you want to separate the download and stem steps.

## manifest.json

Lists playlists to process. Used by `batch.py` and for reference:

```json
[
  {
    "playlist_url": "https://open.spotify.com/playlist/abc123",
    "root": "/home/jake/Music/Playlists/MyPlaylist"
  }
]
```

## `demucs.py`

Separates MP3s in `<root>/unprocessed/` into stems. Skips tracks that already have outputs.

```bash
python demucs.py [--api] <root_dir> [4|2|both]
```

| Option | Description |
|--------|-------------|
| `--api` | submit to Demucs HTTP API instead of running locally |
| `--api-max-duration-seconds <N>` | skip files longer than N seconds before submission (default 1200) |
| `[4\|2\|both]` | stem mode: 4-stem, 2-stem vocal isolate, or both (default: both) |

### Demucs API

Configure in `.env`:

| Variable | Default |
|----------|---------|
| `DEMUCS_API_URL` | `https://demucs.stanley.arpa` |
| `DEMUCS_API_MODEL` | `htdemucs` |
| `DEMUCS_API_CA_CERT` | _(path to CA cert for self-signed TLS)_ |
| `DEMUCS_API_BATCH_SIZE` | `10` |
| `DEMUCS_API_POLL_SECS` | `5` |
| `DEMUCS_API_TIMEOUT_SECS` | `3600` |

Failed jobs are logged to `<root>/demucs.errors.jsonl` and processing continues.

When multiple roots are provided, the script deduplicates stems across roots by hash to save time and disk.

### Demucs GPU setup (Windows, CUDA)

```
winget install --id Python.Python.3.11 -e
pipx install demucs --python "C:\...\Python311\python.exe"
pipx runpip demucs install "torch==2.1.*" "torchvision==0.16.*" "torchaudio==2.1.*" --index-url https://download.pytorch.org/whl/cu121
pipx runpip demucs install "numpy<2"   # if NumPy 2.x errors
pipx runpip demucs install soundfile   # if torchaudio can't write WAVs
```

## Syncing

Sync only `unprocessed/` from server to Mac (for Mixed In Key):

```bash
rsync -av --delete \
  --prune-empty-dirs \
  --exclude='._*' \
  --include='*/' \
  --include='*/unprocessed/***' \
  --exclude='*' \
  jake@adler:~/Music/Playlists/ ~/Music/Playlists/
```

## My setup

Mac handles downloads and Mixed In Key. Server runs Demucs. Both share the same `Music/Playlists/` layout so rsync keeps them in sync. Stems are accessed on the Mac via NFS mount directly into Ableton.

## Getting started in an existing Music directory

```bash
git init -b temp
git remote add origin git@github.com:jakestanley/music.git
git fetch -a
git checkout -t origin
git checkout main

# Ignore any symlinks already in this directory
find . -type l | sed -e s'/^\.\///g' >> .gitignore
```

# Music Tooling

This workspace holds helper scripts for downloading music assets and generating stems.

## Sync only unprocessed for MIK

```
rsync -av --delete \
  --prune-empty-dirs \
  --exclude='._*' \
  --include='*/' \
  --include='*/unprocessed/***' \
  --exclude='*' \
  jake@adler:~/Music/Playlists/ ~/Music/Playlists/
```

## Quick start (end-to-end)

```bash
# 1) Fill in Spotify creds.
cp .env.example .env

# 2) Create manifest.json with playlist URLs + roots (each root must exist).
mkdir -p ~/Music/Playlists/MyPlaylist

# 3) Scrape + convert playlists into spotdl inputs.
python3 scraper.py --manifest manifest.json
python3 convert.py --manifest manifest.json

# 4) Download audio + log spotdl errors.
./spotdl.sh --manifest manifest.json

# 5) Fallback any failures with yt-dlp search.
python3 ytdlp_fallback.py --manifest manifest.json

# 6) Split stems with Demucs (optional).
./demucs.sh --manifest manifest.json

### One-liner (everything + report)

Run the whole pipeline (scrape → convert → spotdl → yt-dlp fallback → demucs) and write an HTML report:

```bash
./all.sh --manifest manifest.json
```

- Add `--select` to pick one playlist from the manifest interactively (numbered list).
```

## Prerequisites

These scripts assume the following tools are installed and available on your `PATH`:

- `spotdl` for Spotify downloads
  - Currently using this fork that has exponential back off on the Spotify API https://github.com/spotDL/spotify-downloader/pull/2583
- `demucs` for stem separation
- `yt-dlp` and `id3v2` for YouTube downloads + tagging
- `curl` for UpSnap API calls (Windows offload only)
- Spotify API creds: set `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` (or reuse `SPOTDL_CLIENT_ID` / `SPOTDL_CLIENT_SECRET`). The scraper now uses the Spotify Web API with pagination, so playlists over 100 tracks are fully ingested.

## Getting started

You're probably going to want to check this project out into an existing Music directory but skip this if you aren't. `cd` into that directory and run the following:

```
# initialise the repository with a temporary branch we'll remove later
git init -b temp

git remote add origin git@github.com:jakestanley/music.git
git fetch -a
git checkout -t origin
git checkout main

# ignore any symlinks in this directory, we don't want to check those in
find . -type l | sed -e s'/^\.\///g' >> .gitignore
```

## Synchronising unprocessed files from server

```bash
rsync -av --delete \
  --prune-empty-dirs \
  --exclude='._*' \
  --include='*/' \
  --include='*/unprocessed/***' \
  --exclude='*' \
  jake@adler:~/Music/Playlists/  ~/Music/Playlists/
```

## Expected folder structure

The scripts assume a root directory per project or playlist. Each root contains an `unprocessed/` folder with source MP3s, plus output folders for Demucs stems:

```
<PLAYLIST_TARGET_DIR>/
  unprocessed/
    Track One.mp3
    Track Two.mp3
  all/
    Track One/
      vocals.wav
      drums.wav
      bass.wav
      other.wav
    Track Two/
      vocals.wav
      drums.wav
      bass.wav
      other.wav
  vocals/
    Track One/
      vocals.wav
    Track Two/
      vocals.wav
```

You can create multiple roots under the main Music directory, for example:

```
~/Music/
  music-tooling/  # this repo
  BATW_Candidates/
  CardioMix/
```

## My use case

I keep two copies of my `Music/` directory: one on my Mac (limited storage) and one on a server (large storage). The Mac has Apple Music, Ableton, and other non-playlist folders, while the server only keeps playlist roots. I use a `Music/Playlists/` folder on both machines so rsync only mirrors playlists, not unrelated music folders.

Workflow:
```
Mac:
  Music/
    Playlists/
      <PLAYLIST_TARGET_DIR>/
        unprocessed/   # downloaded here only

Server:
  Music/
    Playlists/
      <PLAYLIST_TARGET_DIR>/
        unprocessed/
        all/
        vocals/
```

Typical steps:
1) Download playlists on the Mac into `Music/Playlists/<PLAYLIST_TARGET_DIR>/unprocessed/` for Mixed In Key.
2) Sync to the server with `rsync -avF` (same folder layout on both sides).
3) Run `demucs.sh` on the server to generate `all/` and `vocals/` (it can run anywhere; I just prefer the server).
4) When needed, access stems on the Mac via NFS directly from the server into Ableton.

NFS note: I keep a mount on the Mac that points to the server's `Music/Playlists/` so stems are immediately available without copying.

## Usage (Rate-Limit Safe)

1) Populate `manifest.json` with playlist roots and URLs (each `root` must already exist).
2) Scrape playlist metadata into each root: `python3 scraper.py --manifest manifest.json` (writes `<root>/playlist.json`).
3) Convert scraped JSON into spotdl inputs: `python3 convert.py --manifest manifest.json` (writes `<root>/playlist.sync.spotdl` and `<root>/playlist.download.spotdl`).
4) Download audio: `./spotdl.sh --manifest manifest.json` (prefers `<root>/playlist.download.spotdl` to avoid Spotify playlist sync calls; falls back to sync when missing).
5) Fallback any spotdl failures with yt-dlp search: `python3 ytdlp_fallback.py --manifest manifest.json` (uses `<root>/spotdl.errors.json` written by step 4; logs any remaining failures to `<root>/ytdlp_fallback.errors.jsonl`; use `--truncate-log` to reset the log each run).

## `spotdl.sh`

Purpose: keep a Spotify playlist mirrored into an `unprocessed/` folder ready for Demucs, without re-querying/downloading everything every run.

Usage:
```
./spotdl.sh <PLAYLIST_URL> <PLAYLIST_TARGET_DIR>
```

| Option | Description |
|--------|-------------|
| `<PLAYLIST_URL>` | Spotify playlist URL to fetch with `spotdl` |
| `<PLAYLIST_TARGET_DIR>` | base path where the script stores `unprocessed/` |

How it avoids rate limits:
- On first run, it calls `spotdl sync <PLAYLIST_URL> --save-file <PLAYLIST_TARGET_DIR>/playlist.sync.spotdl --use-cache-file`.
- On later runs, it calls `spotdl sync <PLAYLIST_TARGET_DIR>/playlist.sync.spotdl --use-cache-file` so only new additions trigger Spotify lookups + downloads (existing files are skipped by the downloader).

Options:

| Option | Description |
|--------|-------------|
| `--sync-file <FILE>` | name/path under `<PLAYLIST_TARGET_DIR>/` for the sync state (default `playlist.sync.spotdl`) |
| `--delay <SECONDS>` | sleep between playlist syncs (default `2`; useful in `--manifest` mode to avoid 429s) |
| `--threads <N>` | number of threads (defaults to `1`; increase if you want more concurrency) |
| `--max-retries <N>` | increase retries/backoff for transient Spotify 429s (default `5`) |

Setup:
- Copy `.env.example` to `.env` and fill in `SPOTDL_CLIENT_ID` and `SPOTDL_CLIENT_SECRET`.

Rate limit tips:
- Prefer re-running `./spotdl.sh ...` over re-downloading from scratch; the sync file is what keeps API usage low.
- If you still hit 429s, reduce concurrency with `--threads 1`, add `--delay 2` (or higher), and/or bump `--max-retries 20` so the built-in backoff has time to honor `Retry-After` and drain the limit.
- The script aborts if spotdl requests a `Retry-After` greater than 60 seconds.

Manifest mode:
You can pass `--manifest <MANIFEST_FILE>` instead of positional arguments to download multiple playlists in one run. The manifest must be a JSON array (or single object) where each entry is an object that specifies a playlist URL (`playlist_url`, `playlistUrl`, `playlistURL`, or `url`) and a root path (`root`, `path`, `target_dir`, or `targetDir`). Example:

```json
[
  {
    "playlist_url": "https://open.spotify.com/playlist/abc123",
    "root": "/home/jake/Music/Playlists/CardioMix"
  },
  {
    "url": "https://open.spotify.com/playlist/xyz789",
    "path": "/home/jake/Music/Playlists/StudyVibes"
  }
]
```

Each root directory must exist before running `spotdl.sh` (the script keeps the `unprocessed/` subfolder it creates), and the playlist downloads run sequentially in manifest order.

## `scraper.py`

Purpose: generate a `playlist.json` for each playlist in `manifest.json` using `spotifyscraper`. The JSON is written into the playlist root directory (i.e., alongside `unprocessed/`).

Usage:
```
python3 scraper.py --manifest manifest.json
```

Options:
| Option | Description |
|--------|-------------|
| `--manifest <FILE>` | manifest JSON file path (default `manifest.json`) |
| `--output-name <NAME>` | output filename under each playlist root (default `playlist.json`) |

## `convert.py`

Purpose: convert each scraped `playlist.json` (from `scraper.py`) into a `playlist.sync.spotdl` file that `spotdl sync` can consume later. This leaves `playlist.json` untouched and writes the sync file into the playlist root directory.

Usage:
```
python3 convert.py --manifest manifest.json
```

Outputs:
- `<PLAYLIST_ROOT>/playlist.sync.spotdl`: sync state for `spotdl sync` (will refetch playlist from Spotify).
- `<PLAYLIST_ROOT>/playlist.download.spotdl`: a song-list file for `spotdl download` (avoids Spotify playlist sync calls).

Options:
| Option | Description |
|--------|-------------|
| `--manifest <FILE>` | manifest JSON file path (default `manifest.json`) |
| `--playlist-json-name <NAME>` | filename to search for under each root (default `playlist.json`) |
| `--sync-name <NAME>` | output filename under each root (default `playlist.sync.spotdl`) |
| `--download-name <NAME>` | output filename under each root (default `playlist.download.spotdl`) |

## `demucs.sh`

Purpose: separate each MP3 in `<PLAYLIST_TARGET_DIR>/unprocessed` into Demucs outputs (a full 4-stem mix and/or a 2-stem vocal isolate). The script avoids re-running Demucs for tracks that already have the requested stems.

Usage:
```
./demucs.sh [--manifest manifest.json] [--api] [--clean] <PLAYLIST_TARGET_DIR...> [4|2|both]
```

| Option | Description |
|--------|-------------|
| `--manifest <FILE>` | Read playlist roots from a manifest (same fields as spotdl). |
| `<PLAYLIST_TARGET_DIR>` | base path where the script stores `unprocessed/`, `all/`, and `vocals/` outputs |
| `[4|2|both]` | decide whether to produce only the four-stem directories, only the two-stem vocal isolations, or both |

The script creates staging directories, runs Demucs as needed, and keeps the temporary work directories clean.

### Demucs API offload

- Use `--api` to submit batches to the Demucs HTTP API instead of running locally.
- Configure the API endpoint and model in `.env`:
  - `DEMUCS_API_URL` (default `https://demucs.stanley.arpa`)
  - `DEMUCS_API_MODEL` (default `htdemucs` if unset)
  - `DEMUCS_API_CA_CERT` path to a CA bundle or root cert if your API uses a self-signed certificate
  - `DEMUCS_API_BATCH_SIZE` (default 10)
  - `DEMUCS_API_POLL_SECS` (default 5)
  - `DEMUCS_API_TIMEOUT_SECS` (default 3600)
- The API returns a zip of stems; this script unpacks and installs outputs into `all/` and `vocals/`.
- When multiple roots are provided, the script hashes `unprocessed/` files and symlinks missing stems to matching stems from other roots (saves time + disk).

#### Setup (pipx + Python 3.11 + PyTorch 2.1):
1) Install Python 3.11 with winget: `winget install --id Python.Python.3.11 -e`
2) Reinstall demucs under Python 3.11:
   - `pipx uninstall demucs`
   - `pipx install demucs --python "C:\Users\mail\AppData\Local\Programs\Python\Python311\python.exe"`
3) Install CUDA-enabled PyTorch 2.1 stack:
   - `pipx runpip demucs uninstall torch torchaudio torchvision -y`
   - `pipx runpip demucs install "torch==2.1.*" "torchvision==0.16.*" "torchaudio==2.1.*" --index-url https://download.pytorch.org/whl/cu121`
4) If you hit NumPy 2.x compatibility errors, pin NumPy 1.x:
   - `pipx runpip demucs install "numpy<2"`
5) If torchaudio cannot write WAVs, install the soundfile backend:
   - `pipx runpip demucs install soundfile`

Note: Demucs docs recommend `torchaudio` <= 2.1 for CUDA support; newer versions may fail.

### Sidenote: terse GPU usage (Windows)

Use `nvidia-smi` in query mode for compact output:
```
nvidia-smi --query-gpu=timestamp,name,utilization.gpu,utilization.memory,memory.used,memory.total --format=csv,noheader,nounits -l 1
```

PowerShell in-place view (utilization %, temp C, memory GB):
```
while ($true) {
  Clear-Host
  nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv,noheader,nounits |
    % { $p=$_.Split(','); "{0}%  {1}C  {2:0.0}/{3:0.0} GB" -f $p[0].Trim(), $p[1].Trim(), ([double]$p[2]/1024), ([double]$p[3]/1024) }
  Start-Sleep -Seconds 1
}
```

## `ytdlp.sh`

Purpose: download a single YouTube video’s audio as MP3 and tag it with the supplied artist/title.

Usage:
```
./ytdlp.sh <YOUTUBE_LINK> <TARGET_DIR> <ARTIST> <TITLE>
```

The script ensures `yt-dlp` and `id3v2` are installed, sanitizes the artist/title for filenames, echoes the resolved parameters, asks for confirmation, downloads to `<TARGET_DIR>/<ARTIST> - <TITLE>.mp3`, and applies ID3 metadata (`Artist` and `Title` tags).

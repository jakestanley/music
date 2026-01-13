# Music Tooling

This workspace holds helper scripts for downloading music assets and generating stems.

## Prerequisites

These scripts assume the following tools are installed and available on your `PATH`:

- `spotdl` for Spotify downloads
- `demucs` for stem separation
- `yt-dlp` and `id3v2` for YouTube downloads + tagging
- `curl` for UpSnap API calls (Windows offload only)

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

## `spotdl.sh`

Purpose: download a Spotify playlist into an `unprocessed/` folder ready for Demucs.

Usage:
```
./spotdl.sh <PLAYLIST_URL> <PLAYLIST_TARGET_DIR>
```

| Option | Description |
|--------|-------------|
| `<PLAYLIST_URL>` | Spotify playlist URL to fetch with `spotdl` |
| `<PLAYLIST_TARGET_DIR>` | base path where the script stores `unprocessed/` |

The script creates `<PLAYLIST_TARGET_DIR>/unprocessed`, switches into it, and runs `spotdl` for the supplied playlist.

## `demucs.sh`

Purpose: separate each MP3 in `<PLAYLIST_TARGET_DIR>/unprocessed` into Demucs outputs (a full 4-stem mix and/or a 2-stem vocal isolate). The script avoids re-running Demucs for tracks that already have the requested stems.

Usage:
```
./demucs.sh [--windows] [--clean] <PLAYLIST_TARGET_DIR...> [4|2|both]
```

| Option | Description |
|--------|-------------|
| `<PLAYLIST_TARGET_DIR>` | base path where the script stores `unprocessed/`, `all/`, and `vocals/` outputs |
| `[4|2|both]` | decide whether to produce only the four-stem directories, only the two-stem vocal isolations, or both |

The script creates staging directories, runs Demucs as needed, and keeps the temporary work directories clean.

### Windows GPU offload

- Use `--windows` to run Demucs on a Windows GPU box via SSH + UpSnap.
- Use `--clean` to delete the Windows temp work folder before starting.
- Copy `.env.example` to `.env` and fill in the required variables.
- Required vars: `UPSNAP_HOST`, `UPSNAP_USERNAME`, `UPSNAP_PASSWORD`, `UPSNAP_DEVICE_NAME` (or `UPSNAP_DEVICE_ID`), `WINDOWS_SSH_TARGET`, `WINDOWS_SSH_KEY`.
- Optional vars: `WINDOWS_DEMUCS_MODEL`, `WINDOWS_DEMUCS_DEVICE` (defaults to `cuda`), `WINDOWS_PYTHON` (use the pipx demucs venv python for CUDA checks; use forward slashes or double-backslashes), `WINDOWS_GPU_MAX_TEMP`, `WINDOWS_GPU_RESUME_TEMP`, `WINDOWS_BATCH_SIZE` (upload/run in batches; default 10), `WINDOWS_AWAKE_MINUTES` (PowerToys Awake duration per batch; default 10), `WINDOWS_SLEEP_PROMPT_TIMEOUT` (seconds before auto-sleep prompt defaults; default 120).
- Optional: install PowerToys Awake to prevent sleep during long runs: `winget install --id Microsoft.PowerToys -e`.
- During Windows offload runs, the script temporarily sets standby timeout to 0 via `powercfg` and restores it at the end for headless sleep prevention.
- The script does not keep any files on the Windows machine after completion.
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

Client-side SSH helper:
```
./gpu_status.sh --interval 1
./gpu_status.sh --once
```

## `ytdlp.sh`

Purpose: download a single YouTube video’s audio as MP3 and tag it with the supplied artist/title.

Usage:
```
./ytdlp.sh <YOUTUBE_LINK> <TARGET_DIR> <ARTIST> <TITLE>
```

The script ensures `yt-dlp` and `id3v2` are installed, sanitizes the artist/title for filenames, echoes the resolved parameters, asks for confirmation, downloads to `<TARGET_DIR>/<ARTIST> - <TITLE>.mp3`, and applies ID3 metadata (`Artist` and `Title` tags).

# TODO
- Figure out how to work around spotify rate limiting. get a developer account?

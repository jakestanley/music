# Music Tooling

This workspace holds helper scripts for downloading music assets and generating stems.

## Prerequisites

These scripts assume the following tools are installed and available on your `PATH`:

- `spotdl` for Spotify downloads
- `demucs` for stem separation
- `yt-dlp` and `id3v2` for YouTube downloads + tagging

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
./demucs.sh <PLAYLIST_TARGET_DIR> [4|2|both]
```

| Option | Description |
|--------|-------------|
| `<PLAYLIST_TARGET_DIR>` | base path where the script stores `unprocessed/`, `all/`, and `vocals/` outputs |
| `[4|2|both]` | decide whether to produce only the four-stem directories, only the two-stem vocal isolations, or both |

The script creates staging directories, runs Demucs as needed, and keeps the temporary work directories clean.

## `ytdlp.sh`

Purpose: download a single YouTube video’s audio as MP3 and tag it with the supplied artist/title.

Usage:
```
./ytdlp.sh <YOUTUBE_LINK> <TARGET_DIR> <ARTIST> <TITLE>
```

The script ensures `yt-dlp` and `id3v2` are installed, sanitizes the artist/title for filenames, echoes the resolved parameters, asks for confirmation, downloads to `<TARGET_DIR>/<ARTIST> - <TITLE>.mp3`, and applies ID3 metadata (`Artist` and `Title` tags).

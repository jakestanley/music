# Music Tooling

This workspace holds helper scripts for downloading music assets and generating stems.

## `demucspot.sh`

Purpose: download a Spotify playlist with `spotdl`, then separate each downloaded track into demucs outputs (a full 4-stem mix and a 2-stem vocal isolate). The script avoids re-running Demucs for tracks that already have the requested stems.

Usage:
```
./demucspot.sh [--skip-spotdl] <PLAYLIST_URL> <ROOT_DIR> [4|2|both]
```

| Option | Description |
|--------|-------------|
| `--skip-spotdl` | assumes the playlist tracks already exist under `<ROOT_DIR>/unprocessed` and skips downloading |
| `<PLAYLIST_URL>` | Spotify playlist URL to fetch with `spotdl` when not skipping |
| `<ROOT_DIR>` | base path where the script stores `unprocessed/`, `all/`, and `vocals/` outputs |
| `[4|2|both]` | decide whether to produce only the four-stem directories, only the two-stem vocal isolations, or both |

The script creates staging directories, downloads the playlist (unless `--skip-spotdl` is provided), runs Demucs as needed, and keeps the temporary work directories clean.

## `ytdlp.sh`

Purpose: download a single YouTube video’s audio as MP3 and tag it with the supplied artist/title.

Usage:
```
./ytdlp.sh <YOUTUBE_LINK> <TARGET_DIR> <ARTIST> <TITLE>
```

The script ensures `yt-dlp` and `id3v2` are installed, sanitizes the artist/title for filenames, echoes the resolved parameters, asks for confirmation, downloads to `<TARGET_DIR>/<ARTIST> - <TITLE>.mp3`, and applies ID3 metadata (`Artist` and `Title` tags).

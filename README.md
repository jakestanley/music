# Music Tooling

This workspace holds helper scripts for downloading music assets and generating stems.

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

## `spotdl.sh`

Purpose: download a Spotify playlist into an `unprocessed/` folder ready for Demucs.

Usage:
```
./spotdl.sh <PLAYLIST_URL> <ROOT_DIR>
```

| Option | Description |
|--------|-------------|
| `<PLAYLIST_URL>` | Spotify playlist URL to fetch with `spotdl` |
| `<ROOT_DIR>` | base path where the script stores `unprocessed/` |

The script creates `<ROOT_DIR>/unprocessed`, switches into it, and runs `spotdl` for the supplied playlist.

## `demucs.sh`

Purpose: separate each MP3 in `<ROOT_DIR>/unprocessed` into Demucs outputs (a full 4-stem mix and/or a 2-stem vocal isolate). The script avoids re-running Demucs for tracks that already have the requested stems.

Usage:
```
./demucs.sh <ROOT_DIR> [4|2|both]
```

| Option | Description |
|--------|-------------|
| `<ROOT_DIR>` | base path where the script stores `unprocessed/`, `all/`, and `vocals/` outputs |
| `[4|2|both]` | decide whether to produce only the four-stem directories, only the two-stem vocal isolations, or both |

The script creates staging directories, runs Demucs as needed, and keeps the temporary work directories clean.

## `ytdlp.sh`

Purpose: download a single YouTube video’s audio as MP3 and tag it with the supplied artist/title.

Usage:
```
./ytdlp.sh <YOUTUBE_LINK> <TARGET_DIR> <ARTIST> <TITLE>
```

The script ensures `yt-dlp` and `id3v2` are installed, sanitizes the artist/title for filenames, echoes the resolved parameters, asks for confirmation, downloads to `<TARGET_DIR>/<ARTIST> - <TITLE>.mp3`, and applies ID3 metadata (`Artist` and `Title` tags).

# music

Helper scripts to mirror Spotify playlists into local MP3 folders and generate Demucs stems.
Uses a three-step pipeline (scrape → resolve → download) with per-playlist `state.json`, then splits to `all/` and `vocals/` via Demucs.

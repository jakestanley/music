"""
Download resolved tracks from state.json to MP3 using yt-dlp.

Usage:
    python downloader.py <root_dir>

Reads YOUTUBE_COOKIE_FILE from the environment (required for YouTube access).

Example:
    python downloader.py "/home/jake/Music/Playlists/BATW Rejects"
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

from scripts.core import state as st
from scripts.ytdlp.tags import sanitize_value, set_id3_tags

load_dotenv()


def _ytdlp_bin() -> str:
    return str(Path(sys.executable).parent / "yt-dlp")


def _download(youtube_url: str, output_path: Path, cookie_file: str | None) -> tuple[int, str, str]:
    cmd = [_ytdlp_bin(), "--no-playlist"]
    if cookie_file:
        cmd += ["--cookies", cookie_file]
    cmd += [
        "--js-runtimes", "node",
        "--print", "after_move:filepath",
        "-x", "--audio-format", "mp3",
        "-o", str(output_path),
        youtube_url,
    ]
    result = subprocess.run(cmd, text=True, capture_output=True)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Download resolved tracks to MP3.")
    parser.add_argument("root", help="Playlist root directory containing state.json")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be downloaded without downloading")
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    playlist_state = st.load(root)
    if not playlist_state:
        print(f"ERROR: no state.json found in {root}", file=sys.stderr)
        return 1

    resolved = st.tracks_by_status(playlist_state, "resolved")
    if not resolved:
        print("Nothing to download.")
        return 0

    cookie_file = os.environ.get("YOUTUBE_COOKIE_FILE")
    if not cookie_file:
        print("ERROR: YOUTUBE_COOKIE_FILE is not set", file=sys.stderr)
        return 1
    cookie_file = str(Path(cookie_file).expanduser())

    output_dir = root / "unprocessed"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {len(resolved)} tracks...")
    downloaded = 0
    failed = 0

    for track_id, track in resolved:
        artist = track["artist"]
        name = track["name"]
        youtube_url = track["youtube_url"]
        basename = f"{sanitize_value(artist)} - {sanitize_value(name)}.%(ext)s"
        output_path = output_dir / basename

        print(f"  {artist} - {name}")
        print(f"    {youtube_url}")

        if args.dry_run:
            print(f"    -> {output_dir / basename}")
            continue

        code, stdout, stderr = _download(youtube_url, output_path, cookie_file)
        if code != 0:
            print(f"    ERROR: yt-dlp failed (exit {code})", file=sys.stderr)
            if stderr:
                print(f"    {stderr[-500:]}", file=sys.stderr)
            failed += 1
            continue

        # yt-dlp prints the actual output path after conversion
        actual_path = stdout.splitlines()[-1] if stdout else ""
        if not actual_path or not Path(actual_path).is_file():
            # fall back to expected path
            actual_path = str(output_dir / f"{sanitize_value(artist)} - {sanitize_value(name)}.mp3")

        try:
            set_id3_tags(actual_path, artist, name)
        except Exception as exc:
            print(f"    WARNING: failed to set ID3 tags: {exc}", file=sys.stderr)

        st.update_track(playlist_state, track_id, status="downloaded", file=actual_path)
        st.save(root, playlist_state)
        print(f"    -> {actual_path}")
        downloaded += 1

    print(f"\nDone: {downloaded} downloaded, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

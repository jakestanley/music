"""
Analyse downloaded MP3s for BPM and Camelot key, storing results in state.json.

Usage:
    python analyser.py <playlist-root>

Requires: aubio, keyfinder-cli
"""

import argparse
import subprocess
import sys
from pathlib import Path

from scripts.core import state as st


def _aubio_bpm(path: str) -> str | None:
    result = subprocess.run(["aubio", "tempo", path], capture_output=True, text=True)
    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    if not lines:
        return None
    try:
        return str(round(float(lines[-1].split()[0]), 1))
    except (ValueError, IndexError):
        return None


def _camelot_key(path: str) -> str | None:
    result = subprocess.run(
        ["keyfinder-cli", "-n", "camelot", path], capture_output=True, text=True
    )
    key = result.stdout.strip()
    return key if key else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyse MP3s for BPM and Camelot key, storing results in state.json"
    )
    parser.add_argument("root", help="Playlist root directory containing state.json")
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    playlist_state = st.load(root)
    if not playlist_state:
        print(f"ERROR: no state.json found in {root}", file=sys.stderr)
        return 1

    to_analyse = [
        (tid, t)
        for tid, t in playlist_state.get("tracks", {}).items()
        if t.get("status") in ("downloaded", "stems_done")
        and t.get("file")
        and (not t.get("bpm") or not t.get("camelot_key"))
    ]

    if not to_analyse:
        print("All tracks already analysed.")
        return 0

    print(f"Analysing {len(to_analyse)} tracks...", flush=True)
    errors = 0
    for i, (tid, track) in enumerate(to_analyse, 1):
        mp3 = track["file"]
        label = f"{track['artist']} - {track['name']}"
        print(f"  [{i}/{len(to_analyse)}] {label}", flush=True)

        if not Path(mp3).is_file():
            print(f"    SKIP: file not found: {mp3}", flush=True)
            errors += 1
            continue

        bpm = _aubio_bpm(mp3)
        key = _camelot_key(mp3)

        if bpm is None:
            print(f"    WARNING: aubio failed", flush=True)
            errors += 1
        if key is None:
            print(f"    WARNING: keyfinder-cli failed", flush=True)
            errors += 1

        updates = {k: v for k, v in [("bpm", bpm), ("camelot_key", key)] if v is not None}
        if updates:
            st.update_track(playlist_state, tid, **updates)
            st.save(root, playlist_state)
            print(f"    {bpm or '?'} BPM  {key or '?'}", flush=True)

    print(f"Done. {len(to_analyse) - errors} ok, {errors} errors.", flush=True)
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""
Find tracks that mix well with a given seed track, ranked by a combined
harmonic + BPM score and grouped by depth on the Camelot wheel.

Usage:
    python neighbours.py [track_id] [options]
    python neighbours.py --interactive [options]

Examples:
    python neighbours.py 3EYOJ48Et32uATr9ZmLnAo
    python neighbours.py --interactive --depth 3 --limit 5 --bpm-tolerance 15
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

from scripts.core import state as st

_ROOT = Path(__file__).parent

_DEFAULT_DEPTH = 2
_DEFAULT_LIMIT = 10
# Score = harmonic_steps * _STEP_WEIGHT + bpm_delta
# Increasing _STEP_WEIGHT makes harmonic distance matter more relative to BPM.
_STEP_WEIGHT = 10.0


# ---------------------------------------------------------------------------
# Camelot wheel (duplicated from harmonic_bridge per user request)
# ---------------------------------------------------------------------------

def _neighbours(key: str) -> list[str]:
    num = int(key[:-1])
    mode = key[-1]
    return [
        f"{(num - 2) % 12 + 1}{mode}",
        f"{num % 12 + 1}{mode}",
        f"{num}{'B' if mode == 'A' else 'A'}",
    ]


def _bfs_depths(seed_key: str, max_depth: int) -> dict[str, int]:
    """Return {key: depth} for all keys reachable within max_depth steps."""
    depths = {seed_key: 0}
    queue = deque([seed_key])
    while queue:
        key = queue.popleft()
        d = depths[key]
        if d >= max_depth:
            continue
        for nb in _neighbours(key):
            if nb not in depths:
                depths[nb] = d + 1
                queue.append(nb)
    return depths


# ---------------------------------------------------------------------------
# Track loading
# ---------------------------------------------------------------------------

def _load_all_tracks(manifest_path: Path) -> dict[str, dict]:
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        entries = [entries]
    all_tracks: dict[str, dict] = {}
    for entry in entries:
        root = entry.get("root")
        if not root:
            continue
        playlist_state = st.load(Path(root))
        for tid, track in playlist_state.get("tracks", {}).items():
            if (
                track.get("status") in ("downloaded", "stems_done")
                and track.get("bpm")
                and track.get("camelot_key")
            ):
                all_tracks[tid] = track
    return all_tracks


# ---------------------------------------------------------------------------
# Scoring and reporting
# ---------------------------------------------------------------------------

def _score(harmonic_steps: int, bpm_delta: float) -> float:
    return harmonic_steps * _STEP_WEIGHT + bpm_delta


def _track_label(track: dict) -> str:
    return f"{track['artist']} - {track['name']}"


def _report(
    seed_id: str,
    seed_track: dict,
    all_tracks: dict[str, dict],
    depth: int,
    limit: int,
    bpm_tolerance: float | None,
) -> None:
    seed_key = seed_track["camelot_key"]
    seed_bpm = float(seed_track["bpm"])

    print(f"\nSeed: {_track_label(seed_track)}  [{seed_key}  {seed_bpm:.1f} BPM]")
    print(f"Score = harmonic steps × {_STEP_WEIGHT:.0f} + BPM delta  (lower is better)\n")

    key_depths = _bfs_depths(seed_key, depth)

    # Group candidates by depth
    by_depth: dict[int, list[tuple[str, dict, float]]] = {d: [] for d in range(depth + 1)}

    for tid, track in all_tracks.items():
        if tid == seed_id:
            continue
        key = track["camelot_key"]
        if key not in key_depths:
            continue
        bpm_delta = abs(float(track["bpm"]) - seed_bpm)
        if bpm_tolerance is not None and bpm_delta > bpm_tolerance:
            continue
        d = key_depths[key]
        score = _score(d, bpm_delta)
        by_depth[d].append((tid, track, score))

    for d in range(depth + 1):
        candidates = sorted(by_depth[d], key=lambda x: x[2])[:limit]
        keys_at_depth = sorted(k for k, kd in key_depths.items() if kd == d)

        if d == 0:
            label = f"Depth 0 — same key  [{seed_key}]"
        elif d == 1:
            label = f"Depth 1 — direct neighbours  [{', '.join(keys_at_depth)}]"
        else:
            label = f"Depth {d} — {d}-step neighbours  [{', '.join(keys_at_depth)}]"

        print(f"{'─' * 60}")
        print(f"{label}")

        if not candidates:
            print("  —\n")
            continue

        for i, (tid, track, score) in enumerate(candidates, 1):
            bpm_delta = abs(float(track["bpm"]) - seed_bpm)
            harm_steps = key_depths[track["camelot_key"]]
            print(
                f"  {i:3}. {_track_label(track)}"
                f"  [{track['camelot_key']}  {track['bpm']} BPM"
                f"  Δ{bpm_delta:.1f} BPM  score {score:.1f}]"
            )
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find tracks that mix well with a seed track, by harmonic + BPM score."
    )
    parser.add_argument(
        "track_id",
        nargs="?",
        help="Spotify track ID of the seed track (omit to use --interactive)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pick the seed track interactively",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=_DEFAULT_DEPTH,
        metavar="N",
        help=f"How many Camelot steps out to search (default: {_DEFAULT_DEPTH})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=_DEFAULT_LIMIT,
        metavar="N",
        help=f"Max results per depth level (default: {_DEFAULT_LIMIT})",
    )
    parser.add_argument(
        "--bpm-tolerance",
        type=float,
        default=None,
        metavar="BPM",
        help="Exclude candidates more than this many BPM from the seed (default: no limit)",
    )
    parser.add_argument(
        "--manifest",
        default="manifest.json",
        help="Path to manifest.json (default: manifest.json)",
    )
    args = parser.parse_args()

    if not args.track_id and not args.interactive:
        parser.error("provide a track_id or use --interactive")
    if args.track_id and args.interactive:
        parser.error("provide either a track_id or --interactive, not both")

    all_tracks = _load_all_tracks(_ROOT / args.manifest)
    if not all_tracks:
        print("ERROR: no analysed tracks found in manifest playlists.", file=sys.stderr)
        return 1

    if args.interactive:
        from scripts.core.picker import pick_track
        track_list = sorted(all_tracks.items(), key=lambda x: x[1]["artist"].lower())
        result = pick_track(track_list, prompt="Seed track:")
        if result is None:
            return 0
        seed_id, seed_track = result
    else:
        seed_id = args.track_id
        if seed_id not in all_tracks:
            print(f"ERROR: track not found or not yet analysed: {seed_id}", file=sys.stderr)
            return 1
        seed_track = all_tracks[seed_id]

    _report(
        seed_id=seed_id,
        seed_track=seed_track,
        all_tracks=all_tracks,
        depth=args.depth,
        limit=args.limit,
        bpm_tolerance=args.bpm_tolerance,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

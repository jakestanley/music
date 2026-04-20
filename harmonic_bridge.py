"""
Find harmonic bridge tracks for mix joins using the Camelot wheel.

Loads all analysed tracks from every playlist in manifest.json and uses
them as the candidate pool. For each join pair (the last track of one mix
and the first track of the next, referenced by Spotify track ID) it finds
the shortest harmonic path and suggests bridge candidates.

Usage:
    python harmonic_bridge.py FROM_ID:TO_ID [FROM_ID:TO_ID ...] [options]

Example:
    python harmonic_bridge.py abc123:def456 xyz789:uvw012 \\
        --exclude ghi111 jkl222 --bpm-tolerance 8 --chains
"""

import argparse
import json
import sys
from collections import deque
from pathlib import Path

from scripts.core import state as st

_ROOT = Path(__file__).parent

_DEFAULT_BPM_TOLERANCE = 10.0
_DEFAULT_MIN_CANDIDATES = 3
_BPM_TOLERANCE_STEP = 5.0


# ---------------------------------------------------------------------------
# Camelot wheel
# ---------------------------------------------------------------------------

def _neighbours(key: str) -> list[str]:
    """Three harmonic neighbours of a Camelot key: ±1 on the wheel, and A↔B swap."""
    num = int(key[:-1])
    mode = key[-1]
    return [
        f"{(num - 2) % 12 + 1}{mode}",       # one step anticlockwise
        f"{num % 12 + 1}{mode}",              # one step clockwise
        f"{num}{'B' if mode == 'A' else 'A'}", # relative major/minor
    ]


def _shortest_path(start: str, end: str) -> list[str]:
    """BFS shortest path on the Camelot wheel, including both endpoints."""
    if start == end:
        return [start]
    queue: deque[list[str]] = deque([[start]])
    seen = {start}
    while queue:
        path = queue.popleft()
        for nb in _neighbours(path[-1]):
            if nb == end:
                return path + [nb]
            if nb not in seen:
                seen.add(nb)
                queue.append(path + [nb])
    return []  # unreachable on a well-formed wheel


# ---------------------------------------------------------------------------
# Track loading
# ---------------------------------------------------------------------------

def _load_all_tracks(manifest_path: Path) -> dict[str, dict]:
    """Aggregate all downloaded/analysed tracks from every playlist in the manifest."""
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
# BPM helpers
# ---------------------------------------------------------------------------

def _bpm_delta(candidate_bpm: float, from_bpm: float, to_bpm: float) -> float:
    """Average absolute BPM difference between the candidate and both anchor tracks."""
    return (abs(candidate_bpm - from_bpm) + abs(candidate_bpm - to_bpm)) / 2


def _track_label(track: dict) -> str:
    return f"{track['artist']} - {track['name']}"


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _find_single_bridges(
    pool: dict[str, dict],
    intermediate_keys: set[str],
    from_id: str,
    to_id: str,
    from_bpm: float,
    to_bpm: float,
    tolerance: float,
) -> list[tuple[str, dict, float]]:
    results = []
    for tid, track in pool.items():
        if track["camelot_key"] not in intermediate_keys:
            continue
        delta = _bpm_delta(float(track["bpm"]), from_bpm, to_bpm)
        if delta <= tolerance:
            results.append((tid, track, delta))
    results.sort(key=lambda x: x[2])
    return results


def _report_chains(
    path: list[str],
    pool: dict[str, dict],
    from_bpm: float,
    to_bpm: float,
    tolerance: float,
) -> None:
    intermediates = path[1:-1]

    # Index candidates by key for fast lookup
    by_key: dict[str, list[tuple[str, dict]]] = {}
    for tid, track in pool.items():
        k = track["camelot_key"]
        by_key.setdefault(k, []).append((tid, track))

    def _chain_header(n: int) -> None:
        print(f"\n  {n}-track chains:")

    # 2-track chains: pairs of consecutive intermediate keys
    if len(intermediates) >= 2:
        _chain_header(2)
        found_any = False
        for i in range(len(intermediates) - 1):
            k1, k2 = intermediates[i], intermediates[i + 1]
            chains: list[tuple[float, dict, dict]] = []
            for tid1, t1 in by_key.get(k1, []):
                for tid2, t2 in by_key.get(k2, []):
                    if tid1 == tid2:
                        continue
                    d1 = abs(float(t1["bpm"]) - from_bpm)
                    d2 = abs(float(t2["bpm"]) - to_bpm)
                    if d1 <= tolerance and d2 <= tolerance:
                        chains.append(((d1 + d2) / 2, t1, t2))
            chains.sort(key=lambda x: x[0])
            for score, t1, t2 in chains[:3]:
                found_any = True
                print(f"    {_track_label(t1)} [{t1['camelot_key']} {t1['bpm']} BPM]")
                print(f"    → {_track_label(t2)} [{t2['camelot_key']} {t2['bpm']} BPM]")
                print()
        if not found_any:
            print("    None found within BPM tolerance.")

    # 3-track chains: triples of consecutive intermediate keys
    if len(intermediates) >= 3:
        _chain_header(3)
        found_any = False
        for i in range(len(intermediates) - 2):
            k1, k2, k3 = intermediates[i], intermediates[i + 1], intermediates[i + 2]
            chains: list[tuple[float, dict, dict, dict]] = []
            for tid1, t1 in by_key.get(k1, []):
                for tid2, t2 in by_key.get(k2, []):
                    if tid1 == tid2:
                        continue
                    for tid3, t3 in by_key.get(k3, []):
                        if tid3 in (tid1, tid2):
                            continue
                        d1 = abs(float(t1["bpm"]) - from_bpm)
                        d3 = abs(float(t3["bpm"]) - to_bpm)
                        if d1 <= tolerance and d3 <= tolerance:
                            chains.append(((d1 + d3) / 2, t1, t2, t3))
            chains.sort(key=lambda x: x[0])
            for entry in chains[:3]:
                found_any = True
                score, t1, t2, t3 = entry
                print(f"    {_track_label(t1)} [{t1['camelot_key']} {t1['bpm']} BPM]")
                print(f"    → {_track_label(t2)} [{t2['camelot_key']} {t2['bpm']} BPM]")
                print(f"    → {_track_label(t3)} [{t3['camelot_key']} {t3['bpm']} BPM]")
                print()
        if not found_any:
            print("    None found within BPM tolerance.")


def _report_join(
    from_id: str,
    to_id: str,
    all_tracks: dict[str, dict],
    extra_exclude: set[str],
    bpm_tolerance: float,
    min_candidates: int,
    chains: bool,
) -> None:
    from_track = all_tracks[from_id]
    to_track = all_tracks[to_id]
    from_key = from_track["camelot_key"]
    to_key = to_track["camelot_key"]
    from_bpm = float(from_track["bpm"])
    to_bpm = float(to_track["bpm"])

    print(f"\n{'─' * 60}")
    print(f"  {_track_label(from_track)}  [{from_key}  {from_bpm} BPM]")
    print(f"→ {_track_label(to_track)}  [{to_key}  {to_bpm} BPM]")

    path = _shortest_path(from_key, to_key)
    intermediate_keys = set(path[1:-1])
    steps = len(path) - 1

    if not intermediate_keys:
        path_str = f"{from_key} → {to_key}"
        print(f"  Harmonic path: {path_str} ({steps} step — direct, no bridge needed)")
        return

    path_str = " → ".join(path)
    print(f"  Harmonic path: {path_str} ({steps} step{'s' if steps != 1 else ''})")

    excluded = extra_exclude | {from_id, to_id}
    pool = {tid: t for tid, t in all_tracks.items() if tid not in excluded}

    candidates = _find_single_bridges(
        pool, intermediate_keys, from_id, to_id, from_bpm, to_bpm, bpm_tolerance
    )
    effective_tolerance = bpm_tolerance

    if len(candidates) < min_candidates:
        widened = bpm_tolerance
        while len(candidates) < min_candidates and widened < 60.0:
            widened += _BPM_TOLERANCE_STEP
            candidates = _find_single_bridges(
                pool, intermediate_keys, from_id, to_id, from_bpm, to_bpm, widened
            )
        if widened > bpm_tolerance:
            print(
                f"  (widened BPM tolerance from ±{bpm_tolerance:.0f} to ±{widened:.0f} "
                f"to reach {min_candidates} candidates)"
            )
            effective_tolerance = widened

    target_bpm = (from_bpm + to_bpm) / 2
    print(
        f"\n  Single-track bridges  "
        f"[intermediate keys: {', '.join(sorted(intermediate_keys))}  "
        f"target ~{target_bpm:.1f} BPM  ±{effective_tolerance:.0f}]"
    )
    if not candidates:
        print("    No candidates found.")
    else:
        for i, (tid, track, delta) in enumerate(candidates, 1):
            print(
                f"  {i:3}. {_track_label(track)}"
                f"  [{track['camelot_key']}  {track['bpm']} BPM  Δ{delta:.1f}]"
            )

    if chains:
        _report_chains(path, pool, from_bpm, to_bpm, effective_tolerance)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find harmonic bridge tracks for mix joins using the Camelot wheel."
    )
    parser.add_argument(
        "joins",
        nargs="+",
        metavar="FROM_ID:TO_ID",
        help="One or more join pairs as Spotify track IDs separated by ':'",
    )
    parser.add_argument(
        "--manifest",
        default="manifest.json",
        help="Path to manifest.json (default: manifest.json)",
    )
    parser.add_argument(
        "--exclude",
        nargs="*",
        default=[],
        metavar="TRACK_ID",
        help="Additional track IDs to exclude from the candidate pool",
    )
    parser.add_argument(
        "--bpm-tolerance",
        type=float,
        default=_DEFAULT_BPM_TOLERANCE,
        metavar="BPM",
        help=f"Max average BPM difference from anchor tracks (default: {_DEFAULT_BPM_TOLERANCE})",
    )
    parser.add_argument(
        "--min-candidates",
        type=int,
        default=_DEFAULT_MIN_CANDIDATES,
        metavar="N",
        help=f"Widen tolerance if fewer than N candidates found (default: {_DEFAULT_MIN_CANDIDATES})",
    )
    parser.add_argument(
        "--chains",
        action="store_true",
        help="Also suggest 2- and 3-track bridge chains for multi-step joins (off by default)",
    )
    args = parser.parse_args()

    manifest_path = _ROOT / args.manifest
    all_tracks = _load_all_tracks(manifest_path)

    if not all_tracks:
        print("ERROR: no analysed tracks found in manifest playlists.", file=sys.stderr)
        return 1

    extra_exclude = set(args.exclude)

    pairs: list[tuple[str, str]] = []
    for join in args.joins:
        if ":" not in join:
            print(f"ERROR: join must be FROM_ID:TO_ID, got: {join!r}", file=sys.stderr)
            return 1
        from_id, to_id = join.split(":", 1)
        pairs.append((from_id, to_id))

    for from_id, to_id in pairs:
        for label, tid in (("from", from_id), ("to", to_id)):
            if tid not in all_tracks:
                print(
                    f"ERROR: {label} track not found or not yet analysed: {tid}",
                    file=sys.stderr,
                )
                return 1
        _report_join(
            from_id=from_id,
            to_id=to_id,
            all_tracks=all_tracks,
            extra_exclude=extra_exclude,
            bpm_tolerance=args.bpm_tolerance,
            min_candidates=args.min_candidates,
            chains=args.chains,
        )

    print(f"\n{'─' * 60}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

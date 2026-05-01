"""
Greedy mix generator with interactive curation.

Picks a random seed track then greedily extends the mix by combined
harmonic + BPM score. At each step shows the top candidates — accept,
pick an alternative, or finish early. Saves the result as a plain text list.

Usage:
    python mix_generator.py [options]
    python mix_generator.py --interactive   # pick seed via fuzzy picker

Examples:
    python mix_generator.py --count 15 --candidates 8 --bpm-tolerance 15
    python mix_generator.py --interactive --output my_set.txt
"""

import argparse
import json
import random
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

from scripts.core import state as st

_ROOT = Path(__file__).parent

_DEFAULT_COUNT = 20
_DEFAULT_CANDIDATES = 5
_STEP_WEIGHT = 10.0


# ---------------------------------------------------------------------------
# Camelot wheel
# ---------------------------------------------------------------------------

def _neighbours(key: str) -> list[str]:
    num = int(key[:-1])
    mode = key[-1]
    return [
        f"{(num - 2) % 12 + 1}{mode}",
        f"{num % 12 + 1}{mode}",
        f"{num}{'B' if mode == 'A' else 'A'}",
    ]


def _harmonic_distance(a: str, b: str) -> int:
    if a == b:
        return 0
    queue = deque([(a, 0)])
    seen = {a}
    while queue:
        key, dist = queue.popleft()
        for nb in _neighbours(key):
            if nb == b:
                return dist + 1
            if nb not in seen:
                seen.add(nb)
                queue.append((nb, dist + 1))
    return 99


def _score(harmonic_steps: int, bpm_delta: float) -> float:
    return harmonic_steps * _STEP_WEIGHT + bpm_delta


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
# Helpers
# ---------------------------------------------------------------------------

def _track_label(track: dict) -> str:
    return f"{track['artist']} - {track['name']}"


def _track_line(track: dict) -> str:
    return f"{_track_label(track)}  [{track['camelot_key']}  {float(track['bpm']):.1f} BPM]"


def _top_candidates(
    current_track: dict,
    pool: dict[str, dict],
    n: int,
    bpm_tolerance: float | None,
) -> list[tuple[str, dict, int, float, float]]:
    """Return up to n best (tid, track, harm_steps, bpm_delta, score) from pool."""
    current_key = current_track["camelot_key"]
    current_bpm = float(current_track["bpm"])
    scored = []
    for tid, track in pool.items():
        bpm_delta = abs(float(track["bpm"]) - current_bpm)
        if bpm_tolerance is not None and bpm_delta > bpm_tolerance:
            continue
        h = _harmonic_distance(current_key, track["camelot_key"])
        s = _score(h, bpm_delta)
        scored.append((tid, track, h, bpm_delta, s))
    scored.sort(key=lambda x: x[4])
    return scored[:n]


def _print_mix(mix: list[tuple[str, dict]]) -> None:
    print()
    for i, (_, track) in enumerate(mix, 1):
        marker = "  ←" if i == len(mix) else ""
        print(f"  {i:3}. {_track_line(track)}{marker}")
    print()


def _save_mix(mix: list[tuple[str, dict]], output: Path) -> None:
    lines = [
        f"# Mix generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# {len(mix)} tracks",
        "",
    ]
    for i, (_, track) in enumerate(mix, 1):
        lines.append(f"{i:3}. {_track_line(track)}")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nSaved to {output}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Greedy mix generator with interactive curation."
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Pick the seed track via fuzzy picker (default: random)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=_DEFAULT_COUNT,
        metavar="N",
        help=f"Target number of tracks in the mix (default: {_DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=_DEFAULT_CANDIDATES,
        metavar="N",
        help=f"Candidates shown at each step (default: {_DEFAULT_CANDIDATES})",
    )
    parser.add_argument(
        "--bpm-tolerance",
        type=float,
        default=None,
        metavar="BPM",
        help="Max BPM jump per step (default: no limit)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="FILE",
        help="Output file (default: mix_YYYYMMDD_HHMM.txt)",
    )
    parser.add_argument(
        "--manifest",
        default="manifest.json",
        help="Path to manifest.json (default: manifest.json)",
    )
    args = parser.parse_args()

    all_tracks = _load_all_tracks(_ROOT / args.manifest)
    if not all_tracks:
        print("ERROR: no analysed tracks found.", file=sys.stderr)
        return 1

    # Seed
    if args.interactive:
        from scripts.core.picker import pick_track
        track_list = sorted(all_tracks.items(), key=lambda x: x[1]["artist"].lower())
        result = pick_track(track_list, prompt="Seed track:")
        if result is None:
            return 0
        seed_id, seed_track = result
    else:
        seed_id, seed_track = random.choice(list(all_tracks.items()))
        print(f"\nSeed (random): {_track_line(seed_track)}")

    pool = {tid: t for tid, t in all_tracks.items() if tid != seed_id}
    mix: list[tuple[str, dict]] = [(seed_id, seed_track)]

    print(f"\nBuilding mix — target {args.count} tracks.")
    print("At each step: [Enter] accept top pick, [1-N] choose, [d] done, [q] quit\n")

    while len(mix) < args.count:
        current_id, current_track = mix[-1]
        candidates = _top_candidates(current_track, pool, args.candidates, args.bpm_tolerance)

        if not candidates:
            print("  No more candidates available.")
            break

        # Show mix so far
        _print_mix(mix)

        # Show candidates
        print(f"  Step {len(mix) + 1} of {args.count} — from"
              f" [{current_track['camelot_key']}  {float(current_track['bpm']):.1f} BPM]:\n")
        for i, (tid, track, h, bpm_delta, s) in enumerate(candidates, 1):
            print(
                f"  {i}. {_track_line(track)}"
                f"  [Δ{h} step{'s' if h != 1 else ''}  Δ{bpm_delta:.1f} BPM  score {s:.1f}]"
            )

        try:
            raw = input("\n  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break

        if raw in ("q", "quit"):
            return 0
        elif raw in ("d", "done", ""):
            if raw in ("d", "done"):
                break
            # Empty = accept top pick
            chosen = candidates[0]
        else:
            try:
                idx = int(raw) - 1
                if not (0 <= idx < len(candidates)):
                    print(f"  Enter a number between 1 and {len(candidates)}.")
                    continue
                chosen = candidates[idx]
            except ValueError:
                print("  Unrecognised input.")
                continue

        tid, track, h, bpm_delta, s = chosen
        mix.append((tid, track))
        pool.pop(tid)

    # Final review
    print("\n" + "─" * 60)
    print(f"Final mix — {len(mix)} tracks:")
    _print_mix(mix)

    output = args.output or _ROOT / f"mix_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    try:
        raw = input("Save? [Enter to confirm, or type a filename]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return 0

    if raw:
        output = Path(raw)

    _save_mix(mix, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

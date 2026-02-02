import argparse
import os
from pathlib import Path
from typing import Dict, List, Tuple

from scripts.core.env import load_env
from scripts.core.paths import ensure_dir, resolve_dir
from scripts.demucs.cache import HashCache
from scripts.demucs.hashing import get_file_hash
from scripts.demucs.local import run_local
from scripts.demucs.api import RootContext, normalize_windows_name, run_windows
from scripts.report.demucs_report import write_demucs_report
from scripts.spotdl.manifest import parse_manifest
from scripts.upsnap.batch import sleep_if_awake, validate_upsnap_env


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", help="Path to manifest JSON; roots will be read from it.")
    parser.add_argument(
        "--select",
        action="store_true",
        help="Interactively choose a single playlist from the manifest instead of running all.",
    )
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--api", action="store_true", help="Use the Demucs HTTP API instead of local execution.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --api, print the batches and payload details without sending requests.",
    )
    parser.add_argument(
        "--api-max-batches",
        type=int,
        default=None,
        help="With --api, process at most this many batches (debug aid).",
    )
    parser.add_argument("--report", default=None, help="Write HTML report path (default: reports/demucs.html).")
    parser.add_argument("--no-report", action="store_true", help="Skip HTML report generation.")
    parser.add_argument("roots", nargs="*")
    return parser.parse_args()


def _find_stem_dir(stem_root: str, candidates: List[str]) -> str | None:
    for candidate in candidates:
        if os.path.isfile(os.path.join(stem_root, candidate, "vocals.wav")):
            return os.path.join(stem_root, candidate)
        # Some API outputs keep the source extension in the folder name.
        if os.path.isfile(os.path.join(stem_root, f"{candidate}.mp3", "vocals.wav")):
            return os.path.join(stem_root, f"{candidate}.mp3")
    return None


def _select_entry(entries: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    print("Select a playlist to run:")
    for idx, (url, root) in enumerate(entries, start=1):
        print(f"{idx}) {root} - {url}")

    while True:
        choice = input("Enter a number (or press Enter to cancel): ").strip()
        if choice == "":
            print("No selection made; exiting.")
            raise SystemExit(1)
        try:
            index = int(choice)
        except ValueError:
            print("Please enter a valid number.")
            continue
        if 1 <= index <= len(entries):
            return [entries[index - 1]]
        print(f"Enter a number between 1 and {len(entries)}.")


def main() -> int:
    args = _parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))

    if args.api:
        validate_upsnap_env()
    elif args.dry_run:
        raise SystemExit("--dry-run requires --api")
    elif args.api_max_batches is not None:
        raise SystemExit("--api-max-batches requires --api")

    if args.api_max_batches is not None and args.api_max_batches < 1:
        raise SystemExit("--api-max-batches must be >= 1")

    mode = "both"
    roots: List[str] = []
    if args.manifest:
        entries = parse_manifest(args.manifest)
        if args.select:
            entries = _select_entry(entries)
        roots.extend([root for _, root in entries])
    elif args.select:
        raise SystemExit("--select requires --manifest")
    roots.extend(list(args.roots))

    if roots:
        last_arg = roots[-1]
        if last_arg in {"4", "2", "both"}:
            mode = last_arg
            roots = roots[:-1]

    if not roots:
        raise SystemExit(
            "Usage: demucs [--manifest FILE] [--api] [--clean] [--report HTML] [--no-report] <ROOT_DIR...> [4|2|both]"
        )

    root_contexts: List[RootContext] = []
    root_mp3s: Dict[str, List[str]] = {}
    caches: Dict[str, HashCache] = {}

    for root in roots:
        if not os.path.isdir(root):
            raise SystemExit(f"Root directory not found: {root}")
        root_abs = resolve_dir(root)
        base_dir = os.path.join(root_abs, "unprocessed")
        all_dir = os.path.join(root_abs, "all")
        vocals_dir = os.path.join(root_abs, "vocals")
        if not os.path.isdir(base_dir):
            raise SystemExit(f"Unprocessed directory not found: {base_dir}")
        mp3_list = [
            os.path.join(base_dir, f)
            for f in os.listdir(base_dir)
            if f.lower().endswith(".mp3") and not f.startswith("._")
        ]
        root_contexts.append(RootContext(root_abs, base_dir, all_dir, vocals_dir))
        root_mp3s[root_abs] = mp3_list

        cache_file = os.path.join(root_abs, ".demucs_hash_cache")
        cache = HashCache(cache_file)
        cache.load()
        caches[root_abs] = cache

    hash_to_all_dir: Dict[str, str] = {}
    hash_to_vocals_dir: Dict[str, str] = {}

    for ctx in root_contexts:
        mp3_list = root_mp3s[ctx.root]
        if not mp3_list:
            continue
        total = len(mp3_list)
        print(f"Hashing index for {ctx.root} ({total} files)...")
        processed = 0
        for path in mp3_list:
            processed += 1
            if processed == 1 or processed % 10 == 0 or processed == total:
                print(f"  hashed {processed}/{total}")
            name = os.path.splitext(os.path.basename(path))[0]
            candidates = [name]
            if args.api:
                win_name = normalize_windows_name(name)
                if win_name and win_name != name:
                    candidates.append(win_name)
            digest = get_file_hash(path, caches[ctx.root])

            if mode in {"4", "both"}:
                stem_dir = _find_stem_dir(ctx.all_dir, candidates)
                if stem_dir and digest not in hash_to_all_dir:
                    hash_to_all_dir[digest] = stem_dir
            if mode in {"2", "both"}:
                stem_dir = _find_stem_dir(ctx.vocals_dir, candidates)
                if stem_dir and digest not in hash_to_vocals_dir:
                    hash_to_vocals_dir[digest] = stem_dir

        caches[ctx.root].save()
        print(f"Indexed {len(mp3_list)} hashes for {ctx.root}.")

    try:
        for ctx in root_contexts:
            mp3_list = root_mp3s[ctx.root]
            if not mp3_list:
                print(f"No MP3 files found in {ctx.base_dir}")
                continue

            ensure_dir(ctx.all_dir)
            ensure_dir(ctx.vocals_dir)

            missing_files: List[str] = []
            symlinked = 0
            for path in mp3_list:
                name = os.path.splitext(os.path.basename(path))[0]
                candidates = [name]
                if args.api:
                    win_name = normalize_windows_name(name)
                    if win_name and win_name != name:
                        candidates.append(win_name)

                need_all = mode in {"4", "both"} and not _find_stem_dir(ctx.all_dir, candidates)
                need_vocals = mode in {"2", "both"} and not _find_stem_dir(ctx.vocals_dir, candidates)

                if need_all or need_vocals:
                    digest = get_file_hash(path, caches[ctx.root])
                else:
                    digest = ""

                if need_all and digest in hash_to_all_dir:
                    src_dir = hash_to_all_dir[digest]
                    dest_dir = os.path.join(ctx.all_dir, name)
                    if not os.path.exists(dest_dir):
                        os.symlink(src_dir, dest_dir)
                        print(f"✓ exists, symlinking {dest_dir} -> {src_dir}")
                        symlinked += 1
                    if os.path.isfile(os.path.join(dest_dir, "vocals.wav")):
                        need_all = False

                if need_vocals and digest in hash_to_vocals_dir:
                    src_dir = hash_to_vocals_dir[digest]
                    dest_dir = os.path.join(ctx.vocals_dir, name)
                    if not os.path.exists(dest_dir):
                        os.symlink(src_dir, dest_dir)
                        print(f"✓ exists, symlinking {dest_dir} -> {src_dir}")
                        symlinked += 1
                    if os.path.isfile(os.path.join(dest_dir, "vocals.wav")):
                        need_vocals = False

                if need_all or need_vocals:
                    missing_files.append(path)

            print(
                f"Root summary for {ctx.root}: {len(mp3_list)} tracks, {symlinked} symlinked, {len(missing_files)} to process."
            )

            demucs_model = os.environ.get("DEMUCS_MODEL", "htdemucs")
            if args.api:
                run_windows(ctx, missing_files, mode, args.clean, args.dry_run, args.api_max_batches)
            else:
                run_local(missing_files, mode, demucs_model, ctx.base_dir, ctx.all_dir, ctx.vocals_dir)

            caches[ctx.root].save()
    finally:
        if args.api:
            sleep_if_awake()

    if not args.no_report:
        report_path = Path(args.report) if args.report else (repo_root / "reports" / "demucs.html")
        write_demucs_report([ctx.root for ctx in root_contexts], mode, report_path)
        print(f"Report written to {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

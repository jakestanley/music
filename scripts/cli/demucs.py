import argparse
import os
from pathlib import Path
from typing import Dict, List, Tuple

from scripts.core.env import load_env
from scripts.core.paths import ensure_dir, resolve_dir
from scripts.demucs.cache import HashCache
from scripts.demucs.hashing import get_file_hash
from scripts.demucs.local import run_local
from scripts.demucs.windows import RootContext, normalize_windows_name, run_windows
from scripts.spotdl.manifest import parse_manifest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", help="Path to manifest JSON; roots will be read from it.")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--windows", action="store_true")
    parser.add_argument("roots", nargs="*")
    return parser.parse_args()


def _find_stem_dir(stem_root: str, candidates: List[str]) -> str | None:
    for candidate in candidates:
        if os.path.isfile(os.path.join(stem_root, candidate, "vocals.wav")):
            return os.path.join(stem_root, candidate)
    return None


def _load_roots_from_manifest(manifest_path: str) -> List[str]:
    entries: List[Tuple[str, str]] = parse_manifest(manifest_path)
    roots: List[str] = []
    seen = set()
    for _, root in entries:
        if root not in seen:
            roots.append(root)
            seen.add(root)
    return roots


def main() -> int:
    args = _parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))

    mode = "both"
    roots: List[str] = []
    if args.manifest:
        roots.extend(_load_roots_from_manifest(args.manifest))
    roots.extend(list(args.roots))

    if roots:
        last_arg = roots[-1]
        if last_arg in {"4", "2", "both"}:
            mode = last_arg
            roots = roots[:-1]

    if not roots:
        raise SystemExit("Usage: demucs [--manifest FILE] [--windows] [--clean] <ROOT_DIR...> [4|2|both]")

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
            if f.lower().endswith(".mp3")
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
            if args.windows:
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
            if args.windows:
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
        if args.windows:
            run_windows(ctx, missing_files, mode, args.clean)
        else:
            run_local(missing_files, mode, demucs_model, ctx.base_dir, ctx.all_dir, ctx.vocals_dir)

        caches[ctx.root].save()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

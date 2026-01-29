import argparse
import os
import sys
from pathlib import Path

from scripts.core.env import load_env, require_vars
from scripts.core.paths import ensure_dir, resolve_dir
from scripts.spotdl.errors import summarize_errors
from scripts.spotdl.manifest import parse_manifest
from scripts.spotdl.runner import run_spotdl_with_retry_wait_guard


def _print_usage(script_name: str) -> None:
    print(
        f"Usage: {script_name} [--manifest <MANIFEST_FILE>] [--sync-file <FILE>] [--delay <SECONDS>] [--max-retries <N>] | <PLAYLIST_URL> <PLAYLIST_TARGET_DIR>",
        file=sys.stderr,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--manifest")
    parser.add_argument("--sync-file", default="playlist.sync.spotdl")
    parser.add_argument("--delay", default="2")
    parser.add_argument("--max-retries", default="5")
    parser.add_argument("--threads", default="1")
    parser.add_argument("playlist_url", nargs="?")
    parser.add_argument("root", nargs="?")
    args, extra = parser.parse_known_args()
    if extra:
        _print_usage(Path(sys.argv[0]).name)
        raise SystemExit(1)
    return args


def _coerce_int(value: str, name: str, min_value: int | None = None) -> int:
    try:
        number = int(value)
    except ValueError:
        raise SystemExit(f"{name} must be an integer")
    if min_value is not None and number < min_value:
        raise SystemExit(f"{name} must be an integer >= {min_value}")
    return number


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))

    args = _parse_args()

    if args.manifest and (args.playlist_url or args.root):
        print("Cannot mix manifest mode with positional arguments", file=sys.stderr)
        return 1

    if not args.manifest and (not args.playlist_url or not args.root):
        _print_usage(Path(sys.argv[0]).name)
        return 1

    try:
        delay_seconds = float(args.delay)
    except ValueError:
        raise SystemExit("--delay must be a number of seconds (e.g. 2 or 0.5)")

    max_retries = _coerce_int(args.max_retries, "--max-retries")
    threads = _coerce_int(args.threads, "--threads", min_value=1)

    require_vars(["SPOTDL_CLIENT_ID", "SPOTDL_CLIENT_SECRET"])

    entries = []
    if args.manifest:
        if not os.path.isfile(args.manifest):
            raise SystemExit(f"Manifest file not found: {args.manifest}")
        entries = parse_manifest(args.manifest)
        if not entries:
            raise SystemExit(f"Manifest file contains no playlist entries: {args.manifest}")
    else:
        entries = [(args.playlist_url, args.root)]

    for playlist_url, root in entries:
        root_path = resolve_dir(root)
        if not os.path.isdir(root_path):
            raise SystemExit(f"Root directory not found: {root}")

        base_dir = os.path.join(root_path, "unprocessed")
        sync_file = os.path.join(root_path, args.sync_file)
        download_file = os.path.join(root_path, "playlist.download.spotdl")
        ensure_dir(base_dir)
        ensure_dir(os.path.dirname(sync_file))

        spotdl_args = [
            "spotdl",
            "--client-id",
            os.environ["SPOTDL_CLIENT_ID"],
            "--client-secret",
            os.environ["SPOTDL_CLIENT_SECRET"],
            "--use-cache-file",
            "--save-errors",
            os.path.join(root_path, "spotdl.errors.json"),
            "--threads",
            str(threads),
        ]
        if max_retries >= 0:
            spotdl_args.extend(["--max-retries", str(max_retries)])

        status = 0
        if os.path.isfile(download_file):
            status = run_spotdl_with_retry_wait_guard(
                ["spotdl", "download", download_file] + spotdl_args[1:],
                max_retry_wait_seconds=60,
                cwd=base_dir,
            )
        elif os.path.isfile(sync_file):
            status = run_spotdl_with_retry_wait_guard(
                ["spotdl", "sync", sync_file] + spotdl_args[1:],
                max_retry_wait_seconds=60,
                cwd=base_dir,
            )
        else:
            status = run_spotdl_with_retry_wait_guard(
                ["spotdl", "sync", playlist_url, "--save-file", sync_file] + spotdl_args[1:],
                max_retry_wait_seconds=60,
                cwd=base_dir,
            )

        for line in summarize_errors(os.path.join(root_path, "spotdl.errors.json"), download_file):
            print(line)

        if status != 0:
            return status

        if delay_seconds != 0:
            import time

            time.sleep(delay_seconds)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

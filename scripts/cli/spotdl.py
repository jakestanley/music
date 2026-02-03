import argparse
import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.core.env import load_env, require_vars
from scripts.core.paths import ensure_dir, resolve_dir
from scripts.spotdl.errors import summarize_errors
from scripts.spotdl.manifest import parse_manifest
from scripts.state.db import ensure_db
from scripts.state.sync import sync_entries_from_legacy
from scripts.report.html_report import (
    classify_tracks,
    generate_report,
    list_audio_files,
    load_fallback_errors,
    load_fallback_success,
    load_spotdl_errors,
    load_tracks,
    write_html_report,
)
from scripts.spotdl.runner import run_spotdl_with_retry_wait_guard


def _print_usage(script_name: str) -> None:
    print(
        f"Usage: {script_name} [--manifest <MANIFEST_FILE>] [--select] [--sync-file <FILE>] [--delay <SECONDS>] [--max-retries <N>] [--skip-fallback] [--retry-only] [--reset-auto-failures] [--report <HTML>] [--no-report] [--regenerate-report] | <PLAYLIST_URL> <PLAYLIST_TARGET_DIR>",
        file=sys.stderr,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--manifest")
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--sync-file", default="playlist.sync.spotdl")
    parser.add_argument("--delay", default="2")
    parser.add_argument("--max-retries", default="5")
    parser.add_argument("--threads", default="1")
    parser.add_argument("--skip-fallback", action="store_true")
    parser.add_argument("--errors-name", default="spotdl.errors.json")
    parser.add_argument("--download-name", default="playlist.download.spotdl")
    parser.add_argument("--audio-format", default="mp3")
    parser.add_argument("--log-name", default="ytdlp_fallback.errors.jsonl")
    parser.add_argument("--success-log-name", default="ytdlp_fallback.success.jsonl")
    parser.add_argument("--truncate-log", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default="state/music.sqlite3")
    parser.add_argument(
        "--reset-auto-failures",
        action="store_true",
        help="Clear remembered automated fallback failures so yt-dlp auto fallback can be attempted again.",
    )
    parser.add_argument("--report", default=None)
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--regenerate-report", action="store_true")
    parser.add_argument("--retry-only", action="store_true")
    parser.add_argument("playlist_url", nargs="?")
    parser.add_argument("root", nargs="?")
    args, extra = parser.parse_known_args()
    if extra:
        _print_usage(Path(sys.argv[0]).name)
        raise SystemExit(1)
    return args


def _select_entry(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
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


def _coerce_int(value: str, name: str, min_value: int | None = None) -> int:
    try:
        number = int(value)
    except ValueError:
        raise SystemExit(f"{name} must be an integer")
    if min_value is not None and number < min_value:
        raise SystemExit(f"{name} must be an integer >= {min_value}")
    return number


def _build_spotdl_args(root_path: str, args: argparse.Namespace, threads: int, max_retries: int) -> list[str]:
    spotdl_args = [
        "spotdl",
        "--client-id",
        os.environ["SPOTDL_CLIENT_ID"],
        "--client-secret",
        os.environ["SPOTDL_CLIENT_SECRET"],
        "--use-cache-file",
        "--save-errors",
        os.path.join(root_path, args.errors_name),
        "--threads",
        str(threads),
    ]
    if max_retries >= 0:
        spotdl_args.extend(["--max-retries", str(max_retries)])
    return spotdl_args


_SPOTIFY_TRACK_URL_RE = re.compile(r"https?://open\.spotify\.com/track/([A-Za-z0-9]+)")


def _extract_spotify_track_ids(value: Any) -> set[str]:
    ids: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, dict):
            for vv in v.values():
                walk(vv)
            return
        if isinstance(v, list):
            for vv in v:
                walk(vv)
            return
        if isinstance(v, str):
            for m in _SPOTIFY_TRACK_URL_RE.finditer(v):
                ids.add(m.group(1))
            return

    walk(value)
    return ids


def _load_errors_payload(path: Path) -> Any:
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _load_spotdl_download_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError(f"Expected a JSON list of song objects: {path}")
    return data  # type: ignore[return-value]


def _song_query(song: dict[str, Any]) -> str:
    name = song.get("name")
    if not isinstance(name, str) or not name.strip():
        name = "unknown title"

    artists: list[str] = []
    raw_artists = song.get("artists")
    if isinstance(raw_artists, list):
        for a in raw_artists:
            if isinstance(a, str) and a.strip():
                artists.append(a.strip())
    if not artists:
        artist = song.get("artist")
        if isinstance(artist, str) and artist.strip():
            artists = [artist.strip()]

    artist_part = artists[0] if artists else "unknown artist"
    return f"{artist_part} - {name}"


def _run_ytdlp_search_download(
    query: str, output_dir: Path, audio_format: str, dry_run: bool
) -> tuple[int, list[str], str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--default-search",
        "ytsearch1",
        "--print",
        "after_move:filepath",
        "-x",
        "--audio-format",
        audio_format,
        "-o",
        str(output_dir / "%(title)s [%(id)s].%(ext)s"),
        query,
    ]
    print(f"Running: {shlex.join(cmd)}", file=sys.stderr)
    if dry_run:
        return 0, cmd, "", ""
    result = subprocess.run(cmd, text=True, capture_output=True)
    return result.returncode, cmd, result.stdout or "", result.stderr or ""


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
        handle.write("\n")


def _extract_existing_filepaths(text: str) -> list[str]:
    paths: list[str] = []
    for line in (text or "").splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            p = Path(candidate)
        except Exception:
            continue
        if p.is_file():
            paths.append(str(p))
    return paths


def _load_success_index(path: Path) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    if not path.is_file():
        return index

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        track_id = rec.get("spotify_track_id")
        if not isinstance(track_id, str) or not track_id.strip():
            continue
        filepaths = rec.get("filepaths")
        if not isinstance(filepaths, list):
            continue
        existing = [fp for fp in filepaths if isinstance(fp, str) and Path(fp).is_file()]
        if existing:
            index[track_id.strip()] = existing
    return index


def _load_auto_fallback_fail_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.is_file():
        return ids
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        # Only count failures from the automated yt-dlp search fallback.
        if rec.get("source") == "manual_url_fallback":
            continue
        sid = rec.get("spotify_track_id")
        if isinstance(sid, str) and sid.strip():
            ids.add(sid.strip())
    return ids


def _run_fallback(entries: list[tuple[str, str]], args: argparse.Namespace) -> int:
    overall_exit = 0
    for _, root in entries:
        root_path = Path(root)
        if not root_path.is_dir():
            print(f"ERROR: Root directory not found: {root_path}", file=sys.stderr)
            overall_exit = 1
            continue

        errors_path = (root_path / args.errors_name).resolve()
        download_path = (root_path / args.download_name).resolve()
        output_dir = (root_path / "unprocessed").resolve()

        if not errors_path.is_file():
            print(f"Skipping: no errors file: {errors_path}", file=sys.stderr)
            continue
        if not download_path.is_file():
            print(f"ERROR: missing download list: {download_path}", file=sys.stderr)
            overall_exit = 1
            continue

        try:
            errors_payload = _load_errors_payload(errors_path)
        except Exception as exc:
            print(f"ERROR: failed to read errors file {errors_path}: {exc}", file=sys.stderr)
            overall_exit = 1
            continue

        failed_ids = _extract_spotify_track_ids(errors_payload)
        if getattr(args, "_retry_ids", None):
            failed_ids &= args._retry_ids
        if not failed_ids:
            print(f"Skipping: no Spotify track ids found in {errors_path}", file=sys.stderr)
            continue

        try:
            songs = _load_spotdl_download_file(download_path)
        except Exception as exc:
            print(f"ERROR: failed to read download list {download_path}: {exc}", file=sys.stderr)
            overall_exit = 1
            continue

        by_id: dict[str, dict[str, Any]] = {}
        for song in songs:
            song_id = song.get("song_id")
            if isinstance(song_id, str) and song_id.strip():
                by_id[song_id.strip()] = song

        targets = [by_id[sid] for sid in failed_ids if sid in by_id]
        if not targets:
            print(f"Skipping: none of the failed ids matched songs in {download_path}", file=sys.stderr)
            continue

        print(f"Fallback: {root_path} ({len(targets)} songs)", file=sys.stderr)
        fallback_log_path = (root_path / args.log_name).resolve()
        success_log_path = (root_path / args.success_log_name).resolve()
        success_index = _load_success_index(success_log_path)
        prior_auto_fail_ids = _load_auto_fallback_fail_ids(fallback_log_path)
        if not args.dry_run and args.truncate_log:
            try:
                fallback_log_path.write_text("", encoding="utf-8")
            except Exception as exc:
                print(f"ERROR: failed to truncate fallback log {fallback_log_path}: {exc}", file=sys.stderr)
                overall_exit = 1

        logged_path_notice = False
        for song in targets:
            spotify_track_id = song.get("song_id")
            if (
                isinstance(spotify_track_id, str)
                and spotify_track_id.strip()
                and spotify_track_id.strip() in success_index
                ):
                print(
                    f"Skipping (already downloaded): https://open.spotify.com/track/{spotify_track_id.strip()}",
                    file=sys.stderr,
                )
                continue
            if isinstance(spotify_track_id, str) and spotify_track_id.strip() in prior_auto_fail_ids:
                print(
                    f"Skipping auto fallback (prior auto-failure): https://open.spotify.com/track/{spotify_track_id.strip()}",
                    file=sys.stderr,
                )
                continue

            query = _song_query(song)
            code, cmd, stdout, stderr = _run_ytdlp_search_download(
                query=query,
                output_dir=output_dir,
                audio_format=args.audio_format,
                dry_run=args.dry_run,
            )
            if code != 0:
                if not args.dry_run:
                    if not logged_path_notice:
                        print(f"Logging yt-dlp failures to: {fallback_log_path}", file=sys.stderr)
                        logged_path_notice = True
                    try:
                        _append_jsonl(
                            fallback_log_path,
                            {
                                "timestamp": _utc_now_iso(),
                                "spotify_track_id": song.get("song_id"),
                                "spotify_track_url": song.get("url"),
                                "query": query,
                                "yt_dlp_command": cmd,
                                "exit_code": code,
                                "stderr_tail": _tail(stderr, 4000),
                                "stdout_tail": _tail(stdout, 2000),
                            },
                        )
                    except Exception as exc:
                        print(f"ERROR: failed to write fallback log {fallback_log_path}: {exc}", file=sys.stderr)
                print(f"ERROR: yt-dlp failed for: {query}", file=sys.stderr)
                overall_exit = 1
            else:
                if not args.dry_run:
                    filepaths = _extract_existing_filepaths(stdout)
                    if filepaths:
                        try:
                            _append_jsonl(
                                success_log_path,
                                {
                                    "timestamp": _utc_now_iso(),
                                    "spotify_track_id": song.get("song_id"),
                                    "spotify_track_url": song.get("url"),
                                    "query": query,
                                    "yt_dlp_command": cmd,
                                    "filepaths": filepaths,
                                },
                            )
                            if isinstance(spotify_track_id, str) and spotify_track_id.strip():
                                success_index[spotify_track_id.strip()] = filepaths
                        except Exception as exc:
                            print(f"ERROR: failed to write success log {success_log_path}: {exc}", file=sys.stderr)
                            overall_exit = 1

    return overall_exit


def _reset_auto_failure_memory(entries: list[tuple[str, str]], args: argparse.Namespace) -> int:
    overall_exit = 0
    for _, root in entries:
        root_path = Path(root)
        if not root_path.is_dir():
            print(f"ERROR: Root directory not found: {root_path}", file=sys.stderr)
            overall_exit = 1
            continue

        log_path = (root_path / args.log_name).resolve()
        if not log_path.is_file():
            continue

        kept_lines: list[str] = []
        removed = 0
        for raw_line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                kept_lines.append(line)
                continue
            if not isinstance(rec, dict):
                kept_lines.append(line)
                continue

            sid = rec.get("spotify_track_id")
            source = rec.get("source")
            is_auto_failure = isinstance(sid, str) and sid.strip() and source != "manual_url_fallback"
            if is_auto_failure:
                removed += 1
                continue
            kept_lines.append(line)

        if removed == 0:
            continue

        if args.dry_run:
            print(f"Dry run: would remove {removed} remembered auto-failure record(s) from {log_path}", file=sys.stderr)
            continue

        try:
            new_text = "\n".join(kept_lines)
            if new_text:
                new_text += "\n"
            log_path.write_text(new_text, encoding="utf-8")
            print(f"Reset {removed} remembered auto-failure record(s) in {log_path}", file=sys.stderr)
        except Exception as exc:
            print(f"ERROR: failed to rewrite fallback log {log_path}: {exc}", file=sys.stderr)
            overall_exit = 1

    return overall_exit


def _sync_state_db(entries: list[tuple[str, str]], args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    try:
        with ensure_db(db_path) as conn:
            summary = sync_entries_from_legacy(
                conn,
                entries,
                download_name=args.download_name,
                playlist_json_name="playlist.json",
                spotdl_errors_name=args.errors_name,
                fallback_errors_name=args.log_name,
                fallback_success_name=args.success_log_name,
                action_type="state_sync",
            )
        print(
            f"State DB sync complete: {db_path} "
            f"(playlists={summary['playlists']}, tracks={summary['tracks']})"
        )
        return 0
    except Exception as exc:
        print(f"ERROR: failed to sync state DB {db_path}: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))

    args = _parse_args()

    if args.manifest and (args.playlist_url or args.root):
        print("Cannot mix manifest mode with positional arguments", file=sys.stderr)
        return 1

    if args.select and not args.manifest:
        print("--select requires --manifest", file=sys.stderr)
        return 1
    if not args.manifest and (not args.playlist_url or not args.root):
        _print_usage(Path(sys.argv[0]).name)
        return 1

    entries = []
    if args.manifest:
        if not os.path.isfile(args.manifest):
            raise SystemExit(f"Manifest file not found: {args.manifest}")
        entries = parse_manifest(args.manifest)
        if not entries:
            raise SystemExit(f"Manifest file contains no playlist entries: {args.manifest}")
        if args.select:
            entries = _select_entry(entries)
    else:
        entries = [(args.playlist_url, args.root)]

    retry_ids: set[str] = set()
    if args.retry_only:
        require_vars(["SPOTDL_CLIENT_ID", "SPOTDL_CLIENT_SECRET"])
        threads = _coerce_int(args.threads, "--threads", min_value=1)
        max_retries = _coerce_int(args.max_retries, "--max-retries")
    if args.retry_only:
        for _, root in entries:
            root_path = Path(root)
            if not root_path.is_dir():
                print(f"ERROR: Root directory not found: {root_path}", file=sys.stderr)
                return 1
            tracks = load_tracks(root_path, args.download_name, "playlist.json")
            spotdl_fail_ids = load_spotdl_errors(root_path, args.errors_name)
            fallback_fail_ids = load_fallback_errors(root_path, args.log_name)
            fallback_success = load_fallback_success(root_path, args.success_log_name)
            unprocessed_files = list_audio_files(root_path / "unprocessed")
            results = classify_tracks(tracks, unprocessed_files, spotdl_fail_ids, fallback_fail_ids, fallback_success)
            retry_ids |= {r.track.track_id for r in results if r.status in {"missing", "failed"} and r.track.track_id}

    if not args.regenerate_report and not args.retry_only:
        try:
            delay_seconds = float(args.delay)
        except ValueError:
            raise SystemExit("--delay must be a number of seconds (e.g. 2 or 0.5)")

        max_retries = _coerce_int(args.max_retries, "--max-retries")
        threads = _coerce_int(args.threads, "--threads", min_value=1)

        require_vars(["SPOTDL_CLIENT_ID", "SPOTDL_CLIENT_SECRET"])

        for playlist_url, root in entries:
            root_path = resolve_dir(root)
            if not os.path.isdir(root_path):
                raise SystemExit(f"Root directory not found: {root}")

            base_dir = os.path.join(root_path, "unprocessed")
            sync_file = os.path.join(root_path, args.sync_file)
            download_file = os.path.join(root_path, args.download_name)
            ensure_dir(base_dir)
            ensure_dir(os.path.dirname(sync_file))

            spotdl_args = _build_spotdl_args(root_path, args, threads, max_retries)

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

            for line in summarize_errors(os.path.join(root_path, args.errors_name), download_file):
                print(line)

            if status != 0:
                return status

            if delay_seconds != 0:
                import time

                time.sleep(delay_seconds)

        if args.reset_auto_failures:
            reset_status = _reset_auto_failure_memory(entries, args)
            if reset_status != 0:
                return reset_status
        if not args.skip_fallback:
            fallback_status = _run_fallback(entries, args)
            if fallback_status != 0:
                return fallback_status

    if args.retry_only:
        args._retry_ids = retry_ids
        for _, root in entries:
            root_path = resolve_dir(root)
            if not os.path.isdir(root_path):
                raise SystemExit(f"Root directory not found: {root}")
            ensure_dir(os.path.join(root_path, "unprocessed"))
            download_file = os.path.join(root_path, args.download_name)
            if not os.path.isfile(download_file):
                raise SystemExit(f"Missing download list: {download_file}")
            songs = _load_spotdl_download_file(Path(download_file))
            filtered = [s for s in songs if isinstance(s.get("song_id"), str) and s["song_id"] in retry_ids]
            if not filtered:
                print(f"Skipping retry (no missing/failed tracks): {root_path}", file=sys.stderr)
                continue
            tmp_path = Path(download_file).with_suffix(".retry.spotdl")
            tmp_path.write_text(json.dumps(filtered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            spotdl_args = _build_spotdl_args(root_path, args, threads, max_retries)
            status = run_spotdl_with_retry_wait_guard(
                ["spotdl", "download", str(tmp_path)] + spotdl_args[1:],
                max_retry_wait_seconds=60,
                cwd=os.path.join(root_path, "unprocessed"),
            )
            for line in summarize_errors(os.path.join(root_path, args.errors_name), str(tmp_path)):
                print(line)
            if status != 0:
                return status
        if args.reset_auto_failures:
            reset_status = _reset_auto_failure_memory(entries, args)
            if reset_status != 0:
                return reset_status
        if not args.skip_fallback:
            fallback_status = _run_fallback(entries, args)
            if fallback_status != 0:
                return fallback_status

    sync_status = _sync_state_db(entries, args)
    if sync_status != 0:
        return sync_status

    if not args.no_report:
        report_path = Path(args.report) if args.report else (repo_root / "reports" / "spotdl.html")
        manifest_path = Path(args.manifest) if args.manifest else Path("manifest.json")
        report = generate_report(
            manifest_entries=entries,
            manifest_path=manifest_path,
            download_name=args.download_name,
            spotdl_errors_name=args.errors_name,
            fallback_errors_name=args.log_name,
            fallback_success_name=args.success_log_name,
            db_path=Path(args.db),
        )
        write_html_report(report, report_path)
        print(f"Report written to {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

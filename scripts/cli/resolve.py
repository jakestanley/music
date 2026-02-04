#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shlex
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from scripts.core.env import load_env
from scripts.spotdl.manifest import parse_manifest
from scripts.state.db import ensure_db
from scripts.state.planner import ManualResolutionTask, list_manual_resolution_tasks
from scripts.state.sync import sync_entries_from_legacy


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactively resolve manual_pending tracks with direct YouTube URLs."
    )
    parser.add_argument("--manifest")
    parser.add_argument("--select", action="store_true")
    parser.add_argument("--db", default="state/music.sqlite3")
    parser.add_argument("--download-name", default="playlist.download.spotdl")
    parser.add_argument("--playlist-json-name", default="playlist.json")
    parser.add_argument("--errors-name", default="spotdl.errors.json")
    parser.add_argument("--log-name", default="ytdlp_fallback.errors.jsonl")
    parser.add_argument("--success-log-name", default="ytdlp_fallback.success.jsonl")
    parser.add_argument("--audio-format", default="mp3")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Promote current pending tracks to manual_pending before prompting.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("root", nargs="?")
    args = parser.parse_args()
    if args.select and not args.manifest:
        raise SystemExit("--select requires --manifest")
    if not args.manifest and not args.root:
        raise SystemExit("Provide either --manifest or a playlist root path.")
    if args.manifest and args.root:
        raise SystemExit("Cannot mix --manifest with a positional root.")
    return args


def _select_entry(entries: list[tuple[str, str]]) -> list[tuple[str, str]]:
    print("Select a playlist to resolve:")
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


def _song_query(artist: str, title: str) -> str:
    a = artist.strip() if artist.strip() else "unknown artist"
    t = title.strip() if title.strip() else "unknown title"
    return f"{a} - {t}"


def _run_ytdlp_url_download(
    youtube_url: str, output_dir: Path, audio_format: str, dry_run: bool
) -> tuple[int, list[str], str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "--print",
        "after_move:filepath",
        "-x",
        "--audio-format",
        audio_format,
        "-o",
        str(output_dir / "%(title)s [%(id)s].%(ext)s"),
        youtube_url,
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


def _append_jsonl(path: Path, record: dict) -> None:
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
        p = Path(candidate)
        if p.is_file():
            paths.append(str(p))
    return paths


def _manual_prompt(task: ManualResolutionTask) -> str | None:
    query = _song_query(task.primary_artist, task.title)
    print("")
    print(
        f"Missing: {query} "
        f"(attempts spotdl/auto/manual={task.attempt_spotdl}/{task.attempt_auto}/{task.attempt_manual})",
        file=sys.stderr,
    )
    if task.spotify_url:
        print(f"Spotify: {task.spotify_url}", file=sys.stderr)
    try:
        value = input("YouTube URL (press Enter to skip): ").strip()
    except EOFError:
        return None
    if not value:
        return None
    return value


def _insert_action(conn, task: ManualResolutionTask, outcome: str, details: dict) -> None:
    conn.execute(
        """
        INSERT INTO actions(playlist_id, track_id, action_type, outcome, details_json, created_at)
        VALUES(?, ?, 'manual_resolve', ?, ?, ?)
        """,
        (
            task.playlist_id,
            task.track_id,
            outcome,
            json.dumps(details, ensure_ascii=False),
            _utc_now_iso(),
        ),
    )


def _promote_pending_to_manual_queue(conn, roots: list[str], dry_run: bool) -> int:
    if not roots:
        return 0
    marks = ",".join("?" for _ in roots)
    where_clause = f"status = 'pending' AND playlist_id IN (SELECT id FROM playlists WHERE root_path IN ({marks}))"
    params: list[object] = list(roots)
    count = int(conn.execute(f"SELECT COUNT(*) FROM track_state WHERE {where_clause}", params).fetchone()[0])
    if count == 0:
        return 0
    if dry_run:
        return count
    conn.execute(
        f"UPDATE track_state SET status = 'manual_pending', updated_at = ? WHERE {where_clause}",
        [_utc_now_iso(), *params],
    )
    return count


def _run_manual_resolution(entries: list[tuple[str, str]], args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    roots = [str(Path(root).expanduser().resolve()) for _, root in entries]
    overall_exit = 0

    with ensure_db(db_path) as conn:
        sync_entries_from_legacy(
            conn,
            entries,
            download_name=args.download_name,
            playlist_json_name=args.playlist_json_name,
            spotdl_errors_name=args.errors_name,
            fallback_errors_name=args.log_name,
            fallback_success_name=args.success_log_name,
            action_type="resolve_sync",
        )
        if args.force:
            promoted = _promote_pending_to_manual_queue(conn, roots, args.dry_run)
            if args.dry_run:
                print(f"Dry run: would promote {promoted} pending track(s) to manual_pending.", file=sys.stderr)
            elif promoted:
                print(f"Promoted {promoted} pending track(s) to manual_pending.", file=sys.stderr)
        tasks = list_manual_resolution_tasks(conn, roots=roots)

        if not tasks:
            print("Manual resolution: no manual_pending tracks.")
            return 0

        by_root: dict[str, list[ManualResolutionTask]] = defaultdict(list)
        for task in tasks:
            by_root[task.root_path].append(task)

        for root_path_str in sorted(by_root.keys()):
            root_path = Path(root_path_str)
            output_dir = root_path / "unprocessed"
            fallback_log_path = root_path / args.log_name
            success_log_path = root_path / args.success_log_name
            root_tasks = by_root[root_path_str]

            print(f"Manual resolution: {root_path} ({len(root_tasks)} songs)", file=sys.stderr)
            for task in root_tasks:
                youtube_url = _manual_prompt(task)
                if youtube_url is None:
                    _insert_action(conn, task, "skipped", {"reason": "user_skipped"})
                    continue

                code, cmd, stdout, stderr = _run_ytdlp_url_download(
                    youtube_url=youtube_url,
                    output_dir=output_dir,
                    audio_format=args.audio_format,
                    dry_run=args.dry_run,
                )
                query = _song_query(task.primary_artist, task.title)
                if code != 0:
                    if not args.dry_run:
                        _append_jsonl(
                            fallback_log_path,
                            {
                                "timestamp": _utc_now_iso(),
                                "spotify_track_id": task.track_id,
                                "spotify_track_url": task.spotify_url,
                                "query": query,
                                "youtube_url": youtube_url,
                                "source": "manual_url_fallback",
                                "yt_dlp_command": cmd,
                                "exit_code": code,
                                "stderr_tail": _tail(stderr, 4000),
                                "stdout_tail": _tail(stdout, 2000),
                            },
                        )
                        conn.execute(
                            """
                            UPDATE track_state
                            SET attempt_manual = attempt_manual + 1,
                                status = 'manual_pending',
                                last_error = ?,
                                updated_at = ?
                            WHERE playlist_id = ? AND track_id = ?
                            """,
                            (_tail(stderr, 4000) or "manual yt-dlp failure", _utc_now_iso(), task.playlist_id, task.track_id),
                        )
                    _insert_action(
                        conn,
                        task,
                        "failed",
                        {"query": query, "youtube_url": youtube_url, "exit_code": code},
                    )
                    print(f"ERROR: manual yt-dlp failed for: {query}", file=sys.stderr)
                    overall_exit = 1
                    continue

                filepaths = _extract_existing_filepaths(stdout)
                if not args.dry_run:
                    _append_jsonl(
                        success_log_path,
                        {
                            "timestamp": _utc_now_iso(),
                            "spotify_track_id": task.track_id,
                            "spotify_track_url": task.spotify_url,
                            "query": query,
                            "youtube_url": youtube_url,
                            "source": "manual_url_fallback",
                            "yt_dlp_command": cmd,
                            "filepaths": filepaths,
                        },
                    )
                    resolved_path = filepaths[0] if filepaths else None
                    conn.execute(
                        """
                        UPDATE track_state
                        SET attempt_manual = attempt_manual + 1,
                            status = 'manual_ok',
                            resolved_path = COALESCE(?, resolved_path),
                            last_error = NULL,
                            updated_at = ?
                        WHERE playlist_id = ? AND track_id = ?
                        """,
                        (resolved_path, _utc_now_iso(), task.playlist_id, task.track_id),
                    )
                _insert_action(
                    conn,
                    task,
                    "succeeded",
                    {"query": query, "youtube_url": youtube_url, "filepaths": filepaths},
                )
                print(f"Downloaded via manual URL: {query}", file=sys.stderr)

        conn.commit()

    return overall_exit


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    load_env(str(repo_root / ".env"))
    args = _parse_args()

    if args.manifest:
        if not os.path.isfile(args.manifest):
            raise SystemExit(f"Manifest file not found: {args.manifest}")
        entries = parse_manifest(args.manifest)
        if not entries:
            raise SystemExit(f"Manifest file contains no playlist entries: {args.manifest}")
        if args.select:
            entries = _select_entry(entries)
    else:
        entries = [("", args.root)]

    return _run_manual_resolution(entries, args)


if __name__ == "__main__":
    raise SystemExit(main())

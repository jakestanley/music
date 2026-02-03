from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from scripts.report.html_report import (
    classify_tracks,
    list_audio_files,
    load_fallback_errors,
    load_fallback_success,
    load_spotdl_errors,
    load_tracks,
)


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def _synthetic_track_id(root: Path, position: int, title: str, artist: str, url: str | None) -> str:
    raw = f"{root}|{position}|{title}|{artist}|{url or ''}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"synthetic:{digest}"


def _choose_primary_artist(artists: list[str]) -> str:
    return artists[0] if artists else "Unknown Artist"


def _summarize_legacy_logs(
    fallback_errors: list[dict[str, Any]],
    fallback_success: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    fail_stats: dict[str, dict[str, Any]] = {}
    ok_stats: dict[str, dict[str, Any]] = {}

    for rec in fallback_errors:
        sid = rec.get("spotify_track_id")
        if not isinstance(sid, str) or not sid.strip():
            continue
        sid = sid.strip()
        source = rec.get("source")
        is_manual = source == "manual_url_fallback"
        bucket = fail_stats.setdefault(
            sid,
            {
                "auto_fail": 0,
                "manual_fail": 0,
                "last_error": None,
            },
        )
        if is_manual:
            bucket["manual_fail"] += 1
        else:
            bucket["auto_fail"] += 1
        stderr_tail = rec.get("stderr_tail")
        if isinstance(stderr_tail, str) and stderr_tail.strip():
            bucket["last_error"] = stderr_tail.strip()
        elif isinstance(rec.get("query"), str):
            bucket["last_error"] = f"Failed query: {rec['query']}"

    for rec in fallback_success:
        sid = rec.get("spotify_track_id")
        if not isinstance(sid, str) or not sid.strip():
            continue
        sid = sid.strip()
        source = rec.get("source")
        is_manual = source == "manual_url_fallback"
        bucket = ok_stats.setdefault(
            sid,
            {
                "auto_ok": 0,
                "manual_ok": 0,
                "auto_files": [],
                "manual_files": [],
            },
        )
        if is_manual:
            bucket["manual_ok"] += 1
        else:
            bucket["auto_ok"] += 1
        paths = rec.get("filepaths")
        if isinstance(paths, list):
            valid = [p for p in paths if isinstance(p, str) and p]
            if is_manual:
                bucket["manual_files"].extend(valid)
            else:
                bucket["auto_files"].extend(valid)

    return fail_stats, ok_stats


def _derive_status(
    track_id: str,
    result_status: str,
    spotdl_fail_ids: set[str],
    fail_stats: dict[str, dict[str, Any]],
    ok_stats: dict[str, dict[str, Any]],
) -> str:
    ok = ok_stats.get(track_id, {})
    fail = fail_stats.get(track_id, {})

    if ok.get("manual_ok", 0) > 0:
        return "manual_ok"
    if ok.get("auto_ok", 0) > 0:
        return "auto_ok"
    if result_status == "downloaded":
        return "spotdl_ok"
    if fail.get("auto_fail", 0) > 0 and track_id in spotdl_fail_ids:
        return "manual_pending"
    if fail.get("auto_fail", 0) > 0:
        return "auto_failed"
    if track_id in spotdl_fail_ids:
        return "spotdl_failed"
    return "pending"


def sync_entries_from_legacy(
    conn: sqlite3.Connection,
    entries: Sequence[tuple[str, str]],
    *,
    download_name: str = "playlist.download.spotdl",
    playlist_json_name: str = "playlist.json",
    spotdl_errors_name: str = "spotdl.errors.json",
    fallback_errors_name: str = "ytdlp_fallback.errors.jsonl",
    fallback_success_name: str = "ytdlp_fallback.success.jsonl",
    action_type: str = "state_sync",
) -> dict[str, int]:
    now = _now_iso()
    touched_playlists = 0
    touched_tracks = 0

    for playlist_url, root in entries:
        root_path = Path(root).expanduser().resolve()
        if not root_path.is_dir():
            continue

        conn.execute(
            """
            INSERT INTO playlists(root_path, playlist_url, name, updated_at)
            VALUES(?, ?, NULL, ?)
            ON CONFLICT(root_path)
            DO UPDATE SET
              playlist_url=CASE
                WHEN excluded.playlist_url <> '' THEN excluded.playlist_url
                ELSE playlists.playlist_url
              END,
              updated_at=excluded.updated_at
            """,
            (str(root_path), playlist_url, now),
        )
        playlist_id = conn.execute("SELECT id FROM playlists WHERE root_path = ?", (str(root_path),)).fetchone()[0]
        touched_playlists += 1

        tracks = load_tracks(root_path, download_name, playlist_json_name)
        spotdl_fail_ids = load_spotdl_errors(root_path, spotdl_errors_name)
        fallback_fail_ids = load_fallback_errors(root_path, fallback_errors_name)
        fallback_success_index = load_fallback_success(root_path, fallback_success_name)
        unprocessed_files = list_audio_files(root_path / "unprocessed")
        results = classify_tracks(
            tracks=tracks,
            unprocessed_files=unprocessed_files,
            spotdl_fail_ids=spotdl_fail_ids,
            fallback_fail_ids=fallback_fail_ids,
            fallback_success=fallback_success_index,
        )

        result_by_track_id = {r.track.track_id: r for r in results if r.track.track_id}
        raw_fail = _read_jsonl(root_path / fallback_errors_name)
        raw_ok = _read_jsonl(root_path / fallback_success_name)
        fail_stats, ok_stats = _summarize_legacy_logs(raw_fail, raw_ok)

        conn.execute("UPDATE playlist_tracks SET active = 0 WHERE playlist_id = ?", (playlist_id,))

        for pos, track in enumerate(tracks):
            artist = _choose_primary_artist(track.artists)
            track_id = (
                track.track_id
                if track.track_id
                else _synthetic_track_id(root_path, pos, track.name, artist, track.url)
            )
            result = result_by_track_id.get(track.track_id) if track.track_id else None
            result_status = result.status if result else "pending"

            conn.execute(
                """
                INSERT INTO tracks(id, title, primary_artist, spotify_url)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(id)
                DO UPDATE SET
                  title=excluded.title,
                  primary_artist=excluded.primary_artist,
                  spotify_url=COALESCE(excluded.spotify_url, tracks.spotify_url)
                """,
                (track_id, track.name, artist, track.url),
            )

            conn.execute(
                """
                INSERT INTO playlist_tracks(playlist_id, track_id, position, active)
                VALUES(?, ?, ?, 1)
                ON CONFLICT(playlist_id, track_id)
                DO UPDATE SET position=excluded.position, active=1
                """,
                (playlist_id, track_id, pos),
            )

            attempts_spotdl = 1 if (track.track_id and track.track_id in spotdl_fail_ids) else 0
            fail = fail_stats.get(track.track_id or "", {})
            ok = ok_stats.get(track.track_id or "", {})
            attempts_auto = int(fail.get("auto_fail", 0)) + int(ok.get("auto_ok", 0))
            attempts_manual = int(fail.get("manual_fail", 0)) + int(ok.get("manual_ok", 0))

            resolved_path = None
            if ok.get("manual_files"):
                resolved_path = ok["manual_files"][0]
            elif ok.get("auto_files"):
                resolved_path = ok["auto_files"][0]
            elif result and result.files:
                resolved_path = result.files[0]

            last_error = fail.get("last_error")
            if not last_error and track.track_id and track.track_id in spotdl_fail_ids:
                last_error = "spotdl failure (see spotdl errors file)"

            status = _derive_status(
                track_id=track.track_id or "",
                result_status=result_status,
                spotdl_fail_ids=spotdl_fail_ids,
                fail_stats=fail_stats,
                ok_stats=ok_stats,
            )
            if track.track_id is None and result_status == "downloaded":
                status = "spotdl_ok"

            conn.execute(
                """
                INSERT INTO track_state(
                    playlist_id, track_id, status, attempt_spotdl, attempt_auto,
                    attempt_manual, last_error, resolved_path, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(playlist_id, track_id)
                DO UPDATE SET
                  status=CASE
                    WHEN track_state.status IN ('manual_pending', 'manual_ok', 'demucs_done')
                      AND excluded.status IN ('pending', 'spotdl_failed', 'auto_failed')
                    THEN track_state.status
                    ELSE excluded.status
                  END,
                  attempt_spotdl=MAX(track_state.attempt_spotdl, excluded.attempt_spotdl),
                  attempt_auto=MAX(track_state.attempt_auto, excluded.attempt_auto),
                  attempt_manual=MAX(track_state.attempt_manual, excluded.attempt_manual),
                  last_error=COALESCE(excluded.last_error, track_state.last_error),
                  resolved_path=COALESCE(excluded.resolved_path, track_state.resolved_path),
                  updated_at=excluded.updated_at
                """,
                (
                    playlist_id,
                    track_id,
                    status,
                    attempts_spotdl,
                    attempts_auto,
                    attempts_manual,
                    last_error,
                    resolved_path,
                    now,
                ),
            )

            details = {
                "legacy_status": result_status,
                "attempt_spotdl": attempts_spotdl,
                "attempt_auto": attempts_auto,
                "attempt_manual": attempts_manual,
            }
            conn.execute(
                """
                INSERT INTO actions(playlist_id, track_id, action_type, outcome, details_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (playlist_id, track_id, action_type, status, json.dumps(details, ensure_ascii=False), now),
            )
            touched_tracks += 1

    conn.commit()
    return {"playlists": touched_playlists, "tracks": touched_tracks}

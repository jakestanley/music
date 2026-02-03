#!/usr/bin/env python3

from __future__ import annotations

import datetime as _dt
import html
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from scripts.spotdl.errors import extract_ids

AUDIO_EXTS = {".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".wma", ".webm", ".opus"}


def _is_macos_sidecar(path: Path | str) -> bool:
    name = path.name if isinstance(path, Path) else Path(path).name
    return name.startswith("._")


@dataclass(frozen=True)
class TrackEntry:
    name: str
    artists: List[str]
    track_id: str | None
    url: str | None


@dataclass(frozen=True)
class TrackResult:
    track: TrackEntry
    status: str  # downloaded | failed | missing
    source: str
    files: List[str]
    notes: List[str]


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


_TRACK_URI_RE = re.compile(r"^spotify:track:([A-Za-z0-9]+)$")


def _track_id_from_track(track: dict[str, Any]) -> str | None:
    track_id = track.get("id")
    if isinstance(track_id, str) and track_id.strip():
        return track_id.strip()
    uri = track.get("uri")
    if isinstance(uri, str):
        m = _TRACK_URI_RE.match(uri.strip())
        if m:
            return m.group(1)
    return None


def _artists_from_track(track: dict[str, Any]) -> List[str]:
    artists = track.get("artists")
    names: List[str] = []
    if isinstance(artists, list):
        for artist in artists:
            if isinstance(artist, dict):
                name = artist.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            elif isinstance(artist, str) and artist.strip():
                names.append(artist.strip())
    if names:
        return names
    artist = track.get("artist")
    if isinstance(artist, str) and artist.strip():
        return [artist.strip()]
    return ["Unknown Artist"]


def _load_tracks_from_download_list(path: Path) -> List[TrackEntry]:
    songs = _read_json(path)
    if not isinstance(songs, list):
        raise ValueError(f"Expected a JSON list: {path}")

    tracks: List[TrackEntry] = []
    for song in songs:
        if not isinstance(song, dict):
            continue
        name = song.get("name") or "Unknown Title"
        if not isinstance(name, str) or not name.strip():
            name = "Unknown Title"
        artists = _artists_from_track(song)
        track_id = song.get("song_id")
        if isinstance(track_id, str) and track_id.strip():
            track_id = track_id.strip()
        else:
            track_id = None
        url = song.get("url")
        if isinstance(url, str) and url.strip():
            url = url.strip()
        else:
            url = None
        tracks.append(TrackEntry(name=name.strip(), artists=artists, track_id=track_id, url=url))
    return tracks


def _load_tracks_from_playlist_json(path: Path) -> List[TrackEntry]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Playlist JSON must be an object: {path}")
    tracks_raw = payload.get("tracks")
    if not isinstance(tracks_raw, list):
        raise ValueError(f"playlist.json missing 'tracks' array: {path}")

    tracks: List[TrackEntry] = []
    for track in tracks_raw:
        if not isinstance(track, dict):
            continue
        name = track.get("name") or "Unknown Title"
        if not isinstance(name, str) or not name.strip():
            name = "Unknown Title"
        artists = _artists_from_track(track)
        track_id = _track_id_from_track(track)
        url = track.get("external_urls", {}).get("spotify") if isinstance(track.get("external_urls"), dict) else None
        tracks.append(TrackEntry(name=name.strip(), artists=artists, track_id=track_id, url=url))
    return tracks


def load_tracks(root: Path, download_name: str, playlist_json_name: str) -> List[TrackEntry]:
    download_path = root / download_name
    if download_path.is_file():
        return _load_tracks_from_download_list(download_path)
    playlist_path = root / playlist_json_name
    if playlist_path.is_file():
        return _load_tracks_from_playlist_json(playlist_path)
    return []


def load_spotdl_errors(root: Path, filename: str) -> set[str]:
    path = root / filename
    if not path.is_file():
        return set()
    try:
        payload = _read_json(path)
    except Exception:
        return set()
    try:
        return extract_ids(payload)
    except Exception:
        return set()


def load_fallback_errors(root: Path, filename: str) -> set[str]:
    ids: set[str] = set()
    path = root / filename
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
        sid = rec.get("spotify_track_id")
        if isinstance(sid, str) and sid.strip():
            ids.add(sid.strip())
    return ids


def load_fallback_success(root: Path, filename: str) -> dict[str, List[str]]:
    index: dict[str, List[str]] = {}
    path = root / filename
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
        sid = rec.get("spotify_track_id")
        fps = rec.get("filepaths")
        if not (isinstance(sid, str) and sid.strip() and isinstance(fps, list)):
            continue
        paths = [fp for fp in fps if isinstance(fp, str) and not _is_macos_sidecar(fp)]
        if paths:
            index[sid.strip()] = paths
    return index


def list_audio_files(unprocessed_dir: Path) -> List[Path]:
    if not unprocessed_dir.is_dir():
        return []
    files: List[Path] = []
    for path in unprocessed_dir.rglob("*"):
        if path.is_file() and not _is_macos_sidecar(path) and path.suffix.lower() in AUDIO_EXTS:
            files.append(path)
    return files


def _norm(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def match_files(track: TrackEntry, files: Sequence[Path]) -> List[str]:
    patterns: List[str] = []
    if track.track_id:
        patterns.append(track.track_id.lower())
    main_artist = track.artists[0] if track.artists else "unknown"
    patterns.append(_norm(f"{main_artist} - {track.name}"))
    patterns.append(_norm(track.name))

    matches: List[str] = []
    for path in files:
        base = path.stem
        lower_name = base.lower()
        norm_name = _norm(base)
        if track.track_id and track.track_id.lower() in lower_name:
            matches.append(str(path))
            continue
        for pat in patterns:
            if not pat:
                continue
            if norm_name == pat or norm_name.startswith(pat) or pat in norm_name:
                matches.append(str(path))
                break
    return matches


def classify_tracks(
    tracks: List[TrackEntry],
    unprocessed_files: List[Path],
    spotdl_fail_ids: set[str],
    fallback_fail_ids: set[str],
    fallback_success: dict[str, List[str]],
) -> List[TrackResult]:
    results: List[TrackResult] = []
    for track in tracks:
        notes: List[str] = []
        source = ""
        files: List[str] = []
        status = "missing"

        if track.track_id and track.track_id in fallback_success:
            files = fallback_success[track.track_id]
            status = "downloaded"
            source = "yt-dlp fallback"
        else:
            files = match_files(track, unprocessed_files)
            if files:
                status = "downloaded"
                source = "file match"

        if status != "downloaded":
            if track.track_id and track.track_id in spotdl_fail_ids:
                status = "failed"
                source = "spotdl"
            elif track.track_id and track.track_id in fallback_fail_ids:
                status = "failed"
                source = "yt-dlp fallback"
            else:
                status = "missing"
                source = "not found"

        if not track.track_id:
            notes.append("No track id in source playlist data.")
        if not files and status == "downloaded":
            notes.append("Marked downloaded via log, but no files were found.")

        results.append(TrackResult(track=track, status=status, source=source, files=files, notes=notes))
    return results


def build_summary(results: List[TrackResult]) -> dict[str, int]:
    total = len(results)
    downloaded = sum(1 for r in results if r.status == "downloaded")
    failed = sum(1 for r in results if r.status == "failed")
    missing = sum(1 for r in results if r.status == "missing")
    return {"total": total, "downloaded": downloaded, "failed": failed, "missing": missing}


def _html_escape(text: str | None) -> str:
    return html.escape(text or "")


def _load_template() -> str:
    template_path = Path(__file__).resolve().parent / "templates" / "spotdl_report.html"
    return template_path.read_text(encoding="utf-8")


def render_html(report_data: dict) -> str:
    generated_at = report_data["generated_at"]
    manifest_path = report_data["manifest"]
    entries = report_data["entries"]

    html_parts: List[str] = []
    html_parts.append("<p class='muted'>Generated at {generated_at} from manifest {manifest}</p>".format(
        generated_at=html.escape(generated_at),
        manifest=html.escape(manifest_path),
    ))

    # Summary table
    html_parts.append("<h2>Summary</h2>")
    html_parts.append("<table class='summary-table'>")
    html_parts.append("<thead><tr><th>Playlist Root</th><th>Downloaded</th><th>Failed</th><th>Missing</th><th>Total</th></tr></thead><tbody>")
    for entry in entries:
        summary = entry["summary"]
        root = entry["root"]
        html_parts.append(
            "<tr>"
            f"<td>{html.escape(root)}</td>"
            f"<td>{summary['downloaded']}</td>"
            f"<td>{summary['failed']}</td>"
            f"<td>{summary['missing']}</td>"
            f"<td>{summary['total']}</td>"
            "</tr>"
        )
    html_parts.append("</tbody></table>")

    for entry in entries:
        root = entry["root"]
        playlist_url = entry["playlist_url"]
        summary = entry["summary"]
        header = (
            f"<span class='root'>{html.escape(root)}</span>"
            f"<span class='counts'>{summary['downloaded']} downloaded · {summary['failed']} failed · {summary['missing']} missing · {summary['total']} total</span>"
        )
        html_parts.append("<section class='playlist'>")
        html_parts.append("<details>")
        html_parts.append(f"<summary>{header}</summary>")
        if playlist_url:
            html_parts.append(
                f"<div class='muted'>Playlist: <a href='{html.escape(playlist_url)}'>{html.escape(playlist_url)}</a></div>"
            )

        html_parts.append("<table>")
        html_parts.append(
            "<thead><tr><th>#</th><th>Title</th><th>Artists</th><th>Status</th><th>Source</th><th>Files</th><th>Notes</th></tr></thead><tbody>"
        )
        status_order = {"missing": 0, "failed": 1, "downloaded": 2}
        sorted_tracks = sorted(
            entry["tracks"],
            key=lambda r: (status_order.get(r.status, 99), r.track.name.lower()),
        )
        for idx, result in enumerate(sorted_tracks, start=1):
            tr_class = f"status-{result.status}"
            badge_class = f"badge badge-{result.status}"
            artists = ", ".join(result.track.artists)
            file_display = "<br>".join(html.escape(f) for f in result.files) if result.files else "&mdash;"
            notes_display = "<br>".join(html.escape(n) for n in result.notes) if result.notes else "&mdash;"
            title = _html_escape(result.track.name)
            if result.track.url:
                title = f"<a href='{html.escape(result.track.url)}'>{title}</a>"
            html_parts.append(
                f"<tr class='{tr_class}'>"
                f"<td>{idx}</td>"
                f"<td>{title}</td>"
                f"<td>{html.escape(artists)}</td>"
                f"<td><span class='{badge_class}'>{result.status}</span></td>"
                f"<td>{html.escape(result.source)}</td>"
                f"<td>{file_display}</td>"
                f"<td class='small'>{notes_display}</td>"
                "</tr>"
            )
        html_parts.append("</tbody></table>")

        if entry.get("orphan_files"):
            html_parts.append("<p class='muted small'>Files in unprocessed/ not matched to playlist:</p>")
            html_parts.append("<ul>")
            for fp in entry["orphan_files"]:
                html_parts.append(f"<li class='small'>{html.escape(fp)}</li>")
            html_parts.append("</ul>")

        html_parts.append("</details></section>")

    content = "\n".join(html_parts)
    template = _load_template()
    return (
        template.replace("{{CONTENT}}", content)
        .replace("{{GENERATED_AT}}", html.escape(generated_at))
        .replace("{{MANIFEST_PATH}}", html.escape(manifest_path))
    )


_DB_STATUS_MAP: dict[str, tuple[str, str]] = {
    "spotdl_ok": ("downloaded", "spotdl"),
    "auto_ok": ("downloaded", "yt-dlp fallback"),
    "manual_ok": ("downloaded", "manual URL"),
    "demucs_done": ("downloaded", "demucs"),
    "spotdl_failed": ("failed", "spotdl"),
    "auto_failed": ("failed", "yt-dlp fallback"),
    "manual_pending": ("failed", "manual queue"),
    "pending": ("missing", "not found"),
    "ignored": ("missing", "ignored"),
}


def _from_db_status(status: str) -> tuple[str, str]:
    return _DB_STATUS_MAP.get(status, ("missing", status))


def _generate_report_from_db(
    manifest_entries: Iterable[tuple[str, str]],
    manifest_path: Path,
    db_path: Path,
) -> dict:
    entries_data: List[dict] = []
    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for playlist_url, root_str in manifest_entries:
            root = Path(root_str).expanduser().resolve()
            row = conn.execute("SELECT id FROM playlists WHERE root_path = ?", (str(root),)).fetchone()
            if not row:
                entries_data.append(
                    {
                        "root": str(root),
                        "playlist_url": playlist_url,
                        "tracks": [],
                        "summary": {"total": 0, "downloaded": 0, "failed": 0, "missing": 0},
                        "orphan_files": [],
                    }
                )
                continue

            playlist_id = int(row["id"])
            rows = conn.execute(
                """
                SELECT
                  t.id AS track_id,
                  t.title AS title,
                  COALESCE(t.primary_artist, 'Unknown Artist') AS primary_artist,
                  t.spotify_url AS spotify_url,
                  ts.status AS state_status,
                  ts.resolved_path AS resolved_path,
                  ts.last_error AS last_error
                FROM playlist_tracks pt
                JOIN tracks t ON t.id = pt.track_id
                LEFT JOIN track_state ts
                  ON ts.playlist_id = pt.playlist_id AND ts.track_id = pt.track_id
                WHERE pt.playlist_id = ? AND pt.active = 1
                ORDER BY pt.position ASC
                """,
                (playlist_id,),
            ).fetchall()

            results: List[TrackResult] = []
            for r in rows:
                mapped_status, source = _from_db_status(str(r["state_status"] or "pending"))
                files = [str(r["resolved_path"])] if r["resolved_path"] else []
                notes: List[str] = []
                if r["last_error"] and mapped_status != "downloaded":
                    notes.append(str(r["last_error"]))
                entry = TrackEntry(
                    name=str(r["title"] or "Unknown Title"),
                    artists=[str(r["primary_artist"] or "Unknown Artist")],
                    track_id=str(r["track_id"]) if r["track_id"] else None,
                    url=str(r["spotify_url"]) if r["spotify_url"] else None,
                )
                results.append(TrackResult(track=entry, status=mapped_status, source=source, files=files, notes=notes))

            summary = build_summary(results)
            unprocessed_files = list_audio_files(root / "unprocessed")
            matched_files = set(fp for r in results for fp in r.files)
            orphan_files = [str(fp) for fp in unprocessed_files if str(fp) not in matched_files]

            entries_data.append(
                {
                    "root": str(root),
                    "playlist_url": playlist_url,
                    "tracks": results,
                    "summary": summary,
                    "orphan_files": orphan_files,
                }
            )
    finally:
        conn.close()

    return {
        "generated_at": generated_at,
        "manifest": str(manifest_path),
        "entries": entries_data,
    }


def generate_report(
    manifest_entries: Iterable[tuple[str, str]],
    manifest_path: Path,
    playlist_json_name: str = "playlist.json",
    download_name: str = "playlist.download.spotdl",
    spotdl_errors_name: str = "spotdl.errors.json",
    fallback_errors_name: str = "ytdlp_fallback.errors.jsonl",
    fallback_success_name: str = "ytdlp_fallback.success.jsonl",
    db_path: Path | None = None,
) -> dict:
    if db_path is not None and db_path.is_file():
        return _generate_report_from_db(
            manifest_entries=manifest_entries,
            manifest_path=manifest_path,
            db_path=db_path,
        )

    entries_data: List[dict] = []
    generated_at = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for playlist_url, root_str in manifest_entries:
        root = Path(root_str).expanduser()
        tracks = load_tracks(root, download_name, playlist_json_name)

        spotdl_fail_ids = load_spotdl_errors(root, spotdl_errors_name)
        fallback_fail_ids = load_fallback_errors(root, fallback_errors_name)
        fallback_success = load_fallback_success(root, fallback_success_name)
        unprocessed_files = list_audio_files(root / "unprocessed")

        results = classify_tracks(tracks, unprocessed_files, spotdl_fail_ids, fallback_fail_ids, fallback_success)
        summary = build_summary(results)

        matched_files = set(fp for r in results for fp in r.files)
        orphan_files = [str(fp) for fp in unprocessed_files if str(fp) not in matched_files]

        entries_data.append(
            {
                "root": str(root),
                "playlist_url": playlist_url,
                "tracks": results,
                "summary": summary,
                "orphan_files": orphan_files,
            }
        )

    report = {
        "generated_at": generated_at,
        "manifest": str(manifest_path),
        "entries": entries_data,
    }
    return report


def write_html_report(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_html(report)
    output_path.write_text(html_text, encoding="utf-8")

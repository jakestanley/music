from __future__ import annotations

import datetime as _dt
import html
import os
from pathlib import Path
from typing import Dict, List


def _load_template() -> str:
    template_path = Path(__file__).resolve().parent / "templates" / "demucs_report.html"
    return template_path.read_text(encoding="utf-8")


def _is_four_stem_dir(path: Path) -> bool:
    needed = {"vocals.wav", "drums.wav", "bass.wav", "other.wav"}
    return all((path / name).is_file() for name in needed)


def _is_two_stem_dir(path: Path) -> bool:
    needed = {"vocals.wav", "no_vocals.wav"}
    return all((path / name).is_file() for name in needed)


def _track_status(root: str, track_name: str, mode: str) -> Dict[str, str]:
    all_ok = _is_four_stem_dir(Path(root) / "all" / track_name)
    vocals_ok = _is_two_stem_dir(Path(root) / "vocals" / track_name)
    if mode == "4":
        status = "done" if all_ok else "missing"
    elif mode == "2":
        status = "done" if vocals_ok else "missing"
    else:
        status = "done" if (all_ok and vocals_ok) else "missing"
    return {
        "status": status,
        "four": "yes" if all_ok else "no",
        "two": "yes" if vocals_ok else "no",
    }


def _collect_root(root: str, mode: str) -> Dict:
    base_dir = Path(root) / "unprocessed"
    tracks: List[Dict[str, str]] = []
    if base_dir.is_dir():
        for name in sorted(os.listdir(base_dir)):
            if not name.lower().endswith(".mp3") or name.startswith("._"):
                continue
            track_name = os.path.splitext(name)[0]
            status = _track_status(root, track_name, mode)
            tracks.append(
                {
                    "name": track_name,
                    "status": status["status"],
                    "four": status["four"],
                    "two": status["two"],
                }
            )
    missing_first = sorted(tracks, key=lambda t: (0 if t["status"] == "missing" else 1, t["name"].lower()))
    done = sum(1 for t in missing_first if t["status"] == "done")
    missing = len(missing_first) - done
    return {"root": root, "tracks": missing_first, "done": done, "missing": missing, "total": len(missing_first)}


def render_demucs_html(entries: List[Dict], mode: str) -> str:
    parts: List[str] = []
    parts.append(f"<p class='muted'>Generated {_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · mode {html.escape(mode)}</p>")
    parts.append("<table class='summary-table'><thead><tr><th>Root</th><th>Done</th><th>Missing</th><th>Total</th></tr></thead><tbody>")
    for entry in entries:
        parts.append(
            "<tr>"
            f"<td>{html.escape(entry['root'])}</td>"
            f"<td>{entry['done']}</td>"
            f"<td>{entry['missing']}</td>"
            f"<td>{entry['total']}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")

    for entry in entries:
        parts.append("<section class='root'>")
        parts.append("<details>")
        parts.append(
            "<summary>"
            f"<span>{html.escape(entry['root'])}</span>"
            f"<span class='counts'>{entry['done']} done · {entry['missing']} missing · {entry['total']} total</span>"
            "</summary>"
        )
        parts.append(
            "<table><thead><tr><th>#</th><th>Track</th><th>Status</th><th>4-stem</th><th>2-stem</th></tr></thead><tbody>"
        )
        for idx, track in enumerate(entry["tracks"], start=1):
            parts.append(
                "<tr>"
                f"<td>{idx}</td>"
                f"<td>{html.escape(track['name'])}</td>"
                f"<td>{track['status']}</td>"
                f"<td>{track['four']}</td>"
                f"<td>{track['two']}</td>"
                "</tr>"
            )
        parts.append("</tbody></table>")
        parts.append("</details></section>")

    return _load_template().replace("{{CONTENT}}", "\n".join(parts))


def write_demucs_report(roots: List[str], mode: str, output_path: Path) -> None:
    entries = [_collect_root(root, mode) for root in roots]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_demucs_html(entries, mode), encoding="utf-8")

#!/usr/bin/env python3
"""iPod auto-sync: download pending tracks then transfer to iPod, with Telegram notifications."""

import os
import subprocess
import sys
from pathlib import Path

# --- Venv bootstrap: re-exec under the venv python if not already there ---
_ROOT = Path(__file__).parent   # ipod/
_PROJ = _ROOT.parent            # project root
_VENV = _PROJ / ".venv"
_VENV_PYTHON = _VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

if not Path(sys.executable).is_relative_to(_VENV):
    if not _VENV_PYTHON.exists():
        print("Initialising venv...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(_VENV)], check=True)
        subprocess.run(
            [str(_VENV_PYTHON), "-m", "pip", "install", "-r", str(_PROJ / "requirements.txt")],
            check=True,
        )
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)
# --------------------------------------------------------------------------

import json

sys.path.insert(0, str(_PROJ))

from scripts.core.env import load_env

load_env(str(_PROJ / ".env"))

from bot.notify import notify  # noqa: E402


def _pending_info(manifest_path: Path) -> tuple[int, list[str]]:
    """Count resolved (not-yet-downloaded) tracks and collect playlist names."""
    entries = json.loads(manifest_path.read_text())
    pending = 0
    names: list[str] = []
    for entry in entries:
        root = Path(entry["root"])
        names.append(root.name)
        state_file = root / "state.json"
        if not state_file.exists():
            continue
        state = json.loads(state_file.read_text())
        for track in state.get("tracks", {}).values():
            if track.get("status") == "resolved":
                pending += 1
    return pending, names


def main() -> None:
    manifest = _ROOT / "ipod-manifest.json"

    pending, playlist_names = _pending_info(manifest)
    playlists_str = ", ".join(playlist_names) or "none configured"

    notify(
        f"🎵 <b>iPod detected</b> — starting sync\n"
        f"Playlists: {playlists_str}\n"
        # Currently broken:
        # f"Tracks to download: {pending}"
    )

    output_lines: list[str] = []
    returncode = 0

    try:
        with subprocess.Popen(
            [str(_ROOT / "ipod.sh")],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        ) as proc:
            assert proc.stdout
            for line in proc.stdout:
                print(line, end="", flush=True)
                output_lines.append(line)
        returncode = proc.returncode
    except Exception as exc:
        notify(f"❌ <b>Sync error</b>: {exc}")
        sys.exit(1)

    if returncode != 0:
        tail = "".join(output_lines[-20:]).strip()
        notify(f"❌ <b>Sync failed</b> (exit {returncode})\n<pre>{tail[-400:]}</pre>")
        sys.exit(returncode)

    # ipod.sh outputs: "Done: N added, K already synced"
    added = skipped = 0
    for line in reversed(output_lines):
        if line.strip().startswith("Done:"):
            parts = line.strip().split()
            try:
                added = int(parts[1])
                skipped = int(parts[3])
            except (IndexError, ValueError):
                pass
            break

    notify(
        f"✅ <b>Sync complete</b> — safe to disconnect 📱\n"
        f"Added: {added} track(s)\n"
        f"Already synced: {skipped}"
    )


if __name__ == "__main__":
    main()

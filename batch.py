"""
Batch processor: runs the full pipeline for every playlist in manifest.json.

Bootstraps the venv automatically on first run. Intended to be called by cron:
    0 * * * * python3 /path/to/batch.py >> /path/to/batch.log 2>&1

Usage:
    python3 batch.py [--manifest manifest.json] [--demucs-mode 4|2|both|skip]
"""

import os
import subprocess
import sys
from pathlib import Path

# --- Venv bootstrap: re-exec under the venv python if not already there ---
_ROOT = Path(__file__).parent
_VENV = _ROOT / ".venv"
_VENV_PYTHON = _VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

if Path(sys.executable).resolve() != _VENV_PYTHON.resolve():
    if not _VENV_PYTHON.exists():
        print("Initialising venv...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(_VENV)], check=True)
        subprocess.run(
            [str(_VENV_PYTHON), "-m", "pip", "install", "-r", str(_ROOT / "requirements.txt")],
            check=True,
        )
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON)] + sys.argv)
# --------------------------------------------------------------------------

import argparse
import datetime
import json


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run(label: str, cmd: list[str]) -> int:
    print(f"[{_now()}] {label}: {' '.join(cmd)}", flush=True)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"[{_now()}] {label}: exited {result.returncode}", flush=True)
    return result.returncode


def main() -> int:
    _STEPS = ["scrape", "resolve", "download", "demucs"]

    parser = argparse.ArgumentParser(description="Run the full pipeline for all playlists in the manifest.")
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument(
        "--demucs-mode",
        default="both",
        choices=["4", "2", "both", "skip"],
    )
    parser.add_argument(
        "--until",
        choices=_STEPS,
        default=_STEPS[-1],
        help="Stop after this step (default: demucs). e.g. --until resolve",
    )
    args = parser.parse_args()
    stop_after = _STEPS.index(args.until)

    manifest_path = _ROOT / args.manifest
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        entries = [entries]

    py = sys.executable
    overall_ok = True

    for entry in entries:
        url = entry.get("playlist_url") or entry.get("url", "")
        root = entry.get("root", "")
        if not url or not root:
            print(f"[{_now()}] Skipping invalid manifest entry: {entry}", flush=True)
            continue

        print(f"\n[{_now()}] === {root} ===", flush=True)
        playlist_ok = True

        # Scraper is the only hard stop — without metadata nothing else can run.
        if _run("scrape", [py, str(_ROOT / "scraper.py"), url, root]) != 0:
            overall_ok = False
            continue
        if stop_after == 0:
            continue

        # Resolver, download, and demucs all proceed regardless of partial failures.
        # Each step reads state.json and handles an empty work set gracefully.
        remaining = [
            ("resolve",  [py, str(_ROOT / "resolver.py"),  root]),
            ("download", [py, str(_ROOT / "downloader.py"), root]),
            *([] if args.demucs_mode == "skip" else [
                ("demucs", [py, str(_ROOT / "demucs.py"), root, args.demucs_mode])
            ]),
        ]
        for i, (label, cmd) in enumerate(remaining, start=1):
            if _run(label, cmd) != 0:
                playlist_ok = False
            if stop_after == i:
                break

        if not playlist_ok:
            overall_ok = False

    print(f"\n[{_now()}] Batch {'complete' if overall_ok else 'finished with errors'}.", flush=True)
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

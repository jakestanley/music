import os
import shutil
import subprocess
from typing import Iterable

from scripts.core.paths import ensure_dir


def run_local(
    files: Iterable[str],
    mode: str,
    demucs_model: str,
    base_dir: str,
    all_dir: str,
    vocals_dir: str,
) -> None:
    files = list(files)
    if not files:
        print(f"No MP3 files found in {base_dir}")
        return

    tmp_dir = os.path.join(os.path.expanduser("~"), "Music", ".demucs_tmp")
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    ensure_dir(tmp_dir)
    ensure_dir(base_dir)
    ensure_dir(all_dir)
    ensure_dir(vocals_dir)

    print(f"Local demucs: processing {len(files)} files in {base_dir}")

    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        all_track_dir = os.path.join(all_dir, name)
        vocals_track_dir = os.path.join(vocals_dir, name)

        if mode in {"4", "both"}:
            if os.path.isfile(os.path.join(all_track_dir, "vocals.wav")):
                print(f"✓ 4-stem exists: {name}")
            else:
                print(f"→ Demucs 4-stem: {name}")
                subprocess.run(["demucs", "-n", demucs_model, "-o", tmp_dir, path], check=True)
                shutil.move(os.path.join(tmp_dir, demucs_model, name), all_track_dir)
                shutil.rmtree(os.path.join(tmp_dir, demucs_model), ignore_errors=True)

        if mode in {"2", "both"}:
            if os.path.isfile(os.path.join(vocals_track_dir, "vocals.wav")):
                print(f"✓ 2-stem exists: {name}")
            else:
                print(f"→ Demucs vocals: {name}")
                subprocess.run(
                    ["demucs", "-n", demucs_model, "--two-stems=vocals", "-o", tmp_dir, path],
                    check=True,
                )
                shutil.move(os.path.join(tmp_dir, demucs_model, name), vocals_track_dir)
                shutil.rmtree(os.path.join(tmp_dir, demucs_model), ignore_errors=True)

    shutil.rmtree(tmp_dir, ignore_errors=True)

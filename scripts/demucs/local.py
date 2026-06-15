import datetime as _dt
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable

from scripts.core.paths import ensure_dir


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
        handle.write("\n")


def run_local(
    files: Iterable[str],
    mode: str,
    demucs_model: str,
    base_dir: str,
    all_dir: str,
    vocals_dir: str,
    on_file_done: Callable[[str], None] | None = None,
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
    errors_log_path = Path(base_dir).parent / "demucs.errors.jsonl"

    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        all_track_dir = os.path.join(all_dir, name)
        vocals_track_dir = os.path.join(vocals_dir, name)

        if mode in {"4", "both"}:
            if os.path.isfile(os.path.join(all_track_dir, "vocals.wav")):
                print(f"✓ 4-stem exists: {name}")
            else:
                print(f"→ Demucs 4-stem: {name}")
                cmd = ["demucs", "-n", demucs_model, "-o", tmp_dir, path]
                try:
                    subprocess.run(cmd, check=True)
                    shutil.move(os.path.join(tmp_dir, demucs_model, name), all_track_dir)
                    shutil.rmtree(os.path.join(tmp_dir, demucs_model), ignore_errors=True)
                except subprocess.CalledProcessError as exc:
                    print(f"ERROR: local demucs 4-stem failed for {name}; continuing")
                    _append_jsonl(
                        errors_log_path,
                        {
                            "timestamp": _utc_now_iso(),
                            "source": "demucs_local",
                            "file_path": path,
                            "file_name": Path(path).name,
                            "mode": "4",
                            "model": demucs_model,
                            "command": cmd,
                            "exit_code": exc.returncode,
                            "error_type": "job_failed",
                        },
                    )
                    continue

        if mode in {"2", "both"}:
            if os.path.isfile(os.path.join(vocals_track_dir, "vocals.wav")):
                print(f"✓ 2-stem exists: {name}")
            else:
                print(f"→ Demucs vocals: {name}")
                cmd = ["demucs", "-n", demucs_model, "--two-stems=vocals", "-o", tmp_dir, path]
                try:
                    subprocess.run(cmd, check=True)
                    shutil.move(os.path.join(tmp_dir, demucs_model, name), vocals_track_dir)
                    shutil.rmtree(os.path.join(tmp_dir, demucs_model), ignore_errors=True)
                except subprocess.CalledProcessError as exc:
                    print(f"ERROR: local demucs vocals failed for {name}; continuing")
                    _append_jsonl(
                        errors_log_path,
                        {
                            "timestamp": _utc_now_iso(),
                            "source": "demucs_local",
                            "file_path": path,
                            "file_name": Path(path).name,
                            "mode": "2",
                            "model": demucs_model,
                            "command": cmd,
                            "exit_code": exc.returncode,
                            "error_type": "job_failed",
                        },
                    )
                    continue

        if on_file_done is not None:
            on_file_done(path)

    shutil.rmtree(tmp_dir, ignore_errors=True)

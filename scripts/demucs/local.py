import datetime as _dt
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable, List

from scripts.core.paths import ensure_dir
from scripts.demucs.ensemble import blend_stems, parse_model_list
from scripts.demucs.formats import FOUR_STEMS, TWO_STEMS, WAV, StemFormat, has_stem


def _utc_now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False)
        handle.write("\n")


def _run_stem(
    models: List[str],
    cli_extra_args: List[str],
    path: str,
    tmp_dir: str,
    dest_track_dir: str,
    stems: tuple[str, ...],
    stem_format: StemFormat,
    mode_label: str,
    errors_log_path: Path,
) -> bool:
    """Run one or more demucs models against `path` and blend matching stems into dest_track_dir.

    Demucs always writes WAV to tmp_dir; blend_stems averages (for ensembles)
    and encodes to stem_format in one ffmpeg pass, so there is a single lossy step.
    """
    name = os.path.splitext(os.path.basename(path))[0]
    model_dirs: List[Path] = []
    for model in models:
        cmd = ["demucs", "-n", model, *cli_extra_args, "-o", tmp_dir, path]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            print(f"ERROR: local demucs {mode_label} failed for {name} (model={model}); continuing")
            _append_jsonl(
                errors_log_path,
                {
                    "timestamp": _utc_now_iso(),
                    "source": "demucs_local",
                    "file_path": path,
                    "file_name": Path(path).name,
                    "mode": mode_label,
                    "model": ",".join(models),
                    "command": cmd,
                    "exit_code": exc.returncode,
                    "error_type": "job_failed",
                },
            )
            for prior_model in models[: models.index(model)]:
                shutil.rmtree(os.path.join(tmp_dir, prior_model), ignore_errors=True)
            return False
        model_dirs.append(Path(tmp_dir) / model / name)

    dest = Path(dest_track_dir)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for src_name, dest_name in zip(WAV.files(stems), stem_format.files(stems)):
        blend_stems([d / src_name for d in model_dirs], dest / dest_name, stem_format.mp3_bitrate)
    for model in models:
        shutil.rmtree(os.path.join(tmp_dir, model), ignore_errors=True)

    if len(models) > 1:
        print(f"  → blended {mode_label} across {len(models)} models: {', '.join(models)}")
    return True


def run_local(
    files: Iterable[str],
    mode: str,
    demucs_model: str,
    base_dir: str,
    all_dir: str,
    vocals_dir: str,
    on_file_done: Callable[[str], None] | None = None,
    stem_format: StemFormat = WAV,
) -> None:
    files = list(files)
    if not files:
        print(f"No MP3 files found in {base_dir}")
        return

    models = parse_model_list(os.environ.get("DEMUCS_ENSEMBLE_MODELS"), demucs_model)

    tmp_dir = os.path.join(os.path.expanduser("~"), "Music", ".demucs_tmp")
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    ensure_dir(tmp_dir)
    ensure_dir(base_dir)
    ensure_dir(all_dir)
    ensure_dir(vocals_dir)

    print(f"Local demucs: processing {len(files)} files in {base_dir} (models: {', '.join(models)}, format: {stem_format.describe()})")
    errors_log_path = Path(base_dir).parent / "demucs.errors.jsonl"

    for path in files:
        name = os.path.splitext(os.path.basename(path))[0]
        file_format = stem_format.for_source(path)
        all_track_dir = os.path.join(all_dir, name)
        vocals_track_dir = os.path.join(vocals_dir, name)

        if mode in {"4", "both"}:
            if has_stem(all_track_dir):
                print(f"✓ 4-stem exists: {name}")
            else:
                print(f"→ Demucs 4-stem: {name}")
                if not _run_stem(
                    models,
                    [],
                    path,
                    tmp_dir,
                    all_track_dir,
                    FOUR_STEMS,
                    file_format,
                    "4",
                    errors_log_path,
                ):
                    continue

        if mode in {"2", "both"}:
            if has_stem(vocals_track_dir):
                print(f"✓ 2-stem exists: {name}")
            else:
                print(f"→ Demucs vocals: {name}")
                if not _run_stem(
                    models,
                    ["--two-stems=vocals"],
                    path,
                    tmp_dir,
                    vocals_track_dir,
                    TWO_STEMS,
                    file_format,
                    "2",
                    errors_log_path,
                ):
                    continue

        if on_file_done is not None:
            on_file_done(path)

    shutil.rmtree(tmp_dir, ignore_errors=True)

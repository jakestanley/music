import os
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import requests

from scripts.core.paths import ensure_dir
from scripts.upsnap.batch import ensure_awake as ensure_upsnap_awake
from scripts.upsnap.batch import require_ready as require_upsnap_ready


@dataclass
class RootContext:
    root: str
    base_dir: str
    all_dir: str
    vocals_dir: str


def normalize_windows_name(name: str) -> str:
    while name and name[-1] in {" ", "."}:
        name = name[:-1]
    return name


def _read_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SystemExit(f"Invalid {name}: expected integer")


def _read_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"Invalid {name}: expected number")


def _submit_job(
    base_url: str,
    files: List[str],
    mode: str,
    model: str,
    job_name: str,
    verify: str | bool,
) -> str:
    url = f"{base_url}/api/jobs"
    data = {"mode": mode, "model": model, "job_name": job_name}
    opened: List[Tuple[str, Tuple[str, object, str]]] = []
    try:
        for path in files:
            filename = os.path.basename(path)
            handle = open(path, "rb")
            opened.append(("files", (filename, handle, "audio/mpeg")))
        resp = requests.post(url, data=data, files=opened, timeout=120, verify=verify)
        if not resp.ok:
            raise SystemExit(f"Demucs API request failed: HTTP {resp.status_code} {resp.text}")
        payload = resp.json()
        job_id = payload.get("id")
        if not job_id:
            raise SystemExit("Demucs API response missing job id")
        return str(job_id)
    finally:
        for _, (_, handle, _) in opened:
            try:
                handle.close()
            except Exception:
                pass


def _wait_for_job(
    base_url: str,
    job_id: str,
    poll_seconds: float,
    timeout_seconds: float,
    verify: str | bool,
) -> Dict:
    url = f"{base_url}/api/jobs/{job_id}"
    start = time.time()
    while True:
        resp = requests.get(url, timeout=30, verify=verify)
        if not resp.ok:
            raise SystemExit(f"Demucs API status failed: HTTP {resp.status_code} {resp.text}")
        payload = resp.json()
        status = payload.get("status")
        if status in {"succeeded", "failed"}:
            return payload
        if timeout_seconds and (time.time() - start) > timeout_seconds:
            raise SystemExit(f"Timed out waiting for Demucs job {job_id}")
        time.sleep(poll_seconds)


def _download_output(base_url: str, job_id: str, dest_zip: Path, verify: str | bool) -> None:
    url = f"{base_url}/api/jobs/{job_id}/output"
    resp = requests.get(url, stream=True, timeout=120, verify=verify)
    if resp.status_code != 200:
        raise SystemExit(f"Demucs API output download failed: HTTP {resp.status_code} {resp.text}")
    with dest_zip.open("wb") as handle:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)


def _iter_stem_dirs(root: Path) -> Iterable[Path]:
    for current, _, files in os.walk(root):
        if "vocals.wav" in files:
            yield Path(current)


def _classify_dir(path: Path) -> Tuple[bool, bool]:
    files = {p.name for p in path.iterdir() if p.is_file()}
    is_four = {"vocals.wav", "drums.wav", "bass.wav", "other.wav"}.issubset(files)
    is_two = {"vocals.wav", "no_vocals.wav"}.issubset(files)
    return is_four, is_two


def _copy_stem_dir(src: Path, dest_root: str) -> None:
    dest = Path(dest_root) / src.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)


def _extract_outputs(zip_path: Path, ctx: RootContext, mode: str) -> None:
    with tempfile.TemporaryDirectory(prefix="demucs_api_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        with zipfile.ZipFile(zip_path) as zip_handle:
            zip_handle.extractall(tmp_dir_path)

        ensure_dir(ctx.all_dir)
        ensure_dir(ctx.vocals_dir)

        for stem_dir in _iter_stem_dirs(tmp_dir_path):
            is_four, is_two = _classify_dir(stem_dir)
            if mode in {"4", "both"} and is_four:
                _copy_stem_dir(stem_dir, ctx.all_dir)
            if mode in {"2", "both"} and is_two:
                _copy_stem_dir(stem_dir, ctx.vocals_dir)


def _is_invalid_mp3_error(exc: SystemExit) -> bool:
    return "Invalid mp3 data" in str(exc)


def _run_one_batch(
    base_url: str,
    ctx: RootContext,
    batch_files: List[str],
    mode: str,
    model: str,
    job_name: str,
    verify: str | bool,
    poll_seconds: float,
    timeout_seconds: float,
) -> None:
    job_id = _submit_job(base_url, batch_files, mode, model, job_name, verify)
    job = _wait_for_job(base_url, job_id, poll_seconds, timeout_seconds, verify)
    status = job.get("status")
    if status != "succeeded":
        message = job.get("message") or job.get("error") or "unknown error"
        raise SystemExit(f"Demucs API job failed: {job_id} ({message})")

    with tempfile.TemporaryDirectory(prefix="demucs_api_zip_") as tmp_zip_dir:
        zip_path = Path(tmp_zip_dir) / f"{job_id}.zip"
        _download_output(base_url, job_id, zip_path, verify)
        _extract_outputs(zip_path, ctx, mode)


def run_windows(
    ctx: RootContext,
    missing_files: List[str],
    mode: str,
    clean_windows: bool,
) -> None:
    del clean_windows
    base_url = os.environ.get("DEMUCS_API_URL", "https://demucs.stanley.arpa").rstrip("/")
    ca_cert = os.environ.get("DEMUCS_API_CA_CERT")
    verify: str | bool = True
    if ca_cert:
        verify = ca_cert
    model = (
        os.environ.get("DEMUCS_API_MODEL")
        or os.environ.get("WINDOWS_DEMUCS_MODEL")
        or os.environ.get("DEMUCS_MODEL")
        or "htdemucs"
    )
    batch_size = _read_env_int("DEMUCS_API_BATCH_SIZE", _read_env_int("WINDOWS_BATCH_SIZE", 10))
    poll_seconds = _read_env_float("DEMUCS_API_POLL_SECS", 5.0)
    timeout_seconds = _read_env_float("DEMUCS_API_TIMEOUT_SECS", 3600.0)

    if batch_size < 1:
        raise SystemExit("Invalid DEMUCS_API_BATCH_SIZE: expected integer >= 1")

    # Drop macOS resource fork files and non-mp3s to avoid API errors.
    missing_files = [
        path
        for path in missing_files
        if path.lower().endswith(".mp3") and not Path(path).name.startswith("._")
    ]

    if not missing_files:
        print(f"All requested stems exist for {ctx.root}; skipping remote run.")
        return

    ensure_upsnap_awake()

    total_batches = (len(missing_files) + batch_size - 1) // batch_size
    batch_start = 0
    batch_index = 0
    skipped_invalid = 0

    while batch_start < len(missing_files):
        batch_index += 1
        batch_files = missing_files[batch_start : batch_start + batch_size]
        batch_start += batch_size

        if not batch_files:
            continue

        require_upsnap_ready()
        print(f"Batch {batch_index}/{total_batches}: submitting {len(batch_files)} files to {base_url}")
        job_name = f"{Path(ctx.root).name} batch {batch_index}/{total_batches}"
        try:
            _run_one_batch(
                base_url=base_url,
                ctx=ctx,
                batch_files=batch_files,
                mode=mode,
                model=model,
                job_name=job_name,
                verify=verify,
                poll_seconds=poll_seconds,
                timeout_seconds=timeout_seconds,
            )
            print(f"Batch {batch_index}/{total_batches}: output downloaded and installed.")
            continue
        except SystemExit as exc:
            if not _is_invalid_mp3_error(exc):
                raise
            if len(batch_files) == 1:
                skipped_invalid += 1
                print(f"Skipping invalid MP3: {batch_files[0]}")
                continue
            print("Batch rejected due to invalid MP3 data; retrying files individually.")

        for file_path in batch_files:
            single_job_name = f"{Path(ctx.root).name} file {Path(file_path).name}"
            try:
                _run_one_batch(
                    base_url=base_url,
                    ctx=ctx,
                    batch_files=[file_path],
                    mode=mode,
                    model=model,
                    job_name=single_job_name,
                    verify=verify,
                    poll_seconds=poll_seconds,
                    timeout_seconds=timeout_seconds,
                )
                print(f"Processed: {file_path}")
            except SystemExit as exc:
                if _is_invalid_mp3_error(exc):
                    skipped_invalid += 1
                    print(f"Skipping invalid MP3: {file_path}")
                    continue
                raise

    if skipped_invalid:
        print(f"Done with warnings: skipped {skipped_invalid} invalid MP3 file(s).")

import os
import shutil
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple, TypedDict, cast

import requests

from scripts.core.paths import ensure_dir
from scripts.upsnap.batch import ensure_awake as ensure_upsnap_awake
from scripts.upsnap.batch import require_ready as require_upsnap_ready


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class JobResult(TypedDict, total=False):
    mode: str
    cache_hit: bool
    error: str | None
    rate_seconds_per_second: float | None


class JobInputFile(TypedDict, total=False):
    original_filename: str
    stored_name: str
    size_bytes: int
    sha256: str
    results: List[JobResult]


class JobInput(TypedDict, total=False):
    file: JobInputFile


class JobDetails(TypedDict, total=False):
    id: str
    status: str
    message: str
    error: str
    input: JobInput
    output: Dict[str, Any]


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


def canonical_output_name(name: str) -> str:
    base = normalize_windows_name(name)
    if base.lower().endswith(".mp3"):
        base = base[:-4]

    chars: List[str] = []
    previous_was_replacement = False
    for char in base:
        if char.isalnum() or char in {"-", "_"}:
            chars.append(char)
            previous_was_replacement = False
            continue
        if not previous_was_replacement:
            chars.append("_")
            previous_was_replacement = True

    canonical = "".join(chars)
    return canonical or base


def _read_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise SystemExit(f"Invalid {name}: expected number")


def _to_optional_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_job_details(payload: Dict[str, Any]) -> JobDetails:
    job: JobDetails = cast(JobDetails, dict(payload))
    raw_input = payload.get("input")
    if isinstance(raw_input, dict):
        input_entry = cast(JobInput, dict(raw_input))
        raw_file = raw_input.get("file")
        if isinstance(raw_file, dict):
            file_entry = cast(JobInputFile, dict(raw_file))
            raw_results = raw_file.get("results")
            if isinstance(raw_results, list):
                results: List[JobResult] = []
                for raw_result in raw_results:
                    if not isinstance(raw_result, dict):
                        continue
                    result = cast(JobResult, dict(raw_result))
                    result["rate_seconds_per_second"] = _to_optional_float(raw_result.get("rate_seconds_per_second"))
                    results.append(result)
                file_entry["results"] = results
            input_entry["file"] = file_entry
        job["input"] = input_entry

    raw_output = payload.get("output")
    if isinstance(raw_output, dict):
        job["output"] = dict(raw_output)
    return job


def _iter_job_results(job: JobDetails) -> Iterable[JobResult]:
    input_data = job.get("input")
    if not input_data:
        return
    file_entry = input_data.get("file")
    if not file_entry:
        return
    for result in file_entry.get("results", []):
        yield result


def _log_job_result_rates(job_id: str, details: JobDetails) -> None:
    input_data = details.get("input")
    if not input_data:
        return
    file_entry = input_data.get("file")
    if not file_entry:
        return
    file_name = file_entry.get("original_filename") or file_entry.get("stored_name") or "file"
    rates: List[float] = []
    for result in file_entry.get("results", []):
        mode = result.get("mode") or "result"
        parts = [f"Job {job_id}: {file_name} [{mode}]"]
        rate = _to_optional_float(result.get("rate_seconds_per_second"))
        if rate is None:
            parts.append("Rate: n/a")
        else:
            parts.append(f"Rate: {rate:.2f} s/s")
            if not result.get("error"):
                rates.append(rate)
        if "cache_hit" in result:
            parts.append(f"cache_hit={result.get('cache_hit')}")
        if result.get("error"):
            parts.append(f"error={result.get('error')}")
        _log(" ".join(parts))
    if rates:
        _log(f"Job {job_id}: max rate {max(rates):.2f} s/s, avg rate {sum(rates) / len(rates):.2f} s/s")


def _submit_job(
    base_url: str,
    file_path: str,
    mode: str,
    model: str,
    job_label: str | None,
    verify: str | bool,
) -> str:
    url = f"{base_url}/api/jobs"
    data: Dict[str, str] = {"mode": mode, "model": model}
    if job_label:
        data["job_label"] = job_label
    filename = os.path.basename(file_path)
    with open(file_path, "rb") as handle:
        files = {"file": (filename, handle, "audio/mpeg")}
        resp = requests.post(url, data=data, files=files, timeout=120, verify=verify)
    if resp.status_code not in {200, 202}:
        raise SystemExit(f"Demucs API request failed: HTTP {resp.status_code} {resp.text}")
    payload = resp.json()
    job_id = payload.get("id")
    if not job_id:
        raise SystemExit("Demucs API response missing job id")
    return str(job_id)


def _wait_for_job(
    base_url: str,
    job_id: str,
    poll_seconds: float,
    timeout_seconds: float,
    verify: str | bool,
) -> JobDetails:
    url = f"{base_url}/api/jobs/{job_id}"
    start = time.time()
    poll_count = 0
    while True:
        poll_count += 1
        resp = requests.get(url, timeout=30, verify=verify)
        if not resp.ok:
            raise SystemExit(f"Demucs API status failed: HTTP {resp.status_code} {resp.text}")
        payload = _parse_job_details(resp.json())
        status = payload.get("status")
        elapsed = int(time.time() - start)
        _log(f"Job {job_id} poll #{poll_count}: status={status} elapsed={elapsed}s")
        _log_job_result_rates(job_id, payload)
        if status in {"succeeded", "failed"}:
            return payload
        if timeout_seconds and (time.time() - start) > timeout_seconds:
            raise SystemExit(f"Timed out waiting for Demucs job {job_id}")
        time.sleep(poll_seconds)


def _download_output(base_url: str, job_id: str, dest_zip: Path, verify: str | bool) -> None:
    result_url = f"{base_url}/api/jobs/{job_id}/result"
    resp = requests.get(result_url, stream=True, timeout=120, verify=verify)
    if resp.status_code != 200:
        raise SystemExit(f"Demucs API output download failed: HTTP {resp.status_code} {resp.text}")
    with dest_zip.open("wb") as handle:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)


def _classify_dir(path: Path) -> Tuple[bool, bool]:
    files = {p.name for p in path.iterdir() if p.is_file()}
    is_four = {"vocals.wav", "drums.wav", "bass.wav", "other.wav"}.issubset(files)
    is_two = {"vocals.wav", "no_vocals.wav"}.issubset(files)
    return is_four, is_two


def _copy_mode_dir(src: Path, dest_root: str, track_name: str, mode_label: str) -> Path:
    dest = Path(dest_root) / track_name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    _log(f"Saved {mode_label} output: {dest}")
    return dest


def _extract_outputs(zip_path: Path, ctx: RootContext, mode: str, track_name: str) -> None:
    with tempfile.TemporaryDirectory(prefix="demucs_api_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        with zipfile.ZipFile(zip_path) as zip_handle:
            zip_handle.extractall(tmp_dir_path)

        ensure_dir(ctx.all_dir)
        ensure_dir(ctx.vocals_dir)

        all_dir = tmp_dir_path / "all"
        vocals_dir = tmp_dir_path / "vocals"

        # Some archives wrap content in a single top-level directory.
        if not all_dir.is_dir() and not vocals_dir.is_dir():
            top_dirs = [p for p in tmp_dir_path.iterdir() if p.is_dir()]
            if len(top_dirs) == 1:
                all_dir = top_dirs[0] / "all"
                vocals_dir = top_dirs[0] / "vocals"

        copied = False
        if mode in {"4", "both"}:
            if not all_dir.is_dir():
                raise SystemExit("Demucs API output missing expected all/ directory")
            is_four, _ = _classify_dir(all_dir)
            if not is_four:
                raise SystemExit("Demucs API output all/ directory is missing required 4-stem files")
            _copy_mode_dir(all_dir, ctx.all_dir, track_name, "4-stem")
            copied = True
        if mode in {"2", "both"}:
            if not vocals_dir.is_dir():
                raise SystemExit("Demucs API output missing expected vocals/ directory")
            _, is_two = _classify_dir(vocals_dir)
            if not is_two:
                raise SystemExit("Demucs API output vocals/ directory is missing required 2-stem files")
            _copy_mode_dir(vocals_dir, ctx.vocals_dir, track_name, "2-stem")
            copied = True
        if not copied:
            raise SystemExit("Demucs API output did not contain any requested stems")


def _is_invalid_mp3_error(exc: SystemExit) -> bool:
    lowered = str(exc).lower()
    return "invalid mp3" in lowered or "unsupported media type" in lowered


def _run_one_file(
    base_url: str,
    ctx: RootContext,
    file_path: str,
    mode: str,
    model: str,
    verify: str | bool,
    poll_seconds: float,
    timeout_seconds: float,
    job_label: str | None,
) -> None:
    job_id = _submit_job(base_url, file_path, mode, model, job_label, verify)
    job = _wait_for_job(base_url, job_id, poll_seconds, timeout_seconds, verify)
    status = job.get("status")
    if status != "succeeded":
        message = job.get("message") or job.get("error") or "unknown error"
        raise SystemExit(f"Demucs API job failed: {job_id} ({message})")

    with tempfile.TemporaryDirectory(prefix="demucs_api_zip_") as tmp_zip_dir:
        zip_path = Path(tmp_zip_dir) / f"{job_id}.zip"
        _download_output(base_url, job_id, zip_path, verify)
        stem_name = Path(file_path).stem
        track_name = canonical_output_name(stem_name) or normalize_windows_name(stem_name) or stem_name
        _extract_outputs(zip_path, ctx, mode, track_name)


def run_windows(
    ctx: RootContext,
    missing_files: List[str],
    mode: str,
    clean_windows: bool,
    dry_run: bool = False,
    max_jobs: int | None = None,
) -> None:
    del clean_windows
    base_url = os.environ.get("DEMUCS_API_URL", "https://demucs.stanley.arpa").rstrip("/")
    ca_cert = os.environ.get("DEMUCS_API_CA_CERT")
    verify: str | bool = ca_cert if ca_cert else True
    model = (
        os.environ.get("DEMUCS_API_MODEL")
        or os.environ.get("WINDOWS_DEMUCS_MODEL")
        or os.environ.get("DEMUCS_MODEL")
        or "htdemucs"
    )
    poll_seconds = _read_env_float("DEMUCS_API_POLL_SECS", 5.0)
    timeout_seconds = _read_env_float("DEMUCS_API_TIMEOUT_SECS", 3600.0)

    # Drop macOS resource fork files and non-mp3s to avoid API errors.
    missing_files = [
        path
        for path in missing_files
        if path.lower().endswith(".mp3") and not Path(path).name.startswith("._")
    ]

    if not missing_files:
        _log(f"All requested stems exist for {ctx.root}; skipping remote run.")
        return

    total_jobs = len(missing_files)
    effective_jobs = min(total_jobs, max_jobs) if max_jobs is not None else total_jobs

    if dry_run:
        _log(
            f"Demucs API dry run: would submit {effective_jobs}/{total_jobs} "
            f"job(s) (one file per job)."
        )
        _log(f"Demucs API dry run: endpoint={base_url}/api/jobs mode={mode} model={model} verify={verify!r}")
        for index, file_path in enumerate(missing_files[:effective_jobs], start=1):
            job_label = f"{Path(ctx.root).name} file {Path(file_path).name}"
            _log(f"Dry run job {index}/{total_jobs}: job_label={job_label!r} file={Path(file_path).name}")
        return

    _log("Checking/waking UpSnap target...")
    ensure_upsnap_awake()
    _log("UpSnap check complete.")

    skipped_invalid = 0
    for index, file_path in enumerate(missing_files[:effective_jobs], start=1):
        _log(f"Job {index}/{total_jobs}: verifying UpSnap ready...")
        require_upsnap_ready()
        _log(f"Job {index}/{total_jobs}: UpSnap ready.")
        _log(f"Job {index}/{total_jobs}: submitting {Path(file_path).name} to {base_url}")
        job_label = f"{Path(ctx.root).name} file {Path(file_path).name}"
        try:
            _run_one_file(
                base_url=base_url,
                ctx=ctx,
                file_path=file_path,
                mode=mode,
                model=model,
                verify=verify,
                poll_seconds=poll_seconds,
                timeout_seconds=timeout_seconds,
                job_label=job_label,
            )
            _log(f"Job {index}/{total_jobs}: output downloaded and installed.")
        except SystemExit as exc:
            if _is_invalid_mp3_error(exc):
                skipped_invalid += 1
                _log(f"Skipping invalid MP3: {file_path}")
                continue
            raise

    if max_jobs is not None and total_jobs > effective_jobs:
        _log(f"Stopping early after {effective_jobs} job(s) (debug limit); {total_jobs - effective_jobs} file(s) not submitted.")
    if skipped_invalid:
        _log(f"Done with warnings: skipped {skipped_invalid} invalid MP3 file(s).")

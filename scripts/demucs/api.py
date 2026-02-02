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
    progress: float | int | None
    rate_seconds_per_second: float | None


class JobInputFile(TypedDict, total=False):
    name: str
    filename: str
    results: List[JobResult]


class JobInput(TypedDict, total=False):
    files: List[JobInputFile]


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


def _to_optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _parse_job_details(payload: Dict[str, Any]) -> JobDetails:
    job: JobDetails = cast(JobDetails, dict(payload))
    raw_input = payload.get("input")
    if not isinstance(raw_input, dict):
        return job

    raw_files = raw_input.get("files")
    if not isinstance(raw_files, list):
        return job

    files: List[JobInputFile] = []
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            continue
        file_entry: JobInputFile = cast(JobInputFile, dict(raw_file))
        raw_results = raw_file.get("results")
        if isinstance(raw_results, list):
            results: List[JobResult] = []
            for raw_result in raw_results:
                if not isinstance(raw_result, dict):
                    continue
                result: JobResult = cast(JobResult, dict(raw_result))
                result["rate_seconds_per_second"] = _to_optional_float(raw_result.get("rate_seconds_per_second"))
                results.append(result)
            file_entry["results"] = results
        files.append(file_entry)

    input_entry = cast(JobInput, dict(raw_input))
    input_entry["files"] = files
    job["input"] = input_entry

    raw_output = payload.get("output")
    if isinstance(raw_output, dict):
        job["output"] = dict(raw_output)

    return job


def _iter_job_results(job: JobDetails) -> Iterable[JobResult]:
    input_data = job.get("input")
    if not input_data:
        return
    for file_entry in input_data.get("files", []):
        for result in file_entry.get("results", []):
            yield result


def format_result_rate(rate_seconds_per_second: float | None) -> str:
    if rate_seconds_per_second is None:
        return "Rate: n/a"
    return f"Rate: {rate_seconds_per_second:.2f} s/s"


def derive_rate_stats(job: JobDetails) -> Tuple[float | None, float | None]:
    rates: List[float] = []
    for result in _iter_job_results(job):
        if result.get("error"):
            continue
        rate = _to_optional_float(result.get("rate_seconds_per_second"))
        if rate is None:
            continue
        rates.append(rate)
    if not rates:
        return None, None
    return max(rates), (sum(rates) / len(rates))


def _log_job_result_rates(job_id: str, details: JobDetails) -> None:
    input_data = details.get("input")
    if not input_data:
        return
    for file_index, file_entry in enumerate(input_data.get("files", []), start=1):
        file_name = file_entry.get("name") or file_entry.get("filename") or f"file-{file_index}"
        for result_index, result in enumerate(file_entry.get("results", []), start=1):
            mode = result.get("mode") or f"result-{result_index}"
            parts = [f"Job {job_id}: {file_name} [{mode}] {format_result_rate(result.get('rate_seconds_per_second'))}"]
            if "cache_hit" in result:
                parts.append(f"cache_hit={result.get('cache_hit')}")
            if "progress" in result:
                parts.append(f"progress={result.get('progress')}")
            if result.get("error"):
                parts.append(f"error={result.get('error')}")
            _log(" ".join(parts))

    max_rate, avg_rate = derive_rate_stats(details)
    if max_rate is not None and avg_rate is not None:
        _log(f"Job {job_id}: max rate {max_rate:.2f} s/s, avg rate {avg_rate:.2f} s/s")


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


def _copy_stem_dir(src: Path, dest_root: str, dest_name_hint: str | None = None) -> Path:
    if dest_name_hint:
        dest_name = normalize_windows_name(dest_name_hint)
    else:
        # Some archives place stems under mode directories named "2"/"4";
        # in that case use the parent track directory as the destination name.
        source_name = src.parent.name if src.name in {"2", "4"} else src.name
        dest_name = normalize_windows_name(source_name)
    if dest_name.lower().endswith(".mp3"):
        dest_name = dest_name[:-4]
    if not dest_name:
        dest_name = src.name
    dest = Path(dest_root) / dest_name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    return dest


def _relative_manifest_path(raw_path: str) -> Path | None:
    cleaned = raw_path.strip()
    if not cleaned:
        return None
    raw_parts = Path(cleaned).parts
    parts = [part for part in raw_parts if part not in {"", ".", "/", "\\"}]
    if not parts or ".." in parts:
        return None
    return Path(*parts)


def _manifest_output_dirs(job: JobDetails) -> List[str]:
    output = job.get("output")
    if not isinstance(output, dict):
        return []
    manifest = output.get("manifest")
    if not isinstance(manifest, dict):
        return []
    files = manifest.get("files")
    if not isinstance(files, list):
        return []
    output_dirs: List[str] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        output_dir = item.get("output_dir_path") or item.get("output_dir_name")
        if isinstance(output_dir, str) and output_dir.strip():
            output_dirs.append(output_dir)
    return output_dirs


def _manifest_entries(job: JobDetails) -> List[Dict[str, str]]:
    output = job.get("output")
    if not isinstance(output, dict):
        return []
    manifest = output.get("manifest")
    if not isinstance(manifest, dict):
        return []
    files = manifest.get("files")
    if not isinstance(files, list):
        return []

    entries: List[Dict[str, str]] = []
    for item in files:
        if not isinstance(item, dict):
            continue
        output_dir_path = item.get("output_dir_path")
        output_dir_name = item.get("output_dir_name")
        if isinstance(output_dir_path, str) and output_dir_path.strip():
            entry: Dict[str, str] = {"output_dir_path": output_dir_path}
            if isinstance(output_dir_name, str) and output_dir_name.strip():
                entry["output_dir_name"] = output_dir_name
            entries.append(entry)
    return entries


def _log_output_manifest(job: JobDetails) -> None:
    output = job.get("output")
    if not isinstance(output, dict):
        _log("Job output metadata missing.")
        return

    manifest = output.get("manifest")
    if not isinstance(manifest, dict):
        _log("Job output manifest missing (legacy or unavailable).")
        return

    files = manifest.get("files")
    if not isinstance(files, list):
        _log("Job output manifest has no files list.")
        return

    version = manifest.get("version")
    _log(f"Job output manifest: version={version!r} files={len(files)}")
    for idx, item in enumerate(files, start=1):
        if not isinstance(item, dict):
            _log(f"Manifest[{idx}]: invalid entry type={type(item).__name__}")
            continue
        input_name = item.get("input_original_name") or item.get("input_stored_name") or item.get("output_dir_name")
        out_name = item.get("output_dir_name")
        out_path = item.get("output_dir_path")
        raw_modes = item.get("modes")
        mode_labels: List[str] = []
        if isinstance(raw_modes, list):
            for mode_item in raw_modes:
                if isinstance(mode_item, dict):
                    mode = mode_item.get("mode")
                    if isinstance(mode, str) and mode:
                        mode_labels.append(mode)
        modes_text = ",".join(mode_labels) if mode_labels else "n/a"
        _log(
            f"Manifest[{idx}]: input={input_name!r} output_dir_name={out_name!r} "
            f"output_dir_path={out_path!r} modes={modes_text}"
        )


def _extract_outputs(
    zip_path: Path,
    ctx: RootContext,
    mode: str,
    manifest_output_dirs: List[str] | None = None,
    manifest_entries: List[Dict[str, str]] | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="demucs_api_") as tmp_dir:
        tmp_dir_path = Path(tmp_dir)
        with zipfile.ZipFile(zip_path) as zip_handle:
            zip_handle.extractall(tmp_dir_path)

        ensure_dir(ctx.all_dir)
        ensure_dir(ctx.vocals_dir)

        copied_any = False
        if manifest_entries:
            _log(f"Using output manifest mapping for {len(manifest_entries)} file(s).")
            for entry in manifest_entries:
                raw_dir = entry.get("output_dir_path", "")
                dest_name_hint = entry.get("output_dir_name")
                relative_dir = _relative_manifest_path(raw_dir)
                if relative_dir is None:
                    _log(f"Skipping unsafe manifest path: {raw_dir!r}")
                    continue
                base_dir = tmp_dir_path / relative_dir
                if not base_dir.is_dir():
                    _log(f"Manifest output path missing in archive: {raw_dir!r}")
                    continue

                # First try the manifest directory directly.
                is_four, is_two = _classify_dir(base_dir)
                if mode in {"4", "both"} and is_four:
                    dest = _copy_stem_dir(base_dir, ctx.all_dir, dest_name_hint)
                    _log(f"Saved 4-stem output: {dest}")
                    copied_any = True
                if mode in {"2", "both"} and is_two:
                    dest = _copy_stem_dir(base_dir, ctx.vocals_dir, dest_name_hint)
                    _log(f"Saved 2-stem output: {dest}")
                    copied_any = True

                # Then try common mode subdirectories under the manifest directory.
                for mode_dir_name in ("4", "2"):
                    mode_dir = base_dir / mode_dir_name
                    if not mode_dir.is_dir():
                        continue
                    is_four, is_two = _classify_dir(mode_dir)
                    if mode in {"4", "both"} and is_four:
                        dest = _copy_stem_dir(mode_dir, ctx.all_dir, dest_name_hint)
                        _log(f"Saved 4-stem output: {dest}")
                        copied_any = True
                    if mode in {"2", "both"} and is_two:
                        dest = _copy_stem_dir(mode_dir, ctx.vocals_dir, dest_name_hint)
                        _log(f"Saved 2-stem output: {dest}")
                        copied_any = True

                # Finally, scan recursively beneath the manifest directory for compatibility.
                if not (is_four or is_two):
                    for stem_dir in _iter_stem_dirs(base_dir):
                        is_four, is_two = _classify_dir(stem_dir)
                        if mode in {"4", "both"} and is_four:
                            dest = _copy_stem_dir(stem_dir, ctx.all_dir, dest_name_hint)
                            _log(f"Saved 4-stem output: {dest}")
                            copied_any = True
                        if mode in {"2", "both"} and is_two:
                            dest = _copy_stem_dir(stem_dir, ctx.vocals_dir, dest_name_hint)
                            _log(f"Saved 2-stem output: {dest}")
                            copied_any = True
        elif manifest_output_dirs:
            _log(f"Using output manifest mapping for {len(manifest_output_dirs)} file(s).")
            seen_dirs: set[Path] = set()
            for raw_dir in manifest_output_dirs:
                relative_dir = _relative_manifest_path(raw_dir)
                if relative_dir is None:
                    _log(f"Skipping unsafe manifest path: {raw_dir!r}")
                    continue
                stem_dir = tmp_dir_path / relative_dir
                if stem_dir in seen_dirs:
                    continue
                seen_dirs.add(stem_dir)
                if not stem_dir.is_dir():
                    _log(f"Manifest output path missing in archive: {raw_dir!r}")
                    continue
                is_four, is_two = _classify_dir(stem_dir)
                if mode in {"4", "both"} and is_four:
                    dest = _copy_stem_dir(stem_dir, ctx.all_dir)
                    _log(f"Saved 4-stem output: {dest}")
                    copied_any = True
                if mode in {"2", "both"} and is_two:
                    dest = _copy_stem_dir(stem_dir, ctx.vocals_dir)
                    _log(f"Saved 2-stem output: {dest}")
                    copied_any = True

        if copied_any:
            return

        _log("No usable manifest mapping found in output; falling back to stem directory scan.")
        for stem_dir in _iter_stem_dirs(tmp_dir_path):
            is_four, is_two = _classify_dir(stem_dir)
            if mode in {"4", "both"} and is_four:
                dest = _copy_stem_dir(stem_dir, ctx.all_dir)
                _log(f"Saved 4-stem output: {dest}")
            if mode in {"2", "both"} and is_two:
                dest = _copy_stem_dir(stem_dir, ctx.vocals_dir)
                _log(f"Saved 2-stem output: {dest}")


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
    _log_output_manifest(job)
    status = job.get("status")
    if status != "succeeded":
        message = job.get("message") or job.get("error") or "unknown error"
        raise SystemExit(f"Demucs API job failed: {job_id} ({message})")

    with tempfile.TemporaryDirectory(prefix="demucs_api_zip_") as tmp_zip_dir:
        zip_path = Path(tmp_zip_dir) / f"{job_id}.zip"
        _download_output(base_url, job_id, zip_path, verify)
        _extract_outputs(
            zip_path,
            ctx,
            mode,
            _manifest_output_dirs(job),
            _manifest_entries(job),
        )


def run_windows(
    ctx: RootContext,
    missing_files: List[str],
    mode: str,
    clean_windows: bool,
    dry_run: bool = False,
    max_batches: int | None = None,
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
        _log(f"All requested stems exist for {ctx.root}; skipping remote run.")
        return

    total_batches = (len(missing_files) + batch_size - 1) // batch_size
    effective_batches = min(total_batches, max_batches) if max_batches is not None else total_batches
    if dry_run:
        planned_files = min(len(missing_files), effective_batches * batch_size)
        _log(
            f"Demucs API dry run: would submit {planned_files}/{len(missing_files)} "
            f"file(s) in {effective_batches}/{total_batches} batch(es)."
        )
        _log(f"Demucs API dry run: endpoint={base_url}/api/jobs mode={mode} model={model} verify={verify!r}")
        for batch_index in range(effective_batches):
            batch_files = missing_files[batch_index * batch_size : (batch_index + 1) * batch_size]
            job_name = f"{Path(ctx.root).name} batch {batch_index + 1}/{total_batches}"
            file_names = ", ".join(Path(path).name for path in batch_files)
            _log(
                f"Dry run batch {batch_index + 1}/{total_batches}: job_name={job_name!r} "
                f"files={len(batch_files)} [{file_names}]"
            )
        return

    _log("Checking/waking UpSnap target...")
    ensure_upsnap_awake()
    _log("UpSnap check complete.")

    batch_start = 0
    batch_index = 0
    skipped_invalid = 0

    while batch_start < len(missing_files):
        batch_index += 1
        if max_batches is not None and batch_index > max_batches:
            _log(
                f"Stopping early after {max_batches} batch(es) (debug limit); "
                f"{len(missing_files) - batch_start} file(s) not submitted."
            )
            break
        batch_files = missing_files[batch_start : batch_start + batch_size]
        batch_start += batch_size

        if not batch_files:
            continue

        _log(f"Batch {batch_index}/{total_batches}: verifying UpSnap ready...")
        require_upsnap_ready()
        _log(f"Batch {batch_index}/{total_batches}: UpSnap ready.")
        _log(f"Batch {batch_index}/{total_batches}: submitting {len(batch_files)} files to {base_url}")
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
            _log(f"Batch {batch_index}/{total_batches}: output downloaded and installed.")
            continue
        except SystemExit as exc:
            if not _is_invalid_mp3_error(exc):
                raise
            if len(batch_files) == 1:
                skipped_invalid += 1
                _log(f"Skipping invalid MP3: {batch_files[0]}")
                continue
            _log("Batch rejected due to invalid MP3 data; retrying files individually.")

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
                _log(f"Processed: {file_path}")
            except SystemExit as exc:
                if _is_invalid_mp3_error(exc):
                    skipped_invalid += 1
                    _log(f"Skipping invalid MP3: {file_path}")
                    continue
                raise

    if skipped_invalid:
        _log(f"Done with warnings: skipped {skipped_invalid} invalid MP3 file(s).")

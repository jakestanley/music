import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import List

from scripts.core.env import require_vars
from scripts.core.paths import ensure_dir
from scripts.upsnap.client import load_upsnap_client
from scripts.windows.ssh import build_scp_cmd, build_ssh_cmd, run_ps


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


def _prompt_windows_sleep(timeout_seconds: int) -> bool:
    if not sys.stdin.isatty():
        return False
    print(f"Press Enter to skip Windows sleep (auto-sleep in {timeout_seconds}s): ", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout_seconds:
        if sys.stdin in select.select([sys.stdin], [], [], 1)[0]:
            sys.stdin.readline()
            print("Skipping Windows sleep.")
            return True
    return False


def run_windows(
    ctx: RootContext,
    missing_files: List[str],
    mode: str,
    clean_windows: bool,
) -> None:
    require_vars(["UPSNAP_HOST", "UPSNAP_USERNAME", "UPSNAP_PASSWORD", "WINDOWS_SSH_TARGET", "WINDOWS_SSH_KEY"])

    ssh_target = os.environ["WINDOWS_SSH_TARGET"]
    ssh_key = os.path.expanduser(os.environ["WINDOWS_SSH_KEY"])
    if not os.path.isfile(ssh_key):
        raise SystemExit(f"SSH key not found: {ssh_key}")

    demucs_model = os.environ.get("WINDOWS_DEMUCS_MODEL", os.environ.get("DEMUCS_MODEL", "htdemucs"))
    demucs_device = os.environ.get("WINDOWS_DEMUCS_DEVICE", "cuda")
    windows_batch_size = int(os.environ.get("WINDOWS_BATCH_SIZE", "10"))
    windows_awake_minutes = int(os.environ.get("WINDOWS_AWAKE_MINUTES", "10"))
    windows_sleep_prompt_timeout = int(os.environ.get("WINDOWS_SLEEP_PROMPT_TIMEOUT", "120"))
    windows_python = os.environ.get("WINDOWS_PYTHON", "python")
    windows_gpu_max_temp = int(os.environ.get("WINDOWS_GPU_MAX_TEMP", "80"))
    windows_gpu_resume_temp = int(os.environ.get("WINDOWS_GPU_RESUME_TEMP", "70"))

    if windows_batch_size < 1:
        raise SystemExit("Invalid WINDOWS_BATCH_SIZE: expected integer >= 1")

    if not missing_files:
        print(f"All requested stems exist for {ctx.root}; skipping remote run.")
        return

    ssh_cmd = build_ssh_cmd(ssh_key, ssh_target)
    scp_cmd = build_scp_cmd(ssh_key)

    client = load_upsnap_client()
    token = client.authenticate()
    device_id = os.environ.get("UPSNAP_DEVICE_ID", "")
    if not device_id:
        device_name = os.environ.get("UPSNAP_DEVICE_NAME", "")
        if not device_name:
            raise SystemExit("Missing required env var: UPSNAP_DEVICE_NAME")
        device_id = client.find_device_id_by_name(token, device_name) or ""
    if not device_id:
        raise SystemExit("UpSnap device id not found")

    print(f"UpSnap wake requested for Windows host (device id: {device_id})")
    client.wake(token, device_id)

    print("Waiting for Windows SSH to become available...")
    for _ in range(20):
        try:
            subprocess.run(ssh_cmd + ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "exit"], check=True)
            break
        except subprocess.CalledProcessError:
            time.sleep(5)
    else:
        raise SystemExit("SSH not available on Windows host")
    print("Windows SSH connected.")

    sleep_scheme_guid = ""
    sleep_timeout_ac = ""
    sleep_timeout_dc = ""

    def prevent_windows_sleep() -> None:
        nonlocal sleep_scheme_guid, sleep_timeout_ac, sleep_timeout_dc
        info = run_ps(
            ssh_cmd,
            "\"$scheme = (powercfg /getactivescheme) -match 'GUID:\\s+([a-fA-F0-9-]+)' | ForEach-Object { $matches[1] }; if (-not $scheme) { exit 0 }; $raw = powercfg /query $scheme SUB_SLEEP STANDBYIDLE; $ac = ($raw | Select-String -Pattern 'Current AC Power Setting Index:\\s+0x([0-9a-fA-F]+)' | ForEach-Object { [Convert]::ToInt32($_.Matches[0].Groups[1].Value,16) })[0]; $dc = ($raw | Select-String -Pattern 'Current DC Power Setting Index:\\s+0x([0-9a-fA-F]+)' | ForEach-Object { [Convert]::ToInt32($_.Matches[0].Groups[1].Value,16) })[0]; Write-Output ($scheme + [char]9 + $ac + [char]9 + $dc)\"",
        ).strip()
        if info:
            parts = info.split("\t")
            if len(parts) >= 3:
                sleep_scheme_guid, sleep_timeout_ac, sleep_timeout_dc = parts[:3]
                print("Disabling Windows sleep (powercfg standby timeout set to 0).")
                run_ps(
                    ssh_cmd,
                    f"powercfg /setacvalueindex {sleep_scheme_guid} SUB_SLEEP STANDBYIDLE 0; powercfg /setdcvalueindex {sleep_scheme_guid} SUB_SLEEP STANDBYIDLE 0; powercfg /setactive {sleep_scheme_guid}",
                )

    def restore_windows_sleep() -> None:
        if sleep_scheme_guid and sleep_timeout_ac and sleep_timeout_dc:
            print("Restoring Windows sleep timeouts.")
            run_ps(
                ssh_cmd,
                f"powercfg /setacvalueindex {sleep_scheme_guid} SUB_SLEEP STANDBYIDLE {sleep_timeout_ac}; powercfg /setdcvalueindex {sleep_scheme_guid} SUB_SLEEP STANDBYIDLE {sleep_timeout_dc}; powercfg /setactive {sleep_scheme_guid}",
            )

    prevent_windows_sleep()

    try:
        if clean_windows:
            run_ps(
                ssh_cmd,
                "\"$tmp = Join-Path $env:TEMP 'demucs_tmp'; if (Test-Path $tmp) { Get-ChildItem -Force $tmp | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }\"",
            )

        win_tmp = run_ps(
            ssh_cmd,
            "\"$tmp = Join-Path $env:TEMP 'demucs_tmp'; New-Item -ItemType Directory -Path $tmp -Force | Out-Null; Write-Output $tmp\"",
        ).strip()
        if not win_tmp:
            raise SystemExit("Failed to resolve Windows temp path")
        print(f"Windows temp directory: {win_tmp}")

        win_tmp_scp = win_tmp.replace("\\", "/")
        if len(win_tmp_scp) > 2 and win_tmp_scp[1:3] == ":/":
            win_tmp_scp = f"/{win_tmp_scp}"

        win_input_ps = f"{win_tmp}\\input"
        win_out4_ps = f"{win_tmp}\\out4"
        win_out2_ps = f"{win_tmp}\\out2"
        win_input_scp = f"{win_tmp_scp}/input"
        win_out4_scp = f"{win_tmp_scp}/out4"
        win_out2_scp = f"{win_tmp_scp}/out2"

        run_ps(
            ssh_cmd,
            f"New-Item -ItemType Directory -Path '{win_input_ps}' -Force | Out-Null; New-Item -ItemType Directory -Path '{win_out4_ps}' -Force | Out-Null; New-Item -ItemType Directory -Path '{win_out2_ps}' -Force | Out-Null",
        )

        if demucs_device == "cuda":
            run_ps(
                ssh_cmd,
                f"& '{windows_python}' -c \"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('torch') else 1)\"",
            )
            run_ps(
                ssh_cmd,
                f"& '{windows_python}' -c \"import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)\"",
            )

        ensure_dir(ctx.all_dir)
        ensure_dir(ctx.vocals_dir)

        total_batches = (len(missing_files) + windows_batch_size - 1) // windows_batch_size
        batch_start = 0
        batch_index = 0

        awake_exe = ""
        while batch_start < len(missing_files):
            batch_index += 1
            batch_files = missing_files[batch_start : batch_start + windows_batch_size]
            batch_start += windows_batch_size

            run_ps(ssh_cmd, f"Get-ChildItem -Path '{win_input_ps}' -Filter '*.mp3' -File | Remove-Item -Force")

            if not batch_files:
                print(f"Batch {batch_index}/{total_batches}: no valid files to upload.")
                continue

            print(f"Batch {batch_index}/{total_batches}: uploading {len(batch_files)} files...")
            subprocess.run(scp_cmd + ["-r"] + batch_files + [f"{ssh_target}:{win_input_scp}/"], check=True)
            print(f"Batch {batch_index}/{total_batches}: upload complete.")

            size_report = run_ps(
                ssh_cmd,
                f"Get-ChildItem -Path '{win_input_ps}' -Filter '*.mp3' -File | ForEach-Object {{ Write-Output ($_.Name + [char]9 + $_.Length) }}",
            ).strip()
            remote_sizes = {}
            for line in size_report.splitlines():
                if not line.strip():
                    continue
                name, remote_size = line.split("\t")
                remote_sizes[name] = int(remote_size)

            reupload = []
            for path in batch_files:
                name = os.path.basename(path)
                local_size = os.path.getsize(path)
                if remote_sizes.get(name, -1) != local_size:
                    reupload.append(path)

            if reupload:
                print(f"Batch {batch_index}/{total_batches}: re-uploading {len(reupload)} files with size mismatch...")
                subprocess.run(scp_cmd + ["-r"] + reupload + [f"{ssh_target}:{win_input_scp}/"], check=True)

            if not awake_exe:
                awake_path = run_ps(
                    ssh_cmd,
                    "\"$awake = @((Join-Path $env:ProgramFiles 'PowerToys\\PowerToys.Awake.exe'), (Join-Path $env:LOCALAPPDATA 'PowerToys\\PowerToys.Awake.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1; if ($awake) { Write-Output $awake }\"",
                ).strip()
                if awake_path:
                    awake_exe = awake_path

            if awake_exe:
                print(f"Batch {batch_index}/{total_batches}: PowerToys Awake for {windows_awake_minutes} minutes.")
                run_ps(
                    ssh_cmd,
                    f"Start-Process -FilePath '{awake_exe}' -ArgumentList '--mode timed --time {windows_awake_minutes * 60}' -WindowStyle Hidden",
                )
            else:
                print(
                    f"Warning: PowerToys Awake not found on Windows host; sleep may interrupt batch {batch_index}/{total_batches}.",
                    file=sys.stderr,
                )

            if mode in {"4", "both"}:
                print(f"Batch {batch_index}/{total_batches}: running Windows 4-stem separation...")
                run_ps(
                    ssh_cmd,
                    (
                        f"$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; "
                        f"$maxTemp={windows_gpu_max_temp}; $resumeTemp={windows_gpu_resume_temp}; "
                        "function Get-GpuTemp { [int](nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | Select-Object -First 1) }; "
                        "function Wait-ForCool { while ((Get-GpuTemp) -gt $resumeTemp) { Write-Host \"GPU hot ($(Get-GpuTemp))C, waiting to cool to $resumeTemp C\"; Start-Sleep -Seconds 10 } }; "
                        f"$files = Get-ChildItem -Path '{win_input_ps}' -Filter '*.mp3' -File | ForEach-Object {{ $_.FullName }}; "
                        "if ($files.Count -eq 0) { Write-Error 'No MP3 files found in Windows input folder'; exit 1 }; "
                        "foreach ($f in $files) { if ((Get-GpuTemp) -gt $maxTemp) { Wait-ForCool }; "
                        f"demucs --device {demucs_device} -n {demucs_model} -o '{win_out4_ps}' \"$f\" }}"
                    ),
                )

            if mode in {"2", "both"}:
                print(f"Batch {batch_index}/{total_batches}: running Windows 2-stem separation...")
                run_ps(
                    ssh_cmd,
                    (
                        f"$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'; "
                        f"$maxTemp={windows_gpu_max_temp}; $resumeTemp={windows_gpu_resume_temp}; "
                        "function Get-GpuTemp { [int](nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | Select-Object -First 1) }; "
                        "function Wait-ForCool { while ((Get-GpuTemp) -gt $resumeTemp) { Write-Host \"GPU hot ($(Get-GpuTemp))C, waiting to cool to $resumeTemp C\"; Start-Sleep -Seconds 10 } }; "
                        f"$files = Get-ChildItem -Path '{win_input_ps}' -Filter '*.mp3' -File | ForEach-Object {{ $_.FullName }}; "
                        "if ($files.Count -eq 0) { Write-Error 'No MP3 files found in Windows input folder'; exit 1 }; "
                        "foreach ($f in $files) { if ((Get-GpuTemp) -gt $maxTemp) { Wait-ForCool }; "
                        f"demucs --device {demucs_device} -n {demucs_model} --two-stems=vocals -o '{win_out2_ps}' \"$f\" }}"
                    ),
                )

            if mode in {"4", "both"}:
                print(f"Batch {batch_index}/{total_batches}: copying Windows 4-stem outputs back...")
                subprocess.run(
                    scp_cmd + ["-r", f"{ssh_target}:{win_out4_scp}/htdemucs", ctx.all_dir], check=True
                )
                htdemucs_dir = os.path.join(ctx.all_dir, "htdemucs")
                if os.path.isdir(htdemucs_dir):
                    for name in os.listdir(htdemucs_dir):
                        src_dir = os.path.join(htdemucs_dir, name)
                        if not os.path.isdir(src_dir):
                            continue
                        dest_dir = os.path.join(ctx.all_dir, name)
                        if os.path.isdir(dest_dir):
                            for item in os.listdir(src_dir):
                                subprocess.run(["cp", "-a", os.path.join(src_dir, item), dest_dir], check=True)
                            subprocess.run(["rm", "-rf", src_dir], check=True)
                        else:
                            subprocess.run(["mv", src_dir, dest_dir], check=True)
                    subprocess.run(["rmdir", htdemucs_dir], check=False)

            if mode in {"2", "both"}:
                print(f"Batch {batch_index}/{total_batches}: copying Windows 2-stem outputs back...")
                subprocess.run(
                    scp_cmd + ["-r", f"{ssh_target}:{win_out2_scp}/htdemucs", ctx.vocals_dir], check=True
                )
                htdemucs_dir = os.path.join(ctx.vocals_dir, "htdemucs")
                if os.path.isdir(htdemucs_dir):
                    for name in os.listdir(htdemucs_dir):
                        src_dir = os.path.join(htdemucs_dir, name)
                        if not os.path.isdir(src_dir):
                            continue
                        dest_dir = os.path.join(ctx.vocals_dir, name)
                        if os.path.isdir(dest_dir):
                            for item in os.listdir(src_dir):
                                subprocess.run(["cp", "-a", os.path.join(src_dir, item), dest_dir], check=True)
                            subprocess.run(["rm", "-rf", src_dir], check=True)
                        else:
                            subprocess.run(["mv", src_dir, dest_dir], check=True)
                    subprocess.run(["rmdir", htdemucs_dir], check=False)
    finally:
        restore_windows_sleep()

    if _prompt_windows_sleep(windows_sleep_prompt_timeout):
        return

    print("Requesting UpSnap sleep for Windows host...")
    client.shutdown(token, device_id)

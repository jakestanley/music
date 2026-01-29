import os
import shutil
import subprocess
from typing import Iterable, List, Optional, Sequence


def require_command(name: str) -> None:
    if not shutil.which(name):
        raise SystemExit(f"Missing prerequisite: {name}")


def run(cmd: Sequence[str], cwd: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check)


def capture(cmd: Sequence[str], cwd: Optional[str] = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout


def stream_lines(cmd: Sequence[str], cwd: Optional[str] = None) -> Iterable[str]:
    with subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            yield line.rstrip("\n")
        rc = proc.wait()
        if rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)

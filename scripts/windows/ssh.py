import subprocess
from typing import List, Sequence


def build_ssh_cmd(ssh_key: str, target: str) -> List[str]:
    return [
        "ssh",
        "-i",
        ssh_key,
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=6",
        target,
    ]


def build_scp_cmd(ssh_key: str) -> List[str]:
    return [
        "scp",
        "-i",
        ssh_key,
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=6",
    ]


def run_ps(ssh_cmd: Sequence[str], script: str) -> str:
    escaped = script.replace('"', '\\"')
    cmd = list(ssh_cmd) + [f"powershell -NoProfile -Command \"{escaped}\""]
    result = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return result.stdout.replace("\r", "")

import re
import subprocess
import sys
from typing import List, Sequence


def _retry_wait_seconds_from_line(line: str) -> int:
    patterns = [
        r"[Rr]etry[-\s]*After[^0-9]*([0-9]+)",
        r"[Rr]etry[^0-9]*after[^0-9]*([0-9]+)",
        r"occur[^0-9]*after[^0-9]*([0-9]+)",
        r"[Rr]etry(ing)?[^0-9]*in[^0-9]*([0-9]+)\s*(seconds|second|secs|sec|s)\b",
        r"[Ww]ait[^0-9]*([0-9]+)\s*(seconds|second|secs|sec|s)\b",
    ]
    for pat in patterns:
        match = re.search(pat, line)
        if match:
            value = match.group(1)
            try:
                return int(value)
            except ValueError:
                return 0
    return 0


def _print_running(args: Sequence[str]) -> None:
    redacted = []
    skip_next = False
    for arg in args:
        if skip_next:
            redacted.append("***REDACTED***")
            skip_next = False
            continue
        if arg in {"--client-secret", "--auth-token"}:
            redacted.append(arg)
            skip_next = True
            continue
        redacted.append(arg)
    printable = " ".join(["spotdl"] + [repr(a)[1:-1] for a in redacted])
    print(f"Running: {printable}", file=sys.stderr)


def run_spotdl_with_retry_wait_guard(
    args: Sequence[str], max_retry_wait_seconds: int, cwd: str | None = None
) -> int:
    _print_running(list(args))

    proc = subprocess.Popen(
        list(args),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None
    current_song = ""
    suppress_next_url = False
    retry_line = ""
    for raw_line in proc.stdout:
        line = raw_line.rstrip("\n")

        candidate = ""
        m = re.match(r"^Downloading\s+(.+)$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Searching\s+for\s+(.+)$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Searching\s+for\s+(.+)\s+on\s+.+$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Searching\s+for\s+query:\s*(.+)$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Searching\s+(.+)$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Processing\s+(.+)$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Querying\s+(.+)$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Resolving\s+(.+)$", line)
        if m:
            candidate = m.group(1)
        m = re.match(r"^Matching\s+(.+)$", line)
        if m:
            candidate = m.group(1)

        if candidate:
            candidate = candidate.strip()
            if line.startswith("Processing query:"):
                candidate = ""
            elif line.startswith("Searching for query:"):
                candidate = ""
            elif ".spotdl" in candidate:
                candidate = ""
            if candidate and candidate not in {"query:", "query"}:
                current_song = candidate

        retry_wait = _retry_wait_seconds_from_line(line)
        if retry_wait and retry_wait > max_retry_wait_seconds:
            retry_line = line
            print(
                f"ERROR: rate limit hit; spotdl wants to wait {retry_wait}s (> {max_retry_wait_seconds}s). Aborting.",
                file=sys.stderr,
            )
            if retry_line:
                print(f"ERROR: triggering line: {retry_line}", file=sys.stderr)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            return 1

        if re.match(r"^AudioProviderError:\s+YT-DLP\s+download\s+error", line):
            if current_song:
                print(f"ERROR: YT-DLP download failed for {current_song}")
            else:
                print("ERROR: YT-DLP download failed (no song context from spotdl output)")
            suppress_next_url = True
            continue

        if suppress_next_url:
            if re.match(r"^https?://", line):
                suppress_next_url = False
                continue
            suppress_next_url = False

        if re.match(r"^\s*\(duplicate\)\s*$", line):
            continue

        print(line)

    return proc.wait()

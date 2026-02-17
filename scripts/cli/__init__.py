from __future__ import annotations

import re
import sys
from datetime import datetime
from typing import TextIO

_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")


class _TimestampedStream:
    def __init__(self, stream: TextIO) -> None:
        self._stream = stream
        self._at_line_start = True

    def write(self, data: str) -> int:
        if not data:
            return 0
        parts = data.splitlines(keepends=True)
        for part in parts:
            if self._at_line_start and part and part != "\n":
                if not _TIMESTAMP_RE.match(part):
                    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
                    self._stream.write(f"{timestamp} ")
            self._stream.write(part)
            self._at_line_start = part.endswith("\n")
        return len(data)

    def flush(self) -> None:
        self._stream.flush()

    def isatty(self) -> bool:
        return self._stream.isatty()

    def fileno(self) -> int:
        return self._stream.fileno()


def _install_timestamps() -> None:
    if not isinstance(sys.stdout, _TimestampedStream):
        sys.stdout = _TimestampedStream(sys.stdout)
    if not isinstance(sys.stderr, _TimestampedStream):
        sys.stderr = _TimestampedStream(sys.stderr)


_install_timestamps()

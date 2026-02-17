import sys
from datetime import datetime
from typing import Any


def log(*args: Any, quiet: bool = False) -> None:
    if quiet:
        return
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    message = " ".join(str(arg) for arg in args)
    print(f"{timestamp} {message}", file=sys.stderr)

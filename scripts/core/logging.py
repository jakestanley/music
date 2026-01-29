import sys
from typing import Any


def log(*args: Any, quiet: bool = False) -> None:
    if quiet:
        return
    print(*args, file=sys.stderr)

import os
from typing import Iterable, Optional

from dotenv import load_dotenv


def load_env(env_path: Optional[str] = None) -> None:
    if env_path and os.path.isfile(env_path):
        load_dotenv(env_path, override=False)
        return
    load_dotenv(override=False)


def require_var(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"Missing required env var: {name}")
    return value


def require_vars(names: Iterable[str]) -> None:
    for name in names:
        require_var(name)

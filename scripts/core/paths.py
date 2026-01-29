import os


def expand_path(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def resolve_dir(path: str) -> str:
    return os.path.realpath(expand_path(path))

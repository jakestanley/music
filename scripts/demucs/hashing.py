import hashlib
import os
from typing import Callable

from scripts.demucs.cache import HashCache


def file_mtime(path: str) -> int:
    return int(os.stat(path).st_mtime)


def file_size(path: str) -> int:
    return int(os.stat(path).st_size)


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_file_hash(path: str, cache: HashCache) -> str:
    mtime = file_mtime(path)
    size = file_size(path)
    entry = cache.get(path)
    if entry and entry.mtime == mtime and entry.size == size:
        return entry.digest
    digest = sha256_file(path)
    cache.update(path, mtime, size, digest)
    return digest

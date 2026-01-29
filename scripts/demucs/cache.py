import os
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class CacheEntry:
    mtime: int
    size: int
    digest: str


class HashCache:
    def __init__(self, cache_file: str) -> None:
        self.cache_file = cache_file
        self.entries: Dict[str, CacheEntry] = {}
        self.dirty = False

    def load(self) -> None:
        if not os.path.isfile(self.cache_file):
            self.dirty = True
            return
        with open(self.cache_file, encoding="utf-8") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) != 4:
                    continue
                path, mtime, size, digest = parts
                try:
                    self.entries[path] = CacheEntry(int(mtime), int(size), digest)
                except ValueError:
                    continue

    def get(self, path: str) -> CacheEntry | None:
        return self.entries.get(path)

    def update(self, path: str, mtime: int, size: int, digest: str) -> None:
        self.entries[path] = CacheEntry(mtime, size, digest)
        self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        with open(self.cache_file, "w", encoding="utf-8") as handle:
            for path in sorted(self.entries.keys()):
                entry = self.entries[path]
                handle.write(f"{path}\t{entry.mtime}\t{entry.size}\t{entry.digest}\n")
        self.dirty = False

import json
from typing import List, Tuple


def parse_manifest(path: str) -> List[Tuple[str, str]]:
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise SystemExit("Manifest must be a JSON array of playlist entries.")

    entries: List[Tuple[str, str]] = []
    for index, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            raise SystemExit(f"Manifest entry {index} must be an object.")
        url = (
            entry.get("playlist_url")
            or entry.get("playlistUrl")
            or entry.get("playlistURL")
            or entry.get("url")
        )
        root = (
            entry.get("root")
            or entry.get("path")
            or entry.get("target_dir")
            or entry.get("targetDir")
        )
        url = url.strip() if isinstance(url, str) else ""
        root = root.strip() if isinstance(root, str) else ""
        if not url:
            raise SystemExit(f"Manifest entry {index} is missing a playlist URL.")
        if not root:
            raise SystemExit(f"Manifest entry {index} is missing a root path.")
        entries.append((url, root))
    return entries

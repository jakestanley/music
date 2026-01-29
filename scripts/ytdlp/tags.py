import re
import subprocess


def sanitize_value(raw: str) -> str:
    raw = raw.replace("\n", " ").replace("\r", " ")
    return re.sub(r"[<>:\"/\\|?*]", "-", raw)


def set_id3_tags(path: str, artist: str, title: str) -> None:
    subprocess.run(["id3v2", "--artist", artist, "--song", title, path], check=True)

import re
import subprocess


def sanitize_value(raw: str) -> str:
    raw = raw.replace("\n", " ").replace("\r", " ")
    return re.sub(r"[<>:\"/\\|?*]", "-", raw)


def set_id3_tags(path: str, artist: str, title: str, album: str = "", track_number: int = 0) -> None:
    cmd = ["id3v2", "--artist", artist, "--song", title]
    if album:
        cmd += ["--album", album]
    if track_number:
        cmd += ["--track", str(track_number)]
    cmd.append(path)
    subprocess.run(cmd, check=True)

import os
import subprocess


def download_audio(link: str, output_template: str, cookie_file: str | None = None) -> None:
    cmd = ["yt-dlp", "--extract-audio", "--audio-format", "mp3"]
    resolved_cookie = cookie_file or os.environ.get("SPOTDL_COOKIE_FILE")
    if resolved_cookie:
        cmd += ["--cookies", resolved_cookie]
    cmd += ["--output", output_template, link]
    print(cmd)
    subprocess.run(cmd, check=True)

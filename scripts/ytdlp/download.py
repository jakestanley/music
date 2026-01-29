import subprocess


def download_audio(link: str, output_template: str) -> None:
    subprocess.run(
        ["yt-dlp", "--extract-audio", "--audio-format", "mp3", "--output", output_template, link],
        check=True,
    )

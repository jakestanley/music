import argparse
import os
from pathlib import Path

from scripts.core.shell import require_command
from scripts.ytdlp.download import download_audio
from scripts.ytdlp.tags import sanitize_value, set_id3_tags


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Downloads the audio track from the supplied YouTube link, tags it, and "
            "stores the resulting mp3 inside the requested directory."
        )
    )
    parser.add_argument("youtube_link")
    parser.add_argument("target_dir")
    parser.add_argument("artist")
    parser.add_argument("title")
    args = parser.parse_args()

    require_command("yt-dlp")
    require_command("id3v2")

    target_dir = args.target_dir
    os.makedirs(target_dir, exist_ok=True)

    safe_artist = sanitize_value(args.artist)
    safe_title = sanitize_value(args.title)
    output_basename = f"{safe_artist} - {safe_title}"
    output_template = os.path.join(target_dir, f"{output_basename}.%(ext)s")
    final_file = os.path.join(target_dir, f"{output_basename}.mp3")

    print("Parameters:")
    print(f"  YouTube link: {args.youtube_link}")
    print(f"  Target dir:   {target_dir}")
    print(f"  Artist:       {args.artist}")
    print(f"  Title:        {args.title}")

    confirm = input("Proceed with download? (y/N): ").strip()
    if confirm.lower() != "y":
        print("Aborting per user request.")
        return 1

    print(f"Downloading \"{args.title}\" by {args.artist}...")
    download_audio(args.youtube_link, output_template)

    if not os.path.isfile(final_file):
        raise SystemExit(f"Download did not produce the expected file: {final_file}")

    print(f"Setting ID3 tags on {Path(final_file).name}...")
    set_id3_tags(final_file, args.artist, args.title)

    print(f"Done: {final_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

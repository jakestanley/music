#!/usr/bin/env bash
set -euo pipefail

SCRIPT_NAME="${0##*/}"

usage() {
  cat <<EOF
Usage: $SCRIPT_NAME <YOUTUBE_LINK> <TARGET_DIR> <ARTIST> <TITLE>
Downloads the audio track from the supplied YouTube link, tags it, and
stores the resulting mp3 inside the requested directory.
EOF
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing prerequisite: $1" >&2
    exit 1
  fi
}

sanitize_value() {
  local raw="$1"
  raw="${raw//$'\n'/ }"
  raw="${raw//$'\r'/ }"
  printf '%s' "$raw" | sed 's#[<>:"/\\|?*]#-#g'
}

if [ "$#" -ne 4 ]; then
  usage
fi

require_command yt-dlp
require_command id3v2

YOUTUBE_LINK="$1"
TARGET_DIR="$2"
ARTIST="$3"
TITLE="$4"

mkdir -p "$TARGET_DIR"

SAFE_ARTIST="$(sanitize_value "$ARTIST")"
SAFE_TITLE="$(sanitize_value "$TITLE")"
OUTPUT_BASENAME="${SAFE_ARTIST} - ${SAFE_TITLE}"
OUTPUT_TEMPLATE="$TARGET_DIR/${OUTPUT_BASENAME}.%(ext)s"
FINAL_FILE="$TARGET_DIR/${OUTPUT_BASENAME}.mp3"

echo "Parameters:"
echo "  YouTube link: $YOUTUBE_LINK"
echo "  Target dir:   $TARGET_DIR"
echo "  Artist:       $ARTIST"
echo "  Title:        $TITLE"

read -r -p "Proceed with download? (y/N): " CONFIRM
case "$CONFIRM" in
  y|Y) ;;
  *) echo "Aborting per user request." && exit 1 ;;
esac

echo "Downloading \"$TITLE\" by $ARTIST..."
yt-dlp --extract-audio --audio-format mp3 --output "$OUTPUT_TEMPLATE" "$YOUTUBE_LINK"

if [ ! -f "$FINAL_FILE" ]; then
  echo "Download did not produce the expected file: $FINAL_FILE" >&2
  exit 1
fi

echo "Setting ID3 tags on $(basename "$FINAL_FILE")..."
id3v2 --artist "$ARTIST" --song "$TITLE" "$FINAL_FILE"

echo "Done: $FINAL_FILE"

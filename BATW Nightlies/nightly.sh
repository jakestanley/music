#!/usr/bin/env bash
set -euo pipefail

PREVIEW_FLAG=0

if [[ ${1:-} == "--preview" ]]; then
  PREVIEW_FLAG=1
  shift
fi

INPUT=${1:-}

if [[ -z "$INPUT" ]]; then
  echo "Usage: $0 [--preview] /path/to/song.(wav|mp3)" >&2
  exit 1
fi

if [[ ! -f "$INPUT" ]]; then
  echo "Input file not found: $INPUT" >&2
  exit 1
fi

DIR=$(dirname "$INPUT")
FILENAME=$(basename "$INPUT")
BASENAME=${FILENAME%.*}
MP3_FILE="$DIR/$BASENAME.mp3"

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
MP4_DIR="$SCRIPT_DIR/MP4s"
mkdir -p "$MP4_DIR"
OUTPUT_MP4="$MP4_DIR/$BASENAME.mp4"

# Allows overriding with env var but defaults to track 2
TRACK=${TRACK:-2}

# Tag the matching MP3 if it exists
if [[ -f "$MP3_FILE" ]]; then
  id3v2 --artist "Jake Stanley" --album "BATW Nightlies" --song "$BASENAME" --track "$TRACK" "$MP3_FILE"
fi

# Set PREVIEW to pass custom trim args (e.g. \"-ss 0 -t 10\")
PREVIEW=${PREVIEW:-""}
if [[ $PREVIEW_FLAG -eq 1 && -z "$PREVIEW" ]]; then
  PREVIEW="-t 15"
fi

FILTER_COMPLEX="
[0:a]showwaves=s=1920x540:mode=line:rate=30[w];
color=black:s=1920x1080[bg];
[bg][w]overlay=0:540,
drawtext=
fontfile=/System/Library/Fonts/Supplemental/Papyrus.ttc:
text='nightly':
fontcolor=white:
fontsize=42:
x=40:
y=h-text_h-40
[v]
"

if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q '\<h264_videotoolbox\>'; then
  VIDEO_CODEC="-c:v h264_videotoolbox"
  PIX_FMT=""
else
  VIDEO_CODEC="-c:v libx264"
  PIX_FMT="-pix_fmt yuv420p"
fi

ffmpeg -y $PREVIEW -i "$INPUT" \
 -filter_complex "$FILTER_COMPLEX" \
 -map "[v]" -map 0:a \
 -shortest \
 $VIDEO_CODEC $PIX_FMT \
 -c:a aac -b:a 320k \
 "$OUTPUT_MP4"

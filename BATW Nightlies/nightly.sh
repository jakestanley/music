#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--preview] [--portrait] [--range start:end] /path/to/song.(wav|mp3)" >&2
  echo "  --preview            Render only 15s (or use PREVIEW env for custom trim)." >&2
  echo "  --portrait           Output 1080x1920 instead of 1920x1080." >&2
  echo "  --range start:end    Trim to the given second range (overrides preview trim)." >&2
  exit 1
}

PREVIEW_FLAG=0
PORTRAIT_FLAG=0
RANGE_VALUE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preview)
      PREVIEW_FLAG=1
      shift
      ;;
    --portrait)
      PORTRAIT_FLAG=1
      shift
      ;;
    --range)
      RANGE_VALUE=${2:-}
      [[ -z "$RANGE_VALUE" ]] && usage
      shift 2
      ;;
    --help|-h)
      usage
      ;;
    -*)
      usage
      ;;
    *)
      break
      ;;
  esac
done

INPUT=${1:-}

if [[ -z "$INPUT" ]]; then
  usage
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
if [[ $PORTRAIT_FLAG -eq 1 ]]; then
  OUTPUT_MP4="$MP4_DIR/${BASENAME}_portrait.mp4"
fi

# Allows overriding with env var but defaults to track 2
TRACK=${TRACK:-2}

# Tag the matching MP3 if it exists
if [[ -f "$MP3_FILE" ]]; then
  id3v2 --artist "Jake Stanley" --album "BATW Nightlies" --song "$BASENAME" --track "$TRACK" "$MP3_FILE"
fi

# Build trim arguments
TRIM_ARGS=()
if [[ -n "${PREVIEW:-}" ]]; then
  # Allow custom trim via env
  # shellcheck disable=SC2206
  TRIM_ARGS=(${PREVIEW})
fi

if [[ $PREVIEW_FLAG -eq 1 && ${#TRIM_ARGS[@]} -eq 0 ]]; then
  TRIM_ARGS=(-t 15)
fi

if [[ -n "$RANGE_VALUE" ]]; then
  if [[ "$RANGE_VALUE" =~ ^([0-9]+(?:\\.[0-9]+)?):([0-9]+(?:\\.[0-9]+)?)$ ]]; then
    RANGE_START=${BASH_REMATCH[1]}
    RANGE_END=${BASH_REMATCH[2]}
    TRIM_ARGS=(-ss "$RANGE_START" -to "$RANGE_END")
  else
    echo "Invalid --range format. Use start:end (seconds), e.g. 30:75" >&2
    exit 1
  fi
fi

# Canvas + visualizer sizing
CANVAS_W=1920
CANVAS_H=1080
WAVE_W=$CANVAS_W
WAVE_H=540
ROTATE_FILTER=""

if [[ $PORTRAIT_FLAG -eq 1 ]]; then
  CANVAS_W=1080
  CANVAS_H=1920
  # Build tall visualizer: after transpose it will be 1080x1920 (full height)
  WAVE_W=$CANVAS_H   # 1920 -> becomes height after transpose
  WAVE_H=$CANVAS_W   # 1080 -> becomes width after transpose
  ROTATE_FILTER=",transpose=1"
fi

# Account for rotation when positioning overlay
VIS_W=$WAVE_W
VIS_H=$WAVE_H
if [[ -n "$ROTATE_FILTER" ]]; then
  VIS_W=$WAVE_H  # transpose swaps dimensions
  VIS_H=$WAVE_W
fi

OVERLAY_X=$(( (CANVAS_W - VIS_W) / 2 ))
OVERLAY_Y=$(( CANVAS_H - VIS_H ))

FILTER_COMPLEX=$(
  cat <<EOF
[0:a]showwaves=s=${WAVE_W}x${WAVE_H}:mode=line:rate=30${ROTATE_FILTER}[w];
color=black:s=${CANVAS_W}x${CANVAS_H}[bg];
[bg][w]overlay=${OVERLAY_X}:${OVERLAY_Y},
drawtext=
fontfile=/System/Library/Fonts/Supplemental/Papyrus.ttc:
text='nightly':
fontcolor=white:
fontsize=42:
x=40:
y=(h-text_h)/2
[v]
EOF
)

if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q '\<h264_videotoolbox\>'; then
  VIDEO_CODEC="-c:v h264_videotoolbox"
  PIX_FMT=""
else
  VIDEO_CODEC="-c:v libx264"
  PIX_FMT="-pix_fmt yuv420p"
fi

ffmpeg -y "${TRIM_ARGS[@]}" -i "$INPUT" \
 -filter_complex "$FILTER_COMPLEX" \
 -map "[v]" -map 0:a \
 -shortest \
 $VIDEO_CODEC $PIX_FMT \
 -c:a aac -b:a 320k \
 "$OUTPUT_MP4"

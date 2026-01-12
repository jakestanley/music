#!/usr/bin/env bash
set -euo pipefail

### CONFIG
# PLAYLIST_URL="https://open.spotify.com/playlist/7lvQQi0mGKUgyuR9pSjYkB"

# ROOT="$HOME/Music/BATW_Candidates"

### ARGUMENTS
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL[@]}"

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <ROOT_DIR> [4|2|both]"
  exit 1
fi

ROOT="$1"
MODE="${2:-both}"

case "$MODE" in
  4|2|both) ;;
  *)
    echo "Invalid mode: $MODE (use 4, 2, or both)"
    exit 1
    ;;
esac

if [ ! -d "$ROOT" ]; then
  echo "Root directory not found: $ROOT" >&2
  exit 1
fi

ROOT="$(cd "$ROOT" && pwd -P)"

BASE_DIR="$ROOT/unprocessed"
ALL_DIR="$ROOT/all"
VOCALS_DIR="$ROOT/vocals"

TMP_DIR="$HOME/Music/.demucs_tmp"

AUDIO_GLOB="*.mp3"
DEMUCS_MODEL="htdemucs"

### CLEAN START
rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR" "$BASE_DIR" "$ALL_DIR" "$VOCALS_DIR"

cd "$BASE_DIR"

### PROCESS FILES
for f in $AUDIO_GLOB; do
  [ -e "$f" ] || continue

  name="$(basename "$f" .mp3)"

  ALL_TRACK_DIR="$ALL_DIR/$name"
  VOCALS_TRACK_DIR="$VOCALS_DIR/$name"

  ### 4-STEM
  if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
    if [ -f "$ALL_TRACK_DIR/vocals.wav" ]; then
      echo "✓ 4-stem exists: $name"
    else
      echo "→ Demucs 4-stem: $name"

      demucs -n "$DEMUCS_MODEL" -o "$TMP_DIR" "$f"

      mv "$TMP_DIR/$DEMUCS_MODEL/$name" "$ALL_TRACK_DIR"
      rm -rf "$TMP_DIR/$DEMUCS_MODEL"
    fi
  fi

  ### 2-STEM
  if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
    if [ -f "$VOCALS_TRACK_DIR/vocals.wav" ]; then
      echo "✓ 2-stem exists: $name"
    else
      echo "→ Demucs vocals: $name"

      demucs -n "$DEMUCS_MODEL" --two-stems=vocals -o "$TMP_DIR" "$f"

      mv "$TMP_DIR/$DEMUCS_MODEL/$name" "$VOCALS_TRACK_DIR"
      rm -rf "$TMP_DIR/$DEMUCS_MODEL"
    fi
  fi
done

### FINAL CLEANUP
rm -rf "$TMP_DIR"

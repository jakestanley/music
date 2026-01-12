#!/usr/bin/env bash
set -euo pipefail

### ARGUMENTS
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --)
      shift
      break
      ;;
    -* )
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

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <PLAYLIST_URL> <ROOT_DIR>" >&2
  exit 1
fi

PLAYLIST_URL="$1"
ROOT="$2"

BASE_DIR="$ROOT/unprocessed"

if [ ! -d "$ROOT" ]; then
  echo "Root directory not found: $ROOT" >&2
  exit 1
fi

ROOT="$(cd "$ROOT" && pwd -P)"

BASE_DIR="$ROOT/unprocessed"

mkdir -p "$BASE_DIR"
cd "$BASE_DIR"

spotdl "$PLAYLIST_URL"

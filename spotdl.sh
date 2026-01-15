#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

require_var() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    echo "Missing required env var: $name" >&2
    exit 1
  fi
}

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

if [ ! -d "$ROOT" ]; then
  echo "Root directory not found: $ROOT" >&2
  exit 1
fi

ROOT="$(cd "$ROOT" && pwd -P)"

BASE_DIR="$ROOT/unprocessed"

mkdir -p "$BASE_DIR"
cd "$BASE_DIR"

require_var SPOTDL_CLIENT_ID
require_var SPOTDL_CLIENT_SECRET

spotdl "$PLAYLIST_URL" --client-id "$SPOTDL_CLIENT_ID" --client-secret "$SPOTDL_CLIENT_SECRET"

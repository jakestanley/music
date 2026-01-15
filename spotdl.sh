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

print_usage() {
  echo "Usage: $0 [--manifest <MANIFEST_FILE>] [--sync-file <FILE>] [--delay <SECONDS>] [--max-retries <N>] | <PLAYLIST_URL> <PLAYLIST_TARGET_DIR>" >&2
}

MANIFEST=""
SYNC_FILE_NAME="playlist.sync.spotdl"
DOWNLOAD_FILE_NAME="playlist.download.spotdl"
DELAY_SECONDS="2"
MAX_RETRIES="5"
THREADS="1"
MAX_RETRY_WAIT_SECONDS="60"
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --)
      shift
      break
      ;;
    --manifest=*)
      if [ -n "$MANIFEST" ]; then
        echo "Only one --manifest flag is allowed" >&2
        exit 1
      fi
      MANIFEST="${1#--manifest=}"
      if [ -z "$MANIFEST" ]; then
        echo "--manifest requires a file path" >&2
        exit 1
      fi
      shift
      ;;
    --manifest)
      if [ -n "$MANIFEST" ]; then
        echo "Only one --manifest flag is allowed" >&2
        exit 1
      fi
      shift
      if [ "$#" -eq 0 ]; then
        echo "--manifest requires a file path" >&2
        exit 1
      fi
      MANIFEST="$1"
      shift
      ;;
    --sync-file=*)
      SYNC_FILE_NAME="${1#--sync-file=}"
      if [ -z "$SYNC_FILE_NAME" ]; then
        echo "--sync-file requires a file name" >&2
        exit 1
      fi
      shift
      ;;
    --sync-file)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--sync-file requires a file name" >&2
        exit 1
      fi
      SYNC_FILE_NAME="$1"
      shift
      ;;
    --delay=*)
      DELAY_SECONDS="${1#--delay=}"
      if ! [[ "$DELAY_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        echo "--delay must be a number of seconds (e.g. 2 or 0.5)" >&2
        exit 1
      fi
      shift
      ;;
    --delay)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--delay requires a number of seconds" >&2
        exit 1
      fi
      DELAY_SECONDS="$1"
      if ! [[ "$DELAY_SECONDS" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        echo "--delay must be a number of seconds (e.g. 2 or 0.5)" >&2
        exit 1
      fi
      shift
      ;;
    --max-retries=*)
      MAX_RETRIES="${1#--max-retries=}"
      if ! [[ "$MAX_RETRIES" =~ ^[0-9]+$ ]]; then
        echo "--max-retries must be an integer" >&2
        exit 1
      fi
      shift
      ;;
    --max-retries)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--max-retries requires an integer" >&2
        exit 1
      fi
      MAX_RETRIES="$1"
      if ! [[ "$MAX_RETRIES" =~ ^[0-9]+$ ]]; then
        echo "--max-retries must be an integer" >&2
        exit 1
      fi
      shift
      ;;
    --threads=*)
      THREADS="${1#--threads=}"
      if ! [[ "$THREADS" =~ ^[0-9]+$ ]] || [ "$THREADS" -lt 1 ]; then
        echo "--threads must be an integer >= 1" >&2
        exit 1
      fi
      shift
      ;;
    --threads)
      shift
      if [ "$#" -eq 0 ]; then
        echo "--threads requires an integer >= 1" >&2
        exit 1
      fi
      THREADS="$1"
      if ! [[ "$THREADS" =~ ^[0-9]+$ ]] || [ "$THREADS" -lt 1 ]; then
        echo "--threads must be an integer >= 1" >&2
        exit 1
      fi
      shift
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

require_var SPOTDL_CLIENT_ID
require_var SPOTDL_CLIENT_SECRET

retry_wait_seconds_from_line() {
  local line="$1"
  local seconds=""

  if [[ "$line" =~ [Rr]etry[-[:space:]]*After[^0-9]*([0-9]+) ]]; then
    seconds="${BASH_REMATCH[1]}"
  elif [[ "$line" =~ [Rr]etry[^0-9]*after[^0-9]*([0-9]+) ]]; then
    seconds="${BASH_REMATCH[1]}"
  elif [[ "$line" =~ occur[^0-9]*after[^0-9]*([0-9]+) ]]; then
    seconds="${BASH_REMATCH[1]}"
  elif [[ "$line" =~ [Rr]etry(ing)?[^0-9]*in[^0-9]*([0-9]+)[[:space:]]*(seconds|second|secs|sec|s)\b ]]; then
    seconds="${BASH_REMATCH[2]}"
  elif [[ "$line" =~ [Ww]ait[^0-9]*([0-9]+)[[:space:]]*(seconds|second|secs|sec|s)\b ]]; then
    seconds="${BASH_REMATCH[1]}"
  fi

  if [ -n "$seconds" ]; then
    printf '%s' "$seconds"
    return 0
  fi

  return 1
}

run_spotdl_with_retry_wait_guard() {
  local -a args=("$@")
  printf 'Running: spotdl' >&2
  local i=0
  while [ "$i" -lt "${#args[@]}" ]; do
    local a="${args[$i]}"
    if [ "$a" = "--client-secret" ] || [ "$a" = "--auth-token" ]; then
      printf ' %q %q' "$a" "***REDACTED***" >&2
      i=$((i + 2))
      continue
    fi
    printf ' %q' "$a" >&2
    i=$((i + 1))
  done
  printf '\n' >&2

  local fifo
  fifo="$(mktemp "${TMPDIR:-/tmp}/spotdl-output.XXXXXX")"
  rm -f "$fifo"
  mkfifo "$fifo"

  spotdl "${args[@]}" >"$fifo" 2>&1 &
  local spotdl_pid=$!

  local abort=0
  local retry_wait=""
  local retry_line=""
  local line
  while IFS= read -r line; do
    printf '%s\n' "$line"
    if retry_wait="$(retry_wait_seconds_from_line "$line")"; then
      if [ "$retry_wait" -gt "$MAX_RETRY_WAIT_SECONDS" ]; then
        abort=1
        retry_line="$line"
        break
      fi
    fi
  done <"$fifo"

  if [ "$abort" -eq 1 ]; then
    echo "ERROR: rate limit hit; spotdl wants to wait ${retry_wait}s (> ${MAX_RETRY_WAIT_SECONDS}s). Aborting." >&2
    if [ -n "$retry_line" ]; then
      echo "ERROR: triggering line: $retry_line" >&2
    fi
    kill "$spotdl_pid" 2>/dev/null || true
    for _ in {1..50}; do
      if ! kill -0 "$spotdl_pid" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    if kill -0 "$spotdl_pid" 2>/dev/null; then
      kill -KILL "$spotdl_pid" 2>/dev/null || true
    fi
    wait "$spotdl_pid" 2>/dev/null || true
    rm -f "$fifo"
    return 1
  fi

  wait "$spotdl_pid"
  local status=$?
  rm -f "$fifo"
  return "$status"
}

download_playlist() {
  local playlist_url="$1"
  local root="$2"

  if [ -z "$playlist_url" ] || [ -z "$root" ]; then
    echo "Internal error: playlist URL or root missing" >&2
    exit 1
  fi

  if [ ! -d "$root" ]; then
    echo "Root directory not found: $root" >&2
    exit 1
  fi

  local absolute_root
  absolute_root="$(cd "$root" && pwd -P)"
  local base_dir="$absolute_root/unprocessed"
  local sync_file="$absolute_root/$SYNC_FILE_NAME"
  local download_file="$absolute_root/$DOWNLOAD_FILE_NAME"
  mkdir -p "$base_dir"
  mkdir -p "$(dirname "$sync_file")"

  if ! (
    cd "$base_dir"
    local -a spotdl_args=(
      --client-id "$SPOTDL_CLIENT_ID"
      --client-secret "$SPOTDL_CLIENT_SECRET"
      --use-cache-file
      --save-errors "$absolute_root/spotdl.errors.json"
    )
    if [ -n "$MAX_RETRIES" ]; then
      spotdl_args+=(--max-retries "$MAX_RETRIES")
    fi
    spotdl_args+=(--threads "$THREADS")

    if [ -f "$download_file" ]; then
      run_spotdl_with_retry_wait_guard download "$download_file" "${spotdl_args[@]}" || exit $?
    elif [ -f "$sync_file" ]; then
      run_spotdl_with_retry_wait_guard sync "$sync_file" "${spotdl_args[@]}" || exit $?
    else
      run_spotdl_with_retry_wait_guard sync "$playlist_url" --save-file "$sync_file" "${spotdl_args[@]}" || exit $?
    fi
  ); then
    exit $?
  fi

  if [ "$DELAY_SECONDS" != "0" ]; then
    sleep "$DELAY_SECONDS"
  fi
}

parse_manifest() {
  local manifest_path="$1"

  if [ ! -f "$manifest_path" ]; then
    echo "Manifest file not found: $manifest_path" >&2
    exit 1
  fi

  mapfile -t MANIFEST_ENTRIES < <(
    python3 - "$manifest_path" <<'PY'
import json, sys, os

path = sys.argv[1]

with open(path, encoding="utf-8") as handle:
    data = json.load(handle)

if isinstance(data, dict):
    data = [data]
if not isinstance(data, list):
    raise SystemExit("Manifest must be a JSON array of playlist entries.")

for index, entry in enumerate(data, start=1):
    if not isinstance(entry, dict):
        raise SystemExit(f"Manifest entry {index} must be an object.")
    url = (
        entry.get("playlist_url")
        or entry.get("playlistUrl")
        or entry.get("playlistURL")
        or entry.get("url")
    )
    root = (
        entry.get("root")
        or entry.get("path")
        or entry.get("target_dir")
        or entry.get("targetDir")
    )
    url = url.strip() if isinstance(url, str) else ""
    root = root.strip() if isinstance(root, str) else ""
    if not url:
        raise SystemExit(f"Manifest entry {index} is missing a playlist URL.")
    if not root:
        raise SystemExit(f"Manifest entry {index} is missing a root path.")
    print(f"{url}\t{root}")
PY
  )

  if [ "${#MANIFEST_ENTRIES[@]}" -eq 0 ]; then
    echo "Manifest file contains no playlist entries: $manifest_path" >&2
    exit 1
  fi
}

if [ -n "$MANIFEST" ]; then
  if [ "${#POSITIONAL[@]}" -ne 0 ]; then
    echo "Cannot mix manifest mode with positional arguments" >&2
    print_usage
    exit 1
  fi

  parse_manifest "$MANIFEST"

  for entry in "${MANIFEST_ENTRIES[@]}"; do
    PLAYLIST_URL="${entry%%$'\t'*}"
    ROOT="${entry#*$'\t'}"
    if [ "$PLAYLIST_URL" = "$entry" ]; then
      echo "Internal error: manifest entry is malformed" >&2
      exit 1
    fi
    download_playlist "$PLAYLIST_URL" "$ROOT"
  done
else
  if [ "${#POSITIONAL[@]}" -ne 2 ]; then
    print_usage
    exit 1
  fi

  download_playlist "${POSITIONAL[0]}" "${POSITIONAL[1]}"
fi

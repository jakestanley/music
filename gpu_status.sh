#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing .env (copy .env.example and fill in values)" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

require_var() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    echo "Missing required env var: $name" >&2
    exit 1
  fi
}

require_var WINDOWS_SSH_TARGET
require_var WINDOWS_SSH_KEY

if [ ! -f "$WINDOWS_SSH_KEY" ]; then
  echo "SSH key not found: $WINDOWS_SSH_KEY" >&2
  exit 1
fi

INTERVAL=1
ONCE=0
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --once)
      ONCE=1
      shift
      ;;
    --interval)
      INTERVAL="$2"
      shift 2
      ;;
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

if [ "$#" -gt 0 ] || [ "${#POSITIONAL[@]}" -gt 0 ]; then
  echo "Usage: $0 [--once] [--interval <seconds>]" >&2
  exit 1
fi

ssh_cmd=(ssh -i "$WINDOWS_SSH_KEY" "$WINDOWS_SSH_TARGET")

query_gpu() {
  "${ssh_cmd[@]}" "powershell -NoProfile -Command \"nvidia-smi --query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total --format=csv,noheader,nounits\""
}

format_line() {
  local util temp mem_used mem_total
  IFS=',' read -r util temp mem_used mem_total <<<"$1"
  util="$(echo "$util" | xargs)"
  temp="$(echo "$temp" | xargs)"
  mem_used="$(echo "$mem_used" | xargs)"
  mem_total="$(echo "$mem_total" | xargs)"
  local used_gb total_gb
  used_gb="$(awk -v v="$mem_used" 'BEGIN { printf "%.1f", v/1024 }')"
  total_gb="$(awk -v v="$mem_total" 'BEGIN { printf "%.1f", v/1024 }')"
  printf "%s%%  %sC  %s/%s GB\n" "$util" "$temp" "$used_gb" "$total_gb"
}

if [ "$ONCE" -eq 1 ]; then
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    format_line "$line"
  done < <(query_gpu)
  exit 0
fi

while true; do
  output=""
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    output+=$(format_line "$line")
  done < <(query_gpu)
  clear
  printf "%s" "$output"
  sleep "$INTERVAL"
done

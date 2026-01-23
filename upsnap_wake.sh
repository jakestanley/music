#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ENV_FILE="$SCRIPT_DIR/.env"

PRINT_TOKEN=0
QUIET=0
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --print-token)
      PRINT_TOKEN=1
      shift
      ;;
    --quiet)
      QUIET=1
      shift
      ;;
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

if [ "$#" -gt 0 ] || [ "${#POSITIONAL[@]}" -gt 0 ]; then
  echo "Usage: $0 [--print-token] [--quiet]" >&2
  exit 1
fi

log() {
  if [ "$QUIET" -eq 0 ]; then
    echo "$@" >&2
  fi
}

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing .env (copy .env.example and fill in values): $ENV_FILE" >&2
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

require_var UPSNAP_HOST
require_var UPSNAP_USERNAME
require_var UPSNAP_PASSWORD

get_token() {
  curl -s -X POST "$UPSNAP_HOST/api/collections/_superusers/auth-with-password" \
    -H "Content-Type: application/json" \
    -d "{\"identity\":\"$UPSNAP_USERNAME\",\"password\":\"$UPSNAP_PASSWORD\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token", ""))'
}

token="$(get_token)"
if [ -z "$token" ]; then
  echo "Failed to authenticate with UpSnap" >&2
  exit 1
fi

device_id="${UPSNAP_DEVICE_ID:-}"
if [ -z "$device_id" ]; then
  require_var UPSNAP_DEVICE_NAME
  device_id="$(curl -s "$UPSNAP_HOST/api/collections/devices/records" \
    -H "Authorization: Bearer $token" \
    | python3 -c 'import json,sys,os; data=json.load(sys.stdin); name=os.environ.get("UPSNAP_DEVICE_NAME"); records=data.get("items", []); match=next((r for r in records if r.get("name")==name), None); print(match.get("id", "") if match else "")')"
fi

if [ -z "$device_id" ]; then
  echo "Unable to resolve UpSnap device id" >&2
  echo "Available devices:" >&2
  curl -s "$UPSNAP_HOST/api/collections/devices/records" \
    -H "Authorization: Bearer $token" \
    | python3 -c 'import json,sys; data=json.load(sys.stdin); records=data.get("items", []); [print("{}  {}".format(r.get("id",""), r.get("name",""))) for r in records]'
  exit 1
fi

log "Requesting UpSnap wake for device: $device_id"
curl -s "$UPSNAP_HOST/api/upsnap/wake/$device_id" -H "Authorization: Bearer $token" >/dev/null

if [ "$PRINT_TOKEN" -eq 1 ]; then
  printf '%s\t%s\n' "$device_id" "$token"
else
  printf '%s\n' "$device_id"
fi


#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "$SCRIPT_DIR/.." && pwd)

CRON_MINUTE=${CRON_MINUTE:-0}
CRON_HOUR=${CRON_HOUR:-3}
CRON_DOM=${CRON_DOM:-*}
CRON_MONTH=${CRON_MONTH:-*}
CRON_DOW=${CRON_DOW:-*}
CRON_TAG=${CRON_TAG:-homelab-music-all}
CRON_MANIFEST=${CRON_MANIFEST:-manifest.json}
CRON_LOG=${CRON_LOG:-logs/all.cron.log}

if ! command -v crontab >/dev/null 2>&1; then
  echo "ERROR: crontab not found on PATH." >&2
  exit 1
fi

if ! [[ "$CRON_MINUTE" =~ ^[0-9]+$ ]] || ((CRON_MINUTE < 0 || CRON_MINUTE > 59)); then
  echo "ERROR: CRON_MINUTE must be 0-59 (got: $CRON_MINUTE)." >&2
  exit 1
fi

if ! [[ "$CRON_HOUR" =~ ^[0-9]+$ ]] || ((CRON_HOUR < 0 || CRON_HOUR > 23)); then
  echo "ERROR: CRON_HOUR must be 0-23 (got: $CRON_HOUR)." >&2
  exit 1
fi

MANIFEST_PATH=$CRON_MANIFEST
if [[ "$MANIFEST_PATH" != /* ]]; then
  MANIFEST_PATH="$ROOT_DIR/$MANIFEST_PATH"
fi

LOG_PATH=$CRON_LOG
if [[ "$LOG_PATH" != /* ]]; then
  LOG_PATH="$ROOT_DIR/$LOG_PATH"
fi
mkdir -p "$(dirname "$LOG_PATH")"

CRON_CMD="cd $ROOT_DIR && ./all.sh --manifest $MANIFEST_PATH >> $LOG_PATH 2>&1"
CRON_LINE="$CRON_MINUTE $CRON_HOUR $CRON_DOM $CRON_MONTH $CRON_DOW $CRON_CMD # $CRON_TAG"

TMP_CRON=$(mktemp)
trap 'rm -f "$TMP_CRON"' EXIT

if crontab -l >"$TMP_CRON" 2>/dev/null; then
  true
else
  : >"$TMP_CRON"
fi

grep -v "# $CRON_TAG" "$TMP_CRON" >"${TMP_CRON}.new" || true
printf "%s\n" "$CRON_LINE" >>"${TMP_CRON}.new"

crontab "${TMP_CRON}.new"

echo "Installed cron job: $CRON_TAG ($CRON_MINUTE $CRON_HOUR $CRON_DOM $CRON_MONTH $CRON_DOW)"
echo "Command: $CRON_CMD"
echo "Log: $LOG_PATH"

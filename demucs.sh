#!/usr/bin/env bash
set -euo pipefail

### ARGUMENTS
USE_WINDOWS=0
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --windows)
      USE_WINDOWS=1
      shift
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

set -- "${POSITIONAL[@]}"

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 [--windows] <ROOT_DIR> [4|2|both]" >&2
  exit 1
fi

ROOT="$1"
MODE="${2:-both}"

case "$MODE" in
  4|2|both) ;;
  *)
    echo "Invalid mode: $MODE (use 4, 2, or both)" >&2
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

DEMUCS_MODEL="${DEMUCS_MODEL:-htdemucs}"

if [ ! -d "$BASE_DIR" ]; then
  echo "Unprocessed directory not found: $BASE_DIR" >&2
  exit 1
fi

shopt -s nullglob
mp3_files=("$BASE_DIR"/*.mp3)
shopt -u nullglob

if [ "${#mp3_files[@]}" -eq 0 ]; then
  echo "No MP3 files found in $BASE_DIR" >&2
  exit 0
fi

missing_files=()
collect_missing_files() {
  local f name
  missing_files=()
  for f in "${mp3_files[@]}"; do
    name="$(basename "$f" .mp3)"
    if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
      if [ ! -f "$ALL_DIR/$name/vocals.wav" ]; then
        missing_files+=("$f")
        continue
      fi
    fi
    if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
      if [ ! -f "$VOCALS_DIR/$name/vocals.wav" ]; then
        missing_files+=("$f")
        continue
      fi
    fi
  done
}

run_local() {
  local f name
  local tmp_dir="$HOME/Music/.demucs_tmp"

  rm -rf "$tmp_dir"
  mkdir -p "$tmp_dir" "$BASE_DIR" "$ALL_DIR" "$VOCALS_DIR"

  for f in "${mp3_files[@]}"; do
    name="$(basename "$f" .mp3)"

    local all_track_dir="$ALL_DIR/$name"
    local vocals_track_dir="$VOCALS_DIR/$name"

    if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
      if [ -f "$all_track_dir/vocals.wav" ]; then
        echo "✓ 4-stem exists: $name"
      else
        echo "→ Demucs 4-stem: $name"
        demucs -n "$DEMUCS_MODEL" -o "$tmp_dir" "$f"
        mv "$tmp_dir/$DEMUCS_MODEL/$name" "$all_track_dir"
        rm -rf "$tmp_dir/$DEMUCS_MODEL"
      fi
    fi

    if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
      if [ -f "$vocals_track_dir/vocals.wav" ]; then
        echo "✓ 2-stem exists: $name"
      else
        echo "→ Demucs vocals: $name"
        demucs -n "$DEMUCS_MODEL" --two-stems=vocals -o "$tmp_dir" "$f"
        mv "$tmp_dir/$DEMUCS_MODEL/$name" "$vocals_track_dir"
        rm -rf "$tmp_dir/$DEMUCS_MODEL"
      fi
    fi
  done

  rm -rf "$tmp_dir"
}

run_windows() {
  local script_dir env_file
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
  env_file="$script_dir/.env"

  if [ ! -f "$env_file" ]; then
    echo "Missing .env (copy .env.example and fill in values)" >&2
    exit 1
  fi

  set -a
  # shellcheck disable=SC1090
  source "$env_file"
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
  require_var WINDOWS_SSH_TARGET
  require_var WINDOWS_SSH_KEY

  if [ ! -f "$WINDOWS_SSH_KEY" ]; then
    echo "SSH key not found: $WINDOWS_SSH_KEY" >&2
    exit 1
  fi

  DEMUCS_MODEL="${WINDOWS_DEMUCS_MODEL:-$DEMUCS_MODEL}"
  local demucs_device="${WINDOWS_DEMUCS_DEVICE:-cuda}"
  local demucs_device_arg=()
  if [ -n "$demucs_device" ]; then
    demucs_device_arg=(--device "$demucs_device")
  fi
  local windows_python="${WINDOWS_PYTHON:-python}"

  collect_missing_files
  if [ "${#missing_files[@]}" -eq 0 ]; then
    echo "All requested stems exist for $ROOT; skipping remote run."
    exit 0
  fi

  local ssh_cmd=(ssh -i "$WINDOWS_SSH_KEY" "$WINDOWS_SSH_TARGET")
  local scp_cmd=(scp -i "$WINDOWS_SSH_KEY")

  win_ps() {
    local cmd="$1"
    cmd="${cmd//\"/\\\"}"
    "${ssh_cmd[@]}" "powershell -NoProfile -Command \"$cmd\""
  }

  get_token() {
    curl -s -X POST "$UPSNAP_HOST/api/collections/_superusers/auth-with-password" \
      -H "Content-Type: application/json" \
      -d "{\"identity\":\"$UPSNAP_USERNAME\",\"password\":\"$UPSNAP_PASSWORD\"}" \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token", ""))'
  }

  local token
  token="$(get_token)"
  if [ -z "$token" ]; then
    echo "Failed to authenticate with UpSnap" >&2
    exit 1
  fi

  local device_id
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

  local should_sleep=0
  local win_tmp=""

  cleanup() {
    set +e
    if [ -n "${win_tmp-}" ]; then
      win_ps "Remove-Item -Recurse -Force '$win_tmp'" >/dev/null 2>&1
    fi
    if [ "${should_sleep-0}" -eq 1 ] && [ -n "${device_id-}" ] && [ -n "${token-}" ]; then
      if read -r -t 120 -p "Press Enter to skip Windows sleep (auto-sleep in 120s): " _; then
        echo "Skipping Windows sleep."
        return
      fi
      curl -s "$UPSNAP_HOST/api/upsnap/shutdown/$device_id" -H "Authorization: Bearer $token" >/dev/null 2>&1
    fi
  }

  trap cleanup EXIT

  curl -s "$UPSNAP_HOST/api/upsnap/wake/$device_id" -H "Authorization: Bearer $token" >/dev/null
  should_sleep=1

  for _ in {1..20}; do
    if "${ssh_cmd[@]}" -o BatchMode=yes -o ConnectTimeout=5 "exit" >/dev/null 2>&1; then
      break
    fi
    sleep 5
  done

  if ! "${ssh_cmd[@]}" -o BatchMode=yes -o ConnectTimeout=5 "exit" >/dev/null 2>&1; then
    echo "SSH not available on Windows host" >&2
    exit 1
  fi

  win_tmp="$(win_ps "\$tmp = Join-Path \$env:TEMP 'demucs_tmp'; New-Item -ItemType Directory -Path \$tmp -Force | Out-Null; Write-Output \$tmp")"
  win_tmp="${win_tmp//$'\r'/}"
  if [ -z "$win_tmp" ]; then
    echo "Failed to resolve Windows temp path" >&2
    exit 1
  fi
  local win_tmp_scp="${win_tmp//\\//}"
  if [[ "$win_tmp_scp" =~ ^[A-Za-z]:/ ]]; then
    win_tmp_scp="/$win_tmp_scp"
  fi
  local win_input_ps="${win_tmp}\\input"
  local win_out4_ps="${win_tmp}\\out4"
  local win_out2_ps="${win_tmp}\\out2"
  local win_input_scp="$win_tmp_scp/input"
  local win_out4_scp="$win_tmp_scp/out4"
  local win_out2_scp="$win_tmp_scp/out2"

  win_ps "New-Item -ItemType Directory -Path '$win_input_ps' -Force | Out-Null; New-Item -ItemType Directory -Path '$win_out4_ps' -Force | Out-Null; New-Item -ItemType Directory -Path '$win_out2_ps' -Force | Out-Null"

  if [ "$demucs_device" = "cuda" ]; then
    if ! win_ps "& '$windows_python' -c \"import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('torch') else 1)\""; then
      echo "CUDA check failed: Python 'torch' module not available on Windows host" >&2
      exit 1
    fi
    if ! win_ps "& '$windows_python' -c \"import torch,sys; sys.exit(0 if torch.cuda.is_available() else 1)\""; then
      echo "CUDA not available on Windows host (torch.cuda.is_available() is false)" >&2
      exit 1
    fi
  fi

  "${scp_cmd[@]}" -r "${missing_files[@]}" "${WINDOWS_SSH_TARGET}:$win_input_scp/"

  if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
    win_ps "\$env:PYTHONUTF8='1'; \$env:PYTHONIOENCODING='utf-8'; \$files = Get-ChildItem -Path '$win_input_ps' -Filter '*.mp3' -File | ForEach-Object { \$_.FullName }; if (\$files.Count -eq 0) { Write-Error 'No MP3 files found in Windows input folder'; exit 1 } ; demucs ${demucs_device_arg[*]} -n $DEMUCS_MODEL -o '$win_out4_ps' \$files"
  fi

  if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
    win_ps "\$env:PYTHONUTF8='1'; \$env:PYTHONIOENCODING='utf-8'; \$files = Get-ChildItem -Path '$win_input_ps' -Filter '*.mp3' -File | ForEach-Object { \$_.FullName }; if (\$files.Count -eq 0) { Write-Error 'No MP3 files found in Windows input folder'; exit 1 } ; demucs ${demucs_device_arg[*]} -n $DEMUCS_MODEL --two-stems=vocals -o '$win_out2_ps' \$files"
  fi

  mkdir -p "$ALL_DIR" "$VOCALS_DIR"

  if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
    "${scp_cmd[@]}" -r "${WINDOWS_SSH_TARGET}:$win_out4_scp/htdemucs" "$ALL_DIR/"
    if [ -d "$ALL_DIR/htdemucs" ]; then
      if compgen -G "$ALL_DIR/htdemucs/*" >/dev/null; then
        mv "$ALL_DIR"/htdemucs/* "$ALL_DIR/"
      fi
      rmdir "$ALL_DIR/htdemucs" 2>/dev/null || true
    fi
  fi

  if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
    "${scp_cmd[@]}" -r "${WINDOWS_SSH_TARGET}:$win_out2_scp/htdemucs" "$VOCALS_DIR/"
    if [ -d "$VOCALS_DIR/htdemucs" ]; then
      if compgen -G "$VOCALS_DIR/htdemucs/*" >/dev/null; then
        mv "$VOCALS_DIR"/htdemucs/* "$VOCALS_DIR/"
      fi
      rmdir "$VOCALS_DIR/htdemucs" 2>/dev/null || true
    fi
  fi
}

if [ "$USE_WINDOWS" -eq 1 ]; then
  run_windows
else
  run_local
fi

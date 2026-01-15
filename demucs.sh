#!/usr/bin/env bash
set -euo pipefail

### ARGUMENTS
CLEAN_WINDOWS=0
USE_WINDOWS=0
POSITIONAL=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --clean)
      CLEAN_WINDOWS=1
      shift
      ;;
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

if [ "$#" -lt 1 ]; then
  echo "Usage: $0 [--windows] [--clean] <ROOT_DIR...> [4|2|both]" >&2
  exit 1
fi

MODE="both"
last_index=$(($# - 1))
last_arg="${!#}"
if [[ "$last_arg" == "4" || "$last_arg" == "2" || "$last_arg" == "both" ]]; then
  MODE="$last_arg"
  unset "POSITIONAL[$last_index]"
fi

ROOTS=("${POSITIONAL[@]}")
if [ "${#ROOTS[@]}" -eq 0 ]; then
  echo "Usage: $0 [--windows] [--clean] <ROOT_DIR...> [4|2|both]" >&2
  exit 1
fi

case "$MODE" in
  4|2|both) ;;
  *)
    echo "Invalid mode: $MODE (use 4, 2, or both)" >&2
    exit 1
    ;;
esac

DEMUCS_MODEL="${DEMUCS_MODEL:-htdemucs}"

HASH_CMD=()
if command -v sha256sum >/dev/null 2>&1; then
  HASH_CMD=(sha256sum)
elif command -v shasum >/dev/null 2>&1; then
  HASH_CMD=(shasum -a 256)
else
  echo "Missing hash tool (sha256sum or shasum required)" >&2
  exit 1
fi

declare -A ROOT_BASE_DIR ROOT_ALL_DIR ROOT_VOCALS_DIR ROOT_MP3S ROOT_CACHE_FILE
ROOTS_ABS=()
for root in "${ROOTS[@]}"; do
  if [ ! -d "$root" ]; then
    echo "Root directory not found: $root" >&2
    exit 1
  fi
  root_abs="$(cd "$root" && pwd -P)"
  base_dir="$root_abs/unprocessed"
  all_dir="$root_abs/all"
  vocals_dir="$root_abs/vocals"

  if [ ! -d "$base_dir" ]; then
    echo "Unprocessed directory not found: $base_dir" >&2
    exit 1
  fi

  shopt -s nullglob
  mp3_list=("$base_dir"/*.mp3)
  shopt -u nullglob

  ROOTS_ABS+=("$root_abs")
  ROOT_BASE_DIR["$root_abs"]="$base_dir"
  ROOT_ALL_DIR["$root_abs"]="$all_dir"
  ROOT_VOCALS_DIR["$root_abs"]="$vocals_dir"
  ROOT_MP3S["$root_abs"]="$(printf '%s\n' "${mp3_list[@]}")"
  ROOT_CACHE_FILE["$root_abs"]="$root_abs/.demucs_hash_cache"
done

declare -A FILE_HASH HASH_TO_ALL_DIR HASH_TO_VOCALS_DIR ROOT_SYMLINKED ROOT_MISSING ROOT_TOTAL
declare -A CACHE_HASH CACHE_MTIME CACHE_SIZE CACHE_DIRTY
CURRENT_ROOT=""

missing_files=()
normalize_windows_name() {
  local n="$1"
  while [[ "$n" =~ [[:space:].]$ ]]; do
    n="${n%?}"
  done
  printf '%s' "$n"
}

file_mtime() {
  if stat -c %Y "$1" >/dev/null 2>&1; then
    stat -c %Y "$1"
  else
    stat -f %m "$1"
  fi
}

file_size() {
  if stat -c %s "$1" >/dev/null 2>&1; then
    stat -c %s "$1"
  else
    stat -f %z "$1"
  fi
}

load_cache_for_root() {
  local root="$1"
  local cache_file="${ROOT_CACHE_FILE[$root]}"
  if [ -f "$cache_file" ]; then
    while IFS=$'\t' read -r path mtime size hash; do
      [ -n "$path" ] || continue
      CACHE_MTIME["$path"]="$mtime"
      CACHE_SIZE["$path"]="$size"
      CACHE_HASH["$path"]="$hash"
    done < "$cache_file"
  else
    CACHE_DIRTY["$root"]=1
  fi
}

save_cache_for_root() {
  local root="$1"
  local cache_file="${ROOT_CACHE_FILE[$root]}"
  local tmp_file="${cache_file}.tmp"
  : > "$tmp_file"
  local count=0
  local base_dir="${ROOT_BASE_DIR[$root]}"
  local f hash
  shopt -s nullglob
  local mp3_list=("$base_dir"/*.mp3)
  shopt -u nullglob
  for f in "${mp3_list[@]}"; do
    hash="${CACHE_HASH[$f]-}"
    if [ -z "$hash" ]; then
      hash="$(get_file_hash "$f")"
    fi
    local mtime="${CACHE_MTIME[$f]-}"
    local size="${CACHE_SIZE[$f]-}"
    if [ -z "$mtime" ] || [ -z "$size" ]; then
      mtime="$(file_mtime "$f")"
      size="$(file_size "$f")"
      CACHE_MTIME["$f"]="$mtime"
      CACHE_SIZE["$f"]="$size"
    fi
    printf '%s\t%s\t%s\t%s\n' "$f" "$mtime" "$size" "$hash" >> "$tmp_file"
    count=$((count + 1))
  done
  if [ -f "$cache_file" ]; then
    cat "$cache_file" "$tmp_file" \
      | awk -F'\t' '{a[$1]=$0; order[NR]=$1} END{for (i=1;i<=NR;i++){k=order[i]; if(!seen[k]++){print a[k]}}}' \
      > "$cache_file"
    rm -f "$tmp_file"
  else
    mv "$tmp_file" "$cache_file"
  fi
  echo "Saved hash cache to $cache_file ($count entries)"
  CACHE_DIRTY["$root"]=0
}

get_file_hash() {
  local f="$1"
  if [ -n "${FILE_HASH[$f]-}" ]; then
    printf '%s' "${FILE_HASH[$f]}"
    return 0
  fi
  local mtime size
  mtime="$(file_mtime "$f")"
  size="$(file_size "$f")"
  if [ -n "${CACHE_HASH[$f]-}" ] && [ "${CACHE_MTIME[$f]-}" = "$mtime" ] && [ "${CACHE_SIZE[$f]-}" = "$size" ]; then
    FILE_HASH["$f"]="${CACHE_HASH[$f]}"
    printf '%s' "${CACHE_HASH[$f]}"
    return 0
  fi
  local hash
  hash="$("${HASH_CMD[@]}" "$f" | awk '{print $1}')"
  FILE_HASH["$f"]="$hash"
  CACHE_HASH["$f"]="$hash"
  CACHE_MTIME["$f"]="$mtime"
  CACHE_SIZE["$f"]="$size"
  if [ -n "$CURRENT_ROOT" ]; then
    CACHE_DIRTY["$CURRENT_ROOT"]=1
  fi
  printf '%s' "$hash"
}

find_stem_dir() {
  local stem_root="$1"
  shift
  local candidate
  for candidate in "$@"; do
    if [ -f "$stem_root/$candidate/vocals.wav" ]; then
      printf '%s' "$stem_root/$candidate"
      return 0
    fi
  done
  return 1
}

build_hash_index() {
  local root f name win_name
  for root in "${ROOTS_ABS[@]}"; do
    CURRENT_ROOT="$root"
    load_cache_for_root "$root"
    local all_dir="${ROOT_ALL_DIR[$root]}"
    local vocals_dir="${ROOT_VOCALS_DIR[$root]}"
    local mp3_list="${ROOT_MP3S[$root]}"
    [ -n "$mp3_list" ] || continue
    local total=0
    local hashed=0
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      total=$((total + 1))
    done <<<"$mp3_list"
    ROOT_TOTAL["$root"]="$total"
    echo "Hashing index for $root ($total files)..."

    local processed=0
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      processed=$((processed + 1))
      if [ "$processed" -eq 1 ] || [ $((processed % 10)) -eq 0 ] || [ "$processed" -eq "$total" ]; then
        echo "  hashed $processed/$total"
      fi
      name="$(basename "$f" .mp3)"
      win_name=""
      if [ "$USE_WINDOWS" -eq 1 ]; then
        win_name="$(normalize_windows_name "$name")"
      fi
      local name_candidates=("$name")
      if [ -n "$win_name" ] && [ "$win_name" != "$name" ]; then
        name_candidates+=("$win_name")
      fi
      local hash
      hash="$(get_file_hash "$f")"
      hashed=$((hashed + 1))

      if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
        local stem_dir
        if stem_dir="$(find_stem_dir "$all_dir" "${name_candidates[@]}")"; then
          if [ -z "${HASH_TO_ALL_DIR[$hash]-}" ]; then
            HASH_TO_ALL_DIR["$hash"]="$stem_dir"
          fi
        fi
      fi

      if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
        local stem_dir
        if stem_dir="$(find_stem_dir "$vocals_dir" "${name_candidates[@]}")"; then
          if [ -z "${HASH_TO_VOCALS_DIR[$hash]-}" ]; then
            HASH_TO_VOCALS_DIR["$hash"]="$stem_dir"
          fi
        fi
      fi
      if [ $((processed % 10)) -eq 0 ]; then
        CACHE_DIRTY["$root"]=1
        save_cache_for_root "$root"
      fi
    done <<<"$mp3_list"
    CACHE_DIRTY["$root"]=1
    save_cache_for_root "$root"
    echo "Indexed $hashed hashes for $root."
  done
}

prepare_missing_files() {
  local f name win_name hash
  missing_files=()
  local symlinked=0
  local missing=0
  mkdir -p "$ALL_DIR" "$VOCALS_DIR"
  for f in "${mp3_files[@]}"; do
    name="$(basename "$f" .mp3)"
    win_name=""
    if [ "$USE_WINDOWS" -eq 1 ]; then
      win_name="$(normalize_windows_name "$name")"
    fi
    local name_candidates=("$name")
    if [ -n "$win_name" ] && [ "$win_name" != "$name" ]; then
      name_candidates+=("$win_name")
    fi

    local need_all=0
    local need_vocals=0

    if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
      if ! find_stem_dir "$ALL_DIR" "${name_candidates[@]}" >/dev/null; then
        need_all=1
      fi
    fi

    if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
      if ! find_stem_dir "$VOCALS_DIR" "${name_candidates[@]}" >/dev/null; then
        need_vocals=1
      fi
    fi

    if [ "$need_all" -eq 1 ] || [ "$need_vocals" -eq 1 ]; then
      hash="$(get_file_hash "$f")"
    fi

    if [ "$need_all" -eq 1 ] && [ -n "${HASH_TO_ALL_DIR[$hash]-}" ]; then
      local src_dir="${HASH_TO_ALL_DIR[$hash]}"
      local dest_dir="$ALL_DIR/$name"
      if [ ! -e "$dest_dir" ]; then
        ln -s "$src_dir" "$dest_dir"
        echo "✓ exists, symlinking $dest_dir -> $src_dir"
        symlinked=$((symlinked + 1))
      fi
      if [ -f "$dest_dir/vocals.wav" ]; then
        need_all=0
      fi
    fi

    if [ "$need_vocals" -eq 1 ] && [ -n "${HASH_TO_VOCALS_DIR[$hash]-}" ]; then
      local src_dir="${HASH_TO_VOCALS_DIR[$hash]}"
      local dest_dir="$VOCALS_DIR/$name"
      if [ ! -e "$dest_dir" ]; then
        ln -s "$src_dir" "$dest_dir"
        echo "✓ exists, symlinking $dest_dir -> $src_dir"
        symlinked=$((symlinked + 1))
      fi
      if [ -f "$dest_dir/vocals.wav" ]; then
        need_vocals=0
      fi
    fi

    if [ "$need_all" -eq 1 ] || [ "$need_vocals" -eq 1 ]; then
      missing_files+=("$f")
      missing=$((missing + 1))
    fi
  done
  ROOT_SYMLINKED["$ROOT"]="$symlinked"
  ROOT_MISSING["$ROOT"]="$missing"
}

set_root_context() {
  ROOT="$1"
  CURRENT_ROOT="$ROOT"
  BASE_DIR="${ROOT_BASE_DIR[$ROOT]}"
  ALL_DIR="${ROOT_ALL_DIR[$ROOT]}"
  VOCALS_DIR="${ROOT_VOCALS_DIR[$ROOT]}"
  mp3_files=()
  local list="${ROOT_MP3S[$ROOT]}"
  if [ -n "$list" ]; then
    IFS=$'\n' read -r -d '' -a mp3_files < <(printf '%s\0' "$list")
  fi
}

run_local() {
  local f name
  local tmp_dir="$HOME/Music/.demucs_tmp"

  if [ "${#mp3_files[@]}" -eq 0 ]; then
    echo "No MP3 files found in $BASE_DIR"
    return 0
  fi

  echo "Local demucs: processing ${#mp3_files[@]} files in $BASE_DIR"

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
  local windows_batch_size="${WINDOWS_BATCH_SIZE:-10}"
  if ! [[ "$windows_batch_size" =~ ^[0-9]+$ ]] || [ "$windows_batch_size" -lt 1 ]; then
    echo "Invalid WINDOWS_BATCH_SIZE: $windows_batch_size (expected integer >= 1)" >&2
    exit 1
  fi
  local windows_awake_minutes="${WINDOWS_AWAKE_MINUTES:-10}"
  if ! [[ "$windows_awake_minutes" =~ ^[0-9]+$ ]] || [ "$windows_awake_minutes" -lt 1 ]; then
    echo "Invalid WINDOWS_AWAKE_MINUTES: $windows_awake_minutes (expected integer >= 1)" >&2
    exit 1
  fi
  local windows_sleep_prompt_timeout="${WINDOWS_SLEEP_PROMPT_TIMEOUT:-120}"
  if ! [[ "$windows_sleep_prompt_timeout" =~ ^[0-9]+$ ]] || [ "$windows_sleep_prompt_timeout" -lt 1 ]; then
    echo "Invalid WINDOWS_SLEEP_PROMPT_TIMEOUT: $windows_sleep_prompt_timeout (expected integer >= 1)" >&2
    exit 1
  fi
  local windows_python="${WINDOWS_PYTHON:-python}"
  local windows_gpu_max_temp="${WINDOWS_GPU_MAX_TEMP:-80}"
  local windows_gpu_resume_temp="${WINDOWS_GPU_RESUME_TEMP:-70}"

  if [ "${#missing_files[@]}" -eq 0 ]; then
    echo "All requested stems exist for $ROOT; skipping remote run."
    return 0
  fi

  local ssh_cmd=(ssh -i "$WINDOWS_SSH_KEY" -o ServerAliveInterval=30 -o ServerAliveCountMax=6 "$WINDOWS_SSH_TARGET")
  local scp_cmd=(scp -i "$WINDOWS_SSH_KEY" -o ServerAliveInterval=30 -o ServerAliveCountMax=6)

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
  local awake_exe=""
  local awake_reset_done=0
  local did_prompt_sleep=0
  local sleep_scheme_guid=""
  local sleep_timeout_ac=""
  local sleep_timeout_dc=""

  prompt_windows_sleep() {
    if [ "${should_sleep-0}" -eq 1 ] && [ -n "${device_id-}" ] && [ -n "${token-}" ]; then
      if [ -r /dev/tty ] && [ -t 0 ]; then
        if read -r -t "$windows_sleep_prompt_timeout" -p "Press Enter to skip Windows sleep (auto-sleep in ${windows_sleep_prompt_timeout}s): " _ </dev/tty; then
          echo "Skipping Windows sleep."
          return 0
        fi
      else
        if read -r -t "$windows_sleep_prompt_timeout" -p "Press Enter to skip Windows sleep (auto-sleep in ${windows_sleep_prompt_timeout}s): " _; then
          echo "Skipping Windows sleep."
          return 0
        fi
      fi
      echo "Requesting UpSnap sleep for Windows host..."
      curl -s "$UPSNAP_HOST/api/upsnap/shutdown/$device_id" -H "Authorization: Bearer $token" >/dev/null 2>&1
      return 0
    fi
    return 1
  }

  reset_awake() {
    if [ -n "${awake_exe-}" ] && [ "$awake_reset_done" -eq 0 ]; then
      echo "Resetting PowerToys Awake to passive mode."
      win_ps "Start-Process -FilePath '$awake_exe' -ArgumentList '--mode passive' -WindowStyle Hidden" >/dev/null 2>&1 || true
      awake_reset_done=1
    fi
  }

  prevent_windows_sleep() {
    local info
    info="$(win_ps "\$scheme = (powercfg /getactivescheme) -match 'GUID:\\s+([a-fA-F0-9-]+)' | ForEach-Object { \$matches[1] }; if (-not \$scheme) { exit 0 }; \$raw = powercfg /query \$scheme SUB_SLEEP STANDBYIDLE; \$ac = (\$raw | Select-String -Pattern 'Current AC Power Setting Index:\\s+0x([0-9a-fA-F]+)' | ForEach-Object { [Convert]::ToInt32(\$_.Matches[0].Groups[1].Value,16) })[0]; \$dc = (\$raw | Select-String -Pattern 'Current DC Power Setting Index:\\s+0x([0-9a-fA-F]+)' | ForEach-Object { [Convert]::ToInt32(\$_.Matches[0].Groups[1].Value,16) })[0]; Write-Output (\$scheme + [char]9 + \$ac + [char]9 + \$dc)")"
    info="${info//$'\r'/}"
    if [ -n "$info" ]; then
      IFS=$'\t' read -r sleep_scheme_guid sleep_timeout_ac sleep_timeout_dc <<<"$info"
      if [ -n "$sleep_scheme_guid" ]; then
        echo "Disabling Windows sleep (powercfg standby timeout set to 0)."
        win_ps "powercfg /setacvalueindex $sleep_scheme_guid SUB_SLEEP STANDBYIDLE 0; powercfg /setdcvalueindex $sleep_scheme_guid SUB_SLEEP STANDBYIDLE 0; powercfg /setactive $sleep_scheme_guid" >/dev/null 2>&1 || true
      fi
    fi
  }

  restore_windows_sleep() {
    if [ -n "${sleep_scheme_guid-}" ] && [ -n "${sleep_timeout_ac-}" ] && [ -n "${sleep_timeout_dc-}" ]; then
      echo "Restoring Windows sleep timeouts."
      win_ps "powercfg /setacvalueindex $sleep_scheme_guid SUB_SLEEP STANDBYIDLE $sleep_timeout_ac; powercfg /setdcvalueindex $sleep_scheme_guid SUB_SLEEP STANDBYIDLE $sleep_timeout_dc; powercfg /setactive $sleep_scheme_guid" >/dev/null 2>&1 || true
    fi
  }

  cleanup() {
    set +e
    if [ -n "${win_tmp-}" ]; then
      win_ps "Remove-Item -Recurse -Force '$win_tmp'" >/dev/null 2>&1
    fi
    reset_awake
    restore_windows_sleep
    if [ "${did_prompt_sleep-0}" -eq 0 ]; then
      prompt_windows_sleep
      did_prompt_sleep=1
    fi
  }

  trap cleanup EXIT

  echo "Requesting UpSnap wake for Windows host..."
  curl -s "$UPSNAP_HOST/api/upsnap/wake/$device_id" -H "Authorization: Bearer $token" >/dev/null
  should_sleep=1

  echo "Waiting for Windows SSH to become available..."
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
  echo "Windows SSH connected."
  prevent_windows_sleep

  if [ "$CLEAN_WINDOWS" -eq 1 ]; then
    win_ps "\$tmp = Join-Path \$env:TEMP 'demucs_tmp'; if (Test-Path \$tmp) { Get-ChildItem -Force \$tmp | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item -Recurse -Force \$tmp -ErrorAction SilentlyContinue }" >/dev/null 2>&1 || true
  fi

  win_tmp="$(win_ps "\$tmp = Join-Path \$env:TEMP 'demucs_tmp'; New-Item -ItemType Directory -Path \$tmp -Force | Out-Null; Write-Output \$tmp")"
  win_tmp="${win_tmp//$'\r'/}"
  if [ -z "$win_tmp" ]; then
    echo "Failed to resolve Windows temp path" >&2
    exit 1
  fi
  echo "Windows temp directory: $win_tmp"
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

  mkdir -p "$ALL_DIR" "$VOCALS_DIR"

  local total_batches=$(((${#missing_files[@]} + windows_batch_size - 1) / windows_batch_size))
  local batch_index=0
  local batch_start=0
  while [ "$batch_start" -lt "${#missing_files[@]}" ]; do
    batch_index=$((batch_index + 1))
    local batch_files=("${missing_files[@]:batch_start:windows_batch_size}")
    local batch_filtered=()
	    declare -A batch_local_sizes
	    local f name size
	    for f in "${batch_files[@]}"; do
	      size="$(file_size "$f")"
	      name="$(basename "$f")"
	      if [ "$size" -le 0 ]; then
	        echo "Warning: skipping zero-byte file: $f" >&2
	        continue
	      fi
	      batch_local_sizes["$name"]="$size"
	      batch_filtered+=("$f")
	    done
    batch_files=("${batch_filtered[@]}")
    batch_start=$((batch_start + windows_batch_size))

    win_ps "Get-ChildItem -Path '$win_input_ps' -Filter '*.mp3' -File | Remove-Item -Force" >/dev/null 2>&1 || true

    if [ "${#batch_files[@]}" -eq 0 ]; then
      echo "Batch $batch_index/$total_batches: no valid files to upload."
      continue
    fi

    echo "Batch $batch_index/$total_batches: uploading ${#batch_files[@]} files..."
    "${scp_cmd[@]}" -r "${batch_files[@]}" "${WINDOWS_SSH_TARGET}:$win_input_scp/"
    echo "Batch $batch_index/$total_batches: upload complete."

	    local size_report
	    size_report="$(win_ps "Get-ChildItem -Path '$win_input_ps' -Filter '*.mp3' -File | ForEach-Object { Write-Output (\$_.Name + [char]9 + \$_.Length) }")"
	    size_report="${size_report//$'\r'/}"
	    local reupload=()
	    declare -A batch_remote_sizes
	    while IFS=$'\t' read -r name remote_size; do
	      [ -n "$name" ] || continue
	      batch_remote_sizes["$name"]="$remote_size"
	    done <<<"$size_report"
	    for name in "${!batch_local_sizes[@]}"; do
	      local_size="${batch_local_sizes[$name]}"
	      remote_size="${batch_remote_sizes[$name]:--1}"
	      if [ "$remote_size" -lt 0 ] || [ "$remote_size" -ne "$local_size" ]; then
	        reupload+=("$name")
	      fi
	    done
	    if [ "${#reupload[@]}" -gt 0 ]; then
	      echo "Batch $batch_index/$total_batches: re-uploading ${#reupload[@]} files with size mismatch..."
      local reupload_files=()
      for name in "${reupload[@]}"; do
        for f in "${batch_files[@]}"; do
          if [ "$(basename "$f")" = "$name" ]; then
            reupload_files+=("$f")
            break
          fi
        done
      done
      if [ "${#reupload_files[@]}" -gt 0 ]; then
        "${scp_cmd[@]}" -r "${reupload_files[@]}" "${WINDOWS_SSH_TARGET}:$win_input_scp/"
      fi
    fi

    local awake_path
    awake_path="$(win_ps "\$awake = @((Join-Path \$env:ProgramFiles 'PowerToys\\PowerToys.Awake.exe'), (Join-Path \$env:LOCALAPPDATA 'PowerToys\\PowerToys.Awake.exe')) | Where-Object { Test-Path \$_ } | Select-Object -First 1; if (\$awake) { Write-Output \$awake }")"
    awake_path="${awake_path//$'\r'/}"
    if [ -n "$awake_path" ]; then
      awake_exe="$awake_path"
      echo "Batch $batch_index/$total_batches: PowerToys Awake for ${windows_awake_minutes} minutes."
      win_ps "Start-Process -FilePath '$awake_path' -ArgumentList '--mode timed --time $((windows_awake_minutes * 60))' -WindowStyle Hidden" >/dev/null 2>&1 || true
    else
      echo "Warning: PowerToys Awake not found on Windows host; sleep may interrupt batch $batch_index/$total_batches." >&2
    fi

    if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
      echo "Batch $batch_index/$total_batches: running Windows 4-stem separation..."
      win_ps "\$env:PYTHONUTF8='1'; \$env:PYTHONIOENCODING='utf-8'; \$maxTemp=$windows_gpu_max_temp; \$resumeTemp=$windows_gpu_resume_temp; function Get-GpuTemp { [int](nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | Select-Object -First 1) }; function Wait-ForCool { while ((Get-GpuTemp) -gt \$resumeTemp) { Write-Host \"GPU hot (\$(Get-GpuTemp))C, waiting to cool to \$resumeTemp C\"; Start-Sleep -Seconds 10 } }; \$files = Get-ChildItem -Path '$win_input_ps' -Filter '*.mp3' -File | ForEach-Object { \$_.FullName }; if (\$files.Count -eq 0) { Write-Error 'No MP3 files found in Windows input folder'; exit 1 } ; foreach (\$f in \$files) { if ((Get-GpuTemp) -gt \$maxTemp) { Wait-ForCool }; demucs ${demucs_device_arg[*]} -n $DEMUCS_MODEL -o '$win_out4_ps' \"\$f\" }"
    fi

    if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
      echo "Batch $batch_index/$total_batches: running Windows 2-stem separation..."
      win_ps "\$env:PYTHONUTF8='1'; \$env:PYTHONIOENCODING='utf-8'; \$maxTemp=$windows_gpu_max_temp; \$resumeTemp=$windows_gpu_resume_temp; function Get-GpuTemp { [int](nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | Select-Object -First 1) }; function Wait-ForCool { while ((Get-GpuTemp) -gt \$resumeTemp) { Write-Host \"GPU hot (\$(Get-GpuTemp))C, waiting to cool to \$resumeTemp C\"; Start-Sleep -Seconds 10 } }; \$files = Get-ChildItem -Path '$win_input_ps' -Filter '*.mp3' -File | ForEach-Object { \$_.FullName }; if (\$files.Count -eq 0) { Write-Error 'No MP3 files found in Windows input folder'; exit 1 } ; foreach (\$f in \$files) { if ((Get-GpuTemp) -gt \$maxTemp) { Wait-ForCool }; demucs ${demucs_device_arg[*]} -n $DEMUCS_MODEL --two-stems=vocals -o '$win_out2_ps' \"\$f\" }"
    fi

    if [[ "$MODE" == "4" || "$MODE" == "both" ]]; then
      echo "Batch $batch_index/$total_batches: copying Windows 4-stem outputs back..."
      "${scp_cmd[@]}" -r "${WINDOWS_SSH_TARGET}:$win_out4_scp/htdemucs" "$ALL_DIR/"
      if [ -d "$ALL_DIR/htdemucs" ]; then
        if compgen -G "$ALL_DIR/htdemucs/*" >/dev/null; then
          for src_dir in "$ALL_DIR"/htdemucs/*; do
            [ -d "$src_dir" ] || continue
            dest_dir="$ALL_DIR/$(basename "$src_dir")"
            if [ -d "$dest_dir" ]; then
              cp -a "$src_dir/." "$dest_dir/"
              rm -rf "$src_dir"
            else
              mv "$src_dir" "$dest_dir"
            fi
          done
        fi
        rmdir "$ALL_DIR/htdemucs" 2>/dev/null || true
      fi
    fi

    if [[ "$MODE" == "2" || "$MODE" == "both" ]]; then
      echo "Batch $batch_index/$total_batches: copying Windows 2-stem outputs back..."
      "${scp_cmd[@]}" -r "${WINDOWS_SSH_TARGET}:$win_out2_scp/htdemucs" "$VOCALS_DIR/"
      if [ -d "$VOCALS_DIR/htdemucs" ]; then
        if compgen -G "$VOCALS_DIR/htdemucs/*" >/dev/null; then
          for src_dir in "$VOCALS_DIR"/htdemucs/*; do
            [ -d "$src_dir" ] || continue
            dest_dir="$VOCALS_DIR/$(basename "$src_dir")"
            if [ -d "$dest_dir" ]; then
              cp -a "$src_dir/." "$dest_dir/"
              rm -rf "$src_dir"
            else
              mv "$src_dir" "$dest_dir"
            fi
          done
        fi
        rmdir "$VOCALS_DIR/htdemucs" 2>/dev/null || true
      fi
    fi
  done

  reset_awake
  if [ "$did_prompt_sleep" -eq 0 ]; then
    prompt_windows_sleep
    did_prompt_sleep=1
  fi
}

build_hash_index

for root in "${ROOTS_ABS[@]}"; do
  set_root_context "$root"
  if [ "${#mp3_files[@]}" -eq 0 ]; then
    echo "No MP3 files found in $BASE_DIR"
    continue
  fi
  prepare_missing_files
  total_count="${ROOT_TOTAL[$root]:-${#mp3_files[@]}}"
  symlinked_count="${ROOT_SYMLINKED[$root]:-0}"
  missing_count="${ROOT_MISSING[$root]:-0}"
  echo "Root summary for $root: $total_count tracks, $symlinked_count symlinked, $missing_count to process."
  if [ "$USE_WINDOWS" -eq 1 ]; then
    run_windows
  else
    run_local
  fi
  save_cache_for_root "$root"
done

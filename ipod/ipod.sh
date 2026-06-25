#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IPOD_MOUNT="${IPOD_MOUNT:-/mnt/ipod}"

trap 'sudo umount "$IPOD_MOUNT" 2>/dev/null || true' EXIT

# --- Preflight ---
missing=()
for cmd in blkid gnupod_INIT gnupod_addsong mktunes fsck.hfsplus; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "ERROR: Missing required binaries: ${missing[*]}"
    echo "  sudo apt install gnupod-tools hfsprogs util-linux"
    exit 1
fi

# --- Download ---
echo "Downloading iPod playlists..."
"$SCRIPT_DIR/../batch.sh" --manifest "ipod/ipod-manifest.json" --until download

# --- Mount iPod ---
_find_ipod_dev() {
    sudo blkid -t TYPE=hfsplus -o device 2>/dev/null | head -1
}

if ! mountpoint -q "$IPOD_MOUNT"; then
    # Give the kernel a moment to finish probing the filesystem after udev fires.
    sleep 3
    dev=$(_find_ipod_dev)
    if [ -z "$dev" ]; then
        echo "iPod not immediately accessible — reloading USB storage driver..."
        sudo rmmod uas 2>/dev/null || true
        sudo rmmod usb_storage 2>/dev/null || true
        sudo modprobe usb_storage
        echo "Waiting for device..."
        sleep 5
        dev=$(_find_ipod_dev)
    fi
    if [ -z "$dev" ]; then
        echo "ERROR: Could not find iPod block device after driver reload."
        exit 1
    fi
    echo "Mounting $dev at $IPOD_MOUNT..."
    sudo mount -t hfsplus -o "force,rw,uid=$(id -u),gid=$(id -g)" "$dev" "$IPOD_MOUNT"
fi

# --- Init iPod (first time only) ---
if [ ! -f "$IPOD_MOUNT/iPod_Control/.gnupod/GNUtunesDB.xml" ]; then
    echo "Initializing iPod..."
    gnupod_INIT -m "$IPOD_MOUNT"
fi

# --- Sync ---
added=0
skipped=0

while IFS=$'\t' read -r playlist_name playlist_root; do
    unprocessed="$playlist_root/unprocessed"
    synced_log="$playlist_root/.ipod_synced"
    [ -d "$unprocessed" ] || continue
    touch "$synced_log"

    echo ""
    echo "Syncing: $playlist_name"
    while IFS= read -r -d '' mp3; do
        filename="$(basename "$mp3")"
        if grep -qF "$filename" "$synced_log"; then
            skipped=$((skipped + 1))
            continue
        fi
        echo "  Adding: $filename"
        gnupod_addsong -m "$IPOD_MOUNT" -p "$playlist_name" "$mp3"
        echo "$filename" >> "$synced_log"
        added=$((added + 1))
    done < <(find "$unprocessed" -maxdepth 1 -name "*.mp3" -print0 | sort -z)
done < <(python3 -c "
import json
from pathlib import Path
manifest = json.load(open('$SCRIPT_DIR/ipod-manifest.json'))
for e in manifest:
    root = e['root']
    print(f\"{Path(root).name}\t{root}\")
")

if [ "$added" -gt 0 ]; then
    echo ""
    echo "Writing iPod database..."
    mktunes -m "$IPOD_MOUNT"
fi

echo ""
echo "Done: $added added, $skipped already synced"

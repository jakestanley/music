#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IPOD_MOUNT="${IPOD_MOUNT:-/mnt/ipod}"
PLAYLIST_DIR="$SCRIPT_DIR/Playlists/On my iPod"
UNPROCESSED_DIR="$PLAYLIST_DIR/unprocessed"
SYNCED_LOG="$PLAYLIST_DIR/.ipod_synced"

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
echo "Downloading On my iPod playlist..."
"$SCRIPT_DIR/batch.sh" --manifest ipod-manifest.json --until download

# --- Mount iPod ---
_find_ipod_dev() {
    sudo blkid -t TYPE=hfsplus -o device 2>/dev/null | head -1
}

if ! mountpoint -q "$IPOD_MOUNT"; then
    dev=$(_find_ipod_dev)
    if [ -z "$dev" ]; then
        if ! dmesg | grep -qi "ipod\|05ac:"; then
            echo "ERROR: iPod not detected. Is it connected?"
            exit 1
        fi
        echo "iPod detected but not accessible — reloading USB storage driver..."
        sudo rmmod uas 2>/dev/null || true
        sudo rmmod usb_storage 2>/dev/null || true
        sudo modprobe usb_storage
        echo "Waiting for device..."
        sleep 4
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
touch "$SYNCED_LOG"
added=0
skipped=0

while IFS= read -r -d '' mp3; do
    filename="$(basename "$mp3")"
    if grep -qF "$filename" "$SYNCED_LOG"; then
        skipped=$((skipped + 1))
        continue
    fi
    echo "Adding: $filename"
    gnupod_addsong -m "$IPOD_MOUNT" "$mp3"
    echo "$filename" >> "$SYNCED_LOG"
    added=$((added + 1))
done < <(find "$UNPROCESSED_DIR" -maxdepth 1 -name "*.mp3" -print0 | sort -z)

if [ "$added" -gt 0 ]; then
    echo "Writing iPod database..."
    mktunes -m "$IPOD_MOUNT"
fi

echo ""
echo "Done: $added added, $skipped already synced"

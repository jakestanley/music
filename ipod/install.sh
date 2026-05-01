#!/bin/bash
# One-time setup for iPod auto-sync.
# Installs the systemd user service, udev rule, linger, and passwordless sudo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="ipod-sync"
USER_NAME="$(whoami)"

step() { printf '\n>>> %s\n' "$*"; }

# 1. Systemd user service
step "Installing systemd user service"
SERVICE_DIR="$HOME/.config/systemd/user"
mkdir -p "$SERVICE_DIR"
cp "$SCRIPT_DIR/$SERVICE_NAME.service" "$SERVICE_DIR/"
systemctl --user daemon-reload
systemctl --user enable "$SERVICE_NAME.service"
echo "Enabled $SERVICE_NAME.service"

# 2. udev rule
step "Installing udev rule (requires sudo)"
sudo cp "$SCRIPT_DIR/$SERVICE_NAME.rules" "/etc/udev/rules.d/99-$SERVICE_NAME.rules"
sudo udevadm control --reload-rules
echo "Installed /etc/udev/rules.d/99-$SERVICE_NAME.rules"

# 3. loginctl linger
step "Enabling linger for $USER_NAME (requires sudo)"
sudo loginctl enable-linger "$USER_NAME"
echo "Linger enabled"

# 4. Passwordless sudo for mount commands used by ipod.sh
step "Configuring passwordless sudo (requires sudo)"
SUDOERS_FILE="/etc/sudoers.d/$SERVICE_NAME"
SUDOERS_LINE="$USER_NAME ALL=(root) NOPASSWD: /usr/sbin/blkid, /usr/bin/mount, /usr/bin/umount, /usr/sbin/rmmod, /usr/sbin/modprobe"
TMPFILE="$(mktemp)"
echo "$SUDOERS_LINE" > "$TMPFILE"
sudo visudo -c -f "$TMPFILE"
sudo cp "$TMPFILE" "$SUDOERS_FILE"
sudo chmod 440 "$SUDOERS_FILE"
rm -f "$TMPFILE"
echo "Written $SUDOERS_FILE"

step "Setup complete. Plug in your iPod to test."
echo "Logs: journalctl --user -u $SERVICE_NAME.service -f"

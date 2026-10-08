#!/bin/bash
# One-time setup for iPod auto-sync.
# Installs the systemd user service, udev rule and linger; checks that
# hosts-adler has installed the ipod-device sudo wrapper.
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

# 4. Root access for ipod.sh comes from hosts-adler (roles/ipod_sync): the
#    /usr/local/sbin/ipod-device wrapper plus a sudoers rule allowing only
#    that. Not installed from here any more - raw passwordless mount and
#    modprobe would be passwordless root.
step "Checking for the ipod-device wrapper"
if sudo -n -l /usr/local/sbin/ipod-device >/dev/null 2>&1; then
    echo "OK: $USER_NAME can run /usr/local/sbin/ipod-device without a password"
else
    echo "MISSING: apply hosts-adler's ipod_sync role (playbooks/ipod_sync.yml)" >&2
    exit 1
fi

step "Setup complete. Plug in your iPod to test."
echo "Logs: journalctl --user -u $SERVICE_NAME.service -f"

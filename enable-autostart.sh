#!/bin/bash
# enable-autostart.sh — Enable Proton VPN Tray auto-start on login
# Part of protonvpn-mint-mate

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOSTART_DIR="$HOME/.config/autostart"
TEMPLATE="$SCRIPT_DIR/protonvpn-tray.desktop"
DEST="$AUTOSTART_DIR/protonvpn-tray.desktop"

if [ ! -f "$TEMPLATE" ]; then
    echo "ERROR: Template .desktop file not found at $TEMPLATE"
    exit 1
fi

mkdir -p "$AUTOSTART_DIR"

cp "$TEMPLATE" "$DEST"

# Ensure the Exec path is correct for this system
sed -i "s|^Exec=.*|Exec=python3 $SCRIPT_DIR/protonvpn-tray.py --auto-connect|" "$DEST"

echo "Autostart enabled. The Proton VPN tray will start on next login."
echo "Autostart file: $DEST"

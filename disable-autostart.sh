#!/bin/bash
# disable-autostart.sh — Disable Proton VPN Tray auto-start on login
# Part of protonvpn-mint-mate
#
# Usage:
#   ./disable-autostart.sh          Interactive: offers to kill running tray
#   ./disable-autostart.sh --quiet  Non-interactive: just remove desktop file

set -euo pipefail

AUTOSTART_FILE="$HOME/.config/autostart/protonvpn-tray.desktop"
QUIET="${1:-}"

if [ -f "$AUTOSTART_FILE" ]; then
    rm "$AUTOSTART_FILE"
    echo "Autostart disabled. The Proton VPN tray will no longer start on login."
else
    echo "Autostart was not enabled (no file found at $AUTOSTART_FILE)."
fi

if [ "$QUIET" != "--quiet" ]; then
    # Optionally quit the running tray instance
    TRAY_PID=$(pgrep -f "protonvpn-tray.py" 2>/dev/null || true)
    if [ -n "$TRAY_PID" ]; then
        read -p "Kill the running tray instance? (VPN stays connected) [y/N]: " ANSWER
        if [ "$ANSWER" = "y" ] || [ "$ANSWER" = "Y" ]; then
            kill "$TRAY_PID" 2>/dev/null || true
            echo "Tray instance terminated."
        fi
    fi
fi

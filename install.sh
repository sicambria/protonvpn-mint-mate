#!/bin/bash
# install.sh — Full setup for Proton VPN Tray on Linux Mint MATE
# Part of protonvpn-mint-mate
#
# This script:
#   1. Installs required system dependencies
#   2. Adds the Proton VPN repository and installs the CLI if missing
#   3. Creates the config directory
#   4. Makes scripts executable
#   5. Enables auto-start on login
#   6. Verifies Proton VPN sign-in status

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="$HOME/.config/protonvpn-tray"

echo "============================================"
echo " Proton VPN Tray — Installer"
echo "============================================"
echo ""

# --- 1. System dependencies ---
echo "[1/5] Installing system dependencies..."

PACKAGES="python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 gir1.2-notify-0.7 zenity"

MISSING=""
for pkg in $PACKAGES; do
    if ! dpkg -s "$pkg" >/dev/null 2>&1; then
        MISSING="$MISSING $pkg"
    fi
done

if [ -n "$MISSING" ]; then
    echo "  Installing:$MISSING"
    sudo apt-get update -qq
    sudo apt-get install -y $MISSING
else
    echo "  All system dependencies are already installed."
fi

# --- 2. Proton VPN CLI ---
echo "[2/5] Verifying Proton VPN CLI..."

if ! command -v protonvpn &>/dev/null; then
    echo "  Proton VPN CLI not found. Installing..."
    
    # Add Proton VPN repository if not present
    if [ ! -f /etc/apt/sources.list.d/protonvpn-stable.list ]; then
        echo "  Adding Proton VPN repository..."
        TMP_DEB=$(mktemp /tmp/protonvpn-release.XXXXXX.deb)
        wget -q "https://repo.protonvpn.com/debian/dists/stable/main/binary-all/protonvpn-stable-release_1.0.8_all.deb" -O "$TMP_DEB"
        sudo dpkg -i "$TMP_DEB"
        rm -f "$TMP_DEB"
        sudo apt-get update -qq
    fi
    
    sudo apt-get install -y proton-vpn-cli
    echo "  Proton VPN CLI installed."
else
    echo "  Proton VPN CLI is already installed: $(protonvpn --version 2>/dev/null || echo 'unknown version')"
fi

# --- 3. Config directory ---
echo "[3/5] Setting up configuration..."

mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_DIR/config.json" ]; then
    cp "$SCRIPT_DIR/config.json" "$CONFIG_DIR/config.json"
    echo "  Default config created at $CONFIG_DIR/config.json"
else
    echo "  Config already exists at $CONFIG_DIR/config.json (preserved)"
fi

# --- 4. Make scripts executable ---
echo "[4/5] Setting executable permissions..."

chmod +x "$SCRIPT_DIR/protonvpn-tray.py"
chmod +x "$SCRIPT_DIR/enable-autostart.sh"
chmod +x "$SCRIPT_DIR/disable-autostart.sh"
chmod +x "$SCRIPT_DIR/install.sh"

# --- 5. Enable autostart ---
echo "[5/5] Enabling auto-start on login..."

"$SCRIPT_DIR/enable-autostart.sh"

echo ""
echo "============================================"
echo " Installation complete!"
echo "============================================"
echo ""
echo " Configuration: $CONFIG_DIR/config.json"
echo " Autostart:    $HOME/.config/autostart/protonvpn-tray.desktop"
echo ""
echo " To start the tray now:"
echo "   python3 $SCRIPT_DIR/protonvpn-tray.py"
echo ""
echo " To manage autostart:"
echo "   $SCRIPT_DIR/enable-autostart.sh"
echo "   $SCRIPT_DIR/disable-autostart.sh"
echo ""

# --- Verify sign-in ---
if command -v protonvpn &>/dev/null; then
    STATUS_OUTPUT=$(protonvpn status 2>&1 || true)
    if echo "$STATUS_OUTPUT" | grep -qi "not signed in\|not authenticated\|please sign in"; then
        echo "  WARNING: You are not signed in to Proton VPN."
        echo "  Run 'protonvpn signin' in a terminal to authenticate."
    else
        echo "  Proton VPN is signed in and ready."
    fi
fi

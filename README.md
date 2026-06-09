# Proton VPN Tray — Linux Mint MATE
# System tray application for managing Proton VPN connections

## Features

- System tray icon with connection status indicator
- One-click connect/disconnect
- Country selector with favorites
- Secure Core, Tor, and P2P connection modes
- Full configuration management (NetShield, Kill Switch, VPN Accelerator, etc.)
- Auto-connect on login to your preferred country
- Desktop notifications for connection state changes

## Requirements

- Linux Mint MATE (or any GTK3-based desktop with Ayatana appindicator support)
- Proton VPN CLI (`protonvpn`) — installed automatically if missing
- Python 3 with GTK3, Notify, and Ayatana bindings

## Installation

```bash
./install.sh
```

This will:
1. Install required system packages (python3-gi, gir1.2-ayatanaappindicator3-0.1, etc.)
2. Install Proton VPN if not already present
3. Set up auto-start on login
4. Verify you are signed in to Proton VPN

## Usage

### Start manually
```bash
python3 protonvpn-tray.py
```

### Enable auto-start on login
```bash
./enable-autostart.sh
```

### Disable auto-start on login
```bash
./disable-autostart.sh
```

## Configuration

Edit `~/.config/protonvpn-tray/config.json` or use the tray menu:

- **Default Country** — country to auto-connect to on startup
- **Favorites** — countries pinned to the top of the country menu
- **Auto-connect** — automatically connect the VPN when the tray starts

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE)

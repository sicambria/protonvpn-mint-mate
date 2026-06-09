#!/usr/bin/env python3
"""
protonvpn-tray.py — Proton VPN System Tray Application for Linux Mint MATE
Copyright (C) 2026  protonvpn-mint-mate contributors

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.
"""
import os
import sys
import json
import signal
import subprocess
import threading
import argparse
import logging

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Notify", "0.7")
from gi.repository import Gtk, GLib, Notify

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("protonvpn-tray")

CONFIG_DIR = os.path.expanduser("~/.config/protonvpn-tray")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PID_FILE = os.path.join(CONFIG_DIR, "tray.pid")
PROTONVPN_BIN = "/usr/bin/protonvpn"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ENABLE_AUTOSTART_SH = os.path.join(SCRIPT_DIR, "enable-autostart.sh")
DISABLE_AUTOSTART_SH = os.path.join(SCRIPT_DIR, "disable-autostart.sh")

DEFAULT_CONFIG = {
    "default_country": "CH",
    "auto_connect_on_start": True,
    "favorites": ["CH", "DE"],
    "last_connected": None,
}

CMD_TIMEOUT_STATUS = 5
CMD_TIMEOUT_CONNECT = 25
CMD_TIMEOUT_DISCONNECT = 10
CMD_TIMEOUT_COUNTRIES = 45
CMD_TIMEOUT_CONFIG = 8
CMD_TIMEOUT_INFO = 8
CMD_TIMEOUT_CONFIG_SET = 8


def run_cli(args, timeout=CMD_TIMEOUT_STATUS):
    cmd = [PROTONVPN_BIN] + args
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except FileNotFoundError:
        return -2, "", "protonvpn binary not found"
    except Exception as e:
        return -3, "", str(e)


def run_cli_async(args, timeout, callback):
    def target():
        ret, out, err = run_cli(args, timeout)
        GLib.idle_add(lambda: callback(ret, out, err))
    t = threading.Thread(target=target, daemon=True)
    t.start()


def atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.rename(tmp, path)


def is_autostart_enabled():
    desktop_file = os.path.expanduser(
        "~/.config/autostart/protonvpn-tray.desktop"
    )
    if not os.path.exists(desktop_file):
        return False
    try:
        with open(desktop_file, "r") as f:
            content = f.read()
        return "X-GNOME-Autostart-enabled=true" in content
    except Exception:
        return os.path.exists(desktop_file)


def parse_status(output):
    out_lower = output.lower()
    if any(kw in out_lower for kw in [
        "not signed in", "not authenticated", "please sign in",
        "authenticate first", "need to authenticate", "sign in first"
    ]):
        return "not_signed_in", output
    lines = output.split("\n")
    first_line = lines[0].strip() if lines else ""
    if first_line.startswith("Status:"):
        status_part = first_line.split(":", 1)[1].strip()
        if status_part.lower().startswith("disconnected"):
            return "disconnected", output
        if status_part.lower().startswith("connected"):
            info_parts = []
            for line in lines[1:]:
                stripped = line.strip()
                if stripped:
                    info_parts.append(stripped)
            return "connected", "\n".join(info_parts) if info_parts else "Connected"
        return "unknown", output
    if "Status:" not in output and "status" not in output.lower() and output.strip() == "":
        return "not_signed_in", output
    return "unknown", output


def parse_countries(output):
    countries = []
    lines = output.split("\n")
    in_body = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Country") or stripped.startswith("---"):
            in_body = True
            continue
        if not in_body:
            continue
        parts = stripped.rsplit(None, 1)
        if len(parts) == 2 and len(parts[1]) == 2 and parts[1].isalpha():
            code = parts[1].upper()
            name = parts[0].strip()
            countries.append((code, name))
    return countries


def parse_config_list(output):
    settings = {}
    lines = output.split("\n")
    in_body = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Setting") or stripped.startswith("---"):
            in_body = True
            continue
        if not in_body:
            continue
        if "Use '" in stripped or stripped.startswith("Use "):
            break
        parts = stripped.split(None, 1)
        if len(parts) >= 2:
            key = parts[0].strip()
            value = parts[1].strip()
            settings[key] = value
    return settings


_notify_initialized = False

def notify(title, body, icon="network-vpn"):
    if not _notify_initialized:
        return
    try:
        n = Notify.Notification.new(title, body, icon)
        n.set_timeout(5000)
        n.show()
    except Exception as e:
        log.warning("Notification failed: %s", e)


class ProtonVPNTray:
    def __init__(self, auto_connect=False):
        self.auto_connect = auto_connect
        self.busy = False
        self.connected = False
        self.signed_in = True
        self.connection_info = ""
        self.status_raw = ""
        self.countries_cache = []
        self.countries_loaded = False
        self.config = self._load_config()
        self.indicator = None
        self.menu = None
        self.status_item = None
        self.country_submenu = None
        self.autostart_item = None
        self._polling = False
        self._quitting = False

        self._check_pid()
        self._write_pid()

        Notify.init("protonvpn-tray")
        global _notify_initialized
        _notify_initialized = True

        self._create_indicator()
        self.menu = self._build_menu()
        self.indicator.set_menu(self.menu)

        GLib.timeout_add_seconds(5, self._poll_status)
        GLib.timeout_add(500, self._load_countries_async)

        if self.auto_connect and self.config.get("auto_connect_on_start", True):
            GLib.timeout_add_seconds(4, self._auto_connect_startup)

        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    # ── Config management ──────────────────────────────────────────────

    def _load_config(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                merged = dict(DEFAULT_CONFIG)
                merged.update(data)
                return merged
            except (json.JSONDecodeError, IOError) as e:
                log.warning("Config read error: %s, using defaults", e)
        return dict(DEFAULT_CONFIG)

    def _save_config(self):
        atomic_write_json(CONFIG_FILE, self.config)

    # ── PID management ─────────────────────────────────────────────────

    def _check_pid(self):
        if not os.path.exists(PID_FILE):
            return
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print(f"protonvpn-tray is already running (PID {pid}).", file=sys.stderr)
            print("Only one instance is allowed.", file=sys.stderr)
            sys.exit(2)
        except (ValueError, ProcessLookupError, OSError):
            os.remove(PID_FILE)

    def _write_pid(self):
        try:
            fd = os.open(PID_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
        except FileExistsError:
            print("protonvpn-tray: PID file already exists (another instance may be starting).", file=sys.stderr)
            sys.exit(2)

    def _remove_pid(self):
        try:
            if os.path.exists(PID_FILE):
                os.remove(PID_FILE)
        except OSError:
            pass

    def _shutdown(self):
        global _notify_initialized
        _notify_initialized = False
        self._quitting = True
        self._remove_pid()
        try:
            Notify.uninit()
        except Exception:
            pass
        Gtk.main_quit()

    # ── Signal handlers ────────────────────────────────────────────────

    def _on_signal(self, signum, frame):
        log.info("Received signal %s, shutting down", signum)
        self._shutdown()

    # ── Indicator creation ─────────────────────────────────────────────

    def _create_indicator(self):
        self.indicator = self._try_ayatana()
        if self.indicator is None:
            self.indicator = self._try_status_icon()

    def _try_ayatana(self):
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator3
            indicator = AppIndicator3.Indicator.new(
                "protonvpn-tray",
                "network-vpn",
                AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
            )
            indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
            indicator.set_title("Proton VPN")
            log.info("Using AyatanaAppIndicator3")
            return indicator
        except (ValueError, ImportError) as e:
            log.info("AyatanaAppIndicator3 not available: %s", e)
            return None

    def _try_status_icon(self):
        icon = Gtk.StatusIcon()
        icon.set_from_icon_name("network-vpn")
        icon.set_tooltip_text("Proton VPN")
        icon.set_visible(True)
        icon.connect("popup-menu", self._on_status_icon_popup)
        icon.connect("activate", self._on_status_icon_activate)
        log.info("Using Gtk.StatusIcon fallback")
        self._status_icon = icon
        return icon

    def _on_status_icon_popup(self, icon, button, activate_time):
        if self.menu:
            self.menu.popup(None, None, icon.position_menu, icon, button, activate_time)

    def _on_status_icon_activate(self, icon):
        pass

    def set_menu(self, menu):
        if hasattr(self.indicator, "set_menu"):
            self.indicator.set_menu(menu)

    # ── Menu building ──────────────────────────────────────────────────

    def _mk_item(self, label, callback):
        item = Gtk.MenuItem(label=label)
        if callback:
            item.connect("activate", callback)
        return item

    def _mk_sep(self):
        return Gtk.SeparatorMenuItem()

    def _build_menu(self):
        menu = Gtk.Menu()

        self.status_item = Gtk.MenuItem(label="Proton VPN")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Connect (Fastest)", self._on_connect_fastest))

        country_parent = Gtk.MenuItem(label="Connect to Country")
        self.country_submenu = Gtk.Menu()
        self.country_submenu.connect("map", self._on_country_submenu_show)
        country_parent.set_submenu(self.country_submenu)
        menu.append(country_parent)

        menu.append(self._mk_item("Secure Core", self._on_connect_securecore))
        menu.append(self._mk_item("Tor", self._on_connect_tor))
        menu.append(self._mk_item("P2P", self._on_connect_p2p))
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Disconnect", self._on_disconnect))
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Default Country...", self._on_default_country))
        menu.append(self._mk_item("Manage Favorites...", self._on_manage_favorites))
        menu.append(self._mk_sep())

        settings_parent = Gtk.MenuItem(label="Settings")
        self.settings_submenu = Gtk.Menu()
        self.settings_submenu.connect("map", self._on_settings_submenu_show)
        settings_parent.set_submenu(self.settings_submenu)
        menu.append(settings_parent)
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Connection Info", self._on_show_info))
        menu.append(self._mk_sep())

        autostart_enabled = is_autostart_enabled()
        label = "Autostart (%s)" % ("Enabled" if autostart_enabled else "Disabled")
        self.autostart_item = Gtk.MenuItem(label=label)
        self.autostart_item.connect("activate", self._on_toggle_autostart)
        menu.append(self.autostart_item)

        menu.append(self._mk_item("Refresh", self._on_refresh))
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Quit", self._on_quit))

        menu.show_all()
        return menu

    # ── Country submenu ────────────────────────────────────────────────

    def _on_country_submenu_show(self, widget):
        self._rebuild_country_submenu()

    def _rebuild_country_submenu(self):
        for child in self.country_submenu.get_children():
            self.country_submenu.remove(child)

        favorites = self.config.get("favorites", [])
        all_countries = list(self.countries_cache) if self.countries_loaded else []

        done_codes = set()

        fav_entries = []
        for code in favorites:
            for ccode, cname in all_countries:
                if ccode == code:
                    fav_entries.append((ccode, cname))
                    done_codes.add(ccode)
                    break
            else:
                fav_entries.append((code, code))

        for code, name in fav_entries:
            item = Gtk.MenuItem(label="★ " + name + " (" + code + ")")
            item.connect("activate", self._make_country_connect_handler(code))
            self.country_submenu.append(item)

        if fav_entries:
            self.country_submenu.append(self._mk_sep())

        if self.countries_loaded and all_countries:
            other = [(c, n) for c, n in all_countries if c not in done_codes]
            other.sort(key=lambda x: x[1].lower())
            for code, name in other:
                item = Gtk.MenuItem(label=name + " (" + code + ")")
                item.connect("activate", self._make_country_connect_handler(code))
                self.country_submenu.append(item)
        elif not self.countries_loaded:
            item = Gtk.MenuItem(label="Loading countries...")
            item.set_sensitive(False)
            self.country_submenu.append(item)
        else:
            item = Gtk.MenuItem(label="(no countries available)")
            item.set_sensitive(False)
            self.country_submenu.append(item)

        self.country_submenu.show_all()

    def _make_country_connect_handler(self, code):
        def handler(widget):
            self._run_vpn_action(
                ["disconnect"],
                CMD_TIMEOUT_DISCONNECT,
                "Disconnecting",
                on_done=lambda ret, out, err: self._run_vpn_action(
                    ["connect", "--country", code],
                    CMD_TIMEOUT_CONNECT,
                    "Connecting to " + code,
                    on_done=self._default_on_done,
                ),
            )
        return handler

    # ── Settings submenu ───────────────────────────────────────────────

    def _on_settings_submenu_show(self, widget):
        self._rebuild_settings_submenu()

    def _rebuild_settings_submenu(self):
        for child in self.settings_submenu.get_children():
            self.settings_submenu.remove(child)

        loading = Gtk.MenuItem(label="Loading settings...")
        loading.set_sensitive(False)
        self.settings_submenu.append(loading)
        self.settings_submenu.show_all()

        def callback(ret, out, err):
            GLib.idle_add(self._rebuild_settings_submenu_done, ret, out, err)

        run_cli_async(["config", "list"], CMD_TIMEOUT_CONFIG, callback)

    def _rebuild_settings_submenu_done(self, ret, out, err):
        if self._quitting:
            return
        for child in self.settings_submenu.get_children():
            self.settings_submenu.remove(child)

        settings = {}
        if ret == 0:
            settings = parse_config_list(out)

        self._add_setting_radio("NetShield", "netshield", settings,
                                 ["off", "malware-only", "malware-ads-trackers"])
        self._add_setting_radio("Kill Switch", "kill-switch", settings,
                                 ["off", "standard"])
        self._add_setting_radio("VPN Accelerator", "vpn-accelerator", settings,
                                 ["on", "off"])
        self._add_setting_radio("IPv6", "ipv6", settings,
                                 ["on", "off"])
        self._add_setting_radio("Moderate NAT", "moderate-nat", settings,
                                 ["on", "off"])
        self._add_setting_radio("Port Forwarding", "port-forwarding", settings,
                                 ["on", "off"])
        self._add_setting_radio("Anonymous Crash Reports", "anonymous-crash-reports",
                                 settings, ["on", "off"])

        self.settings_submenu.append(self._mk_sep())

        dns_item = Gtk.MenuItem(label="Custom DNS...")
        dns_item.connect("activate", self._on_custom_dns)
        self.settings_submenu.append(dns_item)

        self.settings_submenu.show_all()

    def _add_setting_radio(self, label, setting_key, current_settings, values):
        parent = Gtk.MenuItem(label=label)
        sub = Gtk.Menu()
        parent.set_submenu(sub)

        current = current_settings.get(setting_key, values[0])
        group = None

        for val in values:
            display = val.replace("-", " ").title()
            if group is None:
                item = Gtk.RadioMenuItem.new_with_label(None, display)
                group = item
            else:
                item = Gtk.RadioMenuItem.new_with_label([group], display)

            if val == current:
                item.set_active(True)

            item.connect("activate", self._make_setting_handler(setting_key, val))
            sub.append(item)

        sub.show_all()
        self.settings_submenu.append(parent)

    def _make_setting_handler(self, key, value):
        def handler(widget):
            if not widget.get_active():
                return
            self._run_vpn_action(
                ["config", "set", key, value],
                CMD_TIMEOUT_CONFIG_SET,
                "Setting %s to %s" % (key, value),
                on_done=lambda ret, out, err: notify(
                    "VPN Settings",
                    "%s set to %s" % (key, value),
                ),
            )
        return handler

    def _on_custom_dns(self, widget):
        try:
            result = subprocess.run(
                [
                    "zenity", "--entry",
                    "--title", "Custom DNS",
                    "--text", "Enter DNS server IPs (comma-separated):\n\nLeave empty to disable custom DNS.\n\nExample: 1.1.1.1,8.8.8.8",
                    "--width", "480",
                ],
                capture_output=True, text=True, timeout=60,
            )
            dns_input = result.stdout.strip()
            if result.returncode != 0:
                return
            if not dns_input:
                self._run_vpn_action(
                    ["config", "set", "custom-dns", "off"],
                    CMD_TIMEOUT_CONFIG_SET,
                    "Disabling custom DNS",
                    on_done=lambda ret, out, err: notify("Custom DNS", "Disabled"),
                )
            else:
                # Normalize: strip spaces, join with commas
                ips = [ip.strip() for ip in dns_input.replace(",", " ").split() if ip.strip()]
                if not ips:
                    return
                dns_arg = ",".join(ips)
                self._run_vpn_action(
                    ["config", "set", "custom-dns", "on", "--dns", dns_arg],
                    CMD_TIMEOUT_CONFIG_SET,
                    "Setting custom DNS to " + dns_arg,
                    on_done=lambda ret, out, err: notify(
                        "Custom DNS", "Set to: " + dns_arg if ret == 0 else "Failed: " + (err or "unknown error")
                    ),
                )
        except Exception as e:
            log.warning("zenity custom DNS failed: %s", e)

    # ── Status polling ─────────────────────────────────────────────────

    def _poll_status(self):
        if self.busy or self._polling:
            return True
        self._polling = True

        def callback(ret, out, err):
            self._polling = False
            if self._quitting:
                return
            state, info = parse_status(out)
            old_connected = self.connected
            old_signed_in = self.signed_in

            if state == "not_signed_in":
                self.signed_in = False
                self.connected = False
                self.connection_info = "Not signed in"
            elif state == "disconnected":
                self.signed_in = True
                self.connected = False
                self.connection_info = "Disconnected"
            elif state == "connected":
                self.signed_in = True
                self.connected = True
                self.connection_info = info if info else "Connected"
            else:
                self.signed_in = True
                self.connection_info = out[:80] if out else "Unknown"

            self.status_raw = out

            if self.connected:
                conn_text = self.connection_info.replace("\n", " | ")
                label = "Connected: " + conn_text
            elif not self.signed_in:
                label = "Not signed in — run 'protonvpn signin'"
            else:
                label = "Disconnected"

            if len(label) > 80:
                label = label[:77] + "..."

            self.status_item.set_label(label)

            if hasattr(self.indicator, "set_title"):
                self.indicator.set_title("Proton VPN — %s" % (
                    "Connected" if self.connected else "Disconnected"
                ))

            if self.connected and not old_connected:
                notify("Proton VPN", "Connected to VPN")
            elif not self.connected and old_connected and self.signed_in:
                notify("Proton VPN", "Disconnected from VPN")

        run_cli_async(["status"], CMD_TIMEOUT_STATUS, callback)
        return True

    # ── Country loading ────────────────────────────────────────────────

    def _load_countries_async(self):
        def callback(ret, out, err):
            if ret == 0:
                parsed = parse_countries(out)
                if parsed:
                    self.countries_cache = parsed
                    self.countries_loaded = True
                    log.info("Loaded %d countries", len(parsed))
                else:
                    log.warning("Country parse returned empty list from output:\n%s", out[:200])
            else:
                log.warning("Failed to load countries: ret=%d err=%s", ret, err)
        run_cli_async(["countries", "list"], CMD_TIMEOUT_COUNTRIES, callback)
        return False

    # ── Auto-connect ───────────────────────────────────────────────────

    def _auto_connect_startup(self):
        country = self.config.get("default_country", "CH")
        self._run_vpn_action(
            ["connect", "--country", country],
            CMD_TIMEOUT_CONNECT,
            "Auto-connecting to " + country,
            on_done=lambda ret, out, err: (
                notify("Proton VPN", "Auto-connected to " + country)
                if ret == 0
                else notify("Proton VPN", "Auto-connect failed: " + (err or out)[:80])
            ),
        )
        return False

    # ── VPN actions ────────────────────────────────────────────────────

    def _run_vpn_action(self, args, timeout, status_msg, on_done=None):
        if self.busy:
            notify("Proton VPN", "Already busy — please wait")
            return
        self.busy = True
        self.status_item.set_label(status_msg + "...")
        notify("Proton VPN", status_msg)

        def callback(ret, out, err):
            self.busy = False
            if self._quitting:
                return
            GLib.idle_add(self._poll_status)
            if on_done:
                on_done(ret, out, err)
        run_cli_async(args, timeout, callback)

    def _default_on_done(self, ret, out, err):
        if ret == 0:
            notify("Proton VPN", "Done")
        else:
            msg = (err or out)[:100] if (err or out) else "Unknown error"
            notify("Proton VPN", "Failed: " + msg)

    # ── Menu action handlers ───────────────────────────────────────────

    def _on_connect_fastest(self, widget):
        self._run_vpn_action(
            ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
            on_done=lambda ret, out, err: self._run_vpn_action(
                ["connect"], CMD_TIMEOUT_CONNECT,
                "Connecting (fastest)...",
                on_done=self._default_on_done,
            ),
        )

    def _on_connect_securecore(self, widget):
        self._run_vpn_action(
            ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
            on_done=lambda ret, out, err: self._run_vpn_action(
                ["connect", "--securecore"], CMD_TIMEOUT_CONNECT,
                "Connecting Secure Core...",
                on_done=self._default_on_done,
            ),
        )

    def _on_connect_tor(self, widget):
        self._run_vpn_action(
            ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
            on_done=lambda ret, out, err: self._run_vpn_action(
                ["connect", "--tor"], CMD_TIMEOUT_CONNECT,
                "Connecting Tor...",
                on_done=self._default_on_done,
            ),
        )

    def _on_connect_p2p(self, widget):
        self._run_vpn_action(
            ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
            on_done=lambda ret, out, err: self._run_vpn_action(
                ["connect", "--p2p"], CMD_TIMEOUT_CONNECT,
                "Connecting P2P...",
                on_done=self._default_on_done,
            ),
        )

    def _on_disconnect(self, widget):
        self._run_vpn_action(
            ["disconnect"], CMD_TIMEOUT_DISCONNECT,
            "Disconnecting...",
            on_done=self._default_on_done,
        )

    def _on_default_country(self, widget):
        if not self.countries_loaded:
            notify("Proton VPN", "Country list still loading, try again")
            return

        favorites = self.config.get("favorites", [])
        all_countries = list(self.countries_cache)
        all_countries.sort(key=lambda x: x[1].lower())

        current = self.config.get("default_country", "CH")
        args = [
            "zenity", "--list", "--radiolist",
            "--title", "Default Country",
            "--text", "Select the default country for auto-connect:",
            "--column", "", "--column", "Code", "--column", "Country",
            "--width", "500", "--height", "450",
            "--print-column=2",
        ]

        done = set()
        for code in favorites:
            for ccode, cname in all_countries:
                if ccode == code and ccode not in done:
                    done.add(ccode)
                    args.append("TRUE" if ccode == current else "FALSE")
                    args.append(ccode)
                    args.append(cname)
                    break

        for ccode, cname in all_countries:
            if ccode not in done:
                args.append("TRUE" if ccode == current else "FALSE")
                args.append(ccode)
                args.append(cname)

        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            selected = result.stdout.strip()
            if result.returncode == 0 and selected:
                self.config["default_country"] = selected
                self._save_config()
                notify("Default Country", "Set to " + selected)
        except Exception as e:
            log.warning("zenity default country failed: %s", e)

    def _on_manage_favorites(self, widget):
        if not self.countries_loaded:
            notify("Proton VPN", "Country list still loading, try again")
            return

        current_favorites = set(self.config.get("favorites", []))
        all_countries = list(self.countries_cache)
        all_countries.sort(key=lambda x: x[1].lower())

        args = [
            "zenity", "--list", "--checklist",
            "--title", "Manage Favorites",
            "--text", "Check countries to mark as favorites:",
            "--column", "", "--column", "Code", "--column", "Country",
            "--width", "500", "--height", "450",
            "--print-column=2", "--separator=,",
        ]

        for ccode, cname in all_countries:
            args.append("TRUE" if ccode in current_favorites else "FALSE")
            args.append(ccode)
            args.append(cname)

        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            if result.returncode == 0:
                selected = [s.strip() for s in result.stdout.strip().split(",") if s.strip()]
                self.config["favorites"] = selected
                self._save_config()
                notify("Favorites", "Updated (%d favorites)" % len(selected))
            elif result.returncode == 1:
                pass
            else:
                log.warning("zenity favorites returned %d", result.returncode)
        except Exception as e:
            log.warning("zenity favorites failed: %s", e)

    def _on_show_info(self, widget):
        def callback(ret, out, err):
            if ret != 0:
                text = "Failed: " + (err or out or "Unknown error")
            else:
                text = out
            GLib.idle_add(self._show_info_dialog, text)
        run_cli_async(["info"], CMD_TIMEOUT_INFO, callback)

    def _show_info_dialog(self, text):
        if self._quitting:
            return
        try:
            subprocess.run(
                [
                    "zenity", "--text-info",
                    "--title", "Proton VPN — Connection Info",
                    "--width", "550", "--height", "400",
                    "--font=monospace",
                ],
                input=text, text=True, timeout=30,
            )
        except Exception as e:
            log.warning("zenity info failed: %s", e)

    def _on_toggle_autostart(self, widget):
        if is_autostart_enabled():
            try:
                subprocess.run(
                    [DISABLE_AUTOSTART_SH, "--quiet"],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception as e:
                log.warning("disable autostart script failed: %s", e)
            notify("Autostart", "Disabled")
        else:
            try:
                subprocess.run(
                    [ENABLE_AUTOSTART_SH],
                    capture_output=True, text=True, timeout=10,
                )
            except Exception as e:
                log.warning("enable autostart script failed: %s", e)
            notify("Autostart", "Enabled")

        enabled = is_autostart_enabled()
        label = "Autostart (%s)" % ("Enabled" if enabled else "Disabled")
        self.autostart_item.set_label(label)

    def _on_refresh(self, widget):
        self.countries_loaded = False
        self.countries_cache = []
        GLib.idle_add(self._poll_status)
        GLib.timeout_add(500, self._load_countries_async)
        notify("Proton VPN", "Refreshing...")

    def _on_quit(self, widget):
        notify("Proton VPN", "Tray closed (VPN stays connected if active)")
        self._shutdown()

    # ── Main loop ──────────────────────────────────────────────────────

    def run(self):
        Gtk.main()


def main():
    parser = argparse.ArgumentParser(
        description="Proton VPN System Tray for Linux Mint MATE"
    )
    parser.add_argument(
        "--auto-connect", action="store_true",
        help="Auto-connect to default country on startup"
    )
    args = parser.parse_args()

    app = ProtonVPNTray(auto_connect=args.auto_connect)
    app.run()


if __name__ == "__main__":
    main()

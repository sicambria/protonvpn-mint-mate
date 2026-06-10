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
import time as _time
import re as _re

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
    "favorites": ["CH", "PA", "IS"],
    "last_connected": None,
}

CMD_TIMEOUT_STATUS = 5
CMD_TIMEOUT_CONNECT = 25
CMD_TIMEOUT_DISCONNECT = 10
CMD_TIMEOUT_COUNTRIES = 45
CMD_TIMEOUT_CONFIG = 8
CMD_TIMEOUT_INFO = 15
CMD_TIMEOUT_CONFIG_SET = 8
CONFIG_CACHE_TTL = 15
POLL_MIN_INTERVAL = 4          # minimum seconds between status polls
_MAX_CONSECUTIVE_TIMEOUTS = 3  # consecutive timeouts before breaker trip

_MAX_FAILURES = 10
_failure_count = 0
_consecutive_timeouts = 0
_polling_paused = False
_polling_paused_notified = False
_cli_lock = threading.Lock()


def run_cli(args, timeout=CMD_TIMEOUT_STATUS):
    global _failure_count, _consecutive_timeouts, _polling_paused, _polling_paused_notified
    with _cli_lock:
        if _failure_count > 3:
            delay = min(0.5 * (2 ** (_failure_count - 3)), 30)
            _time.sleep(delay)
        cmd = [PROTONVPN_BIN] + args
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            out, err = proc.communicate(timeout=timeout)
            if proc.returncode < 0:
                _failure_count += 1
                if _failure_count >= _MAX_FAILURES and not _polling_paused:
                    _polling_paused = True
                log.warning("protonvpn %s killed by signal %d (failure #%d)",
                            " ".join(args), -proc.returncode, _failure_count)
                return proc.returncode, out.strip(), err.strip()
            _failure_count = 0
            _consecutive_timeouts = 0
            _polling_paused = False
            _polling_paused_notified = False
            return proc.returncode, out.strip(), err.strip()
        except subprocess.TimeoutExpired:
            _failure_count += 1
            _consecutive_timeouts += 1
            if _consecutive_timeouts >= _MAX_CONSECUTIVE_TIMEOUTS and not _polling_paused:
                _polling_paused = True
                log.warning("protonvpn %s timed out %d times consecutively — "
                            "circuit breaker tripped",
                            " ".join(args), _consecutive_timeouts)
            elif _failure_count >= _MAX_FAILURES and not _polling_paused:
                _polling_paused = True
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            log.warning("protonvpn %s timed out after %ds (failure #%d)",
                        " ".join(args), timeout, _failure_count)
            return -1, "", "Command timed out"
        except FileNotFoundError:
            _failure_count += 1
            if _failure_count >= _MAX_FAILURES and not _polling_paused:
                _polling_paused = True
            return -2, "", "protonvpn binary not found"
        except Exception as e:
            _failure_count += 1
            if _failure_count >= _MAX_FAILURES and not _polling_paused:
                _polling_paused = True
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
            log.warning("protonvpn %s error: %s", " ".join(args), e)
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


def _collect_network_details():
    """Gather VPN IP and network adapter info for diagnostics."""
    details = []

    try:
        result = subprocess.run(
            ["ip", "-4", "addr", "show"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            details.append("--- Network Interfaces ---")
            details.append(result.stdout.strip())
    except Exception:
        details.append("--- Network Interfaces ---")
        details.append("(ip command not available)")

    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            details.append("--- Default Route ---")
            details.append(result.stdout.strip() or "(none)")
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "5", "https://vpn-api.proton.me/vpn/logicals"],
            capture_output=True, text=True, timeout=8,
        )
        if result.returncode == 0:
            details.append("--- VPN Server Info (via Proton API) ---")
            details.append("API reachable: yes")
        else:
            details.append("--- VPN Server Info (via Proton API) ---")
            details.append("API unreachable (VPN may not be active)")
    except Exception:
        details.append("--- VPN Server Info ---")
        details.append("(curl not available)")

    return "\n\n".join(details)


def _show_text_dialog(title, text, width=700, height=500):
    win = Gtk.Window(title=title, modal=True, default_width=width, default_height=height)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.set_keep_above(True)
    win.set_border_width(8)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    label = Gtk.Label(label=text, selectable=False, wrap=False, halign=Gtk.Align.START, valign=Gtk.Align.START)
    label.set_margin_start(6)
    label.set_margin_end(6)
    label.set_margin_top(6)
    label.set_margin_bottom(6)
    override_font = Gtk.CssProvider()
    override_font.load_from_data(b"label { font-family: monospace; }")
    label.get_style_context().add_provider(override_font, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    scrolled.add(label)

    btn = Gtk.Button(label="Close")
    btn.connect("clicked", lambda _: win.destroy())
    btn.set_halign(Gtk.Align.CENTER)
    btn.set_margin_top(8)
    btn.set_margin_bottom(4)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    vbox.pack_start(scrolled, True, True, 0)
    vbox.pack_start(btn, False, False, 0)
    win.add(vbox)
    win.show_all()
    win.present()
    return label


def _raise_window(title):
    for cmd in [
        ["wmctrl", "-a", title],
        ["xdotool", "search", "--sync", "--name", title, "windowactivate"],
    ]:
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
            return
        except FileNotFoundError:
            continue
        except Exception as e:
            log.warning("window raise failed (%s): %s", cmd[0], e)
            continue


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
        self.autostart_item = None
        self._country_parent = None
        self._settings_parent = None
        self._polling = False
        self._quitting = False
        self._info_dialog_active = False
        self._info_label = None
        self._config_cache = {}
        self._config_cache_time = 0
        self._last_poll_time = 0

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

    def _check_pid(self):
        if not os.path.exists(PID_FILE):
            return
        try:
            with open(PID_FILE, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print("protonvpn-tray is already running (PID %d)." % pid, file=sys.stderr)
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
            print(
                "protonvpn-tray: PID file already exists (another instance may be starting).",
                file=sys.stderr,
            )
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

    def _on_signal(self, signum, frame):
        log.info("Received signal %s, shutting down", signum)
        self._shutdown()

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
            self.menu.popup(
                None, None, icon.position_menu, icon, button, activate_time
            )

    def _on_status_icon_activate(self, icon):
        pass

    def _mk_item(self, label, callback):
        item = Gtk.MenuItem(label=label)
        if callback:
            item.connect("activate", callback)
        return item

    def _mk_sep(self):
        return Gtk.SeparatorMenuItem()

    def _build_menu(self):
        menu = Gtk.Menu()
        menu.connect("show", self._on_menu_show)

        self.status_item = Gtk.MenuItem(label="Proton VPN")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Connect (Fastest)", self._on_connect_fastest))

        self._country_parent = Gtk.MenuItem(label="Connect to Country")
        self._country_parent.connect("activate", self._on_country_activate)
        menu.append(self._country_parent)

        menu.append(self._mk_item("Secure Core", self._on_connect_securecore))
        menu.append(self._mk_item("Tor", self._on_connect_tor))
        menu.append(self._mk_item("P2P", self._on_connect_p2p))
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Disconnect", self._on_disconnect))
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Default Country...", self._on_default_country))
        menu.append(self._mk_item("Manage Favorites...", self._on_manage_favorites))
        menu.append(self._mk_sep())

        self._settings_parent = Gtk.MenuItem(label="Settings")
        self._settings_parent.connect("activate", self._on_settings_activate)
        menu.append(self._settings_parent)
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Connection Info", self._on_show_info))
        menu.append(self._mk_sep())

        autostart_enabled = is_autostart_enabled()
        label = "Turn off autostart (now enabled)" if autostart_enabled else "Turn on autostart (now disabled)"
        self.autostart_item = Gtk.MenuItem(label=label)
        self.autostart_item.connect("activate", self._on_toggle_autostart)
        menu.append(self.autostart_item)

        menu.append(self._mk_item("Refresh", self._on_refresh))
        menu.append(self._mk_sep())

        menu.append(self._mk_item("About", self._on_about))
        menu.append(self._mk_sep())

        menu.append(self._mk_item("Quit", self._on_quit))

        menu.show_all()
        return menu

    def _on_menu_show(self, widget):
        self._rebuild_all_submenus()

    def _on_country_activate(self, widget):
        pass

    def _on_settings_activate(self, widget):
        pass

    def _rebuild_all_submenus(self):
        if self._country_parent:
            old_country = self._country_parent.get_submenu()
            if old_country:
                old_country.destroy()
            self._country_parent.set_submenu(self._build_country_menu())
            self._country_parent.show_all()

        if self._settings_parent:
            old_settings = self._settings_parent.get_submenu()
            if old_settings:
                old_settings.destroy()
            self._settings_parent.set_submenu(self._build_settings_menu())
            self._settings_parent.show_all()

    def _build_country_menu(self):
        sub = Gtk.Menu()

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
            sub.append(item)

        if fav_entries:
            sub.append(self._mk_sep())

        if self.countries_loaded and all_countries:
            other = [(c, n) for c, n in all_countries if c not in done_codes]
            other.sort(key=lambda x: x[1].lower())
            for code, name in other:
                item = Gtk.MenuItem(label=name + " (" + code + ")")
                item.connect("activate", self._make_country_connect_handler(code))
                sub.append(item)
        elif not self.countries_loaded:
            item = Gtk.MenuItem(label="Loading countries...")
            item.set_sensitive(False)
            sub.append(item)
        else:
            item = Gtk.MenuItem(label="(no countries available)")
            item.set_sensitive(False)
            sub.append(item)

        sub.show_all()
        return sub

    def _make_country_connect_handler(self, code):
        def handler(widget):
            if not self.connected:
                self._do_connect(["--country", code])
            else:
                self._run_vpn_action(
                    ["disconnect"],
                    CMD_TIMEOUT_DISCONNECT,
                    "Disconnecting",
                    on_done=lambda ret, out, err: self._do_connect(["--country", code]),
                )
        return handler

    def _build_settings_menu(self):
        sub = Gtk.Menu()

        settings = {}
        now = _time.time()
        if self._config_cache and (now - self._config_cache_time) < CONFIG_CACHE_TTL:
            settings = self._config_cache
        else:
            ret, out, err = run_cli(["config", "list"], CMD_TIMEOUT_CONFIG)
            if ret == 0:
                settings = parse_config_list(out)
                self._config_cache = settings
                self._config_cache_time = now
            else:
                settings = self._config_cache

        if not settings:
            item = Gtk.MenuItem(label="Could not read settings")
            item.set_sensitive(False)
            sub.append(item)
            sub.show_all()
            return sub

        self._add_setting_radio(sub, "NetShield", "netshield", settings,
                                 ["off", "malware-only", "malware-ads-trackers"])
        self._add_setting_radio(sub, "Kill Switch", "kill-switch", settings,
                                 ["off", "standard"])
        self._add_setting_radio(sub, "VPN Accelerator", "vpn-accelerator", settings,
                                 ["on", "off"])
        self._add_setting_radio(sub, "IPv6", "ipv6", settings,
                                 ["on", "off"])
        self._add_setting_radio(sub, "Moderate NAT", "moderate-nat", settings,
                                 ["on", "off"])
        self._add_setting_radio(sub, "Port Forwarding", "port-forwarding", settings,
                                 ["on", "off"])
        self._add_setting_radio(sub, "Anonymous Crash Reports",
                                 "anonymous-crash-reports", settings, ["on", "off"])

        sub.append(self._mk_sep())

        dns_item = Gtk.MenuItem(label="Custom DNS...")
        dns_item.connect("activate", self._on_custom_dns)
        sub.append(dns_item)

        sub.show_all()
        return sub

    def _add_setting_radio(self, parent_menu, label, setting_key, current_settings, values):
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
        parent_menu.append(parent)

    def _make_setting_handler(self, key, value):
        def on_done(ret, out, err, _key=key):
            if ret == 0:
                self._config_cache.pop(_key, None)
                notify("VPN Settings", "%s set to %s" % (_key, value))
            else:
                notify("VPN Settings", "Failed: " + (err or out)[:80])

        def handler(widget):
            if not widget.get_active():
                return
            self._run_vpn_action(
                ["config", "set", key, value],
                CMD_TIMEOUT_CONFIG_SET,
                "Setting %s to %s" % (key, value),
                on_done=on_done,
            )
        return handler

    def _on_custom_dns(self, widget):
        try:
            proc = subprocess.Popen(
                [
                    "zenity", "--entry",
                    "--title", "Custom DNS",
                    "--modal",
                    "--text",
                    "Enter DNS server IPs (comma-separated):\n\n"
                    "Leave empty to disable custom DNS.\n\n"
                    "Example: 1.1.1.1,8.8.8.8",
                    "--width", "480",
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            _raise_window("Custom DNS")
            stdout, _ = proc.communicate(timeout=60)
            dns_input = stdout.strip()
            if proc.returncode != 0:
                return
            if not dns_input:
                self._run_vpn_action(
                    ["config", "set", "custom-dns", "off"],
                    CMD_TIMEOUT_CONFIG_SET,
                    "Disabling custom DNS",
                    on_done=lambda ret, out, err: notify(
                        "Custom DNS", "Disabled"
                    ),
                )
            else:
                ips = [
                    ip.strip()
                    for ip in dns_input.replace(",", " ").split()
                    if ip.strip()
                ]
                if not ips:
                    return
                dns_arg = ",".join(ips)
                self._run_vpn_action(
                    ["config", "set", "custom-dns", "on", "--dns", dns_arg],
                    CMD_TIMEOUT_CONFIG_SET,
                    "Setting custom DNS to " + dns_arg,
                    on_done=lambda ret, out, err: notify(
                        "Custom DNS",
                        "Set to: " + dns_arg
                        if ret == 0
                        else "Failed: " + (err or "unknown error"),
                    ),
                )
        except Exception as e:
            log.warning("zenity custom DNS failed: %s", e)

    def _poll_status(self):
        global _polling_paused_notified
        if _polling_paused:
            if not _polling_paused_notified:
                _polling_paused_notified = True
                notify("Proton VPN",
                       "CLI is not responding. Status polling paused. "
                       "Will resume on next successful manual action.",
                       "network-offline")
                self.status_item.set_label("CLI unresponsive — polling paused")
            return True
        if self.busy or self._polling:
            return True
        now = _time.monotonic()
        if self._last_poll_time > 0 and (now - self._last_poll_time) < POLL_MIN_INTERVAL:
            return True
        self._polling = True

        def callback(ret, out, err):
            self._polling = False
            self._last_poll_time = _time.monotonic()
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
                self.indicator.set_title(
                    "Proton VPN — %s"
                    % ("Connected" if self.connected else "Disconnected")
                )

            icon_name = "network-vpn" if self.connected else "network-offline"
            if hasattr(self.indicator, "set_icon_full"):
                self.indicator.set_icon_full(icon_name, "Proton VPN")
            elif hasattr(self.indicator, "set_icon"):
                self.indicator.set_icon(icon_name)
            elif hasattr(self, "_status_icon") and self._status_icon is not None:
                self._status_icon.set_from_icon_name(icon_name)

            if self.connected and not old_connected:
                notify("Proton VPN", "Connected to VPN")
            elif not self.connected and old_connected and self.signed_in:
                notify("Proton VPN", "Disconnected from VPN")

        run_cli_async(["status"], CMD_TIMEOUT_STATUS, callback)
        return True

    def _load_countries_async(self):
        def callback(ret, out, err):
            if ret == 0:
                parsed = parse_countries(out)
                if parsed:
                    self.countries_cache = parsed
                    self.countries_loaded = True
                    log.info("Loaded %d countries", len(parsed))
                else:
                    log.warning(
                        "Country parse returned empty list from output:\n%s",
                        out[:200],
                    )
            else:
                log.warning("Failed to load countries: ret=%d err=%s", ret, err)
        run_cli_async(["countries", "list"], CMD_TIMEOUT_COUNTRIES, callback)
        return False

    def _auto_connect_startup(self):
        country = self.config.get("default_country", "CH")
        self._do_connect(["--country", country], status_msg="Auto-connecting to " + country)
        return False

    def _do_connect(self, connect_args, status_msg=None):
        args = ["connect"] + connect_args
        if status_msg is None:
            status_msg = "Connecting..."
        self._run_vpn_action(
            args,
            CMD_TIMEOUT_CONNECT,
            status_msg,
            on_done=lambda ret, out, err: (
                notify("Proton VPN", status_msg + " — Done")
                if ret == 0
                else notify(
                    "Proton VPN",
                    "Connect failed: " + ((err or out)[:80]),
                )
            ),
        )

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

    def _on_connect_fastest(self, widget):
        if not self.connected:
            self._do_connect([], status_msg="Connecting (fastest)...")
        else:
            self._run_vpn_action(
                ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
                on_done=lambda r, o, e: self._do_connect(
                    [], status_msg="Connecting (fastest)..."
                ),
            )

    def _on_connect_securecore(self, widget):
        if not self.connected:
            self._do_connect(["--securecore"], status_msg="Connecting Secure Core...")
        else:
            self._run_vpn_action(
                ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
                on_done=lambda r, o, e: self._do_connect(
                    ["--securecore"], status_msg="Connecting Secure Core..."
                ),
            )

    def _on_connect_tor(self, widget):
        if not self.connected:
            self._do_connect(["--tor"], status_msg="Connecting Tor...")
        else:
            self._run_vpn_action(
                ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
                on_done=lambda r, o, e: self._do_connect(
                    ["--tor"], status_msg="Connecting Tor..."
                ),
            )

    def _on_connect_p2p(self, widget):
        if not self.connected:
            self._do_connect(["--p2p"], status_msg="Connecting P2P...")
        else:
            self._run_vpn_action(
                ["disconnect"], CMD_TIMEOUT_DISCONNECT, "Disconnecting",
                on_done=lambda r, o, e: self._do_connect(
                    ["--p2p"], status_msg="Connecting P2P..."
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
            args.insert(1, "--modal")
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            _raise_window("Default Country")
            stdout, _ = proc.communicate(timeout=60)
            selected = stdout.strip()
            if proc.returncode == 0 and selected:
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
            args.insert(1, "--modal")
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            _raise_window("Manage Favorites")
            stdout, _ = proc.communicate(timeout=60)
            if proc.returncode == 0:
                selected = [
                    s.strip()
                    for s in stdout.strip().split(",")
                    if s.strip()
                ]
                self.config["favorites"] = selected
                self._save_config()
                self._rebuild_all_submenus()
                notify("Favorites", "Updated (%d favorites)" % len(selected))
            elif proc.returncode == 1:
                pass
            else:
                log.warning("zenity favorites returned %d", proc.returncode)
        except Exception as e:
            log.warning("zenity favorites failed: %s", e)

    def _on_show_info(self, widget):
        if self._info_dialog_active:
            return

        connection_status = self.connection_info

        self._info_label = _show_text_dialog(
            "Proton VPN — Connection Info",
            (
                "═══ VPN Connection ═══\n"
                + (connection_status or self.status_raw or "Unknown")
                + "\n\n═══ Proton VPN Account ═══\n"
                + "Collecting info — please wait...\n\n"
                + "═══ Network Diagnostics ═══\n"
                + "Collecting network details..."
            ),
        )
        self._info_dialog_active = True

        def callback(ret, out, err):
            if self._quitting and not self._info_dialog_active:
                return

            parts = []
            vpn_lines = [connection_status or self.status_raw or "Unknown"]
            try:
                r = subprocess.run(
                    ["curl", "-s", "--max-time", "5", "https://api.ipify.org"],
                    capture_output=True, text=True, timeout=8,
                )
                if r.returncode == 0 and r.stdout.strip():
                    vpn_lines.append("Real IP: " + r.stdout.strip())
            except Exception:
                pass
            try:
                r = subprocess.run(
                    ["ip", "-4", "addr", "show"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    for line in r.stdout.split("\n"):
                        if "inet " in line:
                            cols = line.strip().split()
                            iface = cols[-1]
                            if "tun" in iface or "proton" in iface:
                                vpn_lines.append(
                                    "VPN IP: " + cols[1].split("/")[0]
                                )
                                break
            except Exception:
                pass
            parts.append("═══ VPN Connection ═══")
            parts.append("\n".join(vpn_lines))
            parts.append("═══ Proton VPN Account ═══")
            if ret == 0:
                parts.append(out)
            else:
                parts.append("CLI error: " + (err or out or "Unknown error"))
            parts.append("═══ Network Diagnostics ═══")
            net_text = _collect_network_details()
            parts.append(net_text)
            full = "\n\n".join(parts)
            GLib.idle_add(self._update_info_dialog, full)

        run_cli_async(["info"], CMD_TIMEOUT_INFO, callback)

    def _update_info_dialog(self, full_text):
        if not self._info_dialog_active or self._quitting:
            return
        try:
            self._info_label.set_text(full_text)
        except Exception:
            self._info_dialog_active = False

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
        self._config_cache = {}
        self._config_cache_time = 0
        GLib.idle_add(self._poll_status)
        GLib.timeout_add(500, self._load_countries_async)
        notify("Proton VPN", "Refreshing...")

    def _on_about(self, widget):
        dlg = Gtk.AboutDialog()
        dlg.set_program_name("Proton VPN Tray")
        dlg.set_title("About Proton VPN Tray")
        dlg.set_comments(
            "System tray application for managing Proton VPN "
            "connections on Linux Mint MATE.\n"
            "Provides one-click connect/disconnect, country selector, "
            "Secure Core, Tor, P2P modes, Kill Switch, and more."
        )
        dlg.set_website("https://github.com/sicambria/protonvpn-mint-mate")
        dlg.set_website_label("GitHub Repository")
        dlg.set_copyright("Copyright (C) 2026  protonvpn-mint-mate contributors")
        dlg.set_license_type(Gtk.License.GPL_3_0)
        dlg.set_authors(["protonvpn-mint-mate contributors"])
        dlg.add_credit_section(
            "Related Projects",
            ["Proton VPN CLI — https://github.com/ProtonVPN/proton-vpn-cli"],
        )
        dlg.set_position(Gtk.WindowPosition.CENTER)
        dlg.set_modal(True)
        dlg.set_keep_above(True)
        dlg.present()
        dlg.run()
        dlg.destroy()

    def _on_quit(self, widget):
        notify("Proton VPN", "Tray closed (VPN stays connected if active)")
        self._shutdown()

    def run(self):
        Gtk.main()


def main():
    parser = argparse.ArgumentParser(
        description="Proton VPN System Tray for Linux Mint MATE"
    )
    parser.add_argument(
        "--auto-connect",
        action="store_true",
        help="Auto-connect to default country on startup",
    )
    args = parser.parse_args()

    app = ProtonVPNTray(auto_connect=args.auto_connect)
    app.run()


if __name__ == "__main__":  # pragma: no cover
    main()

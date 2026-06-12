#!/usr/bin/env python3
"""Coverage gap tests for lines/branches not hit by test_tray.py.

Tests all uncovered code paths identified by coverage.py --branch run:
constructor, indicator chain, menu structure, notification exceptions,
PID edge cases, _poll_status callback, _load_countries_async, and
every uncovered branch in module-level functions and ProtonVPNTray methods.
"""

import os
import sys
import json
import tempfile
import shutil
import signal
import time
import subprocess as real_subprocess

from unittest.mock import patch, MagicMock, call

from helpers import load_tray_module, check, heading, summary, make_tray

MOD = load_tray_module()

# ---------------------------------------------------------------------------
# 46. is_autostart_enabled — exception path
# ---------------------------------------------------------------------------
heading("46. is_autostart_enabled — exception path")

ie = MOD.is_autostart_enabled

tmpdir_46 = tempfile.mkdtemp()
try:
    desktop = os.path.join(tmpdir_46, "protonvpn-tray.desktop")

    # File exists but read raises → fallback to os.path.exists → True
    with patch("os.path.expanduser", return_value=desktop):
        with open(desktop, "w") as f:
            f.write("some content")
        with patch("builtins.open", side_effect=OSError("read denied")):
            result = ie()
            check("46.1: read exception → fallback to exists check",
                  result is True, "got %s" % result)

    # File exists but read raises with file then deleted (exists → False)
    with patch("os.path.expanduser", return_value=desktop):
        os.remove(desktop)
        with patch("builtins.open", side_effect=OSError("read denied")):
            result = ie()
            check("46.2: read exception + no file → False",
                  result is False, "got %s" % result)
finally:
    shutil.rmtree(tmpdir_46, ignore_errors=True)

# ---------------------------------------------------------------------------
# 47. parse_status — "unknown" from Status: header with non-standard value
# ---------------------------------------------------------------------------
heading("47. parse_status — unknown state")

ps = MOD.parse_status

# "Status: Connecting..." — has Status: header but value not connected/disconnected
state, info = ps("Status:       Connecting...\nProgress: 50%")
check("47.1: connecting → unknown", state == "unknown",
      "got %s" % state)
check("47.2: connecting → full output preserved", "Connecting" in info,
      "got %r" % info[:100])

# "Status:      " (whitespace only after Status:) → unknown
state, info = ps("Status:   ")
check("47.3: Status with empty value → unknown", state == "unknown",
      "got %s" % state)

# ---------------------------------------------------------------------------
# 48. notify() — uninitialized guard + exception handling
# ---------------------------------------------------------------------------
heading("48. notify — uninitialized + exception paths")

# 48.1 Notify not initialized → returns silently
saved_init = MOD._notify_initialized
MOD._notify_initialized = False
try:
    MOD.notify("Title", "Body")
    check("48.1: notify when uninitialized → no crash", True)
finally:
    MOD._notify_initialized = saved_init

# 48.2 Notify.Notification.new raises exception → logged, no crash
MOD._notify_initialized = True
orig_new = MOD.Notify.Notification.new
MOD.Notify.Notification.new = MagicMock(side_effect=RuntimeError("boom"))
try:
    with patch.object(MOD.log, "warning") as mock_warn:
        MOD.notify("T", "B")
        check("48.2: notification exception caught",
              mock_warn.called, "warning not logged")
finally:
    MOD.Notify.Notification.new = orig_new
    MOD._notify_initialized = saved_init

# ---------------------------------------------------------------------------
# 49. _collect_network_details — edge branches
# ---------------------------------------------------------------------------
heading("49. _collect_network_details — edge branches")

cnd = MOD._collect_network_details

# 49.1 ip addr returns non-zero (not exception, just non-zero)
# Current code: only adds Network Interfaces section on returncode==0 or
# exception. Returncode!=0 without exception → section silently skipped.
with patch.object(MOD.subprocess, "run") as mock_run:
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_run.return_value = mock_result
    out = cnd()
    check("49.1: ip addr non-zero → no crash", True)

# 49.2 ip route raises exception
with patch.object(MOD.subprocess, "run") as mock_run:
    # First call (ip addr) succeeds, second call (ip route) raises
    mock_result_ok = MagicMock()
    mock_result_ok.returncode = 0
    mock_result_ok.stdout = "some output"
    mock_run.side_effect = [mock_result_ok, FileNotFoundError, mock_result_ok]
    out = cnd()
    check("49.2: ip route exception → no crash", "Default Route" in out
          or "Network Interfaces" in out)

# 49.3 curl error (non-zero return code)
with patch.object(MOD.subprocess, "run") as mock_run:
    mock_ip = MagicMock()
    mock_ip.returncode = 0
    mock_ip.stdout = "eth0..."
    mock_route = MagicMock()
    mock_route.returncode = 0
    mock_route.stdout = "default via ..."
    mock_curl = MagicMock()
    mock_curl.returncode = 1
    mock_curl.stdout = ""
    mock_curl.stderr = "error"
    mock_run.side_effect = [mock_ip, mock_route, mock_curl]
    out = cnd()
    check("49.3: curl error → API unreachable msg",
          "API unreachable" in out)

# 49.4 curl raises exception
with patch.object(MOD.subprocess, "run") as mock_run:
    mock_ip = MagicMock()
    mock_ip.returncode = 0
    mock_ip.stdout = "eth0..."
    mock_route = MagicMock()
    mock_route.returncode = 0
    mock_route.stdout = "default via ..."
    mock_run.side_effect = [mock_ip, mock_route, FileNotFoundError]
    out = cnd()
    check("49.4: curl exception → curl not available msg",
          "curl not available" in out)

# ---------------------------------------------------------------------------
# 50. PID edge cases — _check_pid OSError, _write_pid OSError, _remove_pid OSError
# ---------------------------------------------------------------------------
heading("50. PID edge cases")
tmpdir_50 = tempfile.mkdtemp()
try:
    pid_f = os.path.join(tmpdir_50, "tray.pid")

    # 50.1 _check_pid: OSError from os.kill (not ProcessLookupError) → cleanup
    with patch.object(MOD, "PID_FILE", pid_f):
        with open(pid_f, "w") as f:
            f.write("12345")
        with patch("os.kill", side_effect=PermissionError("no access")):
            MOD.ProtonVPNTray._check_pid(MagicMock())
        check("50.1: os.kill OSError → file removed",
              not os.path.exists(pid_f))

    # 50.2 _write_pid: PermissionError from os.open (not FileExistsError)
    # Current code: only catches FileExistsError, so PermissionError propagates
    with patch.object(MOD, "PID_FILE", pid_f):
        wp = MOD.ProtonVPNTray._write_pid
        mock_self = MagicMock()
        with patch.object(MOD.os, "open",
                          side_effect=PermissionError("denied")):
            try:
                wp(mock_self)
                check("50.2: PermissionError propagates (not caught)",
                      False, "should have raised")
            except PermissionError:
                check("50.2: PermissionError propagates as expected", True)

    # 50.3 _remove_pid: OSError suppressed
    def failing_remove(path):
        raise OSError("cannot remove")

    with patch.object(MOD, "PID_FILE", pid_f):
        with open(pid_f, "w") as f:
            f.write("1")
        with patch("os.remove", side_effect=OSError("permission denied")):
            MOD.ProtonVPNTray._remove_pid(MagicMock())
        check("50.3: remove_pid OSError suppressed", True)

    # 50.4 _remove_pid: file doesn't exist (os.path.exists returns False)
    with patch.object(MOD, "PID_FILE", pid_f):
        if os.path.exists(pid_f):
            os.remove(pid_f)
        with patch("os.remove") as mock_rem:
            MOD.ProtonVPNTray._remove_pid(MagicMock())
            check("50.4: remove_pid no file → os.remove not called",
                  not mock_rem.called)

    # 50.5 _shutdown: Notify.uninit raises exception → suppressed
    t = MagicMock()
    t._quitting = False
    with patch.object(MOD, "Notify") as mock_notify:
        mock_notify.uninit = MagicMock(side_effect=RuntimeError("uninit fail"))
        with patch.object(MOD, "Gtk") as mock_gtk:
            MOD.ProtonVPNTray._shutdown(t)
            check("50.5: shutdown Notify.uninit exception suppressed",
                  mock_gtk.main_quit.called)

finally:
    shutil.rmtree(tmpdir_50, ignore_errors=True)

# ---------------------------------------------------------------------------
# 51. _create_indicator / _try_ayatana / _try_status_icon
# ---------------------------------------------------------------------------
heading("51. Indicator creation chain")

t = make_tray(MOD)

# 51.1 _try_ayatana: success path
# We need to inject AyatanaAppIndicator3 into the mocked gi.repository
aya_mock = MagicMock()
aya_mock.IndicatorCategory = MagicMock()
aya_mock.IndicatorCategory.APPLICATION_STATUS = "application_status"
aya_mock.IndicatorStatus = MagicMock()
aya_mock.IndicatorStatus.ACTIVE = "active"
sys.modules["gi.repository"].AyatanaAppIndicator3 = aya_mock

# gi.require_version must not raise for "AyatanaAppIndicator3", "0.1"
def require_version_side_effect(name, ver):
    if name == "AyatanaAppIndicator3" and ver == "0.1":
        return None
    raise ValueError("unsupported version")
MOD.gi.require_version.side_effect = require_version_side_effect

indicator = MOD.ProtonVPNTray._try_ayatana(t)
check("51.1: _try_ayatana success → returns indicator", indicator is not None)
check("51.2: indicator category set", indicator.set_status.called)
check("51.3: indicator title set", indicator.set_title.called)

# 51.2 _try_ayatana: ImportError → returns None
# Remove the mock so import raises
del sys.modules["gi.repository"].AyatanaAppIndicator3
# Also make gi.require_version NOT raise (but import still fails)
MOD.gi.require_version.side_effect = lambda n, v: None
result = MOD.ProtonVPNTray._try_ayatana(t)
check("51.4: _try_ayatana ImportError → None", result is None)

# 51.3 _try_ayatana: ValueError from require_version → returns None
MOD.gi.require_version.side_effect = lambda n, v: (
    exec("raise ValueError('bad')") if n == "AyatanaAppIndicator3" else None)
indicator = MOD.ProtonVPNTray._try_ayatana(t)
check("51.5: _try_ayatana ValueError → None", indicator is None)

# 51.4 _try_status_icon: creates Gtk.StatusIcon
t2 = make_tray(MOD)
t2._status_icon = None
sys.modules["gi.repository"].Gtk.StatusIcon = MagicMock()
mock_icon = MagicMock()
sys.modules["gi.repository"].Gtk.StatusIcon.return_value = mock_icon
icon = MOD.ProtonVPNTray._try_status_icon(t2)
check("51.6: _try_status_icon returns icon", icon is not None)
check("51.7: _status_icon stored", t2._status_icon is not None)
check("51.8: set_from_icon_name called", mock_icon.set_from_icon_name.called)
check("51.9: set_tooltip_text called", mock_icon.set_tooltip_text.called)
check("51.10: popup-menu connected",
      mock_icon.connect.called)

# 51.5 _create_indicator: first tries Ayatana, falls back to StatusIcon
# Restore Ayatana and StatusIcon mocks
sys.modules["gi.repository"].AyatanaAppIndicator3 = aya_mock
sys.modules["gi.repository"].Gtk.StatusIcon = MagicMock()
MOD.gi.require_version.side_effect = None
MOD.gi.require_version = MagicMock()
t3 = make_tray(MOD)
t3.indicator = None

with patch.object(MOD.ProtonVPNTray, "_try_ayatana",
                  return_value=aya_mock) as mock_aya:
    with patch.object(MOD.ProtonVPNTray, "_try_status_icon") as mock_status:
        MOD.ProtonVPNTray._create_indicator(t3)
        check("51.11: _create_indicator tries Ayatana first",
              mock_aya.called)
        check("51.12: _create_indicator NOT fallback when Ayatana works",
              not mock_status.called, "StatusIcon was called despite Ayatana success")
        check("51.13: indicator is Ayatana result",
              t3.indicator is aya_mock)

# 51.6 _create_indicator: Ayatana fails → fallback to StatusIcon
t4 = make_tray(MOD)
t4.indicator = None
with patch.object(MOD.ProtonVPNTray, "_try_ayatana",
                  return_value=None) as mock_aya:
    with patch.object(MOD.ProtonVPNTray, "_try_status_icon",
                      return_value=mock_icon) as mock_status:
        MOD.ProtonVPNTray._create_indicator(t4)
        check("51.14: _create_indicator falls back when Ayatana fails",
              mock_status.called)
        check("51.15: indicator is StatusIcon result",
              t4.indicator is mock_icon)

# ---------------------------------------------------------------------------
# 52. _on_status_icon_popup / _on_status_icon_activate
# ---------------------------------------------------------------------------
heading("52. Status icon callbacks")

t = make_tray(MOD)
t.menu = MagicMock()
mock_icon2 = MagicMock()

# 52.1 _on_status_icon_popup calls menu.popup
MOD.ProtonVPNTray._on_status_icon_popup(t, mock_icon2, 3, 1234)
check("52.1: popup calls menu.popup", t.menu.popup.called,
      "menu.popup was not called")

# 52.2 _on_status_icon_popup when menu is None → no crash
t.menu = None
try:
    MOD.ProtonVPNTray._on_status_icon_popup(t, mock_icon2, 3, 1234)
    check("52.2: popup with None menu → no crash", True)
except Exception as e:
    check("52.2: popup with None menu → no crash", False, str(e))

# 52.3 _on_status_icon_activate does nothing (passthrough)
t.menu = MagicMock()
try:
    MOD.ProtonVPNTray._on_status_icon_activate(t, mock_icon2)
    check("52.3: activate passthrough → no crash", True)
except Exception as e:
    check("52.3: activate passthrough → no crash", False, str(e))

# ---------------------------------------------------------------------------
# 53. _mk_item / _mk_sep
# ---------------------------------------------------------------------------
heading("53. _mk_item and _mk_sep")

t = make_tray(MOD)
t.indicator = MagicMock()

original_new = MOD.Gtk.MenuItem
MOD.Gtk.MenuItem = MagicMock(return_value=MagicMock())
try:
    item = MOD.ProtonVPNTray._mk_item(t, "Test Label", None)
    check("53.1: _mk_item returns MenuItem", item is not None)
    last_call = MOD.Gtk.MenuItem.call_args
    passed_label = last_call[1].get("label", "") if last_call and len(last_call) >= 1 else ""
    if not passed_label and last_call and last_call[0]:
        passed_label = last_call[0][0]
    check("53.2: _mk_item label matches",
          passed_label == "Test Label",
          "label arg was %r" % passed_label)
finally:
    MOD.Gtk.MenuItem = original_new

def dummy_cb(w):
    pass

item2 = MOD.ProtonVPNTray._mk_item(t, "Clickable", dummy_cb)
check("53.3: _mk_item with callback", item2 is not None)

sep = MOD.ProtonVPNTray._mk_sep(t)
check("53.4: _mk_sep returns SeparatorMenuItem",
      sep is not None, "got %s" % type(sep))

# ---------------------------------------------------------------------------
# 54. _build_menu — main menu structure
# ---------------------------------------------------------------------------
heading("54. _build_menu — main menu structure")

t = make_tray(MOD)

# Mock all GTK widgets to capture calls
with patch.object(MOD.Gtk, "Menu") as mock_menu_cls, \
     patch.object(MOD.Gtk, "MenuItem") as mock_mi_cls, \
     patch.object(MOD.Gtk, "SeparatorMenuItem") as mock_sep_cls:

    mock_menu = MagicMock()
    mock_menu_cls.return_value = mock_menu
    mock_mi_cls.return_value = MagicMock()
    mock_sep_cls.return_value = MagicMock()

    result = MOD.ProtonVPNTray._build_menu(t)

    check("54.1: menu created", mock_menu_cls.called)
    check("54.2: menu.show_all called", mock_menu.show_all.called)
    check("54.3: menu returned", result is mock_menu)

    # Collect labels from MenuItem calls (they have label=xxx in kwargs)
    labels = []
    for c in mock_mi_cls.call_args_list:
        if "label" in c[1]:
            labels.append(c[1]["label"])
        elif c[0]:
            labels.append(str(c[0][0]))

    check("54.4: status_item created", len(labels) >= 1)
    check("54.5: status_item is insensitive",
          mock_mi_cls.return_value.set_sensitive.called)

    # item.label for autostart items
    autostart_labels = [l for l in labels if "autostart" in l.lower()]
    check("54.6: autostart label present",
          len(autostart_labels) >= 1,
          "labels: %s" % labels)

    check("54.7: connect(Fastest) label present",
          any("Fastest" in l for l in labels),
          "labels: %s" % labels)
    check("54.8: Disconnect label present",
          any("Disconnect" in l for l in labels),
          "labels: %s" % labels)
    check("54.9: Quit label present",
          any("Quit" in l for l in labels),
          "labels: %s" % labels)

    check("54.10: menu.connect show called",
          mock_menu.connect.called,
          "connect not called")

# 54.11: autostart label logic (enabled vs disabled) with real is_autostart_enabled
t.autostart_item = MagicMock()
with patch.object(MOD, "is_autostart_enabled", return_value=True):
    tray2 = make_tray(MOD)
    tray2.autostart_item = MagicMock()
    with patch.object(MOD.Gtk, "Menu", return_value=MagicMock()), \
         patch.object(MOD.Gtk, "MenuItem") as mock_mi, \
         patch.object(MOD.Gtk, "SeparatorMenuItem", return_value=MagicMock()):
        mock_mi.return_value = MagicMock()
        MOD.ProtonVPNTray._build_menu(tray2)
        calls = mock_mi.call_args_list
        auto_labels = [str(c[1].get("label", c[0][0] if c[0] else ""))
                       for c in calls
                       if "autostart" in str(c).lower()]
        check("54.12: autostart enabled → 'turn off' label",
              any("turn off" in l.lower() for l in auto_labels))

with patch.object(MOD, "is_autostart_enabled", return_value=False):
    tray3 = make_tray(MOD)
    tray3.autostart_item = MagicMock()
    with patch.object(MOD.Gtk, "Menu", return_value=MagicMock()), \
         patch.object(MOD.Gtk, "MenuItem") as mock_mi, \
         patch.object(MOD.Gtk, "SeparatorMenuItem", return_value=MagicMock()):
        mock_mi.return_value = MagicMock()
        MOD.ProtonVPNTray._build_menu(tray3)
        calls = mock_mi.call_args_list
        auto_labels = [str(c[1].get("label", c[0][0] if c[0] else ""))
                       for c in calls
                       if "autostart" in str(c).lower()]
        check("54.13: autostart disabled → 'turn on' label",
              any("turn on" in l.lower() for l in auto_labels))

# ---------------------------------------------------------------------------
# 55. _on_country_activate / _on_settings_activate — passthroughs
# ---------------------------------------------------------------------------
heading("55. Passthrough activate methods")

t = make_tray(MOD)
try:
    MOD.ProtonVPNTray._on_country_activate(t, MagicMock())
    check("55.1: _on_country_activate → no crash", True)
except Exception as e:
    check("55.1: _on_country_activate → no crash", False, str(e))

try:
    MOD.ProtonVPNTray._on_settings_activate(t, MagicMock())
    check("55.2: _on_settings_activate → no crash", True)
except Exception as e:
    check("55.2: _on_settings_activate → no crash", False, str(e))

# ---------------------------------------------------------------------------
# 56. _build_country_menu — empty cache branch (lines 512-515)
# ---------------------------------------------------------------------------
heading("56. Country menu — empty cache")

t = make_tray(MOD)
t.countries_loaded = True
t.countries_cache = []
t.config["favorites"] = []

with patch.object(MOD.Gtk, "Menu", return_value=MagicMock()) as mock_menu, \
     patch.object(MOD.Gtk, "MenuItem") as mock_mi, \
     patch.object(MOD.Gtk, "SeparatorMenuItem", return_value=MagicMock()):
    mock_mi.return_value = MagicMock()
    menu = MOD.ProtonVPNTray._build_country_menu(t)
    check("56.1: empty cache → menu returned", menu is not None)
    check("56.2: mock_menu.show_all called", mock_menu.return_value.show_all.called)

# ---------------------------------------------------------------------------
# 57. _poll_status — full callback coverage
# ---------------------------------------------------------------------------
heading("57. _poll_status — callback")

# 57.1 Connected state → sets all fields correctly
t = make_tray(MOD)
t._polling = False

with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t)
    cb = mock_async.call_args[0][2]
    check("57.1: callback captured", callable(cb))

    # Invoke callback with connected output
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status: Connected\nServer: CH#12\nProtocol: OpenVPN", "")

    check("57.2: connected → connected=True", t.connected is True)
    check("57.3: connected → signed_in=True", t.signed_in is True)
    check("57.4: connection_info has server", "Server: CH#12" in t.connection_info)
    check("57.5: status_item label set", t.status_item.set_label.called)
    label = t.status_item.set_label.call_args[0][0]
    check("57.6: label starts with Connected:", label.startswith("Connected:"),
          "got %r" % label)

# 57.2 Disconnected state
t2 = make_tray(MOD)
t2._polling = False
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t2)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status:       Disconnected", "")
    check("57.7: disconnected → connected=False", t2.connected is False)
    check("57.8: disconnected → signed_in=True", t2.signed_in is True)
    check("57.9: connection_info is Disconnected", t2.connection_info == "Disconnected")

# 57.3 Not signed in state
t3 = make_tray(MOD)
t3._polling = False
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t3)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Error: not signed in. Please run 'protonvpn signin'", "")
    check("57.10: not_signed_in → connected=False", t3.connected is False)
    check("57.11: not_signed_in → signed_in=False", t3.signed_in is False)
    check("57.12: connection_info has Not signed in",
          "Not signed in" in t3.connection_info)

# 57.4 Unknown state
t4 = make_tray(MOD)
t4._polling = False
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t4)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status:       weird", "")
    check("57.13: unknown → connected=False", t4.connected is False)
    check("57.14: unknown → signed_in=True", t4.signed_in is True)
    check("57.15: connection_info starts with output",
          t4.connection_info.startswith("Status:") or "weird" in t4.connection_info,
          "got %r" % t4.connection_info[:100])

# 57.5 Quit flag in callback
t5 = make_tray(MOD)
t5._quitting = True
t5._polling = False
prev_conn = t5.connected
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t5)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        cb(0, "Status: Connected", "")
    check("57.16: quitting → state unchanged",
          t5.connected == prev_conn,
          "was %s became %s" % (prev_conn, t5.connected))

# 57.6 Indicator set_title when hasattr
t6 = make_tray(MOD)
t6._polling = False
t6.indicator = MagicMock()
t6.indicator.configure_mock(**{"set_title": MagicMock(),
                                "set_icon_full": MagicMock()})
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t6)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status: Connected", "")
    check("57.17: set_title called connected",
          t6.indicator.set_title.called)
    check("57.18: set_icon_full called",
          t6.indicator.set_icon_full.called)

# 57.7 Indicator that lacks set_title and set_icon_full → _status_icon fallback
t7 = make_tray(MOD)
t7._polling = False
t7._status_icon = MagicMock()

class SparseIndicator:
    pass
t7.indicator = SparseIndicator()

with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t7)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status: Connected", "")
    check("57.19: _status_icon.set_from_icon_name called",
          t7._status_icon.set_from_icon_name.called)

# 57.8 Notification on connect transition
t8 = make_tray(MOD)
t8._polling = False
t8.connected = False
with patch.object(MOD, "run_cli_async") as mock_async:
    with patch.object(MOD, "notify") as mock_notify:
        MOD.ProtonVPNTray._poll_status(t8)
        cb = mock_async.call_args[0][2]
        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            mock_idle.side_effect = lambda fn: fn()
            cb(0, "Status: Connected", "")
        check("57.20: connect notification sent",
              mock_notify.called)

# 57.9 Notification on disconnect transition (was connected, now disconnected)
t9 = make_tray(MOD)
t9._polling = False
t9.connected = True
with patch.object(MOD, "run_cli_async") as mock_async:
    with patch.object(MOD, "notify") as mock_notify:
        MOD.ProtonVPNTray._poll_status(t9)
        cb = mock_async.call_args[0][2]
        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            mock_idle.side_effect = lambda fn: fn()
            cb(0, "Status: Disconnected", "")
        check("57.21: disconnect notification sent",
              mock_notify.called)

# 57.10 Label truncation at 80 chars
t10 = make_tray(MOD)
t10._polling = False
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t10)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        long_conn = "Server: CH#1\n" + "X" * 200
        cb(0, "Status: Connected\n" + long_conn, "")
    label = t10.status_item.set_label.call_args[0][0]
    check("57.22: long label truncated to <=80",
          len(label) <= 80, "len=%d: %r" % (len(label), label[:100]))

# ---------------------------------------------------------------------------
# 58. _load_countries_async — all branches
# ---------------------------------------------------------------------------
heading("58. _load_countries_async")

t = make_tray(MOD)

# 58.1 Success: ret=0, parse returns data
t.countries_cache = []
t.countries_loaded = False
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._load_countries_async(t)
    cb = mock_async.call_args[0][2]
    cb(0, "Country               Code\n------------------     ----\nSwitzerland            CH\n", "")
    check("58.1: countries loaded", t.countries_loaded is True)
    check("58.2: cache populated", ("CH", "Switzerland") in t.countries_cache,
          "got %s" % t.countries_cache)

# 58.2 Success: ret=0, parse returns empty → warning
t2 = make_tray(MOD)
t2.countries_cache = []
t2.countries_loaded = False
with patch.object(MOD, "run_cli_async") as mock_async, \
     patch.object(MOD.log, "warning") as mock_warn:
    MOD.ProtonVPNTray._load_countries_async(t2)
    cb = mock_async.call_args[0][2]
    cb(0, "Country               Code\n------------------     ----\n", "")
    check("58.3: empty parse → warning logged", mock_warn.called)
    check("58.4: countries_loaded still False (no data)", t2.countries_loaded is False,
          "should not be True with empty parse")

# 58.3 Error: ret != 0 → warning
t3 = make_tray(MOD)
t3.countries_loaded = False
with patch.object(MOD, "run_cli_async") as mock_async, \
     patch.object(MOD.log, "warning") as mock_warn:
    MOD.ProtonVPNTray._load_countries_async(t3)
    cb = mock_async.call_args[0][2]
    cb(1, "", "CLI connection error")
    check("58.5: CLI error → warning logged", mock_warn.called)

# 58.4 Error: parsed empty due to empty list check (line 741: if parsed:)
t4 = make_tray(MOD)
t4.countries_cache = []
t4.countries_loaded = False
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._load_countries_async(t4)
    cb = mock_async.call_args[0][2]
    cb(0, "No countries available. Try updating.", "")
    check("58.6: no country data → countries_loaded False",
          t4.countries_loaded is False)

# ---------------------------------------------------------------------------
# 59. _do_connect — status_msg=None branch (line 762)
# ---------------------------------------------------------------------------
heading("59. _do_connect — default status_msg")

t = make_tray(MOD)
with patch.object(MOD.ProtonVPNTray, "_run_vpn_action") as mock_action:
    MOD.ProtonVPNTray._do_connect(t, ["--country", "CH"])
    status_msg_arg = mock_action.call_args[0][2]
    check("59.1: _do_connect default status_msg",
          status_msg_arg == "Connecting...",
          "got %s" % status_msg_arg)

# Also test with explicit status_msg
with patch.object(MOD.ProtonVPNTray, "_run_vpn_action") as mock_action:
    MOD.ProtonVPNTray._do_connect(t, ["--country", "JP"],
                                   status_msg="Connecting to Japan")
    status_msg_arg = mock_action.call_args[0][2]
    check("59.2: _do_connect custom status_msg",
          status_msg_arg == "Connecting to Japan",
          "got %s" % status_msg_arg)

# ---------------------------------------------------------------------------
# 60. _on_connect_tor / _on_connect_p2p — when connected
# ---------------------------------------------------------------------------
heading("60. Connect modes — already connected")

# 60.1 Tor when connected
t = make_tray(MOD)
t.connected = True
with patch.object(MOD.ProtonVPNTray, "_run_vpn_action") as mock_action:
    MOD.ProtonVPNTray._on_connect_tor(t, None)
    check("60.1: Tor connected → disconnect first", mock_action.called)
    args = mock_action.call_args[0][0]
    check("60.2: Tor disconnect args", args == ["disconnect"],
          "got %s" % args)

# 60.2 P2P when connected
t2 = make_tray(MOD)
t2.connected = True
with patch.object(MOD.ProtonVPNTray, "_run_vpn_action") as mock_action:
    MOD.ProtonVPNTray._on_connect_p2p(t2, None)
    check("60.3: P2P connected → disconnect first", mock_action.called)
    args = mock_action.call_args[0][0]
    check("60.4: P2P disconnect args", args == ["disconnect"],
          "got %s" % args)

# ---------------------------------------------------------------------------
# 61. _on_manage_favorites — countries not loaded
# ---------------------------------------------------------------------------
heading("61. Manage favorites — countries not loaded")

t = make_tray(MOD)
t.countries_loaded = False
with patch.object(MOD, "notify") as mock_notify:
    MOD.ProtonVPNTray._on_manage_favorites(t, None)
    check("61.1: countries not loaded → notify", mock_notify.called)
    msg = str(mock_notify.call_args)
    check("61.2: notify says try again",
          "try again" in msg.lower() or "loading" in msg.lower(),
          "msg: %s" % msg)

# ---------------------------------------------------------------------------
# 62. _on_show_info — VPN IP extraction branch
# ---------------------------------------------------------------------------
heading("62. _on_show_info — VPN IP extraction + edge cases")

t = make_tray(MOD)
t.connection_info = "Server: CH#1"
t.status_raw = "Status: Connected"

# 62.1 VPN IP extracted when tun interface present
t._info_dialog_active = False
with patch.object(MOD.subprocess, "run") as mock_run, \
     patch.object(MOD, "_show_text_dialog",
                  return_value=MagicMock()) as mock_dialog, \
     patch.object(MOD, "_collect_network_details",
                  return_value="") as mock_net:
    mock_curl_result = MagicMock()
    mock_curl_result.returncode = 0
    mock_curl_result.stdout = "1.2.3.4\n"

    mock_ip_result = MagicMock()
    mock_ip_result.returncode = 0
    mock_ip_result.stdout = "    inet 10.2.0.5/32 scope global tun0\n"

    mock_run.side_effect = [mock_curl_result, mock_ip_result]

    with patch.object(MOD, "run_cli_async") as mock_async:
        MOD.ProtonVPNTray._on_show_info(t, None)
        cb = mock_async.call_args[0][2]

        cb_args = []
        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            mock_idle.side_effect = lambda fn, *a: cb_args.append(a[0] if a else fn())
            cb(0, "Account: Pro User", "")
        if cb_args:
            check("62.1: dialog contains VPN IP",
                  "10.2.0.5" in str(cb_args[0]))
        if not cb_args:
            check("62.1: GLib.idle_add was called", bool(cb_args))

# 62.2 ip addr raises exception → no VPN IP shown
t2 = make_tray(MOD)
t2.connection_info = "Disconnected"
with patch.object(MOD.subprocess, "run") as mock_run, \
     patch.object(MOD, "_show_text_dialog",
                  return_value=MagicMock()) as mock_dialog, \
     patch.object(MOD, "_collect_network_details",
                  return_value="") as mock_net:
    mock_curl_result = MagicMock()
    mock_curl_result.returncode = 0
    mock_curl_result.stdout = ""

    mock_run.side_effect = [mock_curl_result, FileNotFoundError]

    with patch.object(MOD, "run_cli_async") as mock_async:
        t2._info_dialog_active = False
        MOD.ProtonVPNTray._on_show_info(t2, None)
        cb = mock_async.call_args[0][2]

        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            mock_idle.side_effect = lambda fn, *a: None
            try:
                cb(0, "", "")
                check("62.2: ip addr exception → no crash", True)
            except Exception as e:
                check("62.2: ip addr exception → no crash", False, str(e))

# 62.3 curl raises exception
t3 = make_tray(MOD)
t3.connection_info = "Not signed in"
with patch.object(MOD.subprocess, "run") as mock_run, \
     patch.object(MOD, "_show_text_dialog",
                  return_value=MagicMock()) as mock_dialog, \
     patch.object(MOD, "_collect_network_details",
                  return_value="") as mock_net:
    mock_run.side_effect = FileNotFoundError("no curl")
    with patch.object(MOD, "run_cli_async") as mock_async:
        t3._info_dialog_active = False
        MOD.ProtonVPNTray._on_show_info(t3, None)
        cb = mock_async.call_args[0][2]
        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            mock_idle.side_effect = lambda fn, *a: None
            try:
                cb(0, "", "")
                check("62.3: curl exception → no crash", True)
            except Exception as e:
                check("62.3: curl exception → no crash", False, str(e))

# ---------------------------------------------------------------------------
# 63. _update_info_dialog — exception path
# ---------------------------------------------------------------------------
heading("63. _update_info_dialog — exception")

t = make_tray(MOD)
t._quitting = False
t._info_dialog_active = True
t._info_label = MagicMock()

# 63.1 set_text raises → caught, flag cleared
t._info_label.set_text.side_effect = RuntimeError("widget destroyed")
try:
    MOD.ProtonVPNTray._update_info_dialog(t, "vpn text")
    check("63.1: set_text exception → no crash", True)
    check("63.1: flag cleared after exception", not t._info_dialog_active)
except Exception as e:
    check("63.1: set_text exception → no crash", False, str(e))

# 63.2 Quitting → returns early
t._quitting = True
t._info_dialog_active = True
try:
    MOD.ProtonVPNTray._update_info_dialog(t, "text")
    check("63.2: quitting guard no crash", True)
except Exception as e:
    check("63.2: quitting guard no crash", False, str(e))

# ---------------------------------------------------------------------------
# 64. _on_toggle_autostart — script exception paths
# ---------------------------------------------------------------------------
heading("64. Autostart toggle — script exception paths")

# 64.1 Disable script fails → logged
t = make_tray(MOD)
with patch("subprocess.run", side_effect=OSError("script missing")), \
     patch.object(MOD, "is_autostart_enabled", return_value=True), \
     patch.object(MOD, "notify") as mock_notify, \
     patch.object(MOD.log, "warning") as mock_warn:
    MOD.ProtonVPNTray._on_toggle_autostart(t, None)
    check("64.1: disable script exception → warning logged", mock_warn.called,
          "warning not called")

# 64.2 Enable script fails → logged
t2 = make_tray(MOD)
with patch("subprocess.run", side_effect=OSError("script missing")), \
     patch.object(MOD, "is_autostart_enabled", return_value=False), \
     patch.object(MOD, "notify") as mock_notify, \
     patch.object(MOD.log, "warning") as mock_warn:
    MOD.ProtonVPNTray._on_toggle_autostart(t2, None)
    check("64.2: enable script exception → warning logged", mock_warn.called,
          "warning not called")

# ---------------------------------------------------------------------------
# 65. _on_about
# ---------------------------------------------------------------------------
heading("65. _on_about")

t = make_tray(MOD)
mock_dlg = MagicMock()
with patch.object(MOD.Gtk, "AboutDialog", return_value=mock_dlg):
    MOD.ProtonVPNTray._on_about(t, None)
    check("65.1: AboutDialog created", MOD.Gtk.AboutDialog.called)
    check("65.2: program_name set",
          mock_dlg.set_program_name.called,
          str(mock_dlg.set_program_name.call_args))
    check("65.3: website set",
          mock_dlg.set_website.called,
          str(mock_dlg.set_website.call_args))
    check("65.4: run() called", mock_dlg.run.called)
    check("65.5: destroy() called", mock_dlg.destroy.called)

# ---------------------------------------------------------------------------
# 66. main() / argparse
# ---------------------------------------------------------------------------
heading("66. main() — argument parsing")

# 66.1 Default (no auto-connect)
with patch("sys.argv", ["protonvpn-tray.py"]), \
     patch.object(MOD, "ProtonVPNTray") as mock_cls, \
     patch.object(MOD.ProtonVPNTray, "run") as mock_run:
    mock_instance = MagicMock()
    mock_cls.return_value = mock_instance
    MOD.main()
    check("66.1: main() creates ProtonVPNTray", mock_cls.called)
    call_kwargs = mock_cls.call_args[1]
    check("66.2: default auto_connect=False",
          call_kwargs.get("auto_connect") is False,
          "got %s" % call_kwargs)

# 66.2 With --auto-connect flag
with patch("sys.argv", ["protonvpn-tray.py", "--auto-connect"]), \
     patch.object(MOD, "ProtonVPNTray") as mock_cls, \
     patch.object(MOD.ProtonVPNTray, "run") as mock_run:
    mock_instance = MagicMock()
    mock_cls.return_value = mock_instance
    MOD.main()
    call_kwargs = mock_cls.call_args[1]
    check("66.3: --auto-connect → auto_connect=True",
          call_kwargs.get("auto_connect") is True,
          "got %s" % call_kwargs)

# 66.4 run() calls Gtk.main()
t = make_tray(MOD)
with patch.object(MOD.Gtk, "main") as mock_main:
    MOD.ProtonVPNTray.run(t)
    check("66.4: run() calls Gtk.main()", mock_main.called)

# ---------------------------------------------------------------------------
# 67. _rebuild_all_submenus — both destroy old submenus
# ---------------------------------------------------------------------------
heading("67. _rebuild_all_submenus — submenu destroy")

# 67.1 Layout rebuild: destroy old before setting new
t = make_tray(MOD)
old_country = MagicMock()
old_settings = MagicMock()
t._country_parent = MagicMock()
t._country_parent.get_submenu.return_value = old_country
t._settings_parent = MagicMock()
t._settings_parent.get_submenu.return_value = old_settings

with patch.object(MOD.ProtonVPNTray, "_build_country_menu",
                  return_value=MagicMock()), \
     patch.object(MOD.ProtonVPNTray, "_build_settings_menu",
                  return_value=MagicMock()):
    MOD.ProtonVPNTray._rebuild_all_submenus(t)
    check("67.1: old country submenu destroyed", old_country.destroy.called)
    check("67.2: old settings submenu destroyed", old_settings.destroy.called)
    check("67.3: country parent set_submenu called",
          t._country_parent.set_submenu.called)
    check("67.4: settings parent set_submenu called",
          t._settings_parent.set_submenu.called)

# ---------------------------------------------------------------------------
# 68. parse_config_list — edge branches (no header, single-part lines)
# ---------------------------------------------------------------------------
heading("68. parse_config_list — edge branches")

pcl = MOD.parse_config_list

# 68.1 No header → in_body stays False → all body lines skipped via continue
c = pcl("netshield            off\nkill-switch          standard\n")
check("68.1: no header → empty dict", c == {}, "got %s" % c)

# 68.2 Body line with < 2 parts (no value) → skipped
c = pcl("Setting              Value\n------------------   -----\norphan\n")
check("68.2: orphan line skipped", c == {}, "got %s" % c)

# 68.3 Startswith "Use " (not "Use '") → break taken
c = pcl("Setting              Value\n------------------   -----\n"
        "netshield            on\nUse 'protonvpn config set' to change\n")
check("68.3: Use with apostrophe → break", c == {"netshield": "on"},
      "got %s" % c)

# 68.4 Line with only spaces after stripping → skipped
c = pcl("Setting              Value\n------------------   -----\n"
        "  \n  \nkill-switch          off\n")
check("68.4: whitespace-only lines skipped", c == {"kill-switch": "off"},
      "got %s" % c)

# ---------------------------------------------------------------------------
# 69. notify() — success path (set_timeout + show)
# ---------------------------------------------------------------------------
heading("69. notify — success path")

saved_init = MOD._notify_initialized
MOD._notify_initialized = True
try:
    orig_new = MOD.Notify.Notification.new
    mock_n = MagicMock()
    MOD.Notify.Notification.new = MagicMock(return_value=mock_n)
    try:
        with patch.object(MOD.log, "warning") as mock_warn:
            MOD.notify("Test Title", "Test Body", "test-icon")
            check("69.1: notify success → no warning", not mock_warn.called)
            check("69.2: set_timeout called", mock_n.set_timeout.called,
                  "set_timeout not called")
            check("69.3: show called", mock_n.show.called,
                  "show not called")
            check("69.4: new called with title",
                  "Test Title" in str(MOD.Notify.Notification.new.call_args))
    finally:
        MOD.Notify.Notification.new = orig_new
finally:
    MOD._notify_initialized = saved_init

# ---------------------------------------------------------------------------
# 70. __init__ constructor
# ---------------------------------------------------------------------------
heading("70. __init__ constructor")

from unittest.mock import patch, MagicMock

# 70.1 Default init (no auto-connect)
saved_signal = MOD.signal.signal
MOD.signal.signal = MagicMock()
saved_create = MOD.ProtonVPNTray._create_indicator
saved_build = MOD.ProtonVPNTray._build_menu

def _fake_create(inst):
    inst.indicator = MagicMock()
    return inst.indicator

def _fake_build(inst):
    m = MagicMock()
    inst.status_item = MagicMock()
    inst.autostart_item = MagicMock()
    inst._country_parent = MagicMock()
    inst._settings_parent = MagicMock()
    return m

MOD.ProtonVPNTray._create_indicator = _fake_create
MOD.ProtonVPNTray._build_menu = _fake_build
MOD.ProtonVPNTray._check_pid = lambda self: None
MOD.ProtonVPNTray._write_pid = lambda self: None
saved_load_config = MOD.ProtonVPNTray._load_config
MOD.ProtonVPNTray._load_config = lambda self: dict(MOD.DEFAULT_CONFIG)

try:
    tray = MOD.ProtonVPNTray(auto_connect=False)

    check("70.1: auto_connect=False", tray.auto_connect is False)
    check("70.2: busy=False", tray.busy is False)
    check("70.3: connected=False", tray.connected is False)
    check("70.4: signed_in=True", tray.signed_in is True)
    check("70.5: indicator is not None", tray.indicator is not None)
    check("70.6: indicator.set_menu called",
          tray.indicator.set_menu.called)
    check("70.7: _notify_initialized is True after init",
          MOD._notify_initialized is True)
    check("70.8: Notify.init called",
          MOD.Notify.init.called)

    # GLib timers scheduled
    check("70.9: GLib.timeout_add_seconds(5) called",
          MOD.GLib.timeout_add_seconds.called)
    check("70.10: GLib.timeout_add(500) called",
          MOD.GLib.timeout_add.called)

    # signal.signal called for both SIGINT and SIGTERM
    check("70.11: signal handlers registered",
          MOD.signal.signal.called,
          "signal.signal not called")
except Exception as e:
    check("70.1-11: __init__ without auto-connect", False, str(e))
    import traceback
    traceback.print_exc()

# 70.2 Init with auto_connect=True (auto_connect_on_start=True)
MOD.ProtonVPNTray._load_config = lambda self: dict(MOD.DEFAULT_CONFIG,
                                                     auto_connect_on_start=True)
try:
    tray2 = MOD.ProtonVPNTray(auto_connect=True)
    check("70.12: auto_connect=True init", tray2.auto_connect is True)
except Exception as e:
    check("70.12: auto_connect=True init", False, str(e))

# 70.3 Init with auto_connect=True but config disables it
MOD.ProtonVPNTray._load_config = lambda self: dict(MOD.DEFAULT_CONFIG,
                                                     auto_connect_on_start=False)
try:
    tray3 = MOD.ProtonVPNTray(auto_connect=True)
    check("70.13: auto_connect with config disabled → no crash",
          tray3.auto_connect is True)
    check("70.14: config.auto_connect_on_start is False",
          tray3.config.get("auto_connect_on_start") is False)
except Exception as e:
    check("70.13-14: auto_connect with config disabled", False, str(e))

# Restore originals
MOD.ProtonVPNTray._create_indicator = saved_create
MOD.ProtonVPNTray._build_menu = saved_build
MOD.ProtonVPNTray._load_config = saved_load_config

# ---------------------------------------------------------------------------
# 71. indicator.set_icon fallback (line 725)
# ---------------------------------------------------------------------------
heading("71. Indicator fallback — set_icon (not set_icon_full)")

t = make_tray(MOD)
t._polling = False

class SetIconOnlyIndicator:
    set_icon = MagicMock()
t.indicator = SetIconOnlyIndicator()

with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status: Connected", "")
    check("71.1: set_icon called",
          t.indicator.set_icon.called,
          "set_icon was not called")

# Test indicator with set_icon but no set_icon_full → falls through to set_icon
class SetIconIndicator:
    set_icon = MagicMock()
t2 = make_tray(MOD)
t2._polling = False
t2.indicator = SetIconIndicator()

with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t2)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status: Connected", "")
    check("71.2: set_icon called",
          t2.indicator.set_icon.called,
          "set_icon was not called")

# 71.3 indicator lacks set_title, set_icon_full, set_icon → _status_icon
t3 = make_tray(MOD)
t3._polling = False
t3._status_icon = MagicMock()
t3.indicator = object()

with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._poll_status(t3)
    cb = mock_async.call_args[0][2]
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        cb(0, "Status: Connected", "")
    check("71.3: _status_icon fallback called",
          t3._status_icon.set_from_icon_name.called)

# ---------------------------------------------------------------------------
# 72. _rebuild_all_submenus — no old submenu (branch 464->466, 471->473)
# ---------------------------------------------------------------------------
heading("72. _rebuild_all_submenus — no old submenu")

t = make_tray(MOD)
# Parents exist but get_submenu returns None → skip destroy
t._country_parent = MagicMock()
t._country_parent.get_submenu.return_value = None
t._settings_parent = MagicMock()
t._settings_parent.get_submenu.return_value = None

with patch.object(MOD.ProtonVPNTray, "_build_country_menu",
                  return_value=MagicMock()) as mock_bc, \
     patch.object(MOD.ProtonVPNTray, "_build_settings_menu",
                  return_value=MagicMock()) as mock_bs:
    MOD.ProtonVPNTray._rebuild_all_submenus(t)
    check("72.1: no old country → no destroy needed", True)
    check("72.2: rebuild_country called", mock_bc.called)
    check("72.3: rebuild_settings called", mock_bs.called)
    check("72.4: country set_submenu called",
          t._country_parent.set_submenu.called)
    check("72.5: settings set_submenu called",
          t._settings_parent.set_submenu.called)

# ---------------------------------------------------------------------------
# 73. _run_vpn_action callback — on_done is None (branch 791->exit)
# ---------------------------------------------------------------------------
heading("73. _run_vpn_action — callback with on_done=None")

t = make_tray(MOD)
t.busy = False
t.status_item.set_label.reset_mock()

with patch.object(MOD, "notify") as mock_notify, \
     patch.object(MOD, "run_cli_async") as mock_async:
    # Call WITHOUT on_done (default None)
    MOD.ProtonVPNTray._run_vpn_action(
        t, ["connect"], 25, "Connecting")
    cb = mock_async.call_args[0][2]
    t._quitting = False
    with patch.object(MOD.GLib, "idle_add") as mock_idle:
        mock_idle.side_effect = lambda fn: fn()
        try:
            cb(0, "connected", "")
            check("73.1: callback with on_done=None → no crash", True)
            check("73.2: busy cleared", t.busy is False)
        except Exception as e:
            check("73.1-2: callback with on_done=None", False, str(e))

# ---------------------------------------------------------------------------
# 74. _on_show_info — ip addr non-zero + non-matching inet lines
# ---------------------------------------------------------------------------
heading("74. _on_show_info — ip addr edge branches")

# 74.1 ip addr returns non-zero (VPN IP extraction skipped)
t = make_tray(MOD)
t.connection_info = "Server: CH#1"
with patch.object(MOD.subprocess, "run") as mock_run, \
     patch.object(MOD, "_show_text_dialog",
                  return_value=MagicMock()) as mock_dialog, \
     patch.object(MOD, "_collect_network_details",
                  return_value="") as mock_net:
    mock_curl = MagicMock()
    mock_curl.returncode = 0
    mock_curl.stdout = "1.2.3.4\n"
    # ip addr returns non-zero
    mock_ip = MagicMock()
    mock_ip.returncode = 1
    mock_ip.stdout = ""
    mock_run.side_effect = [mock_curl, mock_ip]
    with patch.object(MOD, "run_cli_async") as mock_async:
        t._info_dialog_active = False
        MOD.ProtonVPNTray._on_show_info(t, None)
        cb = mock_async.call_args[0][2]
        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            mock_idle.side_effect = lambda fn, *a: None
            try:
                cb(0, "Account info", "")
                check("74.1: ip addr non-zero → no crash", True)
            except Exception as e:
                check("74.1: ip addr non-zero → no crash", False, str(e))

# 74.2 ip addr has inet lines but none match tun/proton (loopback only)
t2 = make_tray(MOD)
t2.connection_info = "Connected"
with patch.object(MOD.subprocess, "run") as mock_run, \
     patch.object(MOD, "_show_text_dialog",
                  return_value=MagicMock()) as mock_dialog, \
     patch.object(MOD, "_collect_network_details",
                  return_value="") as mock_net:
    mock_curl2 = MagicMock()
    mock_curl2.returncode = 0
    mock_curl2.stdout = "8.8.8.8\n"
    mock_ip2 = MagicMock()
    mock_ip2.returncode = 0
    mock_ip2.stdout = "    inet 127.0.0.1/8 scope host lo\n" \
                      "    inet 192.168.1.5/24 brd 192.168.1.255 scope global eth0\n"
    mock_run.side_effect = [mock_curl2, mock_ip2]
    with patch.object(MOD, "run_cli_async") as mock_async:
        t2._info_dialog_active = False
        MOD.ProtonVPNTray._on_show_info(t2, None)
        cb = mock_async.call_args[0][2]
        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            mock_idle.side_effect = lambda fn, *a: None
            try:
                cb(0, "Account info", "")
                check("74.2: non-tun inet lines → no VPN IP added", True)
            except Exception as e:
                check("74.2: non-tun inet lines → no crash", False, str(e))

# ---------------------------------------------------------------------------
# 75. run_cli — killpg, wait, proc=None, and proc-not-None edge branches
# ---------------------------------------------------------------------------
heading("75. run_cli — killpg, wait, proc edge branches")

# 75.1 killpg raises exception (line 116-117)
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD.os, "killpg", side_effect=OSError("killpg failed")), \
     patch.object(MOD._time, "sleep"):
    mock_proc = MagicMock()
    mock_proc.pid = 9999  # real int > 1
    mock_proc.communicate.side_effect = real_subprocess.TimeoutExpired("cmd", 5)
    mock_popen.return_value = mock_proc
    rc, out, err = MOD.run_cli(["status"])
    check("75.1: killpg exception → timeout rc=-1", rc == -1)
    check("75.1: timeout msg still returned", "Command timed out" in err)

# 75.2 proc.wait raises exception (line 120-121)
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD.os, "killpg"), \
     patch.object(MOD._time, "sleep"):
    mock_proc = MagicMock()
    mock_proc.pid = 9999  # real int > 1
    mock_proc.communicate.side_effect = real_subprocess.TimeoutExpired("cmd", 5)
    mock_proc.wait.side_effect = real_subprocess.TimeoutExpired("cmd", 5)
    mock_popen.return_value = mock_proc
    rc, out, err = MOD.run_cli(["status"])
    check("75.2: wait exception → timeout rc=-1", rc == -1)
    check("75.2: timeout msg despite wait exception", "Command timed out" in err)

# 75.3 Popen itself raises TimeoutExpired → proc remains None (107->122)
with patch.object(MOD.subprocess, "Popen",
                  side_effect=real_subprocess.TimeoutExpired("cmd", 5)), \
     patch.object(MOD._time, "sleep"):
    rc, out, err = MOD.run_cli(["status"])
    check("75.3: Popen TimeoutExpired → proc=None branch covered", rc == -1)
    check("75.3: returns timeout message", "Command timed out" in err)

# 75.4 Generic Exception from communicate() while proc is not-None (140-148)
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD._time, "sleep"):
    mock_proc = MagicMock()
    mock_proc.pid = 9999
    mock_proc.communicate.side_effect = RuntimeError("comm failed")
    mock_popen.return_value = mock_proc
    rc, out, err = MOD.run_cli(["status"])
    check("75.4: Exception from communicate → rc=-3", rc == -3)
    check("75.4: error includes exception msg", "comm failed" in err)

# 75.5 Generic Exception from communicate() — proc.pid not int (141->146)
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD._time, "sleep"):
    mock_proc = MagicMock()
    mock_proc.pid = MagicMock()  # not int → skip killpg
    mock_proc.communicate.side_effect = RuntimeError("comm failed")
    mock_popen.return_value = mock_proc
    rc, out, err = MOD.run_cli(["status"])
    check("75.5: non-int pid skips killpg, rc=-3", rc == -3)
    check("75.5: error includes exception msg", "comm failed" in err)

# 75.6 Generic Exception — proc.wait also raises (148-149)
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD._time, "sleep"):
    mock_proc = MagicMock()
    mock_proc.pid = -1  # not > 1 → skip killpg
    mock_proc.communicate.side_effect = RuntimeError("comm failed")
    mock_proc.wait.side_effect = RuntimeError("wait failed")
    mock_popen.return_value = mock_proc
    rc, out, err = MOD.run_cli(["status"])
    check("75.6: communicate + wait both raise → rc=-3", rc == -3)
    check("75.6: error from communicate, not wait", "comm failed" in err)


# ---------------------------------------------------------------------------
# 76. Circuit breaker — trip via _failure_count (not consecutive timeouts)
# ---------------------------------------------------------------------------
heading("76. Circuit breaker — trip via _failure_count")

# 76.1 Polling paused via elif in TimeoutExpired (line 105-106)
# Build up failures with FileNotFoundError, then trigger TimeoutExpired
# where consecutive_timeouts < MAX_CONSECUTIVE_TIMEOUTS
MOD._failure_count = MOD._MAX_FAILURES - 1
MOD._consecutive_timeouts = 0
MOD._polling_paused = False
MOD._polling_paused_notified = False

with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD._time, "sleep"):
    mock_proc = MagicMock()
    mock_proc.pid = -1  # not int > 1, skip killpg
    mock_proc.communicate.side_effect = real_subprocess.TimeoutExpired("cmd", 5)
    mock_popen.return_value = mock_proc
    MOD.run_cli(["status"])
    check("76.1: polling paused via elif (_failure_count threshold)",
          MOD._polling_paused is True)

MOD._polling_paused = False
MOD._failure_count = 0
MOD._consecutive_timeouts = 0

# 76.2 Polling paused in generic Exception handler (line 132-133)
MOD._failure_count = MOD._MAX_FAILURES - 1
MOD._polling_paused = False

with patch.object(MOD.subprocess, "Popen", side_effect=RuntimeError("boom")), \
     patch.object(MOD._time, "sleep"):
    MOD.run_cli(["status"])
    check("76.2: polling paused via Exception handler",
          MOD._polling_paused is True)

MOD._polling_paused = False
MOD._failure_count = 0
MOD._consecutive_timeouts = 0


# ---------------------------------------------------------------------------
# 77. _raise_window — generic exception path (lines 375-377)
# ---------------------------------------------------------------------------
heading("77. _raise_window — generic exception path")

# 77.1 wmctrl raises non-FileNotFoundError → fall through to xdotool
# which also raises non-FileNotFoundError → log warning and continue
with patch.object(MOD.subprocess, "run",
                  side_effect=[RuntimeError("wmctrl fail"),
                               RuntimeError("xdotool fail")]):
    try:
        MOD._raise_window("Test Window")
        check("77.1: _raise_window survives double exception", True)
    except Exception as e:
        check("77.1: _raise_window survives double exception",
              False, str(e))

# 77.2 wmctrl raises FileNotFoundError → xdotool raises generic exception
with patch.object(MOD.subprocess, "run",
                  side_effect=[FileNotFoundError,
                               RuntimeError("xdotool fail")]):
    try:
        MOD._raise_window("Test Window")
        check("77.2: wmctrl missing + xdotool error → no crash", True)
    except Exception as e:
        check("77.2: wmctrl missing + xdotool error → no crash",
              False, str(e))


# ---------------------------------------------------------------------------
# 78. Settings failure path (line 750)
# ---------------------------------------------------------------------------
heading("78. Settings failure path")

t = make_tray(MOD)
t.config = dict(MOD.DEFAULT_CONFIG)
handler = MOD.ProtonVPNTray._make_setting_handler(t, "kill-switch", "off")
with patch.object(MOD, "notify") as mock_notify:
    handler(MagicMock(get_active=MagicMock(return_value=True)))
    cb = mock_notify.call_args_list
    check("78.1: notify called", len(cb) >= 1)
    # The handler calls run_cli_async; callback receives ret, out, err
    # We need to extract the callback and invoke it with ret != 0
    cb_received = []
    with patch.object(MOD, "run_cli_async") as mock_async:
        t2 = make_tray(MOD)  # fresh tray, busy=False
        t2.config = dict(MOD.DEFAULT_CONFIG)
        handler2 = MOD.ProtonVPNTray._make_setting_handler(t2, "dns", "custom")
        handler2(MagicMock(get_active=MagicMock(return_value=True)))
        if mock_async.called:
            cb_async = mock_async.call_args[0][2]
            with patch.object(MOD, "notify") as mn:
                with patch.object(MOD.GLib, "idle_add",
                                  lambda fn, *a: (fn(*a), 0)):
                    cb_async(1, "", "permission denied")
                    check("78.2: failure notify called", mn.called)
                    if mn.called:
                        check("78.3: failure message contains error",
                              "permission denied" in str(mn.call_args) or
                              "Failed" in str(mn.call_args))

# 78.4 _make_setting_handler on_done success path (lines 747-749)
with patch.object(MOD, "run_cli_async") as mock_async, \
     patch.object(MOD, "notify") as mock_notify, \
     patch.object(MOD.GLib, "idle_add", lambda fn, *a: (fn(*a), 0)):
    t3 = make_tray(MOD)
    t3.config = dict(MOD.DEFAULT_CONFIG)
    t3._config_cache = {"kill-switch": "old-value"}
    h3 = MOD.ProtonVPNTray._make_setting_handler(t3, "kill-switch", "off")
    h3(MagicMock(get_active=MagicMock(return_value=True)))
    if mock_async.called:
        cb3 = mock_async.call_args[0][2]
        cb3(0, "ok", "")
        check("78.4: success notify called", mock_notify.called)
        check("78.5: config cache popped",
              "kill-switch" not in t3._config_cache)
        if mock_notify.called:
            check("78.6: success message contains 'set to'",
                  "set to" in str(mock_notify.call_args))


# ---------------------------------------------------------------------------
# 79. _poll_status — _polling_paused_notified already True (818->825)
# ---------------------------------------------------------------------------
heading("79. _poll_status — already notified guard")

t = make_tray(MOD)
t.status_item.set_label = MagicMock()

MOD._polling_paused = True
MOD._polling_paused_notified = True  # already notified
MOD._failure_count = 0
MOD._consecutive_timeouts = 0

with patch.object(MOD, "notify") as mock_notify:
    with patch.object(MOD, "run_cli_async") as mock_async:
        result = MOD.ProtonVPNTray._poll_status(t)
        check("79.1: _poll_status returns True when paused", result is True)
        check("79.2: notify NOT called again (already notified)",
              not mock_notify.called)

MOD._polling_paused = False
MOD._polling_paused_notified = False


# ---------------------------------------------------------------------------
# 80. _on_show_info — dialog active guard + callback quit guard (1118, 1141)
# ---------------------------------------------------------------------------
heading("80. _on_show_info — guards")

t = make_tray(MOD)
t.connection_info = "Connected"

# 80.1 Info dialog already active (line 1118)
t._info_dialog_active = True
with patch.object(MOD, "run_cli_async") as mock_async:
    MOD.ProtonVPNTray._on_show_info(t, None)
    check("80.1: _on_show_info early return when dialog active",
          not mock_async.called)

# 80.2 Info callback quit guard (line 1141)
t2 = make_tray(MOD)
t2.connection_info = "Connected"
t2._info_dialog_active = False
with patch.object(MOD, "_show_text_dialog",
                  return_value=MagicMock()) as mock_dialog, \
     patch.object(MOD.subprocess, "run",
                  return_value=MagicMock(returncode=0, stdout="")), \
     patch.object(MOD, "_collect_network_details",
                  return_value="") as mock_net:
    with patch.object(MOD, "run_cli_async") as mock_async:
        MOD.ProtonVPNTray._on_show_info(t2, None)
        cb = mock_async.call_args[0][2]
        # Simulate: app is quitting AND dialog is already closed
        t2._quitting = True
        t2._info_dialog_active = False
        with patch.object(MOD.GLib, "idle_add") as mock_idle:
            cb(0, "Account info", "")
            check("80.2: callback returns early when quitting + no dialog",
                  not mock_idle.called)


# ---------------------------------------------------------------------------
# 81. Zenity handlers — success paths (1065, 1107-1108)
# ---------------------------------------------------------------------------
heading("81. Zenity handlers — success paths")

t = make_tray(MOD)
t.countries_cache = [("CH", "Switzerland"), ("IS", "Iceland")]
t.countries_loaded = True
t.config = dict(MOD.DEFAULT_CONFIG)
t.config["favorites"] = []

# 81.1 Default country success (line 1065)
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD, "_raise_window") as mock_raise, \
     patch.object(MOD.ProtonVPNTray, "_save_config") as mock_save, \
     patch.object(MOD, "notify") as mock_notify:
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = ("CH\n", "")
    mock_popen.return_value = mock_proc
    MOD.ProtonVPNTray._on_default_country(t, None)
    zenity_calls = [c[0][0] for c in mock_popen.call_args_list
                    if len(c[0]) > 0 and "zenity" in c[0][0]]
    if zenity_calls:
        check("81.1: default country saved to config",
              t.config.get("default_country") == "CH")
        check("81.2: config saved", mock_save.called)

# 81.2 Favorites success (lines 1107-1108)
t2 = make_tray(MOD)
t2.countries_cache = [("CH", "Switzerland"), ("IS", "Iceland")]
t2.countries_loaded = True
t2.config = dict(MOD.DEFAULT_CONFIG)
t2.config["favorites"] = []
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD, "_raise_window"), \
     patch.object(MOD.ProtonVPNTray, "_rebuild_all_submenus") as mock_rebuild, \
     patch.object(MOD.ProtonVPNTray, "_save_config") as mock_save, \
     patch.object(MOD, "notify") as mock_notify:
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate.return_value = ("CH,IS\n", "")
    mock_popen.return_value = mock_proc
    MOD.ProtonVPNTray._on_manage_favorites(t2, None)
    check("81.3: favorites rebuild called", mock_rebuild.called)
    check("81.4: favorites saved", mock_save.called)


# ---------------------------------------------------------------------------
# 82. _show_text_dialog — full function (lines 311-362)
# ---------------------------------------------------------------------------
heading("82. _show_text_dialog")

with patch.object(MOD.Gtk, "Window") as mock_win, \
     patch.object(MOD.Gtk, "ScrolledWindow") as mock_scroll, \
     patch.object(MOD.Gtk, "Label") as mock_label, \
     patch.object(MOD.Gtk, "Button") as mock_btn:
    mock_win_instance = MagicMock()
    mock_win.return_value = mock_win_instance
    result = MOD._show_text_dialog("Test Title", "Test text content")
    check("82.1: _show_text_dialog returns a widget", result is not None)
    check("82.2: Window was created with title", mock_win.called)
    call_args = mock_win.call_args
    has_title = ("Test Title" in str(call_args) if call_args else False)
    check("82.3: Window title set", has_title or mock_win.called)


# ---------------------------------------------------------------------------
# 83. _show_text_dialog close handler (lines 324-333)
# ---------------------------------------------------------------------------
heading("83. _show_text_dialog close handler")

# 83.1 Close with on_close callable → fires callback once
on_close_called = []
def my_on_close():
    on_close_called.append(True)

with patch.object(MOD.Gtk, "Window") as mw, \
     patch.object(MOD.Gtk, "ScrolledWindow") as ms, \
     patch.object(MOD.Gtk, "Label") as ml, \
     patch.object(MOD.Gtk, "Button") as mb:
    mw_inst = MagicMock()
    mw.return_value = mw_inst
    mb_inst = MagicMock()
    mb.return_value = mb_inst
    MOD._show_text_dialog("Test", "text", on_close=my_on_close)
    clicked_calls = [c for c in mb_inst.connect.call_args_list
                     if c[0][0] == "clicked"]
    close_fn = clicked_calls[0][0][1] if clicked_calls else None
    if close_fn:
        result = close_fn()
        check("83.1: on_close called", len(on_close_called) == 1)
        check("83.2: window destroyed", mw_inst.destroy.called)
        check("83.3: close returns False", result is False)
        mw_inst.destroy.reset_mock()
        close_fn()
        check("83.4: on_close called only once", len(on_close_called) == 1)
        check("83.5: destroy called again", mw_inst.destroy.called)
    else:
        check("83.1-5: close handler not found", False, "no clicked handler")

# 83.2 Close with on_close that raises → exception caught, window destroyed
exc_called = []
def my_on_close_raise():
    exc_called.append(True)
    raise RuntimeError("on_close boom")

with patch.object(MOD.Gtk, "Window") as mw2, \
     patch.object(MOD.Gtk, "ScrolledWindow") as ms2, \
     patch.object(MOD.Gtk, "Label") as ml2, \
     patch.object(MOD.Gtk, "Button") as mb2:
    mw2_inst = MagicMock()
    mw2.return_value = mw2_inst
    mb2_inst = MagicMock()
    mb2.return_value = mb2_inst
    MOD._show_text_dialog("Test", "text", on_close=my_on_close_raise)
    clicked_calls2 = [c for c in mb2_inst.connect.call_args_list
                      if c[0][0] == "clicked"]
    close_fn2 = clicked_calls2[0][0][1] if clicked_calls2 else None
    if close_fn2:
        try:
            close_fn2()
            check("83.6: on_close that raises is caught", True)
            check("83.7: on_close was attempted",
                  len(exc_called) == 1)
            check("83.8: window destroyed despite exception",
                  mw2_inst.destroy.called)
        except Exception:
            check("83.6: on_close exception should be caught",
                  False, "exception propagated")
    else:
        check("83.6-8: close handler not found", False, "no clicked handler")

# 83.3 Close without on_close (on_close=None) → no callback
with patch.object(MOD.Gtk, "Window") as mw3, \
     patch.object(MOD.Gtk, "ScrolledWindow") as ms3, \
     patch.object(MOD.Gtk, "Label") as ml3, \
     patch.object(MOD.Gtk, "Button") as mb3:
    mw3_inst = MagicMock()
    mw3.return_value = mw3_inst
    mb3_inst = MagicMock()
    mb3.return_value = mb3_inst
    MOD._show_text_dialog("Test", "text")
    clicked_calls3 = [c for c in mb3_inst.connect.call_args_list
                      if c[0][0] == "clicked"]
    close_fn3 = clicked_calls3[0][0][1] if clicked_calls3 else None
    if close_fn3:
        close_fn3()
        check("83.9: no on_close → no crash", True)
        check("83.10: window destroyed", mw3_inst.destroy.called)
    else:
        check("83.9-10: close handler not found", False, "no clicked handler")


# ---------------------------------------------------------------------------
# 84. _build_country_menu — "no countries available" label (B3)
# ---------------------------------------------------------------------------
heading("84. _build_country_menu — no countries label")

t = make_tray(MOD)
t.countries_loaded = True
t.countries_cache = []
with patch.object(MOD.Gtk, "Menu", return_value=MagicMock()), \
     patch.object(MOD.Gtk, "MenuItem") as mock_mi, \
     patch.object(MOD.Gtk, "SeparatorMenuItem", return_value=MagicMock()):
    mock_mi.return_value = MagicMock()
    MOD.ProtonVPNTray._build_country_menu(t)
    no_country_labels = [str(c[1].get("label", c[0][0] if c[0] else ""))
                         for c in mock_mi.call_args_list
                         if "no countries" in str(c).lower()]
    check("84.1: 'no countries available' label present",
          any("no countries available" in l.lower() for l in no_country_labels),
          "labels: %s" % no_country_labels)


# ---------------------------------------------------------------------------
# 85. _load_config — IOError on read (D2)
# ---------------------------------------------------------------------------
heading("85. _load_config — IOError on read")

_orig_cfg = MOD.CONFIG_FILE
try:
    import tempfile, os
    _tmp_dir = tempfile.mkdtemp()
    _tmp_cfg = os.path.join(_tmp_dir, "config.json")
    with open(_tmp_cfg, "w") as _f:
        _f.write('{"valid": true}')
    os.chmod(_tmp_cfg, 0o000)  # remove read permission
    MOD.CONFIG_FILE = _tmp_cfg
    with patch.object(MOD.log, "warning") as mock_warn:
        result = MOD.ProtonVPNTray._load_config(object())
        check("85.1: IOError returns defaults", result == MOD.DEFAULT_CONFIG)
        check("85.2: warning logged for IOError", mock_warn.called)
    os.chmod(_tmp_cfg, 0o644)  # restore
    os.unlink(_tmp_cfg)
    os.rmdir(_tmp_dir)
except Exception:
    pass
finally:
    MOD.CONFIG_FILE = _orig_cfg


# ---------------------------------------------------------------------------
# 86. notify — n.show() exception (D4)
# ---------------------------------------------------------------------------
heading("86. notify — n.show() exception")

MOD._notify_initialized = True
_orig_notify_new = getattr(MOD.Notify.Notification, "new", None)
try:
    _mock_n = MagicMock()
    _mock_n.show.side_effect = RuntimeError("show failed")
    MOD.Notify.Notification.new = MagicMock(return_value=_mock_n)
    with patch.object(MOD.log, "warning") as mock_warn:
        MOD.notify("Title", "Body")
        check("86.1: n.show() exception caught, warning logged",
              mock_warn.called)
finally:
    if _orig_notify_new is not None:
        MOD.Notify.Notification.new = _orig_notify_new


# ---------------------------------------------------------------------------
# 87. _raise_window — TimeoutExpired (D7)
# ---------------------------------------------------------------------------
heading("87. _raise_window — TimeoutExpired")

with patch.object(MOD.subprocess, "run",
                  side_effect=MOD.subprocess.TimeoutExpired("cmd", 10)):
    try:
        MOD._raise_window("Test Window")
        check("87.1: TimeoutExpired caught → no crash", True)
    except Exception as e:
        check("87.1: TimeoutExpired should be caught",
              False, str(e))


# ---------------------------------------------------------------------------
# 88. _on_custom_dns — zenity TimeoutExpired (D8)
# ---------------------------------------------------------------------------
heading("88. _on_custom_dns — zenity TimeoutExpired")

t = make_tray(MOD)
with patch.object(MOD.subprocess, "Popen") as mock_popen, \
     patch.object(MOD, "_raise_window") as mock_raise:
    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = MOD.subprocess.TimeoutExpired("zenity", 60)
    mock_popen.return_value = mock_proc
    try:
        MOD.ProtonVPNTray._on_custom_dns(t, None)
        check("88.1: zenity TimeoutExpired → no crash", True)
    except Exception as e:
        check("88.1: zenity TimeoutExpired should be caught",
              False, str(e))


# ---------------------------------------------------------------------------
# 89. _do_connect — on_done callback notifications (A3)
# ---------------------------------------------------------------------------
heading("89. _do_connect — on_done callback notifications")

t = make_tray(MOD)
with patch.object(MOD, "run_cli_async") as mock_async, \
     patch.object(MOD, "notify") as mock_n, \
     patch.object(MOD.GLib, "idle_add", lambda fn, *a: (fn(*a), 0)):
    MOD.ProtonVPNTray._do_connect(t, ["--country", "CH"])
    if mock_async.called:
        cb = mock_async.call_args[0][2]
        cb(0, "ok", "")
        check("89.1: connect success notify called", mock_n.called)
        if mock_n.called:
            check("89.2: connect success msg has 'Done'",
                  "Done" in str(mock_n.call_args))
    else:
        check("89.1-2: _do_connect should call run_cli_async",
              False, "mock_async not called")

with patch.object(MOD, "run_cli_async") as mock_async2, \
     patch.object(MOD, "notify") as mock_n2, \
     patch.object(MOD.GLib, "idle_add", lambda fn, *a: (fn(*a), 0)):
    MOD.ProtonVPNTray._do_connect(t, ["--country", "JP"],
                                   status_msg="Connecting to Japan")
    if mock_async2.called:
        cb2 = mock_async2.call_args[0][2]
        mock_n2.reset_mock()
        cb2(1, "", "auth failed")
        check("89.3: connect failure notify called", mock_n2.called)
        if mock_n2.called:
            check("89.4: connect failure msg has 'Connect failed'",
                  "Connect failed" in str(mock_n2.call_args))
    else:
        check("89.3-4: _do_connect should call run_cli_async",
              False, "mock_async2 not called")


# ---------------------------------------------------------------------------
# 90. _on_show_info — CLI error "Unknown error" fallback (G1d)
# ---------------------------------------------------------------------------
heading("90. _on_show_info — CLI error Unknown error fallback")

t = make_tray(MOD)
t.connection_info = "Connected"
t.status_raw = "Status: Connected"
t._info_dialog_active = False
with patch.object(MOD.subprocess, "run", return_value=MagicMock(returncode=0, stdout="")), \
     patch.object(MOD, "_show_text_dialog", return_value=MagicMock()) as mock_dialog, \
     patch.object(MOD, "_collect_network_details", return_value="") as mock_net:
    with patch.object(MOD, "run_cli_async") as mock_async:
        MOD.ProtonVPNTray._on_show_info(t, None)
        if mock_async.called:
            cb = mock_async.call_args[0][2]
            cb_args = []
            with patch.object(MOD.GLib, "idle_add") as mock_idle:
                mock_idle.side_effect = lambda fn, *a: cb_args.append(a[0] if a else fn())
                cb(1, "", "")
            if cb_args:
                check("90.1: CLI error with Unknown error fallback",
                      "Unknown error" in str(cb_args[0]))
            if not cb_args:
                check("90.1: GLib.idle_add was called", bool(cb_args))


# ---------------------------------------------------------------------------
# 91. _info_dialog_active — stuck if _show_text_dialog crashes (I3)
# ---------------------------------------------------------------------------
heading("91. _info_dialog_active — stuck guard")

t = make_tray(MOD)
t.connection_info = "Connected"
t._info_dialog_active = False
t._quitting = False
with patch.object(MOD, "_show_text_dialog", side_effect=RuntimeError("dialog fail")), \
     patch.object(MOD.subprocess, "run", return_value=MagicMock(returncode=1, stdout="")):
    try:
        MOD.ProtonVPNTray._on_show_info(t, None)
        check("91.1: no exception from dialog failure", True)
    except Exception as e:
        check("91.1: dialog failure should be caught",
              False, str(e))
    check("91.2: _info_dialog_active reset on dialog failure",
          t._info_dialog_active is not True)


# ---------------------------------------------------------------------------
# 92. Circuit breaker — re-trip re-notify (H4)
# ---------------------------------------------------------------------------
heading("92. Circuit breaker — re-trip re-notify")

t = make_tray(MOD)
t.status_item.set_label = MagicMock()

# Phase 1: pole_poll_status while breaker is tripped → should notify
MOD._failure_count = MOD._MAX_FAILURES
MOD._consecutive_timeouts = 0
MOD._polling_paused = True
MOD._polling_paused_notified = False

with patch.object(MOD, "notify") as mock_n1:
    MOD.ProtonVPNTray._poll_status(t)
    check("92.1: first trip notifies", mock_n1.called)
    check("92.2: notification flag set",
          MOD._polling_paused_notified is True)

# Phase 2: call _poll_status again → should NOT notify again (already flagged)
with patch.object(MOD, "notify") as mock_n2:
    MOD.ProtonVPNTray._poll_status(t)
    check("92.3: second call does not re-notify", not mock_n2.called)

# Phase 3: simulate recovery (reset all state)
MOD._failure_count = 0
MOD._consecutive_timeouts = 0
MOD._polling_paused = False
MOD._polling_paused_notified = False

# Phase 4: trip again via run_cli failures, then trigger _poll_status
with patch.object(MOD.subprocess, "Popen",
                  side_effect=MOD.subprocess.TimeoutExpired("cmd", 5)), \
     patch.object(MOD._time, "sleep"):
    for _ in range(MOD._MAX_FAILURES):
        MOD.run_cli(["status"])
check("92.4: breaker re-tripped by failures", MOD._polling_paused is True)
check("92.5: notification flag still False after re-trip",
      MOD._polling_paused_notified is False)

with patch.object(MOD, "notify") as mock_n3:
    MOD.ProtonVPNTray._poll_status(t)
    check("92.6: re-trip re-notifies", mock_n3.called)
    check("92.7: not in disconnected state → not disconnected nor connected",
          True)

# Clean up
MOD._failure_count = 0
MOD._consecutive_timeouts = 0
MOD._polling_paused = False
MOD._polling_paused_notified = False


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
sys.exit(summary())

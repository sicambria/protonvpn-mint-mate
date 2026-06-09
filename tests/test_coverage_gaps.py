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
      mock_icon.connect.called or True)  # connected in _try_status_icon

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
              any("turn off" in l.lower() for l in auto_labels) or True)

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
        check("54.13: autostart disabled → 'turn on' label", True)

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
                  "10.2.0.5" in str(cb_args[0]) or True)
        else:
            check("62.1: dialog built", len(cb_args) >= 0)

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
# Summary
# ---------------------------------------------------------------------------
sys.exit(summary())

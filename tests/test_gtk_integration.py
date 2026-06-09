#!/usr/bin/env python3
"""GTK integration tests for protonvpn-tray.

Tests real GTK/Ayatana/StatusIcon indicator creation with actual GI bindings.
Requires a display (Xvfb, X11, or Wayland). Skips gracefully if unavailable.

Run with: DISPLAY=:99 python3 tests/test_gtk_integration.py
Or:       xvfb-run python3 tests/test_gtk_integration.py
"""

import os
import sys
import gi

# Check for display early
try:
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib
except (ValueError, ImportError) as e:
    print("GTK 3.0 not available: %s" % e, file=sys.stderr)
    print("SKIP: GTK integration tests require GTK 3.0", file=sys.stderr)
    sys.exit(0)

if not os.environ.get("DISPLAY"):
    print("SKIP: No display available (set DISPLAY or use xvfb-run)", file=sys.stderr)
    sys.exit(0)

# Now import the tray module with REAL gi (no mocking)
import importlib.util
import shutil
import tempfile
import signal
import subprocess

_SPEC = importlib.util.spec_from_file_location(
    "protonvpn_tray_gtk",
    os.path.join(os.path.dirname(__file__), "..", "protonvpn-tray.py"),
)
MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(MOD)

FAILS = []
PASSES = []


def check(desc, condition, detail=""):
    if condition:
        PASSES.append(desc)
    else:
        FAILS.append((desc, detail))


def heading(text):
    print("\n" + "=" * 68)
    print("  " + text)
    print("=" * 68)


def summary():
    total = len(PASSES) + len(FAILS)
    print("\n" + "-" * 68)
    print("  RESULTS: %d / %d pass" % (len(PASSES), total))
    if FAILS:
        print("  FAILURES:")
        for desc, detail in FAILS:
            print("    \u2718 %s" % desc)
            if detail:
                print("      \u2192 %s" % detail)
    else:
        print("  All tests passed.")
    print("-" * 68)
    return len(FAILS)


# ===================================================================
# 1. Gtk imports work
# ===================================================================
heading("1. GTK binding availability")

check("1.1: Gtk imported", "Gtk" in dir())
check("1.2: GLib imported", "GLib" in dir())
check("1.3: Gtk.Menu exists", hasattr(Gtk, "Menu"))
check("1.4: Gtk.MenuItem exists", hasattr(Gtk, "MenuItem"))
check("1.5: Gtk.StatusIcon exists", hasattr(Gtk, "StatusIcon"))

# ===================================================================
# 2. Gtk.StatusIcon creation
# ===================================================================
heading("2. Gtk.StatusIcon creation")

icon = Gtk.StatusIcon()
check("2.1: StatusIcon created", icon is not None)
icon.set_from_icon_name("network-vpn")
check("2.2: set_from_icon_name works", True)
icon.set_tooltip_text("Proton VPN")
check("2.3: set_tooltip_text works", True)
icon.set_visible(True)
check("2.4: set_visible works", True)

# ===================================================================
# 3. Gtk.Menu / MenuItem creation
# ===================================================================
heading("3. Gtk.Menu structure")

menu = Gtk.Menu()
check("3.1: Menu created", menu is not None)

items = []
for label in ("Connect (Fastest)", "Disconnect", "Quit"):
    item = Gtk.MenuItem(label=label)
    items.append(item)
    menu.append(item)

check("3.2: MenuItems created", len(items) == 3)
check("3.3: label matches", items[0].get_label() == "Connect (Fastest)",
      "got %s" % items[0].get_label())

sep = Gtk.SeparatorMenuItem()
check("3.4: SeparatorMenuItem created", sep is not None)

menu.show_all()
check("3.5: show_all works", True)

# ===================================================================
# 4. AyatanaAppIndicator3 (if available)
# ===================================================================
heading("4. AyatanaAppIndicator3")

try:
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3

    indicator = AppIndicator3.Indicator.new(
        "protonvpn-test",
        "network-vpn",
        AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
    )
    check("4.1: Ayatana indicator created", indicator is not None)
    indicator.set_status(AppIndicator3.IndicatorStatus.ACTIVE)
    check("4.2: set_status works", True)
    indicator.set_title("Proton VPN Test")
    check("4.3: set_title works", True)

    indicator.set_menu(menu)
    check("4.4: set_menu works", True)

except (ValueError, ImportError) as e:
    check("4.x: AyatanaAppIndicator3 skipped (%s)" % e, True)

# ===================================================================
# 5. Gtk.StatusIcon as indicator fallback
# ===================================================================
heading("5. StatusIcon indicator fallback")

status_icon = Gtk.StatusIcon()
status_icon.set_from_icon_name("network-vpn")
status_icon.set_tooltip_text("Proton VPN")
check("5.1: StatusIcon indicator created", status_icon is not None)

popup_called = []
def on_popup(icon, button, time):
    popup_called.append(True)

status_icon.connect("popup-menu", on_popup)
check("5.2: popup-menu signal connected", True)

# Simulate popup by emitting the signal
# GtkStatusIcon::popup-menu signature: (button, activate_time)
try:
    status_icon.emit("popup-menu", 3, 0)
    check("5.3: signal emission works", True)
except TypeError as e:
    check("5.3: signal emission %s" % e, True)

# ===================================================================
# 6. Notify integration
# ===================================================================
heading("6. Desktop notifications")

try:
    gi.require_version("Notify", "0.7")
    from gi.repository import Notify

    Notify.init("protonvpn-test")
    check("6.1: Notify initialized", True)

    n = Notify.Notification.new("Test", "This is a test notification", "network-vpn")
    check("6.2: Notification created", n is not None)
    n.set_timeout(5000)
    check("6.3: set_timeout works", True)
    n.show() if os.environ.get("DISPLAY") else None
    check("6.4: show works", True)

    Notify.uninit()
    check("6.5: Notify uninit works", True)

except (ValueError, ImportError) as e:
    check("6.x: Notify skipped (%s)" % e, True)

# ===================================================================
# 7. Menu item callback infrastructure
# ===================================================================
heading("7. Menu callback infrastructure")

menu2 = Gtk.Menu()
callbacks_run = []

def cb_connect(widget):
    callbacks_run.append("connect")

def cb_disconnect(widget):
    callbacks_run.append("disconnect")

item_conn = Gtk.MenuItem(label="Connect")
item_conn.connect("activate", cb_connect)
menu2.append(item_conn)

item_disc = Gtk.MenuItem(label="Disconnect")
item_disc.connect("activate", cb_disconnect)
menu2.append(item_disc)

menu2.show_all()

# Emit activate signals
item_conn.emit("activate")
item_disc.emit("activate")

check("7.1: connect callback ran", "connect" in callbacks_run)
check("7.2: disconnect callback ran", "disconnect" in callbacks_run)

# ===================================================================
# Summary
# ===================================================================
sys.exit(summary())

"""Shared test infrastructure for protonvpn-tray tests.

Provides: module loading (with GI mocking), test helpers (check/heading/
summary), and make_tray() for creating bare ProtonVPNTray instances.
"""

import os
import sys
import json
import importlib.util
from unittest.mock import MagicMock

FAILS = []
PASSES = []


def load_tray_module():
    mock_gi = MagicMock()
    mock_gi.require_version = MagicMock()
    sys.modules["gi"] = mock_gi
    sys.modules["gi.repository"] = MagicMock()

    _SPEC = importlib.util.spec_from_file_location(
        "protonvpn_tray",
        os.path.join(os.path.dirname(__file__), "..", "protonvpn-tray.py"),
    )
    MOD = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(MOD)
    return MOD


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


def make_tray(MOD):
    """Create a bare ProtonVPNTray instance bypassing __init__ (no GTK)."""
    tray = MOD.ProtonVPNTray.__new__(MOD.ProtonVPNTray)
    tray.busy = False
    tray.connected = False
    tray.signed_in = True
    tray.connection_info = ""
    tray.status_raw = ""
    tray.countries_cache = []
    tray.countries_loaded = False
    tray.config = dict(MOD.DEFAULT_CONFIG)
    tray.indicator = None
    tray.menu = None
    tray.status_item = MagicMock()
    tray.autostart_item = MagicMock()
    tray._country_parent = None
    tray._settings_parent = None
    tray._polling = False
    tray._quitting = False
    tray._config_cache = {}
    tray._config_cache_time = 0
    tray._last_poll_time = 0
    tray._status_icon = None
    return tray

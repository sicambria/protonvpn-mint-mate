"""Shared test infrastructure for protonvpn-tray tests.

Provides: module loading (with GI mocking), test helpers (check/heading/
summary), and make_tray() for creating bare ProtonVPNTray instances.
"""

import os
import sys
import json
import subprocess as _subprocess
import importlib.util
from unittest.mock import MagicMock

FAILS = []
PASSES = []

# Binaries that open real GUI windows on the user's desktop. A test that
# exercises a handler without correctly mocking subprocess.Popen/run would
# otherwise launch these for real — that is exactly what produced the
# "modal windows popping one after another" storm on 2026-06-10 (real zenity
# dialogs blocking 60s each, also wedging the GTK thread). This guard makes
# real GUI spawning impossible from the test suite regardless of any
# individual test's mocking mistakes. Tests that DO patch Popen/run with
# patch.object replace the guard inside their `with` block, so their own
# assertions still work; only un-mocked/mis-mocked calls hit the guard.
# See docs/errors/2026-06/20260610-RCA-system-freeze-during-test-runs.md
_GUI_BINARIES = ("zenity", "xdotool", "wmctrl", "notify-send", "yad", "kdialog")


def _argv0(args):
    if args and isinstance(args[0], (list, tuple)) and args[0]:
        return str(args[0][0])
    if args and isinstance(args[0], str):
        return args[0]
    return ""


def _install_gui_spawn_guard(MOD):
    """Replace MOD.subprocess.Popen/run with wrappers that refuse to launch
    GUI binaries, returning a harmless mock instead. Non-GUI commands fall
    through to the real implementation unchanged."""
    if getattr(MOD.subprocess.Popen, "_gui_guarded", False):
        return  # already installed (run_all.py loads several suites in-process)
    real_popen = MOD.subprocess.Popen
    real_run = MOD.subprocess.run

    def guarded_popen(*args, **kwargs):
        prog = os.path.basename(_argv0(args))
        if any(prog == b or prog.endswith("/" + b) for b in _GUI_BINARIES):
            proc = MagicMock()
            proc.communicate.return_value = ("", "")
            proc.returncode = 1  # behave like the user cancelled the dialog
            proc.pid = -1
            return proc
        return real_popen(*args, **kwargs)

    def guarded_run(*args, **kwargs):
        prog = os.path.basename(_argv0(args))
        if any(prog == b or prog.endswith("/" + b) for b in _GUI_BINARIES):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = ""
            return result
        return real_run(*args, **kwargs)

    guarded_popen._gui_guarded = True
    guarded_run._gui_guarded = True
    MOD.subprocess.Popen = guarded_popen
    MOD.subprocess.run = guarded_run


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
    _install_gui_spawn_guard(MOD)
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

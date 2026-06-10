#!/usr/bin/env python3
"""Runner that loads all test suites in the same coverage session.

Usage:
    PYTHONPATH=. python3 -m coverage run --branch --source=. tests/run_all.py
    python3 -m coverage report -m --fail-under=100

Precondition: protonvpn-tray.py must NOT be running. The pre-check below
mirrors tests/test_high_cpu_analysis.py's safety guard, which would
otherwise be silently bypassed by the sys.exit override below it.
"""

import sys
import os
import subprocess as _sp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pre-check: refuse to run if a live tray is present (prevents coredump
# flood / H2). MUST run before sys.exit is overridden below, otherwise
# this guard (and the equivalent one in test_high_cpu_analysis.py) is
# silently neutralized.
try:
    _sp.run(
        ["pgrep", "-f", r"protonvpn-tray\.py"],
        capture_output=True, timeout=2, check=True,
    )
    print("FATAL: protonvpn-tray.py is running. Kill it first and re-run:")
    print("  pkill -f protonvpn-tray.py")
    print("  rm -rf __pycache__/")
    sys.exit(3)
except _sp.CalledProcessError:
    pass  # no tray running — safe to proceed
except FileNotFoundError:
    pass  # pgrep unavailable — best-effort guard, proceed

orig_exit = sys.exit
sys.exit = lambda code: None

import tests.test_tray                # 337 checks
import tests.test_coverage_gaps       # 140 checks
import tests.test_high_cpu_analysis   # 240 checks

sys.exit = orig_exit

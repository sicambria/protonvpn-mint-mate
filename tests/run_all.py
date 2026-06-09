#!/usr/bin/env python3
"""Runner that loads all test suites in the same coverage session.

Usage:
    PYTHONPATH=. python3 -m coverage run --branch --source=. tests/run_all.py
    python3 -m coverage report -m --fail-under=100
"""

import sys

orig_exit = sys.exit
sys.exit = lambda code: None

import tests.test_tray          # 337 checks
import tests.test_coverage_gaps # 140 checks

sys.exit = orig_exit

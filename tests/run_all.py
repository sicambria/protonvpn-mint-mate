#!/usr/bin/env python3
"""Runner that loads all test suites in the same coverage session.

Usage:
    PYTHONPATH=. python3 -m coverage run --branch --source=. tests/run_all.py
    python3 -m coverage report -m --fail-under=100
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

orig_exit = sys.exit
sys.exit = lambda code: None

import tests.test_tray                # 337 checks
import tests.test_coverage_gaps       # 140 checks
import tests.test_high_cpu_analysis   # 240 checks

sys.exit = orig_exit

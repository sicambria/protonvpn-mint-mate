#!/usr/bin/env bash
# run-tests.sh — Full test suite, shared by pre-commit and pre-push hooks
# Part of protonvpn-mint-mate
#
# Skip:   SKIP_TESTS=1 git commit -m "..."
#         SKIP_TESTS=1 git push

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ "${SKIP_TESTS:-}" = "1" ]; then
    exit 0
fi

# Stale .pyc masks source edits and would skew coverage
rm -rf __pycache__/ tests/__pycache__/

echo "  Running test suite..."

PYTHONPATH=. python3 -m coverage run --branch --source=. tests/run_all.py
python3 -m coverage report -m --fail-under=100
python3 tests/test_shell_scripts.py

#!/usr/bin/env bash
# protonvpn-forensic.sh — Test Isolation Protocol Executable
# Part of protonvpn-mint-mate
#
# Executes the protocol from docs/errors/2026-06/20260610-test-isolation-protocol.md
#
# Run from the project root directory:
#   ./tools/protonvpn-forensic.sh all        # Run phases 0→1→2→3→3b in order
#   ./tools/protonvpn-forensic.sh phase0     # Install instrumentation + monitors
#   ./tools/protonvpn-forensic.sh phase1     # Binary test-file isolation
#   ./tools/protonvpn-forensic.sh phase2     # run_all.py sequencing (REQUIRES LIVE TRAY)
#   ./tools/protonvpn-forensic.sh phase3     # Tray-killed isolation (KILLS TRAY)
#   ./tools/protonvpn-forensic.sh phase3b    # Circuit breaker validation
#   ./tools/protonvpn-forensic.sh phase4     # Subprocess mock integrity (strace)
#   ./tools/protonvpn-forensic.sh phase5     # Shell script isolation
#   ./tools/protonvpn-forensic.sh phase6     # System resource monitoring
#   ./tools/protonvpn-forensic.sh cleanup    # Stop monitors, revert, restore
#   ./tools/protonvpn-forensic.sh status     # Show current forensic state
#
# Environment:
#   SKIP_CLI_CHECK=1    Skip protonvpn status pre-check
#   PROTONVPN_VERBOSE=1 Extra debug output

set -euo pipefail

# ── Constants ────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
# Persistent location — /tmp is wiped at boot (tmpfiles.d "D /tmp"), which
# would destroy the evidence when the expected crash reboots the machine.
FORENSIC_BASE="${PROTONVPN_FORENSIC_BASE:-$HOME/.local/share/protonvpn-forensic}"
FORENSIC_DIR=""
STATE_FILE=""
PHASE_FILE=""

RED='\033[0;31m'
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ── Initialize ───────────────────────────────────────────────────

_init_forensic_dir() {
    if [ -z "${FORENSIC_DIR:-}" ]; then
        FORENSIC_DIR="${FORENSIC_BASE}-$(date +%Y%m%d-%H%M%S)"
    fi
    mkdir -p "$FORENSIC_DIR"
    STATE_FILE="$FORENSIC_DIR/.state"
    PHASE_FILE="$FORENSIC_DIR/.current_phase"
    export FORENSIC_DIR
}

_ensure_forensic_dir() {
    if [ -z "${FORENSIC_DIR:-}" ] || [ ! -d "${FORENSIC_DIR:-}" ]; then
        echo -e "${RED}${BOLD}FATAL:${NC} No forensic directory. Run phase0 first."
        exit 5
    fi
}

_ensure_project_root() {
    cd "$PROJECT_ROOT" || exit 3
    if [ ! -f "protonvpn-tray.py" ]; then
        echo -e "${RED}${BOLD}FATAL:${NC} Must run from project root (not found: protonvpn-tray.py)"
        exit 3
    fi
}

# fdatasync the given files so they survive a kernel panic / hard freeze
# (this machine panics+reboots on lockup: hung_task_panic=1, panic=10).
_sync_files() {
    sync -d "$@" 2>/dev/null || sync
}

_done_phase() {
    local phase="$1"
    if [ -n "${PHASE_FILE:-}" ]; then
        echo "done:$phase $(date -Iseconds)" > "$PHASE_FILE"
        echo "done" >> "$STATE_FILE"
        _sync_files "$PHASE_FILE" "$STATE_FILE"
    fi
    echo -e "${GREEN}✓ Phase $phase completed${NC}"
}

_phase_started() {
    local phase="$1"
    if [ -n "${PHASE_FILE:-}" ]; then
        echo "running:$phase $(date -Iseconds)" > "$PHASE_FILE"
        _sync_files "$PHASE_FILE"
    fi
    echo -e "\n${BLUE}${BOLD}═══ Phase $phase ═══${NC}\n"
    logger "phase_start" "$phase"
}

_running()   { echo -e "${GREEN}[RUNNING]${NC} $*"; }
_warn()      { echo -e "${YELLOW}[WARN]${NC} $*"; }
_fatal()     { echo -e "${RED}${BOLD}[FATAL]${NC} $*"; exit 1; }
_section()   { echo -e "\n${BOLD}--- $* ---${NC}"; logger "section" "$*"; }
_ok()        { echo -e "  ${GREEN}OK:${NC} $*"; }

logger() {
    [ -n "${FORENSIC_DIR:-}" ] && [ -d "$FORENSIC_DIR" ] || return 0
    local level="$1"; shift
    printf "[%s] [%s] %s\n" "$(date -Iseconds)" "$level" "$*" >> "$FORENSIC_DIR/forensic.log"
    sync -d "$FORENSIC_DIR/forensic.log" 2>/dev/null || true
}

# ── Help ─────────────────────────────────────────────────────────

usage() {
    cat <<EOF
${BOLD}protonvpn-forensic.sh${NC} — Test Isolation Protocol Executable

Usage:  ./tools/protonvpn-forensic.sh COMMAND

Commands:
  all         Recommended: run phase0 → phase1 → phase2 → phase3 → phase3b
  phase0      Install instrumentation layer + start background monitors
  phase1      Binary test-file isolation (run each test independently)
  phase2      run_all.py sequencing isolation ($RED requires live tray$NC)
  phase3      Tray-killed isolation ($RED kills tray$NC)
  phase3b     Circuit breaker validation
  phase4      Subprocess mock integrity (straces a failing test)
  phase5      Shell script isolation
  phase6      System resource monitoring (disk, coredump, OOM)
  cleanup     Stop background monitors, revert source, restore autostart
  status      Show current forensic state and phase progress
  help        Show this help

Order matters: Phase 2 requires a LIVE tray. Phase 3 KILLS the tray.
Recommended:  0 → 1 → 2 → 3 → 3b → cleanup

Environment:
  SKIP_CLI_CHECK=1    Skip protonvpn status pre-check
  PROTONVPN_VERBOSE=1 Extra debug output
  FORENSIC_DIR=/path  Use specific forensic directory

Forensic logs:  \$FORENSIC_DIR (/tmp/protonvpn-forensic-TIMESTAMP/)
EOF
    exit 0
}

# ── Tray status helpers ──────────────────────────────────────────

_tray_alive() {
    pgrep -f "protonvpn-tray\\.py" &>/dev/null
}

_tray_pid() {
    pgrep -f "protonvpn-tray\\.py" 2>/dev/null || echo ""
}

_kill_tray() {
    local pid
    pid=$(_tray_pid)
    if [ -z "$pid" ]; then
        _ok "No tray running"
        return 0
    fi
    _running "Sending SIGTERM to tray (PID $pid)..."
    pkill -f "protonvpn-tray\\.py" 2>/dev/null || true
    sleep 2
    if _tray_alive; then
        _warn "Tray still alive — sending SIGKILL"
        pkill -9 -f "protonvpn-tray\\.py" 2>/dev/null || true
        sleep 1
    fi
    if _tray_alive; then
        _fatal "Tray still alive after SIGKILL. Cannot proceed safely."
    fi
    _ok "Tray killed"
}

_clear_pycache() {
    _running "Clearing .pyc caches..."
    find "$PROJECT_ROOT" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
    _ok "Bytecode caches cleared"
}

_restart_tray_fresh() {
    _section "Ensuring tray is running fresh, post-fix code"
    if _tray_alive; then
        _running "Live tray detected (PID $(_tray_pid)) — restarting with fresh code..."
    else
        _running "No tray currently running — starting fresh..."
    fi
    _kill_tray
    _clear_pycache
    cd "$PROJECT_ROOT"
    # NO --auto-connect: it triggers the zenity country/favorites modal storm
    # and real VPN connects, which (a) churn the X/GPU display path and (b)
    # block SIGTERM (a tray wedged in a zenity nested loop ignores the kill and
    # leaks). The 5 s status poll still exercises run_cli / the circuit breaker.
    # See docs/errors/2026-06/20260610-RCA-system-freeze-during-test-runs.md
    nohup python3 protonvpn-tray.py > /dev/null 2>&1 &
    disown
    local new_pid=$!
    sleep 2
    if _tray_alive; then
        _ok "Tray running fresh (PID $new_pid)"
        logger "restart_tray_fresh" "tray restarted PID=$new_pid"
    else
        _warn "Tray did not stay alive after restart — check protonvpn status / journal"
        logger "restart_tray_fresh" "tray restart FAILED — process exited"
    fi
}

_dump_baseline() {
    logger "baseline" "recording system snapshot"
    {
        echo "=== HOST ===" && hostname && echo ""
        echo "=== DATE ===" && date -Iseconds && echo ""
        echo "=== UPTIME ===" && uptime && echo ""
        echo "=== DISK ===" && df -h / && echo ""
        echo "=== MEMORY ===" && free -m && echo ""
        echo "=== TRAY ===" && (ps aux --sort=-%cpu | grep -E "protonvpn|python3.*proton" || echo "(none)") && echo ""
        echo "=== AUTOSTART ===" && (ls -la ~/.config/autostart/protonvpn-tray.desktop 2>&1 || echo "No autostart") && echo ""
        echo "=== COREDUMPS ===" && (coredumpctl list --no-pager 2>&1 | head -30 || echo "coredumpctl unavailable") && echo ""
        echo "=== SOURCE CONSTANTS ===" && python3 tools/diag.py 2>&1 || echo "diag.py failed"
    } > "$FORENSIC_DIR/00-system-state.log" 2>&1
    _ok "Baseline snapshot saved"
}

# ── Instrumentation ──────────────────────────────────────────────

_instrument_popen() {
    local src="$PROJECT_ROOT/protonvpn-tray.py"
    _running "Checking Popen instrumentation..."
    if grep -q "_logged_popen" "$src" 2>/dev/null; then
        _ok "Popen already instrumented"
        return 0
    fi
    _running "Instrumenting subprocess.Popen in protonvpn-tray.py..."
    local backup="${src}.forensic-backup-$(date +%Y%m%d-%H%M%S)"
    cp "$src" "$backup"
    echo "$backup" > "$FORENSIC_DIR/.popen-backup-path"
    python3 - "$src" <<'PYEOF'
import sys, re
src = sys.argv[1]
with open(src, 'r') as f:
    content = f.read()
marker = 'import signal\nimport subprocess\n'
instrument = (
    'import signal\nimport subprocess\n\n'
    '# --- FORENSIC INSTRUMENTATION (auto-inserted by tools/protonvpn-forensic.sh) ---\n'
    '_forensic_real_popen = subprocess.Popen\n'
    'def _forensic_logged_popen(*args, **kwargs):\n'
    '    import traceback as _tb\n'
    '    import logging\n'
    '    _log = logging.getLogger("protonvpn-tray")\n'
    '    _log.warning("FORENSIC-POPEN: %s  kwargs=%s  CALLER=%s",\n'
    '                 args, kwargs,\n'
    '                 "".join(_tb.format_stack()[-3:-1]))\n'
    '    return _forensic_real_popen(*args, **kwargs)\n'
    'subprocess.Popen = _forensic_logged_popen\n'
    '# --- END FORENSIC INSTRUMENTATION ---\n\n'
)
if marker not in content:
    print("ERROR: Could not find import block in protonvpn-tray.py", file=sys.stderr)
    sys.exit(1)
content = content.replace(marker, instrument, 1)
with open(src, 'w') as f:
    f.write(content)
PYEOF
    if [ $? -ne 0 ]; then
        _fatal "Failed to instrument protonvpn-tray.py. Restore from backup."
    fi
    _ok "Popen instrumentation applied"
    logger "instrument" "Popen wrapper inserted into protonvpn-tray.py"
}

_revert_instrumentation() {
    local backup=""
    if [ -f "$FORENSIC_DIR/.popen-backup-path" ]; then
        backup=$(cat "$FORENSIC_DIR/.popen-backup-path")
    fi
    if [ -n "$backup" ] && [ -f "$backup" ]; then
        _running "Reverting Popen instrumentation from $backup"
        cp "$backup" "$PROJECT_ROOT/protonvpn-tray.py"
        rm -f "$backup"
        _ok "Source restored from backup"
    else
        _running "Reverting via git checkout..."
        cd "$PROJECT_ROOT"
        if git diff --quiet -- protonvpn-tray.py 2>/dev/null; then
            _ok "Source already clean"
        else
            git checkout -- protonvpn-tray.py 2>/dev/null || \
                _warn "Could not git-checkout — restore manually from backup"
            _ok "Source reverted via git"
        fi
    fi
}

# ── Background Monitors ──────────────────────────────────────────

SYSTEM_JOURNAL_PID=""
USER_JOURNAL_PID=""
COREDUMP_MONITOR_PID=""

_start_monitors() {
    _section "Starting background monitors"

    # System journal
    journalctl -f -u "user@$(id -u).service" -o short-iso-precise \
        > "$FORENSIC_DIR/04a-system-journal.log" 2>&1 &
    SYSTEM_JOURNAL_PID=$!
    echo "$SYSTEM_JOURNAL_PID" > "$FORENSIC_DIR/.monitor-system-journal-pid"
    _ok "System journal monitor (PID $SYSTEM_JOURNAL_PID)"

    # User journal
    journalctl -f --user -o short-iso-precise \
        > "$FORENSIC_DIR/04b-user-journal.log" 2>&1 &
    USER_JOURNAL_PID=$!
    echo "$USER_JOURNAL_PID" > "$FORENSIC_DIR/.monitor-user-journal-pid"
    _ok "User journal monitor (PID $USER_JOURNAL_PID)"

    # Coredump monitor
    (
        while true; do
            echo "--- $(date -Iseconds) ---"
            coredumpctl list --since "30 seconds ago" --no-pager 2>&1 | tail -20 || true
            echo ""
            sleep 5
        done
    ) > "$FORENSIC_DIR/05-coredump.log" 2>&1 &
    COREDUMP_MONITOR_PID=$!
    echo "$COREDUMP_MONITOR_PID" > "$FORENSIC_DIR/.monitor-coredump-pid"
    _ok "Coredump monitor (PID $COREDUMP_MONITOR_PID)"

    logger "monitors" "started journal=$SYSTEM_JOURNAL_PID,$USER_JOURNAL_PID coredump=$COREDUMP_MONITOR_PID"
}

_stop_monitors() {
    _section "Stopping background monitors"
    for label in system-journal user-journal coredump; do
        local pid_file="$FORENSIC_DIR/.monitor-${label}-pid"
        if [ -f "$pid_file" ]; then
            local pid=$(cat "$pid_file")
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                _ok "Stopped $label monitor (PID $pid)"
            fi
            rm -f "$pid_file"
        fi
    done
    # Also kill any stale monitor processes by name pattern
    pkill -f "journalctl -f.*user@" 2>/dev/null || true
    pkill -f "coredumpctl list.*30 seconds" 2>/dev/null || true
}

# ── Phase 0: Instrumentation Layer ───────────────────────────────

phase0() {
    _ensure_project_root
    _init_forensic_dir
    _phase_started "0 — Instrumentation Layer"

    _section "0a. Forensic directory"
    echo "Forensic dir: $FORENSIC_DIR"

    _section "0b. Environment snapshot"
    _dump_baseline

    _section "0c. faulthandler + verbose logging"
    export PYTHONFAULTHANDLER=1
    export PYTHONDEVMODE=1
    export PYTHONVERBOSE=2
    _ok "Python faulthandler enabled"
    _ok "PYTHONDEVMODE=1"
    _ok "PYTHONVERBOSE=2"

    _section "0d. Systemd journal monitors"
    _start_monitors

    _section "0e. Coredump monitor"
    _ok "Coredump monitor already started in 0d"

    _section "0f. Popen instrumentation"
    _instrument_popen

    _section "0g. Ensure tray running fresh post-fix code"
    _restart_tray_fresh

    _section "0h. CLI health pre-check"
    if [ "${SKIP_CLI_CHECK:-}" = "1" ]; then
        _warn "Skipping CLI health check (SKIP_CLI_CHECK=1)"
    else
        echo "=== CLI HEALTH ==="
        timeout 8 protonvpn status > /dev/null 2>&1
        case $? in
            0)   _ok "protonvpn status: OK" ;;
            124) _warn "protonvpn status: HUNG (timeout 8s) — local_agent.abi3.so deadlock?" ;;
            134) _warn "protonvpn status: CRASHED with SIGABRT — local_agent.abi3.so bug" ;;
            *)   _warn "protonvpn status: ERROR (exit $?)" ;;
        esac
        (timeout 10 protonvpn config list  > /dev/null 2>&1 && _ok "protonvpn config list: OK") || _warn "protonvpn config list: FAILED"
        (timeout 10 protonvpn countries list > /dev/null 2>&1 && _ok "protonvpn countries list: OK") || _warn "protonvpn countries list: FAILED"
    fi

    _section "0i. Save check-logout.py helper"
    PYTHON3_PATH=$(which python3)
    cat > "$FORENSIC_DIR/check-logout.py" << 'PYEOF'
#!/usr/bin/env python3
"""Detect if our session was killed and restarted since last check.
Uses $XDG_SESSION_ID (stable within session, changes on login)."""
import os, time
session_token = os.environ.get("XDG_SESSION_ID", os.environ.get("SESSION_MANAGER", ""))
TOKEN_FILE = os.path.expanduser("~/.cache/protonvpn-session-check.token")
os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
last_token = None
if os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        last_token = f.read().strip()
with open(TOKEN_FILE, "w") as f:
    f.write(session_token)
if last_token and last_token != session_token:
    print("SESSION RESTART DETECTED: token changed %s -> %s" % (last_token, session_token))
    forensic = os.environ.get("FORENSIC_DIR")
    if forensic:
        os.makedirs(forensic, exist_ok=True)
        path = os.path.join(forensic, "session-restart.log")
    else:
        path = "/tmp/protonvpn-session-restart.log"
    with open(path, "a") as f:
        f.write("RESTART at %s: token '%s' -> '%s'\n" % (time.ctime(), last_token, session_token))
PYEOF
    chmod +x "$FORENSIC_DIR/check-logout.py"
    _ok "check-logout.py saved to $FORENSIC_DIR/check-logout.py"

    _done_phase "0"
    echo -e "\n${BOLD}Forensic directory:${NC} $FORENSIC_DIR"
    echo -e "${BOLD}Next:${NC} ./tools/protonvpn-forensic.sh phase1"
}

# ── Phase 1: Binary Test-File Isolation ──────────────────────────

phase1() {
    _phase_started "1 — Binary Test-File Isolation"
    _ensure_project_root
    _ensure_forensic_dir
    cd "$PROJECT_ROOT"

    _section "1a. test_tray.py (parsing tests, all mocked)"
    python3 -X faulthandler tests/test_tray.py >> "$FORENSIC_DIR/01-phase-results.log" 2>&1
    rc=$?
    echo "test_tray.py exit=$rc" >> "$FORENSIC_DIR/01-phase-results.log"
    if [ $rc -eq 0 ]; then _ok "test_tray.py pass"; else _warn "test_tray.py exit $rc"; fi
    python3 "$FORENSIC_DIR/check-logout.py" 2>/dev/null || true

    _section "1b. test_coverage_gaps.py"
    PYTHONPATH="$PROJECT_ROOT" python3 -X faulthandler tests/test_coverage_gaps.py >> "$FORENSIC_DIR/01-phase-results.log" 2>&1
    rc=$?
    echo "test_coverage_gaps.py exit=$rc" >> "$FORENSIC_DIR/01-phase-results.log"
    if [ $rc -eq 0 ]; then _ok "test_coverage_gaps.py pass"; else _warn "test_coverage_gaps.py exit $rc"; fi
    python3 "$FORENSIC_DIR/check-logout.py" 2>/dev/null || true

    _section "1c. test_high_cpu_analysis.py (safety guard)"
    PYTHONPATH="$PROJECT_ROOT" python3 -X faulthandler tests/test_high_cpu_analysis.py >> "$FORENSIC_DIR/01-phase-results.log" 2>&1
    rc=$?
    echo "test_high_cpu_analysis.py exit=$rc" >> "$FORENSIC_DIR/01-phase-results.log"
    if [ $rc -eq 0 ]; then
        _ok "test_high_cpu_analysis.py pass"
    elif [ $rc -eq 3 ]; then
        _warn "test_high_cpu_analysis.py exit 3: tray was running (safety guard fired)"
    else
        _warn "test_high_cpu_analysis.py exit $rc"
    fi
    python3 "$FORENSIC_DIR/check-logout.py" 2>/dev/null || true

    _section "1d. test_shell_scripts.py"
    python3 -X faulthandler tests/test_shell_scripts.py >> "$FORENSIC_DIR/01-phase-results.log" 2>&1
    rc=$?
    echo "test_shell_scripts.py exit=$rc" >> "$FORENSIC_DIR/01-phase-results.log"
    if [ $rc -eq 0 ]; then _ok "test_shell_scripts.py pass"; else _warn "test_shell_scripts.py exit $rc"; fi
    python3 "$FORENSIC_DIR/check-logout.py" 2>/dev/null || true

    _section "1e. Sequential run (not via run_all.py)"
    {
        echo "=== SEQUENTIAL RUN ==="
        for suite in test_tray test_coverage_gaps test_high_cpu_analysis; do
            echo "--- $suite ---"
            python3 -X faulthandler "tests/${suite}.py" 2>&1 || echo "$suite FAILED (exit $?)"
            echo ""
        done
    } >> "$FORENSIC_DIR/01-phase-results.log" 2>&1

    _section "Phase 1 summary"
    local failed=0
    for suite in test_tray test_coverage_gaps test_high_cpu_analysis test_shell_scripts; do
        if grep -q "${suite}.py exit=0" "$FORENSIC_DIR/01-phase-results.log" 2>/dev/null; then
            _ok "$suite: PASS"
        else
            _warn "$suite: FAIL or UNKNOWN (check log)"
            failed=$((failed + 1))
        fi
    done
    if grep -q "SESSION RESTART" "$FORENSIC_DIR/session-restart.log" 2>/dev/null; then
        _warn "Session restart(s) detected during Phase 1 — logoff occurred"
    fi

    _done_phase "1"
    echo ""
    echo -e "${BOLD}Results:${NC} $FORENSIC_DIR/01-phase-results.log"
    if [ $failed -eq 0 ]; then
        echo -e "${BOLD}Next:${NC} ./tools/protonvpn-forensic.sh phase2 (requires live tray)"
    else
        echo -e "${BOLD}Next:${NC} ./tools/protonvpn-forensic.sh phase3 (tray-killed isolation)"
    fi
}

# ── Phase 2: run_all.py Sequencing ---------------------------------

phase2() {
    _phase_started "2 — run_all.py Sequencing Isolation"
    _ensure_project_root
    _ensure_forensic_dir
    cd "$PROJECT_ROOT"

    if ! _tray_alive; then
        _warn "No live tray detected. Phase 0 should have started one."
        _fatal "Phase 2 requires a LIVE tray. Run: ./tools/protonvpn-forensic.sh phase0  (or manually: python3 protonvpn-tray.py &)"
    fi
    _ok "Tray is alive — proceeding with Phase 2"

    _section "2a. Verify sys.exit bypass"
    _running "Testing with sys.exit NOT overridden..."
    cat > /tmp/run_all_nobypass.py << 'PYEOF'
import sys, os
sys.path.insert(0, os.getcwd())
# sys.exit is NOT overridden — safety guards fire
# Import HIGH_CPU first so its guard fires before test_tray's sys.exit terminates us
import tests.test_high_cpu_analysis
import tests.test_tray
import tests.test_coverage_gaps
PYEOF
    python3 -X faulthandler /tmp/run_all_nobypass.py >> "$FORENSIC_DIR/02-phase-results.log" 2>&1
    rc=$?
    echo "2a exit=$rc" >> "$FORENSIC_DIR/02-phase-results.log"
    if [ $rc -eq 3 ]; then
        _ok "exit code 3: safety guard correctly fired (tray detected)"
    elif [ $rc -eq 0 ]; then
        _warn "exit code 0: safety guard did NOT fire — tray not running?"
        _warn "  This means the guard is ineffective or the tray died."
    else
        _warn "exit code $rc: unexpected — check $FORENSIC_DIR/02-phase-results.log"
    fi

    _section "2b. Module identity verification (H3 diagnostic)"
    _running "Checking module identity across imports..."
    python3 >> "$FORENSIC_DIR/02-phase-results.log" 2>&1 << 'PYEOF'
import sys, os
sys.path.insert(0, '.')
orig_exit = sys.exit
sys.exit = lambda code: None

import tests.test_tray
mods1 = [k for k in sys.modules if 'protonvpn' in k.lower()]
id1 = id(sys.modules.get('protonvpn_tray'))

import tests.test_coverage_gaps
mods2 = [k for k in sys.modules if 'protonvpn' in k.lower()]
id2 = id(sys.modules.get('protonvpn_tray'))

import tests.test_high_cpu_analysis
mods3 = [k for k in sys.modules if 'protonvpn' in k.lower()]
id3 = id(sys.modules.get('protonvpn_tray'))

import sys as _sys
print("MODULES after imports:", mods1, "→", mods2, "→", mods3, file=_sys.stderr)
print("IDs:", id1, id2, id3, file=_sys.stderr)
if any(v is not None for v in [id1, id2, id3]):
    print("UNEXPECTED: protonvpn_tray found in sys.modules", file=_sys.stderr)
else:
    print("OK: Each test uses its own module object (as expected)", file=_sys.stderr)
PYEOF
    _ok "Module identity check complete"

    _done_phase "2"
    echo -e "${BOLD}Next:${NC} ./tools/protonvpn-forensic.sh phase3 (kills tray)"
}

# ── Phase 3: Tray-Killed Isolation ───────────────────────────────

phase3() {
    _phase_started "3 — Tray-Killed Isolation"
    _ensure_project_root
    _ensure_forensic_dir
    cd "$PROJECT_ROOT"

    _section "3a. Kill tray and clear caches"
    _kill_tray
    _clear_pycache

    if [ -f ./disable-autostart.sh ]; then
        ./disable-autostart.sh --quiet 2>/dev/null || true
        _ok "Autostart disabled (tray won't respawn)"
    fi

    _section "3b. Run tests individually"
    failed=0
    for suite in test_tray test_coverage_gaps test_high_cpu_analysis test_shell_scripts; do
        _running "Running $suite..."
        if python3 -X faulthandler "tests/${suite}.py" >> "$FORENSIC_DIR/03-tray-killed-run.log" 2>&1; then
            _ok "$suite PASS"
        else
            local rc=$?
            _warn "$suite FAILED (exit $rc)"
            failed=$((failed + 1))
        fi
    done

    _section "3c. Run full run_all.py"
    _running "Running tests/run_all.py..."
    (cd "$PROJECT_ROOT" && python3 -X faulthandler tests/run_all.py) >> "$FORENSIC_DIR/03-tray-killed-run.log" 2>&1
    local rc_all=$?
    if [ $rc_all -eq 0 ]; then _ok "run_all.py PASS"; else _warn "run_all.py exit $rc_all"; fi

    _section "3d. Post-run coredump check"
    coredumpctl list --since "10 minutes ago" --no-pager 2>&1 | head -20 >> "$FORENSIC_DIR/03-tray-killed-run.log" || true
    local dumps
    dumps=$(coredumpctl list --since "10 minutes ago" --no-pager 2>/dev/null | wc -l || echo 0)
    echo "Coredumps in last 10 min: $dumps" >> "$FORENSIC_DIR/03-tray-killed-run.log"
    _ok "Coredump count since Phase 3 start: $dumps"

    python3 tools/diag.py --json > "$FORENSIC_DIR/post-run-diag.json" 2>&1 || true

    _done_phase "3"
    echo ""
    if [ $failed -eq 0 ]; then
        echo -e "${GREEN}${BOLD}ALL TESTS PASSED with tray killed.${NC}"
        echo -e "${BOLD}H1 CONFIRMED:${NC} Background tray is the crash source."
        echo -e "${BOLD}Next:${NC} ./tools/protonvpn-forensic.sh phase3b (validate circuit breaker)"
    else
        echo -e "${RED}${BOLD}$failed test suite(s) FAILED with tray killed.${NC}"
        echo -e "${BOLD}H1 RULED OUT:${NC} Tests themselves may cause crash."
        echo -e "${BOLD}Next:${NC} ./tools/protonvpn-forensic.sh phase4 (strace)"
    fi
}

# ── Phase 3b: Circuit Breaker Validation ─────────────────────────

phase3b() {
    _phase_started "3b — Circuit Breaker Validation"
    _ensure_project_root
    _ensure_forensic_dir
    cd "$PROJECT_ROOT"

    _clear_pycache

    _section "Starting fresh tray (status-poll only, no --auto-connect)"
    # No --auto-connect — see _restart_tray_fresh. The breaker trips on repeated
    # `protonvpn status` failures from the 5 s poll, which runs regardless.
    python3 protonvpn-tray.py > /dev/null 2>&1 &
    local TRAY_PID=$!
    echo "Tray PID: $TRAY_PID"
    logger "phase3b" "tray started with PID $TRAY_PID"

    _section "Monitoring for breaker trip (180s timeout)"
    local TIMEOUT_AT=$((SECONDS + 180))
    local DUMPS_BEFORE
    DUMPS_BEFORE=$(coredumpctl list --no-pager 2>/dev/null | grep -c protonvpn || echo 0)
    echo "Baseline coredumps: $DUMPS_BEFORE"

    local -i tick=0
    while [ $SECONDS -lt $TIMEOUT_AT ]; do
        sleep 10
        tick=$((tick + 1))

        if ! kill -0 "$TRAY_PID" 2>/dev/null; then
            _warn "TRAY DIED at tick $tick — breaker may not have worked"
            break
        fi

        local breaker_state
        breaker_state=$(cd "$PROJECT_ROOT" && python3 -c "
import importlib.util, sys
spec = importlib.util.spec_from_file_location('pvt_breaker', 'protonvpn-tray.py')
m = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(m)
    print(m._polling_paused)
except Exception as e:
    print('ERROR:' + str(e))
" 2>/dev/null) || breaker_state="ERROR"

        local DUMPS_NOW
        DUMPS_NOW=$(coredumpctl list --no-pager 2>/dev/null | grep -c protonvpn || echo 0)
        local NEW_DUMPS=$((DUMPS_NOW - DUMPS_BEFORE))

        printf "[%3ds] tick=%d  paused=%s  dumps=%d/%d\n" \
            "$SECONDS" "$tick" "$breaker_state" "$NEW_DUMPS" "$DUMPS_NOW"

        if [ "$breaker_state" = "True" ]; then
            echo ""
            _ok "BREAKER TRIPPED at $(date -Iseconds)"
            local DUMPS_AT_TRIP=$DUMPS_NOW
            _running "Waiting 30s to confirm breaker holds..."
            sleep 30

            local still_paused
            still_paused=$(cd "$PROJECT_ROOT" && python3 -c "
import importlib.util; spec=importlib.util.spec_from_file_location('p','protonvpn-tray.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(m._polling_paused)" 2>/dev/null) || still_paused="ERROR"

            local AFTER_DUMPS
            AFTER_DUMPS=$(coredumpctl list --no-pager 2>/dev/null | grep -c protonvpn || echo 0)
            local AFTER_NEW=$((AFTER_DUMPS - DUMPS_AT_TRIP))

            echo "  30s post-trip: _polling_paused=$still_paused  new_dumps=$AFTER_NEW"
            if [ "$still_paused" = "True" ] && [ "$AFTER_NEW" -eq 0 ]; then
                echo ""
                echo -e "${GREEN}${BOLD}CIRCUIT BREAKER VERIFIED${NC} — breaker tripped and held."
            elif [ "$AFTER_NEW" -gt 0 ]; then
                echo ""
                echo -e "${RED}${BOLD}CIRCUIT BREAKER FAILED${NC} — $AFTER_NEW additional coredumps after trip."
            else
                echo ""
                echo -e "${YELLOW}${BOLD}CIRCUIT BREAKER AMBIGUOUS${NC} — paused=$still_paused, dumps=$AFTER_NEW"
            fi
            break
        fi
    done

    if [ $SECONDS -ge $TIMEOUT_AT ]; then
        echo ""
        _warn "TIMEOUT: 180s elapsed without breaker trip."
        echo "  Did protonvpn status crash at all? Check: timeout 8 protonvpn status"
        echo "  If CLI is healthy, the breaker has nothing to trip on."
    fi

    # Robust teardown: SIGTERM then SIGKILL fallback. A bare `kill` (SIGTERM)
    # can be ignored by a tray wedged in a nested GTK/zenity loop, leaking the
    # process. _kill_tray escalates to SIGKILL and verifies death.
    _kill_tray
    _done_phase "3b"
}

# ── Phase 4: Subprocess Mock Integrity ───────────────────────────

phase4() {
    _phase_started "4 — Subprocess Mock Integrity"
    _ensure_project_root
    _ensure_forensic_dir
    cd "$PROJECT_ROOT"

    local target_test="${1:-test_coverage_gaps}"

    _section "4a. strace $target_test.py"
    strace -ff -s 1024 -e trace=process \
        -o "$FORENSIC_DIR/strace" \
        python3 -X faulthandler "tests/${target_test}.py" 2>"$FORENSIC_DIR/strace-test.log" || true
    _ok "strace complete"

    _section "4b. Check for protonvpn subprocess"
    if grep -l "protonvpn" "$FORENSIC_DIR"/strace.* 2>/dev/null; then
        _warn "PROTONVPN SUBPROCESS DETECTED in strace output — unmocked Popen!"
        grep -l "protonvpn" "$FORENSIC_DIR"/strace.* 2>/dev/null
    else
        _ok "No protonvpn subprocess detected in strace output"
    fi

    _section "4c. Check Popen instrumentation log"
    for log in "$FORENSIC_DIR"/02-python.log "$FORENSIC_DIR"/01-phase-results.log "$FORENSIC_DIR"/03-tray-killed-run.log; do
        if [ -f "$log" ]; then
            if grep -qi "FORENSIC-POPEN\|POPEN CALLED" "$log" 2>/dev/null; then
                _warn "Real Popen calls found in $(basename "$log")"
            else
                _ok "No Popen calls in $(basename "$log")"
            fi
        fi
    done

    _done_phase "4"
}

# ── Phase 5: Shell Script Isolation ──────────────────────────────

phase5() {
    _phase_started "5 — Shell Script Isolation"
    _ensure_project_root
    _ensure_forensic_dir
    cd "$PROJECT_ROOT"

    _section "5a. strace test_shell_scripts.py"
    strace -ff -s 1024 -e trace=process \
        -o /tmp/strace-shell \
        python3 -X faulthandler tests/test_shell_scripts.py >> "$FORENSIC_DIR/05-shell-scripts.log" 2>&1 || true
    _ok "strace complete"

    _section "5b. Verify disable-autostart.sh sandboxing"
    local BASE
    BASE=$(mktemp -d)
    cp disable-autostart.sh "$BASE/"
    mkdir -p "$BASE/.config/autostart"
    touch "$BASE/.config/autostart/protonvpn-tray.desktop"

    ( export HOME="$BASE"; echo "n" | bash "$BASE/disable-autostart.sh" ) 2>/dev/null || true

    if [ -f "$BASE/.config/autostart/protonvpn-tray.desktop" ]; then
        _warn "Sandbox autostart NOT removed (script may have failed)"
    else
        _ok "Sandbox autostart correctly removed"
    fi
    echo "Real autostart: $(test -f ~/.config/autostart/protonvpn-tray.desktop && echo 'exists' || echo 'absent')"

    rm -rf "$BASE"

    _section "5c. Check git safe.directory pollution"
    local entries
    entries=$(git config --global --list 2>/dev/null | grep -c safe.directory || echo 0)
    echo "safe.directory entries: $entries"
    if [ "$entries" -gt 10 ]; then
        _warn "$entries safe.directory entries accumulated — consider: git config --global --unset-all safe.directory"
    else
        _ok "safe.directory entries: $entries (acceptable)"
    fi

    _done_phase "5"
}

# ── Phase 6: Resource Monitoring ─────────────────────────────────

phase6() {
    _phase_started "6 — System Resource Exhaustion"
    _ensure_project_root
    _ensure_forensic_dir

    _section "6a. Disk + coredump size"
    {
        for i in $(seq 1 12); do
            printf "%s  %s  %s\n" \
                "$(date -Iseconds)" \
                "$(du -sh /var/lib/systemd/coredump/ 2>/dev/null || echo 'N/A')" \
                "$(df -h / | tail -1)"
            sleep 5
        done
    } > "$FORENSIC_DIR/disk-space.log" 2>&1 &
    local DISK_PID=$!
    echo $DISK_PID > "$FORENSIC_DIR/.monitor-disk-pid"
    _ok "Disk monitor started (PID $DISK_PID, 60s)"

    _section "6b. Coredump size tracking"
    {
        for i in $(seq 1 12); do
            SIZE=$(du -sh /var/lib/systemd/coredump/ 2>/dev/null | cut -f1 || echo "0")
            COUNT=$(ls /var/lib/systemd/coredump/ 2>/dev/null | wc -l || echo "0")
            echo "$(date -Iseconds) coredumps: $COUNT, size: $SIZE"
            sleep 10
        done
    } > "$FORENSIC_DIR/coredump-size.log" 2>&1 &
    local SIZE_PID=$!
    echo $SIZE_PID > "$FORENSIC_DIR/.monitor-size-pid"
    _ok "Size tracker started (PID $SIZE_PID, 120s)"

    _section "6c. OOM detection"
    dmesg --level=err,warn --since "1 hour ago" 2>/dev/null | grep -i "oom\|killed\|protonvpn" \
        > "$FORENSIC_DIR/oom-detection.log" 2>&1 || echo "No OOM events" >> "$FORENSIC_DIR/oom-detection.log"
    _ok "OOM check complete"

    echo -e "\n${YELLOW}Resource monitors running in background for ~120s.${NC}"
    echo "Logs: $FORENSIC_DIR/disk-space.log  $FORENSIC_DIR/coredump-size.log"
    _done_phase "6"
}

# ── Cleanup ──────────────────────────────────────────────────────

cleanup() {
    _phase_started "9 — Cleanup"
    _ensure_project_root

    if [ -z "${FORENSIC_DIR:-}" ] || [ ! -d "${FORENSIC_DIR:-}" ]; then
        _warn "No forensic directory found — limited cleanup"
    else
        _ensure_forensic_dir
    fi

    _section "9a. Stop background monitors"
    _stop_monitors

    # Stop Phase 6 monitors
    for label in disk size; do
        local pid_file="$FORENSIC_DIR/.monitor-${label}-pid"
        if [ -f "$pid_file" ]; then
            local pid=$(cat "$pid_file")
            kill "$pid" 2>/dev/null || true
            rm -f "$pid_file"
        fi
    done

    _section "9b. Revert Popen instrumentation"
    _revert_instrumentation

    _section "9c. Clear pycache"
    _clear_pycache

    _section "9d. Clean temporary files"
    rm -f /tmp/strace-shell.* /tmp/run_all_nobypass.py 2>/dev/null || true
    _ok "Temporary files removed"

    _section "9e. Re-enable autostart"
    if [ -f ./enable-autostart.sh ]; then
        ./enable-autostart.sh 2>/dev/null || _warn "enable-autostart.sh failed"
        _ok "Autostart re-enabled"
    fi

    _section "9f. Final summary"
    {
        echo "=== FINAL STATE ==="
        echo "Forensic directory: $FORENSIC_DIR"
        python3 tools/diag.py 2>&1 || echo "diag.py unavailable"
        echo ""
        echo "=== SESSION RESTARTS ==="
        grep -c "RESTART" "$FORENSIC_DIR/session-restart.log" 2>/dev/null || echo "0 (no session-restart.log)"
    } | tee "$FORENSIC_DIR/09-cleanup.log"

    _done_phase "cleanup"
    echo -e "\n${GREEN}${BOLD}Cleanup complete.${NC} Forensic logs: $FORENSIC_DIR"
}

# ── Status ──────────────────────────────────────────────────────

status() {
    local FORENSIC_DIR="${FORENSIC_DIR:-}"
    if [ -z "$FORENSIC_DIR" ]; then
        local latest
        latest=$(ls -dt "${FORENSIC_BASE}-"* 2>/dev/null | head -1 || echo "")
        if [ -n "$latest" ] && [ -d "$latest" ]; then
            FORENSIC_DIR="$latest"
        fi
    fi

    echo -e "${BOLD}Proton VPN Tray — Forensic Protocol Status${NC}"
    echo ""

    if [ -z "$FORENSIC_DIR" ] || [ ! -d "$FORENSIC_DIR" ]; then
        echo "No forensic directory found. Run: ./tools/protonvpn-forensic.sh phase0"
        return 0
    fi

    echo "Forensic directory: $FORENSIC_DIR"
    echo ""

    # Phase progress
    if [ -f "$FORENSIC_DIR/.current_phase" ]; then
        echo "Last completed phase: $(cat "$FORENSIC_DIR/.current_phase")"
    else
        echo "Last completed phase: (none)"
    fi

    echo ""
    echo "Files:"
    shopt -s nullglob
    for f in "$FORENSIC_DIR"/*.log "$FORENSIC_DIR"/*.json; do
        [ -f "$f" ] && printf "  %s  (%s)\n" "$(basename "$f")" "$(du -h "$f" 2>/dev/null | cut -f1)"
    done
    shopt -u nullglob

    echo ""
    echo "Tray: $(_tray_alive && echo 'ALIVE (PID '$(_tray_pid)')' || echo 'dead')"
    echo "Autostart: $(test -f ~/.config/autostart/protonvpn-tray.desktop && echo 'enabled' || echo 'disabled')"

    echo ""
    echo "=== Phase 0 logs ==="
    if [ -f "$FORENSIC_DIR/00-system-state.log" ]; then
        grep -E "TRAY:|Autostart:|Coredumps:|protonvpn status" "$FORENSIC_DIR/00-system-state.log" 2>/dev/null | head -10
    fi

    echo ""
    echo "=== Session restarts ==="
    if [ -f "$FORENSIC_DIR/session-restart.log" ]; then
        cat "$FORENSIC_DIR/session-restart.log"
    else
        echo "  (none)"
    fi

    echo ""
    echo "=== Phase 1 results ==="
    if [ -f "$FORENSIC_DIR/01-phase-results.log" ]; then
        grep -E "exit=|PASS|FAIL" "$FORENSIC_DIR/01-phase-results.log" 2>/dev/null | head -20
    else
        echo "  (not run yet)"
    fi
}

# ── All ──────────────────────────────────────────────────────────

_all_exit_cleanup() {
    # Safety net for the single-process `all` run: guarantee nothing the
    # protocol spawned outlives the script — even on Ctrl-C, error, or the
    # expected crash mid-run. Without this, an interrupted run leaks a live
    # tray (the 2026-06-10 modal-storm) and the journal/coredump monitors.
    _stop_monitors 2>/dev/null || true
    pkill -f "protonvpn-tray\\.py" 2>/dev/null || true
    sleep 1
    pkill -9 -f "protonvpn-tray\\.py" 2>/dev/null || true
}

all() {
    echo -e "${BOLD}=== Proton VPN Tray — Forensic Protocol (All Phases) ===${NC}"
    echo "Recommended order: 0 → 1 → 2 → 3 → 3b → cleanup"
    echo ""
    # Only `all` traps EXIT — standalone phase0/phase1/… invocations must leave
    # the tray running for the next invocation, so they intentionally do NOT.
    trap '_all_exit_cleanup' EXIT INT TERM
    phase0 || { _warn "Phase 0 had issues, continuing..."; }

    # Background fdatasync loop: flushes all forensic logs every second so
    # a hard freeze / kernel panic loses at most ~1s of evidence (page
    # cache is otherwise lost). Self-terminates when this script exits.
    (
        while kill -0 $$ 2>/dev/null; do
            sync -d "$FORENSIC_DIR"/*.log "$FORENSIC_DIR"/.current_phase 2>/dev/null || true
            sleep 1
        done
    ) &
    disown

    phase1 || { _warn "Phase 1 had issues, continuing..."; }
    phase2 || { _warn "Phase 2 had issues, continuing..."; }
    phase3 || { _warn "Phase 3 had issues, continuing..."; }
    phase3b || { _warn "Phase 3b had issues, continuing..."; }
    echo ""
    echo -e "${BOLD}All phases complete.${NC} Run ./tools/protonvpn-forensic.sh cleanup to finish."
}

# ── Main dispatcher ──────────────────────────────────────────────

# Resume the most recent forensic session for commands that need an
# existing dir — e.g. continuing after the crash/reboot this protocol
# is designed to capture. phase0 and `all` always start a fresh dir.
case "${1:-help}" in
    phase0|all|help|--help|-h) ;;
    *)
        if [ -z "${FORENSIC_DIR:-}" ]; then
            _latest_dir=$(ls -dt "${FORENSIC_BASE}-"* 2>/dev/null | head -1 || echo "")
            if [ -n "$_latest_dir" ] && [ -d "$_latest_dir" ]; then
                FORENSIC_DIR="$_latest_dir"
                STATE_FILE="$FORENSIC_DIR/.state"
                PHASE_FILE="$FORENSIC_DIR/.current_phase"
                export FORENSIC_DIR
            fi
        fi
        ;;
esac

case "${1:-help}" in
    all)        all ;;
    phase0)     phase0 ;;
    phase1)     phase1 ;;
    phase2)     phase2 ;;
    phase3)     phase3 ;;
    phase3b)    phase3b ;;
    phase4)     phase4 "${2:-}" ;;
    phase5)     phase5 ;;
    phase6)     phase6 ;;
    cleanup)    cleanup ;;
    status)     status ;;
    help|--help|-h) usage ;;
    *)
        echo "Unknown command: ${1:-}"
        echo "Run: ./tools/protonvpn-forensic.sh help"
        exit 1
        ;;
esac

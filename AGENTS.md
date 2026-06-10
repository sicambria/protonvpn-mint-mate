# AGENTS.md — Proton VPN Tray for Linux Mint MATE

This file helps developers and AI agents understand the project structure, architecture, performance-sensitive code paths, and known issues.

---

## 1. Project Purpose

**Proton VPN Tray** is a GTK3 system tray application that provides a graphical interface for managing Proton VPN connections on Linux Mint MATE (and any GTK3 desktop with Ayatana appindicator support). It wraps the official `protonvpn` CLI tool with a system tray menu.

**Key features:**
- System tray icon with connection status (connected/disconnected/not signed in)
- One-click connect/disconnect to fastest server
- Country selector with favorites (pinned to top)
- Secure Core, Tor, and P2P connection modes
- Full configuration management (NetShield, Kill Switch, VPN Accelerator, IPv6, Moderate NAT, Port Forwarding, Custom DNS, Crash Reports)
- Auto-connect on login to a preferred country
- Desktop notifications (libnotify) for connection state changes
- Connection info dialog (VPN IP, public IP, network diagnostics)

**License:** GPL v3.0

---

## 2. Directory Structure

```
protonvpn-mint-mate/
├── protonvpn-tray.py              # Main application (1216 lines)
├── config.json                    # Default configuration template (6 lines)
├── install.sh                     # System dependency installation (122 lines)
├── enable-autostart.sh            # Enable autostart helper (25 lines)
├── disable-autostart.sh           # Disable autostart helper (31 lines)
├── pre-commit-hook                # Git pre-commit secrets scanner (242 lines)
├── protonvpn-tray.desktop         # Desktop entry for autostart (10 lines)
├── AGENTS.md                      # This file
├── README.md                      # Project documentation (59 lines)
├── LICENSE                        # GPL v3 license (674 lines)
├── .coveragerc                    # Coverage configuration (13 lines)
├── .gitignore                     # Git ignore rules (13 lines)
├── .github/workflows/
│   └── ci.yml                     # GitHub Actions CI pipeline (56 lines)
├── tests/
│   ├── __init__.py                # Empty package init
│   ├── run_all.py                 # Test runner — imports all suites (17 lines)
│   ├── helpers.py                 # Shared test infrastructure (83 lines)
│   ├── test_tray.py               # Main test suite (~2412 lines)
│   ├── test_coverage_gaps.py      # Branch coverage gap tests (~1303 lines)
│   ├── test_gtk_integration.py    # Real GTK/Ayatana binding tests (241 lines)
│   └── test_shell_scripts.py      # Shell script tests (301 lines)
└── docs/errors/2026-06/
    ├── 20260609-code-review-bugs.md
    ├── 20260609-RCA-systemd-kill-cascade.md    # Critical: poll + SIGABRT crashed user session
    ├── 20260609-bug10-libnotify-selfkill-crash.md
    ├── 20260609-bug11-attributeerror-quitting-doublematch.md
    ├── 20260609-RCA-bug10-missed.md
    ├── 20260609-test-plan.md
    ├── 20260609-test-results.md
    └── 20260610-RCA-high-cpu-boliling-ocean.md  # RCA: 98% CPU from poll loop hang
```

---

## 3. All Python Source Files and Purposes

### `protonvpn-tray.py` (1216 lines) — Main application

The entire tray application in a single file. Architecture:

| Section | Lines | Purpose |
|---------|-------|---------|
| Imports & constants | 1–63 | Module imports, paths, timeouts, circuit breaker state |
| `run_cli()` | 65–128 | **Synchronous** subprocess runner with timeout, lock, circuit breaker |
| `run_cli_async()` | 131–136 | **Asynchronous** wrapper — spawns daemon thread, calls `GLib.idle_add` callback |
| `parse_status()` | 162–185 | Parses `protonvpn status` CLI output |
| `parse_countries()` | 188–206 | Parses `protonvpn countries list` output |
| `parse_config_list()` | 209–229 | Parses `protonvpn config list` output |
| `notify()` | 234–242 | Desktop notification helper |
| `_collect_network_details()` | 245–275 | Runs `ip addr`/`ip route` for diagnostics |
| `_show_text_dialog()` | 277–318 | Native GTK text dialog |
| `_raise_window()` | 321–333 | Tries `wmctrl` then `xdotool` to raise a window |
| `class ProtonVPNTray` | 336–1197 | Main application class |
| `__init__()` | 337–378 | PID file, config load, create indicator + menu, **registers 5s poll timer**, auto-connect |
| `_load_config()` / `_save_config()` | 380–413 | JSON config management |
| `_create_indicator()` | 415–428 | Ayatana/StatusIcon creation |
| `_build_menu()` | 431–544 | Builds the full tray menu tree |
| `_build_country_menu()` | 570–625 | Country submenu with favorites |
| `_build_settings_menu()` | 627–718 | Settings submenu with config toggles (may call CLI synchronously) |
| `_rebuild_all_submenus()` | 555–568 | Destroys and recreates country + settings submenus |
| `_poll_status()` | 770–844 | **5-second timer callback** — polls `protonvpn status` via async runner |
| `_run_vpn_action()` | 887–902 | Template method for connect/disconnect actions |
| Connection handlers | 911–960 | Fastest, Secure Core, Tor, P2P connect, disconnect |
| Zenity dialogs | 962–768 | Default country, manage favorites, custom DNS (zenity subprocess) |
| `_on_connection_info()` | 1058–1123 | Connection info dialog with VPN IP, public IP, `ip addr`/`ip route` |
| `_on_toggle_autostart()` | 1133–1155 | Enables/disables autostart via shell scripts |
| `_on_refresh()` | 1157–1164 | Refresh countries + config cache + trigger immediate poll |
| `_on_about()` | 1166–1190 | About dialog |
| `_on_quit()` / `_shutdown()` | 1192–1197 | Clean shutdown |
| `main()` | 1200–1216 | CLI argument parser, entry point |

### `tests/helpers.py` (83 lines)
Shared test infrastructure: `load_tray_module()` (mocks GI), `make_tray()` (creates bare instance), `check()`/`heading()`/`summary()`.

### `tests/test_tray.py` (~2412 lines)
Main test suite. Mocks `subprocess.Popen` throughout (never calls real protonvpn). Covers: all `parse_*` functions, `run_cli`, config, PID management, thread patterns, error paths, circuit breaker, signal death detection.

### `tests/test_coverage_gaps.py` (~1303 lines)
Branch coverage gap tests for all uncovered code paths. Validates edge cases: empty output, malformed input, timeout races, concurrent access.

### `tests/test_gtk_integration.py` (241 lines)
Real GTK/Ayatana/StatusIcon binding tests. Requires Xvfb to run. Safety-gated behind `PROTONVPN_TEST_LIVE=1` environment variable.

### `tests/test_shell_scripts.py` (301 lines)
Tests `enable-autostart.sh`, `disable-autostart.sh`, and `pre-commit-hook` via git plumbing.

---

## 4. Loops, Polling Mechanisms, Timers, and Background Threads

### 4a. STATUS POLL TIMER — CRITICAL CPU RISK (line 371)

```python
GLib.timeout_add_seconds(5, self._poll_status)
```

**Type:** Permanent recurring GLib timer (returns `True`).
**Interval:** ~5 seconds (actual may drift under load).
**Duration:** Entire lifetime of the tray process.
**What it does:**
1. Checks `_polling_paused` (circuit breaker) — skips if tripped
2. Checks `self.busy` or `self._polling` (concurrency guard) — skips if in-flight
3. Sets `self._polling = True`
4. Calls `run_cli_async(["status"], CMD_TIMEOUT_STATUS=5, callback)`
5. The callback (via `GLib.idle_add`) parses output, updates GTK widgets, sets `self._polling = False`

**Risk:** If `protonvpn status` hangs or crashes, every ~5 seconds a new subprocess + thread is created. In worst case, this spawns ~12 children/minute, each consuming 75–100% CPU until killed by timeout.

**Mitigations in place:**
- `_polling` flag prevents concurrent polls
- Circuit breaker (`_polling_paused`) after `_MAX_FAILURES=10` consecutive failures — **only catches crashes/timeouts, NOT hangs**
- Exponential backoff: after 3 failures, sleeps `min(0.5 * 2^(n-3), 30)` seconds
- Auto-recovery: successful manual action resets breaker

### 4b. DAEMON THREAD PER POLL (line 131–136)

```python
def run_cli_async(args, timeout, callback):
    def target():
        ret, out, err = run_cli(args, timeout)
        GLib.idle_add(lambda: callback(ret, out, err))
    t = threading.Thread(target=target, daemon=True)
    t.start()
```

**Type:** One daemon thread per async CLI call.
**Frequency:** Every poll tick (5s) + every user VPN action.
**Lifetime:** Each thread lives until `run_cli` returns (subprocess completes or times out).
**Churn:** ~720 threads/hour at 5s interval. Daemon threads don't accumulate (exited on completion).
**Risk:** Thread creation (`clone()`) has overhead visible in system CPU. At high spawn rates, ~42% of the tray's CPU is kernel time.

### 4c. COUNTRY LIST LOAD (line 372)

```python
GLib.timeout_add(500, self._load_countries_async)
```

**Type:** One-shot GLib timer (returns `False`).
**Interval:** 500ms after startup (once).
**What it does:** Spawns a daemon thread to call `protonvpn countries list` (45s timeout). Parses output into `self.countries_cache`.

### 4d. AUTO-CONNECT STARTUP (line 374–375)

```python
if self.auto_connect and self.config.get("auto_connect_on_start", True):
    GLib.timeout_add_seconds(4, self._auto_connect_startup)
```

**Type:** One-shot GLib timer.
**Interval:** 4 seconds after startup.
**What it does:** Triggers `_do_connect(["--country", country])`.

### 4e. SUBMENU REBUILD ON EVERY MENU SHOW (line 546–568)

```python
def _on_menu_show(self, widget):
    self._rebuild_all_submenus()
```

**Type:** GTK signal handler (user-triggered).
**What it does:** Destroys and recreates both country + settings submenus from scratch every time the tray menu opens. The settings submenu calls `run_cli(["config", "list"], ...)` **synchronously on the GTK main thread** unless a 5-second TTL cache is fresh (line 632).
**Risk:** Blocks the GTK main thread while `protonvpn config list` runs (up to 8s timeout). If the CLI hangs, the entire UI freezes.

### 4f. `_polling` FLOW

```
Timer fires (every ~5s)
  └─ _poll_status()
       ├─ Check _polling_paused? → skip
       ├─ Check self.busy or self._polling? → skip
       ├─ Set self._polling = True
       ├─ run_cli_async(["status"], 5, callback)
       │    └─ Daemon thread:
       │         ├─ _cli_lock.acquire()
       │         ├─ subprocess.Popen(["protonvpn", "status"])
       │         ├─ proc.communicate(timeout=5)  ← blocks here
       │         ├─ GLib.idle_add(callback)      ← on GTK main thread
       │         └─ _cli_lock.release()
       └─ Return True (keep timer alive)

Later (on GTK main thread):
  └─ callback(ret, out, err)
       ├─ self._polling = False
       ├─ parse_status(out)
       ├─ Update status_item label
       ├─ Update indicator icon
       └─ Notify on state change
```

---

## 5. Subprocess Calls

All subprocess calls in the application. Every call to `protonvpn` CLI runs as a Python subprocess.

### 5a. `protonvpn` CLI calls (via `run_cli`)

| Call | Timeout | Trigger | Frequency |
|------|---------|---------|-----------|
| `protonvpn status` | 5s | Timer `_poll_status` | Every ~5s |
| `protonvpn connect [args]` | 25s | User menu action | On demand |
| `protonvpn disconnect` | 10s | User menu action | On demand |
| `protonvpn countries list` | 45s | Startup `_load_countries_async` | Once at startup + on refresh |
| `protonvpn config list` | 8s | Menu show `_build_settings_menu` | Every menu open (cached 5s TTL) |
| `protonvpn config set ...` | 8s | User toggle in settings | On demand |
| `protonvpn info` | 15s | Connection info dialog | On demand |

**All calls** are serialized through `_cli_lock` (global `threading.Lock`, line 62). This prevents concurrent native library access but means a slow `status` call blocks ALL other protonvpn operations.

### 5b. External tool calls

| Call | Timeout | Purpose | Frequency |
|------|---------|---------|-----------|
| `zenity --list --radiolist` | 60s | Default country picker | On demand |
| `zenity --list --checklist` | 60s | Manage favorites | On demand |
| `zenity --entry` | 60s | Custom DNS input | On demand |
| `wmctrl -a <title>` | 10s | Raise zenity dialog to foreground | Per zenity dialog |
| `xdotool search ... windowactivate` | 10s | Fallback window raise | If `wmctrl` missing |
| `curl -s https://api.ipify.org` | 8s | Get public IP | Connection info dialog |
| `ip -4 addr show` | 5s | Show network interfaces | Connection info dialog |
| `ip route show` | 5s | Show routing table | Connection info dialog |
| `enable-autostart.sh` | 10s | Enable autostart | On demand |
| `disable-autostart.sh --quiet` | 10s | Disable autostart | On demand |

### 5c. Subprocess creation pattern

All CLI calls use `subprocess.Popen` with `start_new_session=True` (line 79):
```python
proc = subprocess.Popen(
    [PROTONVPN_BIN] + args,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    start_new_session=True,
)
```

On timeout, the process group is killed with `os.killpg(proc.pid, signal.SIGKILL)` (line 99).

---

## 6. Known Issues and TODOs

### Performance / CPU

| Issue | Location | Impact | Status |
|-------|----------|--------|--------|
| **Poll loop + CLI hang = 98% CPU** | `protonvpn-tray.py:371`, `:65-128` | Tray at 98–99% CPU, desktop lag, coredump flood | **Active** — see RCA `docs/errors/2026-06/20260610-RCA-high-cpu-boliling-ocean.md` |
| **Circuit breaker misses hangs** | `protonvpn-tray.py:58-91` | Hang loops are detected as `TimeoutExpired` → count increments → resets if any call succeeds | Needs `_consecutive_timeouts` counter |
| **`_cli_lock` serializes all CLI ops** | `protonvpn-tray.py:67` | A single hung `status` poll blocks connect/disconnect/config until timeout | By design (thread safety) but painful |
| **Settings menu blocks GTK main thread** | `protonvpn-tray.py:635` | UI freezes for up to 8s if `protonvpn config list` hangs | Cache TTL=5s mitigates partially |
| **Thread churn from async runner** | `protonvpn-tray.py:131-136` | ~720 threads/hour at 5s poll interval | Low impact individually, adds up over hours |
| **Submenu rebuild on every menu show** | `protonvpn-tray.py:555-568` | Redundant widget creation/destruction | Low impact; only on user interaction |

### Reliability

| Issue | Location | Impact | Status |
|-------|----------|--------|--------|
| **Stale `.pyc` cache masks code changes** | `__pycache__/` | Old code runs after git pull/edit | Fix: add `rm -rf __pycache__` to `install.sh` |
| **No persistent polling-paused indicator** | `protonvpn-tray.py:772-779` | User can't tell polling is stopped | Notification sent once; tray label updates but no persistent visual |
| **systemd-coredump rate-limit hides crashes** | systemd config | Only 5 coredumps/min persisted; circuit breaker counter may diverge from visible crashes | Cosmetic; breaker still works |
| **Zenity dialog blocks shutdown** | `protonvpn-tray.py:973-1010` | If zenity is open when SIGTERM arrives, shutdown delays until dialog closes | Documented limitation |

### Testing

| Issue | Location | Impact | Status |
|-------|----------|--------|--------|
| **Tests mock Popen — don't catch real CLI issues** | All test files | Tests pass even when `protonvpn status` crashes IRL | By design (test isolation) |
| **GTK tests require Xvfb** | `test_gtk_integration.py` | Can't run in plain CI without display server | Gated behind env var |
| **No hang/crash simulation tests** | Missing | Circuit breaker logic for hangs is untested | Gap |

### Upstream

| Issue | Reference | Impact | Status |
|-------|-----------|--------|--------|
| **`local_agent.abi3.so` SIGABRT** | Proton VPN CLI native library | CLI crashes on `status`, `connect`, `config` calls | Upstream bug; report to ProtonVPN |
| **`local_agent.abi3.so` deadlock** | Same library | CLI hangs indefinitely, consuming CPU | Upstream bug |

---

## 7. Configuration

Config file: `~/.config/protonvpn-tray/config.json`

```json
{
    "default_country": "CH",
    "auto_connect_on_start": true,
    "favorites": ["CH", "PA", "IS"],
    "last_connected": null
}
```

### Key constants (`protonvpn-tray.py:49-62`)

```python
CMD_TIMEOUT_STATUS = 5
CMD_TIMEOUT_CONNECT = 25
CMD_TIMEOUT_DISCONNECT = 10
CMD_TIMEOUT_COUNTRIES = 45
CMD_TIMEOUT_CONFIG = 8
CMD_TIMEOUT_INFO = 15
CMD_TIMEOUT_CONFIG_SET = 8
CONFIG_CACHE_TTL = 15
POLL_MIN_INTERVAL = 4          # minimum seconds between status polls
_MAX_CONSECUTIVE_TIMEOUTS = 3  # consecutive timeouts before breaker trip
_MAX_FAILURES = 10
```

---

## 8. Test Execution

```bash
# Run all tests
python3 tests/run_all.py

# Run individual test suites
python3 tests/test_tray.py
python3 tests/test_coverage_gaps.py
python3 tests/test_shell_scripts.py

# GTK tests (requires Xvfb display)
PROTONVPN_TEST_LIVE=1 python3 tests/test_gtk_integration.py
```

All tests mock `subprocess.Popen` — no real `protonvpn` binary is ever executed during testing.

---

## 9. Architecture Diagram (Data Flow)

```
User clicks tray icon
  └─ Menu shown → _on_menu_show()
       ├─ _rebuild_all_submenus()
       │    ├─ _build_country_menu()        [uses countries_cache]
       │    └─ _build_settings_menu()       [may call run_cli synchronously]
       └─ GTK renders menu

GLib timer (every 5s)
  └─ _poll_status()
       └─ run_cli_async(["status"], 5, callback)
            └─ Daemon thread
                 ├─ _cli_lock.acquire()
                 ├─ subprocess.Popen(["protonvpn", "status"])
                 ├─ proc.communicate(timeout=5)
                 ├─ GLib.idle_add(callback)
                 └─ _cli_lock.release()
                      └─ [GTK main thread] callback()
                           ├─ parse_status(out)
                           ├─ self.status_item.set_label(...)
                           └─ self.indicator.set_icon_full(...)

User clicks "Connect"
  └─ _run_vpn_action(["connect", ...], 25, ...)
       └─ run_cli_async(["connect", ...], 25, callback)
            └─ [same pattern as poll]
                 └─ callback() → GLib.idle_add(self._poll_status) → trigger immediate refresh
```

---

## 10. Signal Handling

```python
signal.signal(signal.SIGINT, self._on_signal)   # Keyboard interrupt
signal.signal(signal.SIGTERM, self._on_signal)  # systemd stop
```

Both signals call `self._shutdown()` which calls `Gtk.main_quit()`.

---

## Quick Reference for Debugging

| Symptom | Likely cause | Check |
|---------|-------------|-------|
| Tray at 98%+ CPU | Clock source spinning / `protonvpn status` hanging | `ps aux --sort=-%cpu \| head`, `/proc/<tray_pid>/stack` |
| Desktop session crashes ~6min after login | Poll loop → SIGABRT → coredump flood → systemd watchdog kill | `ls /var/lib/systemd/coredump/` |
| Settings menu unresponsive | `protonvpn config list` hanging | `strace -p <tray_pid>` |
| Coredumps accumulating | `protonvpn status` crashing in `local_agent.abi3.so` | `coredumpctl list` |
| Thread count growing | Daemon threads accumulating (blocked on lock) | `ls /proc/<tray_pid>/task/ \| wc -l` |

---

## Before Running Tests

1. Run the diagnostic: `python3 tools/diag.py`
2. If tray is running: `pkill -f protonvpn-tray.py`
3. If autostart is enabled: `./disable-autostart.sh`
4. Clear stale bytecode: `rm -rf __pycache__/`
5. Run tests: `PYTHONPATH=. python3 tests/run_all.py`

## After Verifying Fix

1. Confirm `protonvpn status` completes in <1s
2. Re-enable autostart: `./enable-autostart.sh`
3. Start tray manually: `python3 protonvpn-tray.py`
4. Watch CPU for 30s: `top -p $(pgrep -f protonvpn-tray.py)`

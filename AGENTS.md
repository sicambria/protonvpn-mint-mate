# AGENTS.md — Proton VPN Tray for Linux Mint MATE

Single-file GTK3 system tray app (`protonvpn-tray.py`, 1231 lines) that wraps the `protonvpn` CLI with an Ayatana tray menu. All CLI calls go through `run_cli()` → `subprocess.Popen` + `proc.communicate(timeout=...)` + `os.killpg` on timeout.

## Commands

```bash
# Install system deps (python3-gi, gir1.2-ayatanaappindicator3-0.1, zenity, etc.)
./install.sh

# Run manually
python3 protonvpn-tray.py

# Enable/disable autostart
./enable-autostart.sh        # creates ~/.config/autostart/protonvpn-tray.desktop
./disable-autostart.sh       # --quiet flag for non-interactive use
```

## Tests

**Precondition: `protonvpn-tray.py` must NOT be running.** All test runners check this via `pgrep` and exit 3 otherwise.

```bash
# Clear stale .pyc first — masks source edits after git pull
rm -rf __pycache__/

# Run all suites in one coverage session (required for 100% branch coverage)
PYTHONPATH=. python3 -m coverage run --branch --source=. tests/run_all.py
python3 -m coverage report -m --fail-under=100

# Individual suites (custom test framework, no unittest/pytest)
python3 tests/test_tray.py              # 2413 lines, core feature coverage
python3 tests/test_coverage_gaps.py      # 1303 lines, branch coverage edge cases
python3 tests/test_high_cpu_analysis.py  # 955 lines, circuit breaker + hang analysis
python3 tests/test_shell_scripts.py      # 301 lines, shell scripts via git plumbing

# GTK integration tests (REQUIRES xvfb + PROTONVPN_TEST_LIVE=1)
PROTONVPN_TEST_LIVE=1 xvfb-run python3 tests/test_gtk_integration.py
```

All test files mock `subprocess.Popen` — no real `protonvpn` binary is ever called. Tests use a custom `check()`/`heading()`/`summary()` framework (see `tests/helpers.py`). No test functions use `test_` prefix.

CI (`.github/workflows/ci.yml`): runs coverage session, shell script tests, GTK tests under `xvfb-run`, generates HTML report.

## Architecture

- **Entrypoint**: `ProtonVPNTray.__init__()` at line 347
- **Poll timer**: `GLib.timeout_add_seconds(5, self._poll_status)` at line 382 — permanent recurring timer. Calls `run_cli_async(["status"], 5, callback)` which spawns a daemon thread per tick.
- **Circuit breaker** (lines 60–64, module-level globals): `_failure_count`, `_consecutive_timeouts`, `_polling_paused`. After 10 failures or 3 consecutive timeouts, polling pauses.
- **Serial lock**: `_cli_lock` (line 65) — all CLI calls serialized. A single hung `status` blocks connect/disconnect/config.
- **Config**: `~/.config/protonvpn-tray/config.json` — `{"default_country", "auto_connect_on_start", "favorites", "last_connected"}`.
- **PID file**: `~/.config/protonvpn-tray/tray.pid` — prevents multiple instances (exit 2).
- **Autostart detection**: `is_autostart_enabled()` reads `~/.config/autostart/protonvpn-tray.desktop` for `X-GNOME-Autostart-enabled=true`.
- **Indicator**: tries AyatanaAppIndicator3 first, falls back to Gtk.StatusIcon.
- **Menu**: `_on_menu_show` (line 557) destroys and rebuilds country + settings submenus *every time the tray opens*. Settings submenu calls `protonvpn config list` synchronously on the GTK main thread (5s cache TTL).

## Known Gotchas

- **Poll hangs = 98% CPU**: If `protonvpn status` hangs, every 5s a new subprocess spawns. Circuit breaker only catches *timeouts/crashes*, not CPU-spinning CLI processes. If you see this, check `/proc/<tray_pid>/stack` and `coredumpctl list`.
- **Stale `.pyc`**: After `git pull` or editing, `__pycache__/` may contain old bytecode. Always `rm -rf __pycache__/` before testing.
- **Zenity blocks shutdown**: If a zenity dialog is open when SIGTERM arrives, shutdown delays until it closes.
- **`notify()` is a no-op until `Notify.init()`**: `_notify_initialized` flag guards it — `notify()` called before `__init__` completes does nothing.
- **Subprocess timeout pattern**: On `TimeoutExpired`, kills process group with `SIGKILL` (`os.killpg`). On `returncode < 0` (SIGABRT from `local_agent.abi3.so`), increments failure count.

## Key Source Structure

| Section | Line | Purpose |
|---------|------|---------|
| Constants + circuit breaker state | 49–65 | Timeouts (status=5s, connect=25s, countries=45s), `_MAX_FAILURES=10`, `_MAX_CONSECUTIVE_TIMEOUTS=3` |
| `run_cli()` | 68–138 | Synchronous subprocess runner with lock, timeout, circuit breaker, backoff |
| `run_cli_async()` | 141–146 | Daemon thread + `GLib.idle_add` callback |
| `parse_status/countries/config_list` | 172–239 | CLI output parsers |
| `notify()` | 244–252 | libnotify (guarded by `_notify_initialized`) |
| `ProtonVPNTray.__init__` | 347–389 | PID check, indicator, menu, poll timer, auto-connect, signal handlers |
| `_poll_status()` | 781–855 | 5s timer callback — rate-limited, guarded by `_polling` flag + circuit breaker |
| Connection handlers | 898–970 | Fastest/Secure Core/Tor/P2P connect, disconnect |
| `_on_quit()` / `_shutdown()` | 1207–1212, 439–448 | Clean shutdown — `_quitting=True`, PID removal, Notify.uninit, Gtk.main_quit |

## Documentation

- `docs/errors/2026-06/` — RCAs for known bugs (poll loop hang, systemd kill cascade, libnotify crash, AttributeError)
- `plans/20260610-fix-high-cpu-boliling-ocean.md` — fix plan for 98% CPU issue
- `tools/diag.py` — read-only health check (tray running, autostart, stale .pyc, coredumps, CLI latency)
- `tools/protonvpn-forensic.sh` — test isolation protocol (phases 0–6)

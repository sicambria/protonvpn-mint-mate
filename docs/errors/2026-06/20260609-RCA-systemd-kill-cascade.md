# RCA: systemd Kill Cascade — protonvpn status crashes nuke user session

**Date:** 2026-06-09
**Severity:** Critical (full user session killed, dropped to LightDM greeter)
**Incident:** Desktop session terminated ~6 minutes after login with protonvpn-tray autostart enabled

---

## Symptom

```
user@1000.service: Main process exited, code=killed, status=9/KILL
```

systemd PID 1 killed the user's systemd instance (`user@1000.service`), which nuked all user processes: Xorg cleanly shut down, session ended, user dropped to the LightDM greeter login screen.

---

## Root Cause Chain

### 1. Entry point: Autostart on login

`~/.config/autostart/protonvpn-tray.desktop` launches `protonvpn-tray.py --auto-connect` at every login.

`protonvpn-tray.py:353` schedules a status poll every 5 seconds:
```python
GLib.timeout_add_seconds(5, self._poll_status)
```

### 2. Poll loop: `protonvpn status` crashes every call

`_poll_status()` calls `run_cli_async(["status"], ...)` → `run_cli(["status"])` → `subprocess.Popen(["protonvpn", "status"])`.

`protonvpn status` crashes with **SIGABRT** inside `local_agent.abi3.so` on every invocation. The crash is a native library abort — unrecoverable from the CLI perspective.

### 3. Coredump flood

Each crash produces a ~15.8 MB coredump. In the incident being analyzed, **34 coredumps** were generated in a single boot session, totaling **~622 MB** of coredump data written to disk within ~6 minutes.

### 4. systemd user manager overwhelmed

The repeated process spawn→crash→coredump→reap→spawn cycle floods the systemd user manager with process lifecycle events. Each crash triggers:
- `systemd-coredump` to handle the core dump
- D-Bus signals for the process exit
- Journal logging for each event

At 34 crashes in 6 minutes, the user manager becomes unresponsive to `systemctl --user` commands.

### 5. PID 1 kills the user manager

When the user manager (`user@1000.service`) becomes unresponsive beyond its watchdog timeout, PID 1 (the system systemd instance) sends it **SIGKILL (status=9/KILL)**. This is the systemd watchdog mechanism — if a service manager doesn't respond to pings, it's assumed dead and forcibly terminated.

### 6. All user processes die

When the user manager is killed, it takes down the entire user cgroup: Xorg, the desktop environment, all applications — everything running under that user. The session ends and the user is dropped back to the LightDM greeter.

---

## Why Existing Mitigations Weren't Enough

Three defenses were already in place, plus the circuit breaker had a critical bug:

| Defense | Mechanism | Why it helped | Why it wasn't enough |
|---------|-----------|---------------|---------------------|
| Global lock (`_cli_lock`) | Serializes all `protonvpn` calls | Prevents concurrent native library access (thread safety) | Only prevents concurrent crashes, not sequential ones |
| Exponential backoff | `_failure_count > 3` → sleep up to 30s | Slows the crash rate from 12/min to 2/min | Never stops — just slows down |
| SIGKILL via `os.killpg` | Kills process group on timeout/error | Cleaner process cleanup | The CLI still crashes with SIGABRT before the kill can fire |
| Circuit breaker (v1) | `_polling_paused` after 10 failures | Should have stopped polling | **Didn't fire** — see next section |

### The critical bug: SIGABRT looks like success to Python

`proc.communicate()` does **not** raise an exception when the child process is killed by a signal. The child dies, pipes close, Python returns normally with `proc.returncode = -6` (negative of SIGABRT). No `Exception`, no `TimeoutExpired` — just a normal return with a negative code.

The v1 circuit breaker only incremented `_failure_count` inside exception handlers (`except TimeoutExpired`, `except FileNotFoundError`, `except Exception`). A SIGABRT crash hit none of these — it hit the success path which reset `_failure_count = 0`. The counter never reached 10. The breaker never fired.

**48 crashes × 16 MB coredumps = 812 MB** were written across two boots before the bug was found.

---

## Fix

### 1. Signal death detection (critical)

The success path in `run_cli` must check `proc.returncode < 0` before resetting the failure counter:

```python
out, err = proc.communicate(timeout=timeout)
if proc.returncode < 0:                          # ← added
    _failure_count += 1                           # ← added
    if _failure_count >= _MAX_FAILURES and not _polling_paused:
        _polling_paused = True                    # ← added
    log.warning("protonvpn %s killed by signal %d (failure #%d)",
                " ".join(args), -proc.returncode, _failure_count)
    return proc.returncode, out.strip(), err.strip()
_failure_count = 0                                # ← was unconditional
_polling_paused = False
_polling_paused_notified = False
return proc.returncode, out.strip(), err.strip()
```

### 2. Circuit breaker

A module-level flag `_polling_paused` acts as a hard stop. When `_failure_count` reaches `_MAX_FAILURES` (10), all status polling ceases.

### 3. Poll guard

`_poll_status()` checks `_polling_paused` before calling `run_cli_async`:

```python
def _poll_status(self):
    if _polling_paused:
        return True  # keep timer alive, but don't poll
    ...
```

### 4. Auto-recovery

If the user fixes the CLI issue and a manual VPN action (connect, disconnect, config) succeeds, `_failure_count` resets to 0 and `_polling_paused` is cleared. The next 5-second timer tick resumes normal polling.

### 5. User notification

A libnotify notification is sent once when polling is paused:
> "Proton VPN CLI is not responding. Status polling paused until next manual action succeeds."

### 6. Stale `.pyc` cache (operational)

Python caches compiled bytecode in `__pycache__/protonvpn-tray.cpython-312.pyc`. If the `.py` source mtime matches the `.pyc` embedded mtime, Python uses the cache without recompiling. After a source edit, the `.pyc` must be deleted or recompiled — otherwise the old bytecode runs even after `git commit`. The `install.sh` should be updated to clear the cache on install; for now, manual deletion suffices.

---

## Timeline (actual, from coredump timestamps)

| Time (CEST) | Event |
|-------------|-------|
| **Boot 1 (June 9)** | |
| 23:48 | Login, tray starts with old code (no circuit breaker) |
| 23:48–23:59 | ~15 crashes, 16 MB coredumps each |
| ~23:59 | systemd user manager becomes unresponsive, PID 1 sends SIGKILL |
| 23:59 | User logged out, drops to LightDM greeter |
| **Boot 2 (June 10)** | |
| 00:07 | Login, tray starts with v1 circuit breaker code |
| 00:07–00:10 | Circuit breaker never fires — SIGABRT hits success path, resets `_failure_count = 0` every time |
| 00:10+ | ~33 more crashes, 48 total across both boots |
| 00:10 | **User session alive because I inspect the system during this session** |
| (ongoing) | Circuit breaker code fixed with `proc.returncode < 0` check |

**Totals: 48 coredumps, ~812 MB written to `/var/lib/systemd/coredump/`**

---

## Lessons Learned

1. **`proc.communicate()` does not raise on child signal death.** A process killed by SIGABRT/SIGSEGV/SIGKILL looks like a normal exit with a negative return code. Always check `proc.returncode < 0` if the child might crash.
2. **Exception-based error handling misses signal deaths.** Writing `try: ... except Exception:` gives false confidence — the crash hit neither the try nor any except.
3. **Coredumps are cheap individually but expensive in aggregate.** 48 × 16 MB = 812 MB filled the coredump directory.
4. **Python `.pyc` caching can mask code changes.** After editing source, delete `__pycache__/*.pyc` to guarantee the new code runs.
5. **Poll loops must have a death condition.** A 5-second `GLib.timeout_add_seconds` returning `True` forever is a guaranteed infinite retry loop.
6. **Process crash storms can cascade to the init system.** systemd's user manager watchdog kills the entire user session — not just the offending service.
7. **The circuit breaker should be automatic, not config-based.** No user should need to know to configure a failure limit.

---

## Files Changed

- `protonvpn-tray.py` — Added `proc.returncode < 0` check, `_polling_paused` circuit breaker, `_MAX_FAILURES` constant, poll guard, auto-recovery on manual action success, user notification
- `pre-commit-hook` — Added `_is_safe_ip()` function covering RFC 1918/5737/6598 ranges; re-synced stale installed hook with `tests/*` skip rule
- `tests/test_gtk_integration.py` — Safety gate: requires `PROTONVPN_TEST_LIVE=1` env var to run
- `docs/errors/2026-06/20260609-RCA-systemd-kill-cascade.md` — This document

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

Three defenses were already in place:

| Defense | Mechanism | Why it helped | Why it wasn't enough |
|---------|-----------|---------------|---------------------|
| Global lock (`_cli_lock`) | Serializes all `protonvpn` calls | Prevents concurrent native library access (thread safety) | Only prevents concurrent crashes, not sequential ones |
| Exponential backoff | `_failure_count > 3` → sleep up to 30s | Slows the crash rate from 12/min to 2/min | Never stops — just slows down |
| SIGKILL via `os.killpg` | Kills process group on timeout/error | Cleaner process cleanup | The CLI still crashes with SIGABRT before the kill can fire |

**The fatal gap**: There was no **circuit breaker** — no mechanism to permanently stop polling when the CLI is fundamentally broken. The backoff reduced frequency but never stopped. With `_poll_status` returning `True` every cycle, the GLib timer kept firing indefinitely.

---

## Fix

### Circuit breaker

A new module-level flag `_polling_paused` acts as a hard stop. When `_failure_count` reaches `MAX_FAILURES` (10), all status polling ceases:

```python
_MAX_FAILURES = 10
_polling_paused = False

# In run_cli, after incrementing _failure_count:
if _failure_count >= _MAX_FAILURES and not _polling_paused:
    _polling_paused = True
    log.critical("protonvpn CLI failing repeatedly — polling paused")
```

### Poll guard

`_poll_status()` checks `_polling_paused` before calling `run_cli_async`:

```python
def _poll_status(self):
    if global_state._polling_paused:
        return True  # keep timer alive, but don't poll
    ...
```

### Auto-recovery

If the user fixes the CLI issue and a manual VPN action (connect, disconnect, config) succeeds, `_failure_count` resets to 0 and `_polling_paused` is cleared. The next 5-second timer tick resumes normal polling.

```python
# In run_cli, on success:
_failure_count = 0
_polling_paused = False
```

### User notification

A libnotify notification is sent once when polling is paused:
> "Proton VPN CLI is not responding. Status polling paused until next manual action succeeds."

---

## Timeline (approximate, reconstructed)

| T+0s | Login, `protonvpn-tray.py --auto-connect` starts |
| T+5s | First status poll → `protonvpn status` crashes (SIGABRT in local_agent.abi3.so) |
| T+10s | Second poll → crash |
| T+15s | Third poll → crash |
| T+20s | Fourth poll → crash, backoff begins (1s delay) |
| T+26s–T+360s | ~30 more crashes with exponential backoff (capped at 30s delay), ~34 coredumps written |
| T+360s | systemd user manager becomes unresponsive, PID 1 sends SIGKILL |
| T+360s | Xorg dies, session ends, LightDM greeter appears |

---

## Lessons Learned

1. **Exponential backoff without a ceiling is a delay, not a defense.** A hard stop (circuit breaker) is required when a subsystem is fundamentally broken.
2. **Poll loops must have a death condition.** A 5-second `GLib.timeout_add_seconds` returning `True` forever is a guaranteed infinite retry loop.
3. **Process crash storms can cascade to the init system.** systemd's user manager watchdog is a safety net, but triggering it means the user session is already doomed.
4. **Coredumps are cheap individually but expensive in aggregate.** 34 × 15.8 MB = 622 MB in 6 minutes can overwhelm I/O on slower disks.
5. **The circuit breaker should be automatic, not config-based.** This should have been the default behavior from day one — no user should need to know to configure a failure limit.

---

## Files Changed

- `protonvpn-tray.py` — Added `_polling_paused` circuit breaker, `_MAX_FAILURES` constant, poll guard, auto-recovery on manual action success, user notification

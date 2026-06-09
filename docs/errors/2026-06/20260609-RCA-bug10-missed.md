# RCA: Bug 10 — libnotify crash on autostart toggle (self-kill race)

**Date:** 2026-06-09
**Incident:** `libnotify:ERROR:notification.c:notify_notification_show: code should not be reached` → core dump
**Root cause commit:** `ac81d4f init`
**Fix commit:** `9f3d814`

---

## Why was this bug missed during implementation and code review?

### 1. Blind spot: signal handler + subprocess interaction

The code review focused on:
- **Static correctness**: syntax, parsing logic, GTK API usage, thread safety
- **Data races**: PID file, config file, concurrent menu actions
- **Lifecycle**: startup, normal shutdown, kill -9 recovery

What was **not** explicitly validated:
- **Code that executes after a signal handler interrupts `subprocess.run`**
- **A subprocess that kills its own parent process**

The signal handler `_on_signal()` must run *during* `subprocess.run` because POSIX signals interrupt the current system call. When `_shutdown()` calls `Notify.uninit()` and `Gtk.main_quit()`, execution then *resumes* at the line after `subprocess.run`. Any code on those subsequent lines runs in a half-shutdown state.

This is a non-obvious execution flow that standard "read the code" review does not catch because the human mind reads code sequentially — not considering signal interleaving.

### 2. Why the test suite didn't catch it

| Test layer | Why it missed this |
|------------|-------------------|
| Unit tests (parsing) | Pure functions, no signal handling |
| CLI settings tests | Subprocess to protonvpn, not self-kill scripts |
| Tray lifecycle tests | Tested signal handling, but never tested "disable autostart → kill tray → continue executing" |
| Shell script tests | Tested standalone, not via tray's subprocess |

The specific scenario — "tray calls script that kills tray, then continues executing" — was never explicitly enumerated in the test plan. The test plan covered signal handling (SIGTERM cleanup) but not the *combination* of subprocess self-kill + post-subprocess code.

### 3. Root cause of the blind spot

The mental model was: "the script kills the tray → tray is gone → nothing else happens." This is incorrect because the signal handler runs and returns, and `subprocess.run` returns normally (the script exited after killing). Execution continues to the next line.

---

## What other errors of the same class could occur?

### Class: "Late operations after shutdown initiation"

Any code path where:
1. A shutdown/signal handler runs during a blocking operation
2. Shutdown tears down a resource (Notify, GTK objects)
3. Code after the blocking operation uses the torn-down resource

### Deep scan results — ranked by risk:

| # | Location | Pattern | Risk | Mitigation |
|---|----------|---------|------|------------|
| 1 | `_on_toggle_autostart` L860 | `subprocess.run(disable_script)` → then `notify()` | **Critical** — FIXED (Bug 10): removed `input="y\n"`, added `--quiet` flag | ✅ |
| 2 | `_on_quit` L890 | `notify()` → then `_shutdown()` → `Notify.uninit()` | **Safe**: notify runs before uninit | N/A |
| 3 | `_show_info_dialog` L846 | `subprocess.run(zenity)` blocks → signal handler runs during → then returns | **Low**: zenity is a UI dialog, not shutdown-related | `_quitting` guard (added now) |
| 4 | `_on_default_country` L789 | `subprocess.run(zenity)` → then `notify()` | **Low**: `_notify_initialized` guard suppresses if shutdown occurred during zenity | Guard exists |
| 5 | `_on_manage_favorites` L822 | `subprocess.run(zenity)` → then `notify()` | **Low**: same as above | Guard exists |
| 6 | All `GLib.idle_add` callbacks | Queued before quit → execute during shutdown loop | **Low**: GTK widgets persist after quit (not destroyed) | `_quitting` guard (added now) |
| 7 | All `run_cli_async` callbacks | Thread completes after shutdown → idle_add queues UI update | **Low**: `_quitting` guard returns early | Guard exists |

### Defense-in-depth added:

1. **`_notify_initialized` flag** — `notify()` is a no-op if Notify is not initialized (protects all 17 call sites)
2. **`_quitting` flag** — all async callbacks that touch UI return immediately if shutdown has started (protects 5 callback chains)
3. **`--quiet` flag** — `disable-autostart.sh` no longer kills the tray when called from the tray itself (eliminates the self-kill path)

### Remaining known risk:

**Zenity dialog open during system shutdown**: If the user has a zenity dialog open (Connection Info, Default Country, etc.) and the system sends SIGTERM, the tray's shutdown is delayed until the zenity dialog closes (because `subprocess.run` blocks the main thread). This is acceptable — the tray survives until the dialog closes, then exits. No crash, just delayed shutdown. Documented as known behavior.

---

## Lessons learned

1. **Never trust `input=` to a subprocess that can kill the parent** — the parent continues executing after `subprocess.run` returns
2. **All code after any `subprocess.run` must be safe to execute after a signal handler has torn down resources** — or add a post-shutdown guard
3. **Resource teardown should use a flag set-before-destroy pattern** — `_notify_initialized = False` before `Notify.uninit()` prevents all late access
4. **All async callbacks touching UI need a `_quitting` check** — idle callbacks queued before `Gtk.main_quit()` still execute
5. **Test plans must explicitly enumerate "subprocess kills parent" scenarios** — this is a non-obvious execution flow that code review alone won't catch

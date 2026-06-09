# Bug 10: libnotify crash on autostart toggle — self-kill race

**Date:** 2026-06-09
**Severity:** Critical (core dump)
**Discovered:** Manual testing — toggle autostart enable/disable multiple times

## Symptom

```
libnotify-WARNING: you must call notify_init() before showing
libnotify:ERROR:notification.c:notify_notification_show: code should not be reached
Bail out! Aborted (core dumped)
```

## Root Cause

The kill chain when the user clicks "Autostart (Enabled)" to disable:

1. `_on_toggle_autostart()` calls `subprocess.run([DISABLE_AUTOSTART_SH], input="y\n")` (line 853)
2. The disable script reads `"y"` from stdin → executes `kill $TRAY_PID`
3. **SIGTERM is delivered to the tray process *during* `subprocess.run`**
4. The signal handler `_on_signal()` runs → calls `_shutdown()` → calls `Notify.uninit()`
5. `Gtk.main_quit()` marks the main loop for exit, but `subprocess.run` returns control
6. Back in `_on_toggle_autostart`, line 860 executes: `notify("Autostart", "Disabled")`
7. `Notify.Notification.new()` is called **after** `Notify.uninit()` → **CRASH**

The same bug would crash on any notify() call after shutdown:
- `_on_quit` → `notify()` → `_shutdown()` → `Notify.uninit()` — fine (notify runs first)
- But any async callback that runs after `_shutdown()` would crash

## Fix (3-part)

### 1. Defensive guard in `notify()`
Module-level `_notify_initialized` flag. Set `True` after `Notify.init()`, set `False` before `Notify.uninit()` in `_shutdown()`. The `notify()` function silently returns if the flag is `False`. This prevents *all* future crashes of this class, regardless of code path.

### 2. Remove self-kill path from tray
`_on_toggle_autostart()` now calls `disable-autostart.sh --quiet` instead of sending `input="y\n"`. The `--quiet` flag in `disable-autostart.sh` skips the interactive "kill tray?" prompt. The tray process never kills itself through its own subprocess.

### 3. `disable-autostart.sh` `--quiet` flag
Added `--quiet` argument. When passed, the script only removes the `.desktop` file and skips the interactive kill prompt. Terminal users still get the interactive prompt when running without `--quiet`.

## Files changed
- `protonvpn-tray.py` — `notify()` guard, `_notify_initialized` flag, autostart toggle uses `--quiet`
- `disable-autostart.sh` — added `--quiet` mode

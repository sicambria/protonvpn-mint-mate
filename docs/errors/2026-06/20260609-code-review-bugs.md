# Code Review Bugs — 2026-06-09

Validation pass on `protonvpn-tray.py` after initial implementation.
All bugs fixed in the same session.

---

## Bug 1: Status polling blocked GTK main loop

**Severity:** High
**File:** `protonvpn-tray.py:558`
**Symptom:** Tray UI freezes for up to 5 seconds when the Proton VPN daemon is unresponsive.

**Root cause:** `_poll_status()` called `subprocess.run(["protonvpn", "status"], timeout=5)` synchronously on the GTK main thread via `GLib.timeout_add_seconds`. Even though the command normally returns in <200ms, the 5-second timeout was a blocking `subprocess.run` call on the main event loop thread.

**Fix:** Converted `_poll_status` to use `run_cli_async` (daemon thread + `GLib.idle_add` callback). Added `self._polling` guard to prevent overlapping poll requests. The status update logic now runs in a `GLib.idle_add` callback on the main thread after the async CLI call completes.

---

## Bug 2: Settings submenu blocked GTK main loop

**Severity:** High
**File:** `protonvpn-tray.py:447`
**Symptom:** Settings submenu appears blank/hung for up to 8 seconds when opened.

**Root cause:** `_rebuild_settings_submenu()` called `subprocess.run(["protonvpn", "config", "list"], timeout=8)` synchronously on the GTK main thread. The "map" signal handler blocked until the CLI call returned, so the submenu didn't render until data arrived.

**Fix:** Split into two methods:
1. `_rebuild_settings_submenu()` — clears children, shows "Loading settings...", starts async CLI call
2. `_rebuild_settings_submenu_done()` — called via `GLib.idle_add` when data arrives, rebuilds the full settings menu

---

## Bug 3: Connection Info blocked GTK main loop

**Severity:** High
**File:** `protonvpn-tray.py:795`
**Symptom:** Tray UI freezes when clicking "Connection Info" until the CLI call completes (up to 8s).

**Root cause:** Same pattern as Bug 1 and 2 — `_on_show_info()` called `run_cli(["protonvpn", "info"], timeout=8)` synchronously on the main thread.

**Fix:** Converted to async: `run_cli_async(["info"], ...)` with a `GLib.idle_add` callback that pipes the result to `zenity --text-info`.

---

## Bug 4: RadioMenuItem initialized with empty list instead of None

**Severity:** Medium
**File:** `protonvpn-tray.py:485`
**Symptom:** Settings radio menu items might not form correct mutual-exclusion groups depending on PyGObject version behavior with `[]` vs `None`.

**Root cause:** `Gtk.RadioMenuItem.new_with_label([], display)` used an empty Python list `[]` as the group argument. The GTK3 C API expects `NULL` (None in Python) for a new group, and a `GSList*` for joining. While PyGObject may treat `[]` as a null group in some versions, the behavior is undefined.

**Fix:** Changed to `Gtk.RadioMenuItem.new_with_label(None, display)` for the first item (new group) and `Gtk.RadioMenuItem.new_with_label([group], display)` for subsequent items in the same group.

---

## Bug 5: Missing Notify.uninit() cleanup

**Severity:** Medium
**File:** `protonvpn-tray.py:261-262`
**Symptom:** Notification daemon resources not released on clean exit. No visible symptom on most systems, but can leak D-Bus connections in long-running sessions where the tray is restarted many times.

**Root cause:** `Notify.init()` was called in `__init__` but `Notify.uninit()` was never called on shutdown. `gir1.2-notify-0.7` expects explicit cleanup.

**Fix:** Added `_shutdown()` method that calls `Notify.uninit()` then `Gtk.main_quit()`. Both the signal handler (`_on_signal`) and the Quit menu handler (`_on_quit`) now delegate to `_shutdown()`.

---

## Bug 6: PID file race condition between check and write

**Severity:** Low
**File:** `protonvpn-tray.py:246-248`
**Symptom:** Two tray instances started in extremely rapid succession could both pass the PID check and both write PID files.

**Root cause:** `_check_pid()` and `_write_pid()` were two separate operations with a time-of-check-to-time-of-use (TOCTOU) gap. Between the liveness check and the open-for-write, another process could claim the slot.

**Fix:** Replaced `_write_pid()` plain `open(PID_FILE, "w")` with exclusive create: `os.open(PID_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL)`. This atomically creates the file only if it doesn't already exist. The `FileExistsError` exception is caught and exits gracefully.

---

## Bug 7: Dead field `_indicating`

**Severity:** Low
**File:** `protonvpn-tray.py:193`
**Symptom:** None. Dead code, no runtime impact.

**Root cause:** Field was initialized in `__init__` but never read or checked anywhere. Possibly a leftover from a planned feature.

**Fix:** Removed the `self._indicating = False` line.

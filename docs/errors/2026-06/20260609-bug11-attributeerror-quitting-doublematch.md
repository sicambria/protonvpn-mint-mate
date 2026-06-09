# Bug 11: AttributeError '_quitting' — edit-tool double-match corruption

**Date:** 2026-06-09
**Severity:** Critical (crash on tray operation after enable/disable toggle)
**Caused by:** Commit `a9b4489` (defense-in-depth guards)

## Symptom

```
AttributeError: 'ProtonVPNTray' object has no attribute '_quitting'
  File "protonvpn-tray.py", line 602, in callback
    if self._quitting:
```

## Root Cause

The edit to add `self._quitting = False` after `self._polling = False` used an ambiguous oldString:

```
Old:     self._polling = False
New:     self._polling = False
         self._quitting = False
```

The string `self._polling = False` appeared in **two** places in the file:
1. In `__init__` (line 201) — correct, with proper indentation (8 spaces)
2. Inside the broken `_poll_status` callback (line 602) — with wrong 4-space indentation (this was leftover corruption from a previous indentation fix)

The edit tool matched **both** occurrences and inserted `self._quitting = False` after both. However:
- Match 1 (in `__init__`): The oldString `self._polling = False` was at 8-space indent, but the newString had lines at 8-space indent. Applied correctly.
- Match 2 (in the corrupted callback): The oldString `self._polling = False` was at 4-space indent, and the newString also had 4-space intent. Applied — but this made the indentation even more wrong, causing a syntax error.

When the syntax error was discovered and a fix was applied (removing the extra `self._quitting = False` from the callback body), the fix accidentally also matched against the `__init__` section's `self._quitting = False` line via the same oldString `self._quitting = False`. This removed the correct instance from `__init__`, leaving no `_quitting` field at all.

## Contributing factor: Previous indentation corruption

The callback body at line 602 was already corrupted from an earlier indentation fix that used `replaceAll` or ambiguous matching. This created the second copy of `self._polling = False` at the wrong indentation level, which provided the second match target.

## Fix

Manually ensured `self._quitting = False` exists in `__init__` immediately after `self._polling = False`, and the callback body has correct 8-space indentation with only `self._polling = False` inside it (no `_quitting` reset).

## Lessons

1. **Always use unique context around oldString** — `self._polling = False` was too short and ambiguous
2. **After edits that touch indentation, read back the entire function** — the callback body corruption should have been caught
3. **Prefer longer oldString with surrounding unique context** — e.g., include the line before or after to disambiguate
4. **The edit tool's `replaceAll` or implicit multi-match behavior is dangerous** — when the same simple string appears in multiple contexts, manual verification of all matches is needed

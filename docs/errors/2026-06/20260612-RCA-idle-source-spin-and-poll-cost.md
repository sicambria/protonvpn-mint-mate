# RCA: Tray high CPU — leaked idle source (100% spin) + per-poll CLI spawn cost

**Date:** 2026-06-12
**Severity:** High (sustained CPU, battery drain, coredump flood, prior session kills)
**Component:** `protonvpn-tray.py`
**Supersedes/extends:** `20260610-RCA-high-cpu-boliling-ocean.md` (this one adds a
*measured* baseline and identifies a second, independent root cause the earlier
RCA only described vaguely as an "idle callback storm").

---

## Summary

The tray's high CPU had **two independent causes**, both now fixed and measured:

1. **Leaked GLib idle source → 100% CPU spin.** Every post-action refresh and
   every manual *Refresh* did `GLib.idle_add(self._poll_status)`. `_poll_status`
   returns `True` on **every** path (correct for its recurring `timeout_add`
   timer, fatal for `idle_add`). For an idle source, returning `True`
   (`G_SOURCE_CONTINUE`) means *the source is never removed and is re-dispatched
   on every main-loop iteration*. Measured: **1,669,618 dispatches in 2.0 s = 100%
   of one core**, permanently, after the first connect/disconnect/refresh.

2. **Each status poll spawns a ~1.5 CPU-second child.** `protonvpn status` is a
   full Python interpreter that loads `local_agent.abi3.so`. A single *successful*
   call costs **1.29 s user + 0.21 s sys = ~1.5 CPU-s, 98% CPU, 170 MB RSS, 45 866
   minor page faults** (measured with `/usr/bin/time -v`). At the original 5 s poll
   interval that is a **~28% one-core CPU floor just from polling** — before any
   hang or overlap pushes it to the 98% seen in the boiling-ocean incident.

Bonus finding: the CLI is *unstable*. In a 60 s baseline of 12 calls, **2 returned
`-6` (SIGABRT)** — each writing a ~16 MB systemd coredump — and 5 returned `rc=1`.
Fewer polls therefore also means fewer coredumps.

---

## Measured baseline (this investigation)

Harness: `docs/errors/2026-06/cpu-baseline-logs/measure_poll_cost.py` reproduces
*only* the poll path (spawn `protonvpn status` on a cadence) and sums child CPU via
`RUSAGE_CHILDREN`. Logs in the same directory.

| Config | Calls / min | CPU (one core) | Crashes in window |
|--------|-------------|----------------|-------------------|
| **Old — 5 s poll** (`baseline-5s.log`) | 12 | **27.7%** | 2× SIGABRT + 5× rc=1 |
| Fixed — 20 s poll (`fixed-20s.log`) | 3 | 7.5% | proportionally fewer |
| **Fixed — live tray, adaptive** (`live-fixed.log`) | settles to 1 | **4.4%** | — |

The two SIGABRT coredumps from the baseline run were confirmed fresh in
`coredumpctl` (PIDs 538322, 539838) — polling actively produces coredumps.

Live end-to-end measurement (`measure_live_tray.py`, whole process tree via
`/proc/<pid>` `utime+stime+cutime+cstime`) shows the fixed tray averaging **4.4%**
over 70 s, with trailing 10 s windows at **0%** as the adaptive backoff grows the
spacing to 60 s.

---

## Root cause detail

### Cause 1 — `idle_add` returning `True`

```python
# _poll_status returns True on every path (lines for paused / busy / rate-limit / tail).
GLib.idle_add(self._poll_status)   # in _run_vpn_action callback and _on_refresh
```

GLib idle sources interpret the return value as "keep me?" — `True` keeps the
source, so it fires again immediately. With nothing else pending the main loop
calls it back-to-back forever. The internal guards (`busy`, `_polling`,
rate-limit) all `return True`, so the source stays hot regardless and the main
thread never sleeps in `poll()`. This matches the original incident's
`wchan=0, state=R` main thread.

### Cause 2 — per-poll spawn cost × frequency

A 1.5 CPU-s child every 5 s = 1.5/5 ≈ 30% of a core, sustained, even when every
call succeeds. The expensive part (interpreter start + native agent init) is paid
on *every* poll for a job that only needs to refresh a status label.

---

## The fix

### 1. One-shot idle wrapper (Cause 1)

`_poll_now()` forces a poll and **returns `False`** so GLib removes the idle source
after a single dispatch. The two `GLib.idle_add(self._poll_status)` sites now call
`GLib.idle_add(self._poll_now)`. `run_cli_async`'s idle dispatch was also hardened
to always `return False`, so no callback can ever leak a spinning source again.

### 2. Adaptive poll backoff (Cause 2)

The recurring timer still fires every `POLL_INTERVAL` (20 s, up from 5 s), but a
timer-driven poll only actually spawns the CLI once `self._poll_backoff` has
elapsed. `_poll_backoff` starts at `POLL_INTERVAL` and **grows by `POLL_INTERVAL`
toward `POLL_INTERVAL_MAX` (60 s) each time the connection state is unchanged**,
snapping back to `POLL_INTERVAL` on any state change *or* user action (`force=True`).
A stable, idle tray therefore polls once per 60 s (≈2.5% one core) instead of once
per 5 s, while connects/disconnects/refreshes still update the UI immediately.
`POLL_MIN_INTERVAL` (4 s) remains as an anti-hammer floor on all polls, forced or not.

---

## Verification

- `PYTHONPATH=. python3 -m coverage run --branch --source=. tests/run_all.py`
  → **340/340 pass**, `coverage report --fail-under=100` → **100.00%** branch.
- New tests: `tests/test_high_cpu_analysis.py` §6B (backoff grow/reset/cap, forced
  vs timer poll, `_poll_now` one-shot), updated §6.3 (no real sleep).
- Empirical GLib repro of the spin and the one-shot fix.
- Live tray measurement: 27.7% → 4.4% one core (6.3×).

---

## Residual / upstream

The CPU-spinning-*success* and SIGABRT modes inside `local_agent.abi3.so` are
upstream Proton bugs the tray cannot fix. They are now *mitigated* by polling up to
12× less often (fewer spawns = less spin exposure, fewer coredumps), plus the
existing circuit breaker (`_MAX_CONSECUTIVE_TIMEOUTS`, `_MAX_FAILURES`). The menu
rebuild on open still runs a synchronous `protonvpn config list` on the GTK thread,
but that is user-triggered and bounded by a 15 s cache TTL, not continuous CPU.

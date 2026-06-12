#!/usr/bin/env python3
"""Measure the CPU cost of the tray's status-poll loop.

This reproduces *only* the dominant cost path of protonvpn-tray.py — spawning
`protonvpn status` on a fixed cadence — and measures the actual CPU time the
spawned children consume over a wall-clock window. It does NOT start GTK, so
the number it reports is a clean lower bound on tray CPU attributable to
polling (GTK redraw churn is additive on top).

Usage:
    measure_poll_cost.py <interval_seconds> <duration_seconds> [label]

Output: a JSON line + human summary to stdout, and a detailed per-call log.
"""
import json
import os
import resource
import subprocess
import sys
import time

PROTONVPN_BIN = os.path.expanduser("~/.local/bin/protonvpn")
if not os.path.exists(PROTONVPN_BIN):
    PROTONVPN_BIN = "protonvpn"

STATUS_TIMEOUT = 5  # mirrors CMD_TIMEOUT_STATUS in the tray


def one_call():
    """Spawn `protonvpn status` exactly as the tray does and time it."""
    t0 = time.monotonic()
    r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    rc = None
    timed_out = False
    proc = subprocess.Popen(
        [PROTONVPN_BIN, "status"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        proc.communicate(timeout=STATUS_TIMEOUT)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        if isinstance(proc.pid, int) and proc.pid > 1:
            try:
                os.killpg(proc.pid, 9)
            except Exception:
                pass
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        rc = -1
    r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
    wall = time.monotonic() - t0
    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    return {
        "wall": round(wall, 3),
        "cpu": round(cpu, 3),
        "rc": rc,
        "timed_out": timed_out,
    }


def main():
    interval = float(sys.argv[1])
    duration = float(sys.argv[2])
    label = sys.argv[3] if len(sys.argv) > 3 else "interval=%g" % interval

    print("# poll-cost measurement: %s" % label)
    print("# interval=%gs duration=%gs bin=%s" % (interval, duration, PROTONVPN_BIN))
    print("# %-4s %-8s %-8s %-4s %s" % ("call", "wall_s", "cpu_s", "rc", "timed_out"))

    start = time.monotonic()
    next_at = start
    calls = []
    n = 0
    while time.monotonic() - start < duration:
        now = time.monotonic()
        if now < next_at:
            time.sleep(min(0.2, next_at - now))
            continue
        n += 1
        res = one_call()
        calls.append(res)
        print("  %-4d %-8.3f %-8.3f %-4s %s"
              % (n, res["wall"], res["cpu"], res["rc"], res["timed_out"]))
        next_at += interval

    total_wall = time.monotonic() - start
    total_cpu = sum(c["cpu"] for c in calls)
    summary = {
        "label": label,
        "interval_s": interval,
        "duration_s": round(total_wall, 2),
        "num_calls": len(calls),
        "total_child_cpu_s": round(total_cpu, 2),
        "avg_cpu_per_call_s": round(total_cpu / len(calls), 3) if calls else 0,
        "avg_wall_per_call_s": round(sum(c["wall"] for c in calls) / len(calls), 3) if calls else 0,
        "sustained_cpu_pct_one_core": round(100.0 * total_cpu / total_wall, 1),
        "timeouts": sum(1 for c in calls if c["timed_out"]),
        "nonzero_rc": sum(1 for c in calls if c["rc"] not in (0, None)),
    }
    print("SUMMARY " + json.dumps(summary))
    return summary


if __name__ == "__main__":
    main()

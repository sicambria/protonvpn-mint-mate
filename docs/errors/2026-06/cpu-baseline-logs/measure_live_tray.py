#!/usr/bin/env python3
"""Launch the real tray and measure its whole-process-tree CPU over a window.

/proc/<pid>/stat fields utime+stime+cutime+cstime accumulate the process's own
CPU *and* the CPU of all reaped children (each `protonvpn status` spawn). Sampling
that total at the start and end of a wall-clock window yields the true CPU the
tray costs, including every CLI child it spawned.

Usage: measure_live_tray.py <duration_seconds> [extra tray args...]
"""
import os
import signal
import subprocess
import sys
import time

CLK_TCK = os.sysconf("SC_CLK_TCK")
TRAY = os.path.join(os.path.dirname(__file__),
                    "..", "..", "..", "..", "protonvpn-tray.py")
TRAY = os.path.abspath(TRAY)
assert os.path.exists(TRAY), "tray not found at %s" % TRAY


def cpu_ticks(pid):
    with open("/proc/%d/stat" % pid) as f:
        parts = f.read().rsplit(")", 1)[1].split()
    # after the ')' split, fields are shifted: index 11..14 == utime,stime,cutime,cstime
    utime, stime, cutime, cstime = (int(parts[11]), int(parts[12]),
                                    int(parts[13]), int(parts[14]))
    return utime + stime + cutime + cstime


def main():
    duration = float(sys.argv[1])
    extra = sys.argv[2:]
    env = dict(os.environ)
    # fresh bytecode
    proc = subprocess.Popen(
        [sys.executable, TRAY] + extra,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid = proc.pid
    print("# launched tray pid=%d args=%r, measuring %gs" % (pid, extra, duration))
    time.sleep(3)  # let it settle past the startup poll burst
    if proc.poll() is not None:
        print("FATAL: tray exited early rc=%s" % proc.returncode)
        return
    t0 = time.monotonic()
    c0 = cpu_ticks(pid)
    # sample a few times for a CPU% trace
    samples = []
    last_t, last_c = t0, c0
    while time.monotonic() - t0 < duration:
        time.sleep(10)
        if proc.poll() is not None:
            print("# tray exited mid-measurement rc=%s" % proc.returncode)
            break
        now = time.monotonic()
        c = cpu_ticks(pid)
        pct = 100.0 * (c - last_c) / CLK_TCK / (now - last_t)
        samples.append(round(pct, 1))
        print("  +%4.0fs  window CPU = %5.1f%% (one core)" % (now - t0, pct))
        last_t, last_c = now, c
    total_t = time.monotonic() - t0
    total_c = cpu_ticks(pid) - c0
    avg = 100.0 * total_c / CLK_TCK / total_t
    print("SUMMARY avg_cpu_pct_one_core=%.1f over %.0fs  samples=%r"
          % (avg, total_t, samples))
    # clean shutdown
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception:
        pass
    try:
        proc.wait(timeout=8)
    except Exception:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            pass
    print("# tray stopped")


if __name__ == "__main__":
    main()

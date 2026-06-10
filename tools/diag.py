#!/usr/bin/env python3
"""Diagnostic: check protonvpn-tray health, CLI status, coredump state.

Read-only — no modifications. Run before/after code changes to confirm
fixes are effective. Can be run alongside the tray without interfering.

Usage:
    python3 tools/diag.py
    python3 tools/diag.py --json    # machine-readable output
"""

import os
import sys
import json
import time
import subprocess
import argparse


def check_tray():
    try:
        r = subprocess.run(["pgrep", "-fl", "protonvpn-tray"],
                           capture_output=True, text=True, timeout=2)
        lines = [l.strip() for l in r.stdout.strip().split("\n") if l]
        pids = []
        for line in lines:
            parts = line.split()
            if parts:
                pids.append(parts[0])
        if pids:
            pid_info = []
            for pid in pids:
                try:
                    cpu = subprocess.check_output(
                        ["ps", "-p", pid, "-o", "%cpu,etime", "--no-headers"],
                        text=True, timeout=2,
                    ).strip()
                    pid_info.append({"pid": pid, "ps": cpu})
                except Exception:
                    pid_info.append({"pid": pid, "ps": "n/a"})
            return True, pid_info
        return False, []
    except Exception as e:
        return None, [str(e)]


def check_autostart():
    path = os.path.expanduser("~/.config/autostart/protonvpn-tray.desktop")
    return os.path.exists(path), path


def check_pycache():
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(proj_root, "__pycache__")
    if os.path.exists(d):
        return [f for f in os.listdir(d) if "protonvpn-tray" in f], d
    return [], d


def test_cli_status():
    start = time.monotonic()
    try:
        r = subprocess.run(["protonvpn", "status"],
                           capture_output=True, text=True, timeout=8)
        elapsed = time.monotonic() - start
        return {
            "ok": True,
            "code": r.returncode,
            "time": round(elapsed, 3),
            "output": r.stdout[:500],
            "stderr": r.stderr[:200] if r.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "TIMEOUT after 8s",
                "time": time.monotonic() - start}
    except FileNotFoundError:
        return {"ok": False, "error": "BINARY NOT FOUND", "time": 0}
    except Exception as e:
        return {"ok": False, "error": str(e),
                "time": time.monotonic() - start}


def count_coredumps():
    try:
        files = os.listdir("/var/lib/systemd/coredump/")
        return len([f for f in files if "core.protonvpn" in f])
    except Exception:
        return -1


def check_source_constants():
    """Load key constants from protonvpn-tray.py via source parsing."""
    proj_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(proj_root, "protonvpn-tray.py")
    constants = {}
    try:
        with open(src, "r") as f:
            for line in f:
                for name in ("POLL_MIN_INTERVAL", "_MAX_CONSECUTIVE_TIMEOUTS",
                             "_MAX_FAILURES", "CMD_TIMEOUT_STATUS",
                             "CONFIG_CACHE_TTL"):
                    if line.startswith(name) and "=" in line:
                        try:
                            val = line.split("=", 1)[1].split("#")[0].strip()
                            constants[name] = eval(val)
                        except Exception:
                            constants[name] = "parse_error"
    except Exception:
        pass
    return constants


def main():
    parser = argparse.ArgumentParser(
        description="Proton VPN Tray system diagnostic"
    )
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output")
    args = parser.parse_args()

    results = {}

    running, pid_info = check_tray()
    results["tray_running"] = running
    results["tray_pids"] = pid_info

    autostart, autostart_path = check_autostart()
    results["autostart_enabled"] = autostart
    results["autostart_path"] = autostart_path

    pycs, pycache_dir = check_pycache()
    results["pycache_files"] = pycs
    results["pycache_dir"] = pycache_dir

    coredumps = count_coredumps()
    results["coredump_count"] = coredumps

    cli_result = test_cli_status()
    results["cli_status"] = cli_result

    constants = check_source_constants()
    results["constants"] = constants

    issues = []
    if running:
        issues.append("Tray is running → pkill -f protonvpn-tray.py")
    if autostart:
        issues.append("Autostart enabled → ./disable-autostart.sh")
    if pycs:
        issues.append("Stale .pyc → rm -rf __pycache__/")
    if coredumps > 10:
        issues.append("Many coredumps (%d) → check coredumpctl list" % coredumps)
    if cli_result.get("ok") and cli_result.get("time", 0) > 2.0:
        issues.append("protonvpn status took %.1fs — may indicate CLI hang" %
                      cli_result["time"])
    results["issues"] = issues
    results["clean"] = len(issues) == 0

    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return

    print("=" * 60)
    print("  Proton VPN Tray — System Diagnostic")
    print("=" * 60)

    state = "RUNNING" if running else ("ERROR" if running is None else "stopped")
    print(f"\nTray:       {state}")
    if pid_info:
        for pi in pid_info:
            print(f"            PID {pi['pid']}: {pi['ps']}")

    print(f"Autostart:  {'ENABLED' if autostart else 'disabled'}")
    print(f"Stale .pyc: {len(pycs)} file(s)" +
          (f" in {pycache_dir}" if pycs else " (clean)"))
    print(f"Coredumps:  {coredumps} protonvpn-*.zst")

    print("\nSource constants:")
    for k, v in sorted(constants.items()):
        print(f"  {k}: {v}")

    print("\n--- protonvpn status ---")
    if cli_result["ok"]:
        print(f"  Exit: {cli_result['code']}   Time: {cli_result['time']:.3f}s")
        dur = cli_result["time"]
        if dur > 5.0:
            print(f"  CRITICAL: {dur:.1f}s — CLI is hanging (timeout would be 5s)")
        elif dur > 2.0:
            print(f"  WARNING: >2s — may indicate CLI health issue")
        else:
            print(f"  OK: under 2s threshold")
        if cli_result["output"]:
            print(f"  Output: {cli_result['output'][:200]}")
    else:
        print(f"  ERROR: {cli_result['error']}")

    print("\n" + "-" * 60)
    if not issues:
        print("System appears clean. Safe to run tests and re-enable.")
    else:
        for i in issues:
            print(f"  ACTION: {i}")
    print("-" * 60)

    sys.exit(0 if results["clean"] else 1)


if __name__ == "__main__":
    main()

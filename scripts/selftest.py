#!/usr/bin/env python3
"""
Correctness tests T1-T3 (run from the project root: python3 scripts/selftest.py)

T1  read_psi() returns the same avg10 values as /proc/pressure/memory
T2  measured working set of `wsload 500 400` is about 400 MB
T3  measured working set of `wsload 500 100` is about 100 MB
    (so the measurement follows the working set, not the allocation)
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "controller"))
from controller import read_psi  # noqa: E402

WSLOAD = os.path.join(ROOT, "workload", "wsload")


def referenced_kb(pid):
    with open(f"/proc/{pid}/smaps_rollup") as f:
        for line in f:
            if line.startswith("Referenced:"):
                return int(line.split()[1])
    return 0


def clear_refs(pid):
    with open(f"/proc/{pid}/clear_refs", "w") as f:
        f.write("1")


def measure(total_mb, ws_mb, window=5):
    p = subprocess.Popen([WSLOAD, str(total_mb), str(ws_mb), "100000"],
                         stdout=subprocess.DEVNULL)
    time.sleep(4)                    # let it finish touching every page once
    clear_refs(p.pid)
    time.sleep(window)
    kb = referenced_kb(p.pid)
    p.send_signal(signal.SIGTERM)
    p.wait()
    return kb / 1024


def result(name, ok, detail):
    print(f"{name}: {'PASS' if ok else 'FAIL'}  {detail}")
    return ok


def main():
    if not os.path.exists(WSLOAD):
        sys.exit("build the workload first: bash scripts/setup_vm.sh")
    ok = True

    raw = open("/proc/pressure/memory").read().split()
    some_raw = float(raw[1].split("=")[1])
    full_raw = float(raw[6].split("=")[1])
    some, full = read_psi()
    print("T1  cat /proc/pressure/memory:\n    " + open("/proc/pressure/memory").read().replace("\n", "\n    "))
    ok &= result("T1", abs(some - some_raw) < 0.5 and abs(full - full_raw) < 0.5,
                 f"read_psi() -> some={some} full={full}; file -> some={some_raw} full={full_raw}")

    mb = measure(500, 400)
    ok &= result("T2", 360 <= mb <= 440, f"wsload 500 400 -> measured WSS {mb:.1f} MB (expected ~400)")

    mb = measure(500, 100)
    ok &= result("T3", 90 <= mb <= 110, f"wsload 500 100 -> measured WSS {mb:.1f} MB (expected ~100)")

    print("\nALL PASS" if ok else "\nSOME TESTS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

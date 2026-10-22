#!/usr/bin/env python3
"""
Calibrate the workload so one copy alone runs for about TARGET seconds on this
machine, and write the value into controller/config.json.

Run from the project root:  python3 scripts/calibrate.py [target_seconds]
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "controller", "config.json")
WSLOAD = os.path.join(ROOT, "workload", "wsload")

target = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
with open(CFG) as f:
    cfg = json.load(f)
w = cfg["workload"]

print(f"Running one wsload {w['total_mb']} MB / {w['ws_mb']} MB / 300M touches alone ...")
out = subprocess.run([WSLOAD, str(w["total_mb"]), str(w["ws_mb"]), "300"],
                     capture_output=True, text=True, check=True).stdout
print("  " + out.strip())
rate = float(out.split("throughput=")[1].split()[0])        # M touches / s
mtouches = max(50, int(round(rate * target / 50.0)) * 50)
w["million_touches"] = mtouches
with open(CFG, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"Throughput alone: {rate:.1f} M touches/s")
print(f"Set million_touches = {mtouches} in controller/config.json "
      f"(about {mtouches / rate:.0f} s per workload when running alone)")

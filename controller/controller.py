#!/usr/bin/env python3
"""
Working-set-aware load controller for thrashing prevention using PSI.

  I/P  -> M1 Monitor -> M2 Decision engine -> M3 Actuator -> O/P

M1  reads /proc/pressure/memory, /proc/meminfo, /proc/vmstat every second and
    measures each running workload's working set W(t, delta) every delta
    seconds with /proc/<pid>/clear_refs + the Referenced field of smaps_rollup.
M2  decides whether to suspend or resume a workload (load control with
    hysteresis and a cooldown).
M3  applies the decision with SIGSTOP / SIGCONT.
O/P one CSV row per second, a live status line, and a JSON summary per run.

Usage (from the controller/ directory):
  python3 controller.py --n 5 --mode on      # controller active
  python3 controller.py --n 5 --mode off     # baseline: monitor and log only
"""
import argparse
import json
import os
import resource
import signal
import subprocess
import sys
import time

PROC_STATES = ("RUN", "STOP", "DONE")


# ----------------------------------------------------------------- data types
class Proc:
    def __init__(self, popen, priority):
        self.popen = popen
        self.pid = popen.pid
        self.priority = priority        # larger number = more important
        self.state = "RUN"
        self.wss_kb = 0                 # last working set measured while running
        self.finished_at = None
        self.result = {}


class Snapshot:
    pass


class State:
    def __init__(self):
        self.high = 0                   # consecutive samples with full > HIGH
        self.low = 0                    # consecutive seconds with some < LOW
        self.last_action = -1e9
        self.suspends = 0
        self.resumes = 0


# --------------------------------------------------------------- M1 Monitor
def read_psi(path="/proc/pressure/memory"):
    vals = {}
    with open(path) as f:
        for line in f:                  # "some avg10=.. avg60=.. avg300=.. total=.."
            kind, *fields = line.split()
            vals[kind] = float(fields[0].split("=")[1])
    return vals["some"], vals["full"]


def read_meminfo():
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, val = line.split(":")
            out[key] = int(val.split()[0])          # kB
    return out


def read_vmstat(keys=("pgmajfault", "pswpin", "pswpout")):
    out = {}
    with open("/proc/vmstat") as f:
        for line in f:
            k, v = line.split()
            if k in keys:
                out[k] = int(v)
    return out


def read_snapshot(t, prev_vm):
    s = Snapshot()
    s.t = t
    s.some, s.full = read_psi()
    s.mem_avail_kb = read_meminfo()["MemAvailable"]
    vm = read_vmstat()
    s.majflt = vm["pgmajfault"] - prev_vm["pgmajfault"]
    s.swpin = vm["pswpin"] - prev_vm["pswpin"]
    s.swpout = vm["pswpout"] - prev_vm["pswpout"]
    return s, vm


def sample_wss(procs):
    """Read pages referenced since the last call, then clear the bits again."""
    for p in procs:
        try:
            with open(f"/proc/{p.pid}/smaps_rollup") as f:
                for line in f:
                    if line.startswith("Referenced:"):
                        p.wss_kb = int(line.split()[1])
            with open(f"/proc/{p.pid}/clear_refs", "w") as f:
                f.write("1")
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            pass                        # exited; mark_finished() handles it


# ------------------------------------------------------- M2 Decision engine
def decide(snap, procs, st, cfg):
    running = [p for p in procs if p.state == "RUN"]
    suspended = [p for p in procs if p.state == "STOP"]
    st.high = st.high + 1 if snap.full > cfg["high"] else 0
    st.low = st.low + 1 if snap.some < cfg["low"] else 0
    if suspended and not running:       # CPU would sit idle: resume at once
        cand = max(suspended, key=lambda p: p.priority)
        return ("RESUME", cand, "no workload is running")
    if snap.t - st.last_action < cfg["cooldown"]:
        return None
    total = sum(p.wss_kb for p in running)
    psi_trigger = st.high >= cfg["high_samples"]
    wss_trigger = total > cfg["capacity_kb"]
    if len(running) > 1 and (psi_trigger or wss_trigger):
        victim = min(running, key=lambda p: (p.priority, -p.wss_kb))
        if psi_trigger:
            why = f"PSI full avg10 {snap.full:.1f}% > {cfg['high']}% for {st.high} s"
        else:
            why = f"total WSS {total/1024:.0f} MB > capacity {cfg['capacity_kb']/1024:.0f} MB"
        return ("SUSPEND", victim, why)
    if suspended and st.low >= cfg["low_seconds"]:
        cand = max(suspended, key=lambda p: p.priority)
        limit = cfg["resume_fraction"] * cfg["capacity_kb"]
        if total + cand.wss_kb <= limit:
            why = (f"PSI some avg10 {snap.some:.1f}% < {cfg['low']}% for {st.low} s and "
                   f"{(total + cand.wss_kb)/1024:.0f} MB <= {limit/1024:.0f} MB")
            return ("RESUME", cand, why)
    return None


# --------------------------------------------------------------- M3 Actuator
def actuate(action, p):
    os.kill(p.pid, signal.SIGSTOP if action == "SUSPEND" else signal.SIGCONT)
    p.state = "STOP" if action == "SUSPEND" else "RUN"


# --------------------------------------------------------------------- helpers
def parse_result(text):
    out = {}
    for tok in text.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                out[k] = float(v)
            except ValueError:
                out[k] = v
    return out


def mark_finished(procs, t):
    for p in procs:
        if p.state != "DONE" and p.popen.poll() is not None:
            p.state = "DONE"
            p.finished_at = t
            out = p.popen.stdout.read() if p.popen.stdout else ""
            p.result = parse_result(out)


def stop_all(procs, t):
    """Timeout: wake stopped workloads and ask every workload to finish."""
    for p in procs:
        if p.state != "DONE":
            try:
                os.kill(p.pid, signal.SIGCONT)
                os.kill(p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and any(p.popen.poll() is None for p in procs):
        time.sleep(0.5)
    for p in procs:
        if p.popen.poll() is None:
            p.popen.kill()
            p.popen.wait()
    mark_finished(procs, t)


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description="Working-set-aware load controller (PSI)")
    ap.add_argument("--n", type=int, required=True, help="number of workloads to start")
    ap.add_argument("--mode", choices=["on", "off"], required=True,
                    help="on = controller acts, off = baseline (monitor only)")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--rep", type=int, default=1, help="repetition number (for file names)")
    ap.add_argument("--out", default="../results")
    ap.add_argument("--timeout", type=int, help="override timeout_s from the config")
    args = ap.parse_args()

    cfg_dir = os.path.dirname(os.path.abspath(args.config))
    with open(args.config) as f:
        cfg = json.load(f)
    if args.timeout:
        cfg["timeout_s"] = args.timeout
    mem_total_kb = read_meminfo()["MemTotal"]
    if cfg.get("capacity_mb"):
        cfg["capacity_kb"] = int(cfg["capacity_mb"] * 1024)
    else:
        cfg["capacity_kb"] = mem_total_kb - cfg["reserve_mb"] * 1024
    w = cfg["workload"]
    binary = os.path.normpath(os.path.join(cfg_dir, w["binary"]))
    if not os.path.exists(binary):
        sys.exit(f"workload binary not found: {binary} (run: bash scripts/setup_vm.sh)")
    try:
        read_psi()
    except FileNotFoundError:
        sys.exit("/proc/pressure/memory not found: this kernel has no PSI")

    out_dir = os.path.normpath(os.path.join(cfg_dir, args.out))
    os.makedirs(out_dir, exist_ok=True)
    name = f"N{args.n}_{args.mode}_r{args.rep}"
    csv_path = os.path.join(out_dir, name + ".csv")

    # I/P: start the managed workloads, priority n (most important) down to 1
    cmd = [binary, str(w["total_mb"]), str(w["ws_mb"]), str(w["million_touches"])]
    procs = []
    for prio in range(args.n, 0, -1):
        po = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True)
        procs.append(Proc(po, prio))

    def resume_all_and_exit(signum, frame):
        for p in procs:
            if p.state == "STOP":
                os.kill(p.pid, signal.SIGCONT)
                p.state = "RUN"
                log(f"[exit] SIGCONT sent to pid {p.pid} (P{p.priority})")
        log("[exit] controller stopped; no workload left suspended")
        sys.exit(0)

    signal.signal(signal.SIGINT, resume_all_and_exit)
    signal.signal(signal.SIGTERM, resume_all_and_exit)

    log(f"=== run {name}: mode={args.mode} workloads={args.n} x "
        f"wsload {w['total_mb']}MB/{w['ws_mb']}MB/{w['million_touches']}M touches")
    log(f"    MemTotal={mem_total_kb//1024} MB capacity C={cfg['capacity_kb']//1024} MB "
        f"HIGH={cfg['high']}% LOW={cfg['low']}% delta={cfg['window_s']} s "
        f"timeout={cfg['timeout_s']} s")
    for p in procs:
        log(f"    started pid {p.pid} priority P{p.priority}")

    order = sorted(procs, key=lambda p: -p.priority)
    header = ["t", "some_avg10", "full_avg10", "mem_avail_mb", "majflt_s", "pswpin_s",
              "pswpout_s", "running", "suspended", "action", "action_prio"]
    for p in order:
        header += [f"wss_mb_P{p.priority}", f"state_P{p.priority}"]
    csvf = open(csv_path, "w")
    csvf.write(",".join(header) + "\n")

    st = State()
    t0 = time.monotonic()
    cpu0 = resource.getrusage(resource.RUSAGE_SELF)
    prev_vm = read_vmstat()
    samples = []
    tick = 0
    timed_out = False
    while any(p.state != "DONE" for p in procs):
        time.sleep(max(0.0, t0 + tick + 1 - time.monotonic()))
        tick += 1
        t = round(time.monotonic() - t0, 1)
        snap, prev_vm = read_snapshot(t, prev_vm)                  # M1
        mark_finished(procs, t)
        if tick % cfg["window_s"] == 0:
            sample_wss([p for p in procs if p.state == "RUN"])     # M1: working sets
        act = decide(snap, procs, st, cfg) if args.mode == "on" else None   # M2
        action, action_prio = "", ""
        if act:
            kind, p, why = act
            try:
                actuate(kind, p)                                   # M3
                st.last_action = t
                if kind == "SUSPEND":
                    st.suspends += 1
                    log(f"[t={t:5.0f}s] SUSPEND pid {p.pid} (P{p.priority}, WSS "
                        f"{p.wss_kb/1024:.0f} MB) with SIGSTOP -- {why}")
                else:
                    st.resumes += 1
                    log(f"[t={t:5.0f}s] RESUME  pid {p.pid} (P{p.priority}, WSS "
                        f"{p.wss_kb/1024:.0f} MB) with SIGCONT -- {why}")
                action, action_prio = kind, p.priority
            except ProcessLookupError:
                pass
        running = sum(p.state == "RUN" for p in procs)
        suspended = sum(p.state == "STOP" for p in procs)
        row = [t, snap.some, snap.full, round(snap.mem_avail_kb / 1024), snap.majflt,
               snap.swpin, snap.swpout, running, suspended, action, action_prio]
        for p in order:
            row += [round(p.wss_kb / 1024), p.state]
        csvf.write(",".join(str(x) for x in row) + "\n")
        csvf.flush()
        samples.append(snap)
        procs_txt = " ".join(f"P{p.priority}:{p.state}/{p.wss_kb/1024:.0f}MB" for p in order)
        log(f"t={t:5.0f}s some={snap.some:5.1f}% full={snap.full:5.1f}% "
            f"avail={snap.mem_avail_kb/1024:5.0f}MB majflt/s={snap.majflt:6d} "
            f"swap/s={snap.swpin + snap.swpout:6d} | {procs_txt}")
        if t >= cfg["timeout_s"] and any(p.state != "DONE" for p in procs):
            log(f"[t={t:5.0f}s] TIMEOUT: stopping unfinished workloads")
            timed_out = True
            stop_all(procs, t)
    csvf.close()

    # O/P: per-run summary
    wall = time.monotonic() - t0
    cpu1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu_s = (cpu1.ru_utime - cpu0.ru_utime) + (cpu1.ru_stime - cpu0.ru_stime)
    finished = [p for p in procs if p.result.get("done") == 1]
    makespan = cfg["timeout_s"] if timed_out else max(p.finished_at for p in procs)
    touches = sum(p.result.get("touches", 0) for p in procs)
    n_s = max(len(samples), 1)
    summary = {
        "run": name, "n": args.n, "mode": args.mode, "rep": args.rep,
        "psi_full_avg10_mean": round(sum(s.full for s in samples) / n_s, 2),
        "psi_some_avg10_mean": round(sum(s.some for s in samples) / n_s, 2),
        "majflt_per_s": round(sum(s.majflt for s in samples) / n_s, 1),
        "swap_pages_per_s": round(sum(s.swpin + s.swpout for s in samples) / n_s, 1),
        "throughput_m_per_s": round(touches / makespan / 1e6, 3) if makespan else 0,
        "makespan_s": round(makespan, 1),
        "finished": len(finished), "timed_out": timed_out,
        "suspends": st.suspends, "resumes": st.resumes,
        "controller_cpu_pct": round(100 * cpu_s / wall, 2),
        "per_process": {f"P{p.priority}": {"finished_at_s": p.finished_at, **p.result}
                        for p in order},
    }
    with open(os.path.join(out_dir, name + ".json"), "w") as f:
        json.dump(summary, f, indent=2)
    log("=== summary " + name)
    for k, v in summary.items():
        if k != "per_process":
            log(f"    {k:22s} {v}")
    log(f"    log: {csv_path}")


if __name__ == "__main__":
    main()

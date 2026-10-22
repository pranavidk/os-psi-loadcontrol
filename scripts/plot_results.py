#!/usr/bin/env python3
"""
Summarise all runs in results/ and draw the report figures.

Run from the project root:  python3 scripts/plot_results.py [N]
  N = which workload count to plot (default: the largest N that has both an
      'off' and an 'on' run).

Writes:
  results/summary.csv            mean of all repetitions per (N, mode) -> Table 8
  results/summary.md             same table in Markdown
  results/fig8_psi_full.png      PSI full avg10 over time, off vs on   -> Figure 8
  results/fig9_wss.png           working set per process, controller on -> Figure 9
  results/fig_throughput.png     throughput and makespan per N, off vs on
"""
import csv
import glob
import json
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
FIELDS = ["psi_full_avg10_mean", "psi_some_avg10_mean", "majflt_per_s", "swap_pages_per_s",
          "throughput_m_per_s", "makespan_s", "suspends", "resumes", "finished",
          "controller_cpu_pct"]
OFF_COLOR, ON_COLOR = "#c2410c", "#1d4ed8"


def load_runs():
    runs = []
    for path in glob.glob(os.path.join(RES, "N*_*_r*.json")):
        with open(path) as f:
            runs.append(json.load(f))
    if not runs:
        sys.exit("No runs found in results/. Run scripts/run_experiment.sh first.")
    return runs


def summarise(runs):
    groups = defaultdict(list)
    for r in runs:
        groups[(r["n"], r["mode"])].append(r)
    rows = []
    for (n, mode), rs in sorted(groups.items()):
        row = {"N": n, "mode": mode, "reps": len(rs)}
        for k in FIELDS:
            row[k] = round(sum(r[k] for r in rs) / len(rs), 2)
        row["timed_out_runs"] = sum(1 for r in rs if r["timed_out"])
        rows.append(row)
    with open(os.path.join(RES, "summary.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    head = ["N", "Controller", "PSI full avg10 (%)", "Major faults/s", "Swap pages/s",
            "Throughput (M/s)", "Makespan (s)", "Suspends", "Finished", "Timed-out runs"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in [
            r["N"], r["mode"].capitalize(), r["psi_full_avg10_mean"], r["majflt_per_s"],
            r["swap_pages_per_s"], r["throughput_m_per_s"], r["makespan_s"],
            r["suspends"] if r["mode"] == "on" else "-", r["finished"],
            r["timed_out_runs"]]) + " |")
    md = "\n".join(lines)
    with open(os.path.join(RES, "summary.md"), "w") as f:
        f.write(md + "\n")
    print("Table 8 (mean of repetitions):\n")
    print(md)
    return rows


def read_csv(name):
    path = os.path.join(RES, name + ".csv")
    with open(path) as f:
        return list(csv.DictReader(f))


def style(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", color="#e5e7eb", linewidth=0.8)
    ax.set_axisbelow(True)


def fig_psi(n):
    off, on = read_csv(f"N{n}_off_r1"), read_csv(f"N{n}_on_r1")
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=150)
    ax.plot([float(r["t"]) for r in off], [float(r["full_avg10"]) for r in off],
            color=OFF_COLOR, lw=1.8, label="Baseline (controller off)")
    ax.plot([float(r["t"]) for r in on], [float(r["full_avg10"]) for r in on],
            color=ON_COLOR, lw=1.8, label="Controller on")
    top = max([float(r["full_avg10"]) for r in off + on] + [12])
    for r in on:
        if r["action"]:
            x = float(r["t"])
            ax.axvline(x, color="#6b7280", lw=0.8, ls="--")
            ax.text(x, top * 1.02, f"{'S' if r['action'] == 'SUSPEND' else 'R'}P{r['action_prio']}",
                    ha="center", fontsize=7, color="#374151")
    ax.set_ylim(0, top * 1.1)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("PSI memory full avg10 (%)")
    ax.set_title(f"Memory stall time with {n} workloads (S = suspend, R = resume)", loc="left")
    style(ax)
    ax.legend(frameon=False)
    fig.tight_layout()
    out = os.path.join(RES, "fig8_psi_full.png")
    fig.savefig(out)
    print("wrote", out)


def fig_wss(n):
    on = read_csv(f"N{n}_on_r1")
    with open(os.path.join(RES, f"N{n}_on_r1.json")) as f:
        summ = json.load(f)
    cap = None
    cfg_path = os.path.join(ROOT, "controller", "config.json")
    with open(cfg_path) as f:
        cfg = json.load(f)
    if cfg.get("capacity_mb"):
        cap = cfg["capacity_mb"]
    else:
        with open("/proc/meminfo") as f:
            total_kb = int(f.readline().split()[1])
        cap = total_kb / 1024 - cfg["reserve_mb"]
    t = [float(r["t"]) for r in on]
    prios = sorted([k[len("wss_mb_P"):] for k in on[0] if k.startswith("wss_mb_P")],
                   key=int, reverse=True)
    fig, ax = plt.subplots(figsize=(9, 4.6), dpi=150)
    cmap = plt.get_cmap("tab10")
    bottom = [0.0] * len(t)
    for i, p in enumerate(prios):
        vals = [float(r[f"wss_mb_P{p}"]) if r[f"state_P{p}"] == "RUN" else 0.0 for r in on]
        ax.fill_between(t, bottom, [b + v for b, v in zip(bottom, vals)], step="post",
                        color=cmap(i), alpha=0.75, lw=0, label=f"P{p} (running)")
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.axhline(cap, color="#111827", lw=1.4, ls="--")
    ax.text(t[-1], cap, f"  capacity C = {cap:.0f} MB", va="bottom", ha="right", fontsize=8)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Working set of running workloads (MB)")
    ax.set_title(f"Working set of running workloads vs capacity C ({summ['suspends']} "
                 f"suspends, {summ['resumes']} resumes)", loc="left")
    style(ax)
    ax.legend(frameon=False, fontsize=8, ncol=min(5, len(prios)), loc="upper right",
              bbox_to_anchor=(1, -0.14))
    fig.tight_layout()
    out = os.path.join(RES, "fig9_wss.png")
    fig.savefig(out)
    print("wrote", out)


def fig_throughput(rows):
    ns = sorted({r["N"] for r in rows})
    get = lambda n, m, k: next((r[k] for r in rows if r["N"] == n and r["mode"] == m), 0)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), dpi=150)
    x = range(len(ns))
    for ax, key, label in [(axes[0], "throughput_m_per_s", "Total throughput (M touches/s)"),
                           (axes[1], "makespan_s", "Makespan (s)")]:
        ax.bar([i - 0.2 for i in x], [get(n, "off", key) for n in ns], 0.4,
               color=OFF_COLOR, label="Controller off")
        ax.bar([i + 0.2 for i in x], [get(n, "on", key) for n in ns], 0.4,
               color=ON_COLOR, label="Controller on")
        ax.set_xticks(list(x))
        ax.set_xticklabels([f"N = {n}" for n in ns])
        ax.set_ylabel(label)
        style(ax)
    axes[0].legend(frameon=False)
    fig.tight_layout()
    out = os.path.join(RES, "fig_throughput.png")
    fig.savefig(out)
    print("wrote", out)


def main():
    runs = load_runs()
    rows = summarise(runs)
    pairs = sorted({r["n"] for r in runs if r["mode"] == "on"} &
                   {r["n"] for r in runs if r["mode"] == "off"})
    n = int(sys.argv[1]) if len(sys.argv) > 1 else (pairs[-1] if pairs else None)
    print()
    if n is not None and os.path.exists(os.path.join(RES, f"N{n}_on_r1.csv")) \
            and os.path.exists(os.path.join(RES, f"N{n}_off_r1.csv")):
        fig_psi(n)
        fig_wss(n)
    else:
        print("Need both N{n}_off_r1 and N{n}_on_r1 for Figures 8 and 9.")
    fig_throughput(rows)


if __name__ == "__main__":
    main()

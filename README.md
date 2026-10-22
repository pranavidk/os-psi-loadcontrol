# Working-Set-Aware Load Control for Thrashing Prevention in Linux Using PSI

BCSE303L Operating Systems project. A user-space controller that prevents thrashing by
suspending and resuming processes according to their working sets, driven by Linux
Pressure Stall Information (PSI).

```
I/P -> M1 Monitor -> M2 Decision engine -> M3 Actuator -> O/P
       PSI, vmstat,   suspend/resume rules   SIGSTOP /      CSV log, status
       working sets   with hysteresis        SIGCONT        line, plots
```

## Repository layout

| Path | Contents |
| --- | --- |
| `controller/controller.py` | The controller (M1, M2, M3, logging) |
| `controller/config.json` | Thresholds and workload settings |
| `workload/wsload.c` | Workload generator with a known working-set size |
| `scripts/setup_vm.sh` | Installs packages, adds swap, builds, checks PSI |
| `scripts/selftest.py` | Correctness tests T1-T3 |
| `scripts/calibrate.py` | Sets the workload length for this machine |
| `scripts/run_experiment.sh` | Runs every configuration, controller off and on |
| `scripts/plot_results.py` | Builds the results table and figures |
| `results/` | CSV logs, JSON summaries and plots from each run |

## Requirements

Ubuntu 24.04 LTS (or any Linux with `/proc/pressure/memory`), 2 vCPU, 2 GB RAM,
2 GB swap. Experiments in the report used a Multipass VM:

```bash
multipass launch 24.04 --name osproj --cpus 2 --memory 2G --disk 10G
```

## Quick start (inside the Linux machine, from the project root)

```bash
bash scripts/setup_vm.sh            # packages, 2 GB swap, build, PSI check
python3 scripts/selftest.py         # T1-T3 must print ALL PASS
python3 scripts/calibrate.py 30     # one workload alone runs ~30 s
cd controller
python3 controller.py --n 5 --mode off   # baseline: monitor only
python3 controller.py --n 5 --mode on    # controller active
cd ..
bash scripts/run_experiment.sh      # N = 3,4,5, off and on, 3 repetitions
python3 scripts/plot_results.py     # results/summary.md + figures
```

`NS="5" REPS=1 bash scripts/run_experiment.sh` runs a single quick pair.

## Controller rules (defaults in `config.json`)

- **Suspend** the lowest-priority running workload when PSI `full avg10` > 10 % for 3 s,
  or the total working set of running workloads exceeds capacity C = MemTotal - 300 MB.
  The last running workload is never suspended.
- **Resume** the highest-priority suspended workload when no workload is running, or when
  PSI `some avg10` < 2 % for 10 s and its working set fits within 0.9 C.
- At most one action every 5 s. Ctrl+C resumes every suspended workload before exiting.

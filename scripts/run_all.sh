#!/bin/bash
# =============================================================================
# run_all.sh - run the whole project from the Mac in one go.
#
#   Setup -> tests T1-T3 -> calibration -> baseline run -> controller run
#   -> tests T6, T7 -> full experiment -> plots -> screenshots of every figure
#
# Run in the macOS Terminal app (not VS Code / iTerm), from anywhere:
#     bash ~/projects/os-psi-loadcontrol/scripts/run_all.sh
# Quicker round (1 repetition instead of 3):
#     REPS=1 bash ~/projects/os-psi-loadcontrol/scripts/run_all.sh
#
# Screenshots of this Terminal window are saved to docs/screenshots/.
# Terminal needs Screen Recording permission for the screenshots to show text:
# System Settings -> Privacy & Security -> Screen & System Audio Recording -> Terminal.
# =============================================================================
set -u

VM=${VM:-osproj}
REPS=${REPS:-3}
HERE="$(cd "$(dirname "$0")/.." && pwd)"          # project folder on the Mac
VMDIR=${VMDIR:-/home/ubuntu/os-psi-loadcontrol}  # same folder inside the VM
RES="$HERE/results"
SHOTS="$HERE/docs/screenshots"
mkdir -p "$SHOTS" "$RES"

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'; OFF=$'\033[0m'

say()  { printf '%s\n' "${BOLD}==> $*${OFF}"; }
warn() { printf '%s\n' "${RED}!! $*${OFF}"; }

# Run a command inside the VM, from the project folder.
vm() { multipass exec "$VM" -- bash -c "cd $VMDIR && $1"; }

# Clear the screen, show the command, run it in the VM (output stays on screen).
show() {
    clear
    printf '%s\n' "${BOLD}ubuntu@${VM}:~/os-psi-loadcontrol\$ $1${OFF}"
    vm "$1"
}

# Start the controller in the background inside the VM; its output goes to
# results/<log> and its PID to results/<log>.pid
vm_bg() {
    multipass exec "$VM" -- bash -c "cd $VMDIR/controller && \
        { nohup $1 > $VMDIR/results/$2 2>&1 < /dev/null & echo \$! > $VMDIR/results/$2.pid; }"
}

# Wait until a pattern appears in a results file (seen from the Mac side).
# usage: wait_for <pattern> <file> <timeout_s>
wait_for() {
    local waited=0
    while ! grep -q -- "$1" "$2" 2>/dev/null; do
        sleep 2; waited=$((waited + 2))
        if [ $((waited % 20)) -eq 0 ]; then
            printf '%s\n' "${DIM}   ... ${waited}s: $(tail -n 1 "$2" 2>/dev/null | cut -c1-110)${OFF}"
        fi
        [ "$waited" -ge "$3" ] && return 1
    done
    return 0
}

# Screenshot of this Terminal window.
WIN=""
if [ "${TERM_PROGRAM:-}" = "Apple_Terminal" ]; then
    WIN=$(osascript -e 'tell application "Terminal" to id of front window' 2>/dev/null || true)
fi
shot() {
    sleep 1
    if [ -n "$WIN" ]; then
        screencapture -x -o -l "$WIN" "$SHOTS/$1.png"
    else
        screencapture -x "$SHOTS/$1.png"
    fi
    printf '%s\n' "${GREEN}   [screenshot saved: docs/screenshots/$1.png]${OFF}"
    sleep 1
}

rest() { printf '%s\n' "${DIM}   resting ${1}s so memory pressure settles ...${OFF}"; sleep "$1"; }

# ----------------------------------------------------------------- checks
printf '\033[8;42;150t'          # make the window 150 x 42 so screenshots are readable
clear
say "PSI load-control project: full automatic run (REPS=$REPS)"
caffeinate -dimsu -w $$ &      # keep the Mac awake until this script ends

if ! command -v multipass >/dev/null; then
    warn "Multipass is not installed. Install it first: brew install --cask multipass"; exit 1
fi
if ! multipass info "$VM" >/dev/null 2>&1; then
    warn "VM '$VM' not found. Create it first:"
    echo "   multipass launch 24.04 --name $VM --cpus 2 --memory 2G --disk 10G"; exit 1
fi
multipass start "$VM" >/dev/null 2>&1
if ! multipass info "$VM" | grep -q "$HERE => $VMDIR"; then
    say "Sharing $HERE with the VM"
    multipass umount "$VM:$VMDIR" >/dev/null 2>&1
    multipass mount "$HERE" "$VM:$VMDIR" || { warn "mount failed"; exit 1; }
fi
vm "pkill -x wsload; pkill -f '[c]ontroller.py'; true" >/dev/null 2>&1

# ----------------------------------------------------------------- 1. setup
show "bash scripts/setup_vm.sh" || { warn "setup failed - see the messages above"; exit 1; }
sleep 2

# Figure 2
show "cat /proc/pressure/memory && free -h"
shot fig2_psi_available

# Tests T1-T3
show "python3 scripts/selftest.py" || warn "a self-test failed - see above"
shot test_T1_T3_selftest

# Calibration
show "python3 scripts/calibrate.py 30" || { warn "calibration failed"; exit 1; }
sleep 3

# ------------------------------------------------- 2. baseline (Figure 4)
clear
say "Baseline: 5 workloads, controller OFF (monitor only). Up to ~6 minutes."
rm -f "$RES/live_baseline.log"
vm_bg "python3 controller.py --n 5 --mode off" live_baseline.log
sleep 40
show "vmstat 1 15"
shot fig4_baseline_vmstat
say "Waiting for the baseline run to finish ..."
wait_for "=== summary" "$RES/live_baseline.log" 900 || warn "baseline did not finish in time"
clear
printf '%s\n' "${BOLD}Baseline run (controller off): summary${OFF}"
sed -n '/=== summary/,$p' "$RES/live_baseline.log"
shot extra_baseline_summary
rest 20

# ------------------------------------- 3. controller on (Figures 3, 5, 6, 7)
clear
say "Controller ON: 5 workloads."
rm -f "$RES/live_controller.log"
vm_bg "python3 controller.py --n 5 --mode on" live_controller.log
wait_for "started pid .* priority P1" "$RES/live_controller.log" 60
sleep 3
clear
printf '%s\n' "${BOLD}ubuntu@${VM}:~/os-psi-loadcontrol/controller\$ cat config.json${OFF}"
cat "$HERE/controller/config.json"
printf '%s\n' "${BOLD}ubuntu@${VM}:~/os-psi-loadcontrol/controller\$ python3 controller.py --n 5 --mode on${OFF}"
head -n 9 "$RES/live_controller.log"
shot fig3_controller_startup

if wait_for "SUSPEND pid" "$RES/live_controller.log" 240; then
    clear
    printf '%s\n' "${BOLD}Controller output up to the first suspension:${OFF}"
    sed -n '1,/SUSPEND pid/p' "$RES/live_controller.log" | tail -n 30
    shot fig5_suspend
    show "ps -o pid,stat,cmd -C wsload"
    shot fig7_ps_state_T
else
    warn "no suspension happened in 240 s (Figures 5 and 7 not taken)"
fi

if wait_for "RESUME  pid" "$RES/live_controller.log" 900; then
    clear
    printf '%s\n' "${BOLD}Controller output up to the first resume:${OFF}"
    sed -n '1,/RESUME  pid/p' "$RES/live_controller.log" | tail -n 30
    shot fig6_resume
else
    warn "no resume happened (Figure 6 not taken)"
fi

wait_for "=== summary" "$RES/live_controller.log" 900 || warn "controller run did not finish in time"
clear
printf '%s\n' "${BOLD}Controller run: suspend/resume events and summary (test T8)${OFF}"
grep -E "SUSPEND|RESUME|TIMEOUT" "$RES/live_controller.log"
sed -n '/=== summary/,$p' "$RES/live_controller.log"
shot test_T8_summary
rest 20

# ----------------------------------------------------- 4. test T6 (Ctrl+C)
clear
say "Test T6: interrupt the controller while a workload is suspended."
rm -f "$RES/live_t6.log"
vm_bg "python3 controller.py --n 5 --mode on --rep 9" live_t6.log
if wait_for "SUSPEND pid" "$RES/live_t6.log" 240; then
    sleep 2
    BEFORE=$(vm "ps -o pid,stat,cmd -C wsload")
    vm "kill -INT \$(cat results/live_t6.log.pid)"
    sleep 4
    AFTER=$(vm "ps -o pid,stat,cmd -C wsload")
    clear
    printf '%s\n' "${BOLD}Test T6 - before interrupting (one workload in state T = stopped):${OFF}"
    printf '%s\n' "\$ ps -o pid,stat,cmd -C wsload" "$BEFORE"
    printf '%s\n' "" "${BOLD}Controller receives SIGINT (same signal as Ctrl+C):${OFF}"
    grep -E "SUSPEND|RESUME|\[exit\]" "$RES/live_t6.log"
    printf '%s\n' "" "${BOLD}After the controller exits (no workload left in state T):${OFF}"
    printf '%s\n' "\$ ps -o pid,stat,cmd -C wsload" "$AFTER"
    shot test_T6_ctrl_c
else
    warn "no suspension in T6 run"
fi
vm "pkill -x wsload; rm -f results/N5_on_r9.csv results/N5_on_r9.json; true"
rest 20

# ------------------------------------------- 5. test T7 (single workload)
show "cd controller && python3 controller.py --n 1 --mode on --rep 9 | grep -v '^t='"
shot test_T7_single_workload
vm "rm -f results/N1_on_r9.csv results/N1_on_r9.json results/*.pid; true"
rest 20

# ------------------------------------------------- 6. full experiment
clear
say "Full experiment: N = 3, 4, 5, controller off and on, $REPS repetition(s)."
say "This is the long part (up to ~$((REPS * 32)) minutes). You can leave the Mac; keep it plugged in."
vm "REPS=$REPS bash scripts/run_experiment.sh" | grep --line-buffered -E "####|SUSPEND|RESUME|TIMEOUT|makespan_s|finished "

# ------------------------------------------------- 7. plots and table
show "python3 scripts/plot_results.py"
shot results_table8
cp "$RES/fig8_psi_full.png" "$SHOTS/fig8_psi_full.png" 2>/dev/null
cp "$RES/fig9_wss.png" "$SHOTS/fig9_wss.png" 2>/dev/null
cp "$RES/fig_throughput.png" "$SHOTS/fig_throughput.png" 2>/dev/null

clear
say "All done."
echo "Screenshots and figures:   $SHOTS"
ls -1 "$SHOTS"
echo
echo "Numbers for Table 8:       $RES/summary.md"
echo "Per-run details:           $RES/*.json"
open "$SHOTS" 2>/dev/null

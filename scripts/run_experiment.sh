#!/usr/bin/env bash
# Run every configuration: N in NS, controller off and on, REPS repetitions.
# Run from the project root:
#   bash scripts/run_experiment.sh            # N = 3 4 5, 3 repetitions
#   NS="5" REPS=1 bash scripts/run_experiment.sh   # quick single run
set -euo pipefail
cd "$(dirname "$0")/../controller"

NS=${NS:-"3 4 5"}
REPS=${REPS:-3}
REST=${REST:-20}      # seconds between runs so PSI averages settle

for rep in $(seq 1 "$REPS"); do
  for n in $NS; do
    for mode in off on; do
      echo
      echo "################ N=$n mode=$mode rep=$rep ################"
      python3 controller.py --n "$n" --mode "$mode" --rep "$rep"
      pkill -x wsload 2>/dev/null || true
      echo "resting ${REST}s ..."
      sleep "$REST"
    done
  done
done
echo
echo "All runs done. Now run: python3 scripts/plot_results.py"

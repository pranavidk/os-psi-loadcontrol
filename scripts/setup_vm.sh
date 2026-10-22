#!/usr/bin/env bash
# One-time setup inside the Ubuntu VM: packages, swap, build, PSI check.
# Run from the project root:  bash scripts/setup_vm.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Installing packages (gcc, make, matplotlib)"
sudo apt-get update -qq
sudo apt-get install -y -qq build-essential python3-matplotlib procps

echo "== Swap"
swap_kb=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
if [ "$swap_kb" -lt 1000000 ]; then
    echo "   no swap found; creating a 2 GB swap file at /swapfile"
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
else
    echo "   swap already present: $((swap_kb / 1024)) MB"
fi

echo "== Building the workload generator"
gcc -O2 -Wall -Wextra -o workload/wsload workload/wsload.c
echo "   built workload/wsload"

echo "== PSI check"
if [ -r /proc/pressure/memory ]; then
    cat /proc/pressure/memory
    echo "   PSI is available."
else
    echo "   ERROR: /proc/pressure/memory not found. This kernel has no PSI." >&2
    exit 1
fi

echo "== Memory"
free -h
echo "Setup complete."

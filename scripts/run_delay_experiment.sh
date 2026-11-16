#!/usr/bin/env bash
# Run the current pipeline using an already generated scenario_sites.csv.
# Usage: bash scripts/run_delay_experiment.sh K S MAX_RTT_US
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 K S MAX_RTT_US" >&2
    exit 2
fi

k_pools=$1
synthetic_count=$2
max_rtt_us=$3

# Shared physical assumptions for pool placement and delay evaluation.
speed_km_s=200000
route_factor=1.0
fixed_rtt_us=0.0

if [[ ! $k_pools =~ ^[1-9][0-9]*$ || ! $synthetic_count =~ ^(0|[1-9][0-9]*)$ ]]; then
    echo "K must be a positive integer; S must be a nonnegative integer." >&2
    exit 2
fi

if command -v python >/dev/null 2>&1; then
    python_bin=python
elif command -v python3 >/dev/null 2>&1; then
    python_bin=python3
else
    echo "Python is not available. Activate the project environment." >&2
    exit 1
fi

max_distance_km=$("$python_bin" -c '
import math, sys
try:
    max_rtt, speed, factor, fixed_rtt = map(float, sys.argv[1:])
    if not all(math.isfinite(v) for v in (max_rtt, speed, factor, fixed_rtt)):
        raise ValueError
    if max_rtt <= fixed_rtt or speed <= 0 or factor < 1 or fixed_rtt < 0:
        raise ValueError
    radius = (max_rtt - fixed_rtt) * speed / (2 * factor * 1e6)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError
except ValueError:
    sys.exit("Invalid delay model: MAX_RTT_US must exceed the fixed RTT and yield a finite positive radius.")
print(radius)
' "$max_rtt_us" "$speed_km_s" "$route_factor" "$fixed_rtt_us")

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd -- "$script_dir/.."

echo "Delay model: speed=$speed_km_s km/s, route factor=$route_factor, fixed RTT=$fixed_rtt_us us"
echo "[1/4] K-Medoids: K=$k_pools, maximum distance=$max_distance_km km (using existing scenario_sites.csv)"
"$python_bin" scripts/bbu_initial_assignment_kmedian.py -k "$k_pools" --max-distance-km "$max_distance_km"

echo "[2/4] Delay feasibility: K=$k_pools S=$synthetic_count RTT<$max_rtt_us us"
"$python_bin" scripts/analyze_delay_feasibility.py -k "$k_pools" -s "$synthetic_count" \
    --max-rtt-us "$max_rtt_us" --speed-km-s "$speed_km_s" \
    --route-factor "$route_factor" --fixed-rtt-us "$fixed_rtt_us"

echo "[3/4] Assignment map"
"$python_bin" scripts/create_delay_assignment_map.py -k "$k_pools" -s "$synthetic_count"

echo "[4/4] Delay report"
"$python_bin" scripts/generate_delay_report.py -k "$k_pools" -s "$synthetic_count"

echo "Experiment completed. Results: output/delay/"

#!/usr/bin/env bash
# Regenerate every number and figure in the paper.
# Requires Python 3.10+ (with requirements.txt installed) and Go 1.22+.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/5] Byte accounting on Loghub (Table 2, Fig. 3)"
python3 bytes/measure_bytes.py

echo "[2/5] Go benchmarks (Table 3); takes about 2 minutes"
( cd bench && go mod tidy && go test -run TestExpandRoundTrip . && \
  go test -run xxx -bench . -benchtime=2s -count=5 ) | grep '^Benchmark' | tee results/bench.txt
python3 bench/summarize.py results/bench.txt

echo "[2b] Sensitivity (Tables 8, 9) and ingest/envelope benchmarks"
python3 bytes/sensitivity.py --offline
( cd bench/ingest && go test -run xxx -bench . -benchtime=2s -count=5 ) | grep '^Benchmark' | tee results/bench_ingest.txt
python3 bench/summarize.py results/bench_ingest.txt results/bench_ingest_summary.csv
( cd bench/ingest && go test -run TestEnvelopeFootprint -v . ) | grep heap_per | sed 's/^ *//' > results/envelope.txt

echo "[3/5] Emulator experiments (Tables 4 to 7, Fig. 4)"
python3 experiments/run_all.py --seeds 5

echo "[4/5] Figures"
python3 analysis/make_figures.py

echo "[5/5] Done. Results in results/, figures in figures/"

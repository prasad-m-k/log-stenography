#!/usr/bin/env python3
"""Turn `go test -bench` output into medians.
Usage: python bench/summarize.py results/bench.txt [results/bench_summary.csv]"""
import collections, csv, os, re, statistics as st, sys

src = sys.argv[1] if len(sys.argv) > 1 else "results/bench.txt"
runs = collections.defaultdict(list)
for line in open(src):
    f = line.split()
    if not f or not f[0].startswith("Benchmark"):
        continue
    name = re.sub(r"-\d+$", "", f[0])
    metrics = {f[i + 1]: float(f[i]) for i in range(2, len(f) - 1, 2)}
    runs[name].append(metrics)
out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(src), "bench_summary.csv")
with open(out, "w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["benchmark", "runs", "ns_per_op_median", "ns_per_op_min", "ns_per_op_max", "bytes_per_line", "B_per_op", "allocs_per_op"])
    for name, ms in runs.items():
        ns = [m["ns/op"] for m in ms]
        w.writerow([name, len(ms), st.median(ns), min(ns), max(ns),
                    ms[0].get("B/line", ms[0].get("B/record", "")), ms[0].get("B/op", ""), ms[0].get("allocs/op", "")])
        print(f"{name:24s} median {st.median(ns):8.1f} ns  (min {min(ns):.1f}, max {max(ns):.1f})")
print("wrote", out)

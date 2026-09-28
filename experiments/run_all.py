#!/usr/bin/env python3
"""Run every emulator experiment in the Log Stenography paper.

Line sizes come from results/bytes.json (pooled Loghub means plus the 41-byte
CRI prefix), so run bytes/measure_bytes.py first.

Usage:  python experiments/run_all.py [--seeds 5]
Output: results/*.csv
"""
import argparse
import csv
import json
import os
import statistics as st
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sim"))
from nodesim_steno import Params, run, MB  # noqa: E402

RES = os.path.join(ROOT, "results")
PREFIX = 41


def line_sizes():
    pooled = json.load(open(os.path.join(RES, "bytes.json")))[-1]
    return {"json": pooled["J"] + PREFIX, "stroke": pooled["S2"] + PREFIX}


def agg(runs, key):
    vals = [r[key] for r in runs if r[key] is not None]
    if not vals:
        return None, None
    return st.mean(vals), (st.stdev(vals) if len(vals) > 1 else 0.0)


def write(name, rows):
    with open(os.path.join(RES, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    print(f"wrote results/{name} ({len(rows)} rows)")


def summarize(runs, **cond):
    row = dict(cond)
    for key in ("loss_ratio", "first_loss_s", "largest_0log_MB", "escrow_held_max_MB", "never_opened_files", "rotations"):
        m, s = agg(runs, key)
        row[key + "_mean"] = m
        if key == "loss_ratio":
            row["loss_ratio_sd"] = s
    return row


def validation(seeds):
    """Reproduce the companion paper's Table 5 and Fig. 4 with 256-byte lines."""
    ref_rate = {40: 14.4, 48: 28.4, 64: 46.1}
    ref_poll = {10: 0.0, 5: 50.0, 2: 78.7, 1: 89.3}
    rows = []
    for W in (8, 16, 24, 32, 40, 48, 64):
        rs = [run(Params(W=W * MB, line=256, seed=s)) for s in range(seeds)]
        r = summarize(rs, experiment="write_rate", W_MBps=W, I_m=10, shipper="event")
        r["companion_loss_pct"] = ref_rate.get(W, 0.0)
        rows.append(r)
    for I_m in (10, 5, 2, 1):
        rs = [run(Params(W=16 * MB, line=256, I_m=I_m, mode="poll", seed=s)) for s in range(seeds)]
        r = summarize(rs, experiment="polling", W_MBps=16, I_m=I_m, shipper="poll")
        r["companion_loss_pct"] = ref_poll[I_m]
        rows.append(r)
    write("validation.csv", rows)


def event_rate_sweep(seeds, L):
    rows = []
    for rate_k in (50, 100, 125, 150, 175, 200, 250, 300, 350, 400):
        for enc in ("json", "stroke"):
            W = rate_k * 1000 * L[enc]
            rs = [run(Params(W=W, line=L[enc], seed=s)) for s in range(seeds)]
            rows.append(summarize(rs, encoding=enc, events_per_s=rate_k * 1000, line_B=L[enc], W_MBps=W / MB))
            re = [run(Params(W=W, line=L[enc], escrow=True, seed=s)) for s in range(seeds)]
            rows[-1]["loss_with_escrow"] = st.mean(r["loss_ratio"] for r in re)
            rows[-1]["escrow_held_max_MB_mean"] = st.mean(r["escrow_held_max_MB"] for r in re)
    write("event_rate_sweep.csv", rows)


def per_line_cost(seeds, L, rate=150_000):
    rows = []
    R = 32 * MB
    for c_us in (0.0, 0.5, 1.0, 2.0, 4.0):
        c = c_us * 1e-6
        onset = {e: 1.0 / (L[e] / R + c) for e in L}
        row = dict(c_line_us=c_us, onset_json_eps=onset["json"], onset_stroke_eps=onset["stroke"],
                   k_eff=onset["stroke"] / onset["json"], events_per_s=rate)
        for enc in ("json", "stroke"):
            rs = [run(Params(W=rate * L[enc], line=L[enc], c_line=c, seed=s)) for s in range(seeds)]
            row[f"loss_{enc}"] = st.mean(r["loss_ratio"] for r in rs)
        rows.append(row)
    write("per_line_cost.csv", rows)


def polling(seeds, L):
    rate = 16 * MB / L["json"]                  # JSON writes 16 MB/s, as in the companion's Fig. 4
    rows = []
    for I_m in (10, 5, 2, 1):
        for enc in ("json", "stroke"):
            W = rate * L[enc]
            rs = [run(Params(W=W, line=L[enc], I_m=I_m, mode="poll", seed=s)) for s in range(seeds)]
            rows.append(summarize(rs, encoding=enc, I_m=I_m, events_per_s=rate, W_MBps=W / MB))
    write("polling.csv", rows)


def eviction(seeds, L):
    rate = 8 * MB / L["json"]                   # JSON writes 8 MB/s, as in the companion's Section 10.7
    rows = []
    for ev in (30, 40, 50, 60):
        for enc in ("json", "stroke"):
            W = rate * L[enc]
            rs = [run(Params(W=W, line=L[enc], pause=(20, 70), evict_at=ev, seed=s)) for s in range(seeds)]
            re = [run(Params(W=W, line=L[enc], pause=(20, 70), evict_at=ev, escrow=True, seed=s)) for s in range(seeds)]
            row = summarize(rs, encoding=enc, evict_at_s=ev, events_per_s=rate, W_MBps=W / MB)
            row["loss_with_escrow"] = st.mean(r["loss_ratio"] for r in re)
            row["escrow_held_max_MB_mean"] = st.mean(r["escrow_held_max_MB"] for r in re)
            rows.append(row)
    write("eviction.csv", rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    a = ap.parse_args()
    t0 = time.time()
    L = line_sizes()
    print("on-disk line sizes (bytes):", {k: round(v, 2) for k, v in L.items()})
    validation(a.seeds)
    event_rate_sweep(a.seeds, L)
    per_line_cost(a.seeds, L)
    polling(a.seeds, L)
    eviction(a.seeds, L)
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()

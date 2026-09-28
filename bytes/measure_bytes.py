#!/usr/bin/env python3
"""Byte accounting for log stenography on Loghub samples.

For each Loghub 2k sample it builds three encodings of every line (plaintext,
JSON, stroke), checks that every stroke expands back to the original message,
adds the CRI prefix the container runtime writes, and measures gzip sizes of
CRI-framed files, since the kubelet gzips rotated log files.

Usage:
    python bytes/measure_bytes.py            # downloads Loghub samples into data/
    python bytes/measure_bytes.py --offline  # uses files already in data/

Outputs: results/bytes.csv, results/bytes.json
"""
import argparse
import csv
import gzip
import json
import os
import random
import re
import urllib.request

SYSTEMS = ["HDFS", "Hadoop", "Spark", "Zookeeper", "BGL", "OpenStack", "Apache", "Android"]
BASE = "https://raw.githubusercontent.com/logpai/loghub/master"
PREFIX = 41                       # "2026-09-24T10:00:00.123456789Z stdout F " + newline
HDR_SKIP = {"LineId", "Content", "EventId", "EventTemplate", "Label", "Logrecord"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
RES = os.path.join(ROOT, "results")


def b36(n):
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    while True:
        n, r = divmod(n, 36)
        out = digits[r] + out
        if n == 0:
            return out


def template_regex(t):
    parts = t.split("<*>")
    return re.compile("^" + "(.*?)".join(re.escape(x) for x in parts) + "$", re.S)


def escape_arg(s):
    return s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")


def fetch(system):
    os.makedirs(DATA, exist_ok=True)
    for suffix in ("_2k.log", "_2k.log_structured.csv"):
        path = os.path.join(DATA, system + suffix)
        if not os.path.exists(path):
            urllib.request.urlretrieve(f"{BASE}/{system}/{system}{suffix}", path)


def cri_frame(payloads, seed):
    """Prepend a CRI prefix with a realistic RFC 3339 nano timestamp to each line."""
    rng = random.Random(seed)
    out = []
    sec = 36000                                  # 10:00:00
    for p in payloads:
        ns = rng.randrange(1_000_000_000)
        frac = f"{ns:09d}".rstrip("0") or "0"    # RFC3339Nano trims trailing zeros
        h, m, s = sec // 3600, (sec // 60) % 60, sec % 60
        out.append(f"2026-09-24T{h:02d}:{m:02d}:{s:02d}.{frac}Z stdout F {p}\n")
        if rng.random() < 0.02:
            sec += 1
    return "".join(out).encode()


def encode_system(system):
    raw = [l.rstrip("\n").rstrip("\r") for l in open(os.path.join(DATA, f"{system}_2k.log"), encoding="utf-8", errors="replace")]
    rows = list(csv.DictReader(open(os.path.join(DATA, f"{system}_2k.log_structured.csv"), encoding="utf-8", errors="replace")))
    n = min(len(raw), len(rows))
    raw, rows = raw[:n], rows[:n]
    tid, cid, cache = {}, {}, {}
    P, J, S1, S2 = [], [], [], []
    fallback = roundtrip = 0
    dict_bytes = 0
    for i, r in enumerate(rows):
        hdr = {k: v for k, v in r.items() if k not in HDR_SKIP}
        j = {"ts": (r.get("Date", "") + "T" + r.get("Time", "")).strip("T")}
        for k, v in hdr.items():
            if k in ("Date", "Time"):
                continue
            j[{"Level": "level", "Component": "logger"}.get(k, k.lower())] = v
        j["msg"] = r["Content"]
        J.append(json.dumps(j, separators=(",", ":")))
        t, c = r["EventTemplate"], r["Content"]
        if t not in tid:
            tid[t] = len(tid)
            dict_bytes += len(t.encode()) + 4
        if t not in cache:
            cache[t] = template_regex(t)
        m = cache[t].match(c)
        if m:
            params = list(m.groups())
            rebuilt = t
            for a in params:
                rebuilt = rebuilt.replace("<*>", a, 1)
            roundtrip += rebuilt == c
            body = b36(tid[t]) + ("".join("\t" + escape_arg(a) for a in params))
        else:
            fallback += 1
            body = "~" + escape_arg(c)
        comp = r.get("Component", "")
        if comp not in cid:
            cid[comp] = len(cid)
        lvl = r.get("Level", "") or "-"
        keep1 = [v for k, v in hdr.items() if k not in ("Level", "Component")]
        keep2 = [v for k, v in hdr.items() if k not in ("Level", "Component", "Date", "Time", "Timestamp")]
        S1.append(" ".join([b36(i), "a", lvl] + keep1 + [b36(cid[comp]), body]))
        S2.append(" ".join([b36(i), "a", lvl] + keep2 + [b36(cid[comp]), body]))
        P.append(raw[i])
    size = lambda xs: sum(len(x.encode()) for x in xs)
    gz = {}
    for name, xs in (("P", P), ("J", J), ("S1", S1), ("S2", S2)):
        framed = cri_frame(xs, seed=sum(map(ord, system)))
        gz[name] = (len(framed), len(gzip.compress(framed, compresslevel=6)))
    res = dict(system=system, lines=n, templates=len(tid), fallback=fallback, roundtrip_exact=roundtrip,
               dict_kb=round(dict_bytes / 1024, 2),
               P=size(P) / n, J=size(J) / n, S1=size(S1) / n, S2=size(S2) / n)
    res["k_payload_plain"] = size(P) / size(S1)
    res["k_payload_json"] = size(J) / size(S2)
    res["k_disk_plain"] = (size(P) + PREFIX * n) / (size(S1) + PREFIX * n)
    res["k_disk_json"] = (size(J) + PREFIX * n) / (size(S2) + PREFIX * n)
    for name in ("P", "J", "S1", "S2"):
        res[f"gzip_ratio_{name}"] = gz[name][0] / gz[name][1]
        res[f"gzip_bytes_per_line_{name}"] = gz[name][1] / n
    res["k_gzip_json"] = gz["J"][1] / gz["S2"][1]
    res["k_gzip_plain"] = gz["P"][1] / gz["S1"][1]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    os.makedirs(RES, exist_ok=True)
    rows = []
    for s in SYSTEMS:
        if not a.offline:
            fetch(s)
        rows.append(encode_system(s))
    n = sum(r["lines"] for r in rows)
    tot = lambda k: sum(r[k] * r["lines"] for r in rows)
    pooled = dict(system="Pooled", lines=n, templates=sum(r["templates"] for r in rows),
                  fallback=sum(r["fallback"] for r in rows), roundtrip_exact=sum(r["roundtrip_exact"] for r in rows),
                  dict_kb=round(sum(r["dict_kb"] for r in rows), 2),
                  P=tot("P") / n, J=tot("J") / n, S1=tot("S1") / n, S2=tot("S2") / n)
    pooled["k_payload_plain"] = tot("P") / tot("S1")
    pooled["k_payload_json"] = tot("J") / tot("S2")
    pooled["k_disk_plain"] = (tot("P") + PREFIX * n) / (tot("S1") + PREFIX * n)
    pooled["k_disk_json"] = (tot("J") + PREFIX * n) / (tot("S2") + PREFIX * n)
    for name in ("P", "J", "S1", "S2"):
        pooled[f"gzip_bytes_per_line_{name}"] = tot(f"gzip_bytes_per_line_{name}") / n
    pooled["gzip_ratio_J"] = (pooled["J"] + PREFIX) / pooled["gzip_bytes_per_line_J"]
    pooled["gzip_ratio_S2"] = (pooled["S2"] + PREFIX) / pooled["gzip_bytes_per_line_S2"]
    pooled["gzip_ratio_P"] = (pooled["P"] + PREFIX) / pooled["gzip_bytes_per_line_P"]
    pooled["gzip_ratio_S1"] = (pooled["S1"] + PREFIX) / pooled["gzip_bytes_per_line_S1"]
    pooled["k_gzip_json"] = pooled["gzip_bytes_per_line_J"] / pooled["gzip_bytes_per_line_S2"]
    pooled["k_gzip_plain"] = pooled["gzip_bytes_per_line_P"] / pooled["gzip_bytes_per_line_S1"]
    rows.append(pooled)
    keys = list(rows[0].keys())
    with open(os.path.join(RES, "bytes.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    json.dump(rows, open(os.path.join(RES, "bytes.json"), "w"), indent=1)
    for r in rows:
        print(f"{r['system']:10s} disk k plain={r['k_disk_plain']:.2f} json={r['k_disk_json']:.2f} "
              f"| gzip k plain={r['k_gzip_plain']:.2f} json={r['k_gzip_json']:.2f} "
              f"| roundtrip {r['roundtrip_exact']}/{r['lines']} fallback {r['fallback']}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Sensitivity of the stroke encoding to argument bytes, framing, and escaping.

Answers four questions on the same Loghub samples used by measure_bytes.py:

1. How does the on-disk reduction factor k (JSON vs stroke S2) depend on the
   share of each JSON payload that is argument bytes? Lines are binned by that
   share and compared with the closed form k = (b + p) / (delta*b + h + p).
2. What happens when every line gains one high-entropy argument (UUID, trace
   context, Base64 blob)? Measured as written and after gzip.
3. How often do arguments need escaping, and what does a multiline stack
   trace cost as plaintext, JSON, and a stroke?
4. How does k change if the runtime framing differs from the 41-byte CRI
   prefix (trimmed nanoseconds, Docker json-file framing)?

Usage: python bytes/sensitivity.py [--offline]   (needs data/ from measure_bytes.py)
Outputs: results/sensitivity.json, results/sensitivity_bins.csv
"""
import argparse
import base64
import csv
import gzip
import json
import os
import random
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import measure_bytes as mb  # noqa: E402

P = mb.PREFIX


def encode_lines(system):
    """Per-line JSON payload, stroke S2 payload, argument bytes, and raw args."""
    raw_rows = list(csv.DictReader(open(os.path.join(mb.DATA, f"{system}_2k.log_structured.csv"),
                                        encoding="utf-8", errors="replace")))
    raw = [l.rstrip("\n").rstrip("\r") for l in open(os.path.join(mb.DATA, f"{system}_2k.log"),
                                                       encoding="utf-8", errors="replace")]
    n = min(len(raw), len(raw_rows))
    tid, cid, cache, out = {}, {}, {}, []
    for i, r in enumerate(raw_rows[:n]):
        hdr = {k: v for k, v in r.items() if k not in mb.HDR_SKIP}
        j = {"ts": (r.get("Date", "") + "T" + r.get("Time", "")).strip("T")}
        for k, v in hdr.items():
            if k in ("Date", "Time"):
                continue
            j[{"Level": "level", "Component": "logger"}.get(k, k.lower())] = v
        j["msg"] = r["Content"]
        t, c = r["EventTemplate"], r["Content"]
        tid.setdefault(t, len(tid))
        if t not in cache:
            cache[t] = mb.template_regex(t)
        m = cache[t].match(c)
        params = list(m.groups()) if m else None
        comp = r.get("Component", "")
        cid.setdefault(comp, len(cid))
        lvl = r.get("Level", "") or "-"
        keep2 = [v for k, v in hdr.items() if k not in ("Level", "Component", "Date", "Time", "Timestamp")]
        head = " ".join([mb.b36(i), "a", lvl] + keep2 + [mb.b36(cid[comp])])
        out.append(dict(j=j, head=head, tid=mb.b36(tid[t]), params=params, content=c, plain=raw[i]))
    return out


def s2_of(x, extra=None):
    params = list(x["params"]) + ([extra] if extra is not None else [])
    return x["head"] + " " + x["tid"] + "".join("\t" + mb.escape_arg(a) for a in params)


def json_of(x, extra=None):
    j = dict(x["j"])
    if extra is not None:
        j["msg"] = j["msg"] + " id=" + extra
    return json.dumps(j, separators=(",", ":"))


def argbytes(x, extra=None):
    params = list(x["params"]) + ([extra] if extra is not None else [])
    return sum(1 + len(mb.escape_arg(a).encode()) for a in params)


def gz_per_line(payloads, seed):
    framed = mb.cri_frame(payloads, seed)
    return len(gzip.compress(framed, compresslevel=6)) / len(payloads)


def docker_jsonfile(payload, ts):
    # Docker json-file driver line: {"log":"...\n","stream":"stdout","time":"..."}\n
    return len(json.dumps({"log": payload + "\n", "stream": "stdout", "time": ts},
                          separators=(",", ":")).encode()) + 1


def java_trace(n, rng):
    pk = ["org.apache.hadoop.hdfs.server.datanode", "org.apache.zookeeper.server", "java.util.concurrent",
          "org.eclipse.jetty.server", "com.example.orders.service"]
    lines = ["java.io.IOException: Connection reset by peer"]
    for _ in range(n):
        p = rng.choice(pk)
        cls = rng.choice(["DataXceiver", "NIOServerCnxn", "ThreadPoolExecutor", "HttpChannel", "OrderHandler"])
        lines.append(f"\tat {p}.{cls}.{rng.choice(['run', 'process', 'handle', 'readBlock'])}"
                     f"({cls}.java:{rng.randrange(40, 900)})")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    if not a.offline:
        for s in mb.SYSTEMS:
            mb.fetch(s)
    allx, per_sys = [], {}
    for s in mb.SYSTEMS:
        xs = encode_lines(s)
        assert all(x["params"] is not None for x in xs), s
        per_sys[s] = xs
        allx += xs

    # ---- 1. per-line decomposition and binning by argument share
    rows = []
    sysof = [s for s, xs in per_sys.items() for _ in xs]
    for x in allx:
        b = len(json_of(x).encode())
        s2 = len(s2_of(x).encode())
        ab = argbytes(x)
        rows.append((b, s2, ab))
    B = sum(r[0] for r in rows); S = sum(r[1] for r in rows); A = sum(r[2] for r in rows); n = len(rows)
    h = (S - A) / n                         # mean non-argument stroke bytes
    bmean = B / n
    delta_pooled = A / B
    edges = [0, .05, .10, .15, .20, .30, .40, .60, 1.01]
    bins = []
    for lo, hi in zip(edges, edges[1:]):
        idx = [i for i, r in enumerate(rows) if lo <= r[2] / r[0] < hi]
        sel = [rows[i] for i in idx]
        if not sel:
            continue
        comp = {}
        for i in idx:
            comp[sysof[i]] = comp.get(sysof[i], 0) + 1
        top = max(comp, key=comp.get)
        sb = sum(r[0] for r in sel); ss = sum(r[1] for r in sel); sa = sum(r[2] for r in sel); m = len(sel)
        d = sa / sb
        hb = (ss - sa) / m
        bins.append(dict(bin=f"{lo:.2f}-{min(hi, 1):.2f}", lines=m, mean_json_payload=sb / m,
                         mean_delta=d, mean_h=hb,
                         k_measured=(sb + P * m) / (ss + P * m),
                         k_model_pooled_h=(sb / m + P) / (d * sb / m + h + P),
                         top_system=top, top_share=comp[top] / m))
    sysd = {s: sum(argbytes(x) for x in xs) / sum(len(json_of(x).encode()) for x in xs) for s, xs in per_sys.items()}
    # break-even argument share at pooled b and h: delta* = 1 - h/b
    delta_star = 1 - h / bmean
    per_line_k = [(r[0] + P) / (r[1] + P) for r in rows]
    k_min, lines_below_1 = min(per_line_k), sum(1 for k in per_line_k if k < 1)

    # ---- 2. inject one high-entropy argument into every line
    rng = random.Random(7)
    kinds = {
        "uuid (36 B)": lambda: str(uuid.UUID(int=rng.getrandbits(128))),
        "trace+span ids (49 B)": lambda: f"{rng.getrandbits(128):032x}-{rng.getrandbits(64):016x}",
        "base64 blob (256 B)": lambda: base64.b64encode(rng.randbytes(192)).decode(),
        "base64 blob (1024 B)": lambda: base64.b64encode(rng.randbytes(768)).decode(),
    }
    inject = []
    J0 = [json_of(x) for x in allx]; S0 = [s2_of(x) for x in allx]
    base_disk = (sum(len(j.encode()) for j in J0) + P * n) / (sum(len(s.encode()) for s in S0) + P * n)
    base_gz = gz_per_line(J0, 1) / gz_per_line(S0, 1)
    eq10 = lambda d: (bmean + d + P) / (S / n + d + P)
    inject.append(dict(kind="none", added_bytes=0, k_disk=base_disk, k_eq10=eq10(0), k_gzip=base_gz))
    for name, gen in kinds.items():
        extras = [gen() for _ in allx]
        J = [json_of(x, e) for x, e in zip(allx, extras)]
        S2 = [s2_of(x, e) for x, e in zip(allx, extras)]
        kd = (sum(len(j.encode()) for j in J) + P * n) / (sum(len(s.encode()) for s in S2) + P * n)
        inject.append(dict(kind=name, added_bytes=len(extras[0]), k_disk=kd, k_eq10=eq10(len(extras[0])),
                           k_gzip=gz_per_line(J, 1) / gz_per_line(S2, 1)))

    # ---- 3. escaping and stack traces
    allargs = [p for x in allx for p in x["params"]]
    esc_args = sum(1 for p in allargs if any(ch in p for ch in "\t\n\\"))
    esc_bytes = sum(len(mb.escape_arg(p)) - len(p) for p in allargs)
    traces = []
    trng = random.Random(11)
    head_json = {"ts": "2026-09-24T10:00:00Z", "level": "ERROR", "logger": "com.example.orders.OrderHandler",
                 "msg": "request failed"}
    for frames in (10, 30, 60):
        tl = java_trace(frames, trng)
        text = "\n".join(tl)
        plain_lines = ["2026-09-24 10:00:00 ERROR OrderHandler request failed"] + tl
        plain_disk = sum(len(l.encode()) + P for l in plain_lines)
        jd = dict(head_json, stack=text)
        json_disk = len(json.dumps(jd, separators=(",", ":")).encode()) + P
        stroke = "1k a ERROR 3 7\t" + mb.escape_arg(text)
        stroke_disk = len(stroke.encode()) + P
        traces.append(dict(frames=frames, trace_bytes=len(text.encode()), plain_lines=len(plain_lines),
                           plain_disk=plain_disk, json_disk=json_disk, stroke_disk=stroke_disk,
                           stroke_escape_bytes=len(mb.escape_arg(text)) - len(text),
                           k_vs_plain=plain_disk / stroke_disk, k_vs_json=json_disk / stroke_disk))

    # ---- 4. framing variants
    frng = random.Random(3)
    fr = []
    for _ in range(100000):
        ns = frng.randrange(1_000_000_000)
        fr.append(len(f"2026-09-24T10:00:00.{(f'{ns:09d}'.rstrip('0') or '0')}Z stdout F \n"))
    mean_prefix = sum(fr) / len(fr)
    ts = "2026-09-24T10:00:00.123456789Z"
    dj = sum(docker_jsonfile(j, ts) for j in J0); ds = sum(docker_jsonfile(s, ts) for s in S0)
    dj_fixed = docker_jsonfile("", ts)
    framing = dict(cri_nominal=P, cri_mean_trimmed=mean_prefix,
                   k_cri_nominal=base_disk,
                   k_cri_trimmed=(B + mean_prefix * n) / (S + mean_prefix * n),
                   docker_jsonfile_fixed_bytes=dj_fixed,
                   docker_json_per_line=dj / n, docker_stroke_per_line=ds / n,
                   k_docker_jsonfile=dj / ds)

    # the same break-even against plaintext, where stroke S1 keeps the app timestamp
    pooled = json.load(open(os.path.join(mb.RES, "bytes.json")))[-1]
    h1 = pooled["S1"] - A / n
    plain = dict(mean_plain_payload=pooled["P"], mean_h_s1=h1, delta_plain=(A / n) / pooled["P"],
                 delta_star_plain=1 - h1 / pooled["P"])
    res = dict(lines=n, plain=plain, mean_json_payload=bmean, mean_stroke_payload=S / n, mean_arg_bytes=A / n,
               mean_h=h, delta_pooled=delta_pooled, delta_star=delta_star,
               per_line_k_min=k_min, lines_k_below_1=lines_below_1,
               delta_by_system=sysd, bins=bins, inject=inject,
               escaping=dict(args=len(allargs), args_needing_escape=esc_args, escape_bytes=esc_bytes),
               traces=traces, framing=framing)
    os.makedirs(mb.RES, exist_ok=True)
    json.dump(res, open(os.path.join(mb.RES, "sensitivity.json"), "w"), indent=1)
    with open(os.path.join(mb.RES, "sensitivity_bins.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(bins[0].keys()))
        w.writeheader()
        for r in bins:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    print(json.dumps({k: v for k, v in res.items() if k not in ("bins",)}, indent=1))
    for r in bins:
        print(r)


if __name__ == "__main__":
    main()

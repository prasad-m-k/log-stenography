# Log stenography: code and data

This repository holds every script, raw result, and figure behind the paper

> Kameswara Prasad Mukkamala (Prasad MK). *Log stenography: Shrinking container log bytes at the source to stay inside Kubernetes rotation and eviction limits.* Manuscript submitted to the Journal of Systems and Software, 2026.

Log stenography is a source-side log encoding. The application writes a short text "stroke" (sequence number, dictionary epoch, template id, arguments) instead of a full JSON or text line, and an expander rebuilds the full record downstream from a dictionary built at compile time. The paper asks how much this helps against the three ways Kubernetes loses container logs silently: rotation lag, disk-pressure eviction, and the race between kubelet rotation and the log shipper.

The companion study that describes those mechanisms, and its node emulator, agent, and cluster kit, lives at [prasad-m-k/k8s-silent-log-loss](https://github.com/prasad-m-k/k8s-silent-log-loss).

## Headline numbers

| Result | Value | Where it comes from |
|---|---|---|
| On-disk reduction vs JSON, 16,000 Loghub lines | 2.22x (range 1.61x to 2.73x) | `results/bytes.csv` |
| On-disk reduction vs plaintext | 1.48x (range 1.24x to 1.71x) | `results/bytes.csv` |
| Same comparison after gzip (rotated files) | 1.30x vs JSON, 0.99x vs plaintext | `results/bytes.csv` |
| Encode plus write(2) to a pipe, per event | 544 ns stroke vs 1,202 ns slog JSON | `results/bench_summary.csv` |
| Lines lost at 200,000 events/s, shipper at 32 MB/s | 28.4% JSON vs 0% stroke | `results/event_rate_sweep.csv` |
| Loss onset (events/s) | about 133,500 JSON vs 296,500 stroke | `results/per_line_cost.csv` |
| Gain with a 4 µs per-line shipper cost | 1.56x instead of 2.22x | `results/per_line_cost.csv` |
| Polling shipper, tick-bound rotation | no gain at 10, 5, 2 s monitor intervals | `results/polling.csv` |

Every figure except the benchmark times is deterministic for a given seed. Benchmark times depend on the machine; the paper's values came from Go 1.22.2 on one vCPU of an Intel Xeon at 2.10 GHz.

## Layout

```
bytes/measure_bytes.py     Byte accounting on Loghub samples, with CRI framing and gzip
bench/                     Go benchmarks: stroke encoder, slog, zap, pipe writes, expander
bench/summarize.py         Medians from `go test -bench` output
sim/nodesim_steno.py       Discrete-time emulator of one container's log path
experiments/run_all.py     Every emulator experiment in the paper
analysis/make_figures.py   Every figure in the paper
scripts/reproduce.sh       Runs all of the above in order
results/                   Raw results (CSV, JSON, benchmark output)
figures/                   Figures as PNG (300 dpi), JPG, and vector PDF
data/                      Loghub samples, downloaded on first run (not committed)
```

## Requirements

- Python 3.10 or later, with `pip install -r requirements.txt`
- Go 1.22 or later, for the benchmarks only
- Network access to `raw.githubusercontent.com` on the first run, to fetch the Loghub samples

## Reproduce everything

```bash
git clone https://github.com/prasad-m-k/log-stenography.git
cd log-stenography
pip install -r requirements.txt
./scripts/reproduce.sh
```

The full run takes about three minutes. Most of that is the Go benchmarks; the emulator experiments finish in seconds.

## Reproduce one part

**Byte accounting (Table 2, Fig. 3).** Downloads eight Loghub 2k samples into `data/`, encodes every line as plaintext, JSON, and stroke, checks that each stroke expands back to the original message, and measures sizes with the 41-byte CRI prefix and after gzip.

```bash
python bytes/measure_bytes.py            # add --offline to reuse files in data/
```

**Benchmarks (Table 3).** The encode-only benchmarks write to a counting sink. The `Pipe` benchmarks make one write system call per event to an `os.Pipe`, which is the path a container's unbuffered stdout takes.

```bash
cd bench
go mod tidy
go test -run TestExpandRoundTrip .       # checks that the expander restores the message
go test -run xxx -bench . -benchtime=2s -count=5 | grep '^Benchmark' | tee ../results/bench.txt
cd .. && python bench/summarize.py results/bench.txt
```

**Emulator experiments (Tables 4 to 7, Fig. 4).** Line sizes come from `results/bytes.json`, so run the byte accounting first.

```bash
python experiments/run_all.py --seeds 5
python analysis/make_figures.py
```

## Paper element to file

| Paper element | Script | Result file |
|---|---|---|
| Table 2, Fig. 3: byte accounting and gzip | `bytes/measure_bytes.py` | `results/bytes.csv`, `results/bytes.json` |
| Fig. 2: prefix ceiling (analytic) | `analysis/make_figures.py` | `figures/fig2_ceiling.*` |
| Table 3: encoder, pipe, expander cost | `bench/*_test.go` | `results/bench.txt`, `results/bench_summary.csv` |
| Table 4: validation against the companion study | `experiments/run_all.py` | `results/validation.csv` |
| Fig. 4, Table 5: loss against event rate | `experiments/run_all.py` | `results/event_rate_sweep.csv` |
| Table 6: per-line shipper cost | `experiments/run_all.py` | `results/per_line_cost.csv` |
| Table 7: polling shipper | `experiments/run_all.py` | `results/polling.csv` |
| Section 6.6: eviction during a shipper outage | `experiments/run_all.py` | `results/eviction.csv` |

## The emulator

`sim/nodesim_steno.py` follows the node model of the companion study (Section 10.1 there):

- A writer appends fixed-size lines at W bytes per second for 90 s.
- A kubelet actor checks the active file on each monitor tick and rotates it once it reaches `containerLogMaxSize` (10 MiB by default).
- A shipper actor reads at R = 32 MB/s with ±20% jitter per 20 ms step, reads rotated files oldest first, and keeps each rotated file open for `Rotate_Wait` (5 s). The `event` profile reopens the active file within 100 ms of a rotation, like Fluent Bit's rotation handler. The `poll` profile only notices new files every 10 s.
- Optional escrow takes every file the shipper abandons and drains it at 16 MB/s.
- Optional per-line shipper cost `c_line`, a shipper pause (downstream outage), and an eviction time.

It keeps byte counts in memory instead of doing file operations, so it runs without byte scaling. Before comparing encodings, `experiments/run_all.py` checks it against the companion study's published figures:

| Condition (256-byte lines) | This emulator | Companion |
|---|---|---|
| Event shipper, 40 MB/s | 14.4% lost | 14.4% |
| Event shipper, 48 MB/s | 28.5% lost | 28.4% |
| Event shipper, 64 MB/s | 46.2% lost | 46.1% |
| Polling shipper, 16 MB/s, 5 s / 2 s / 1 s monitor interval | 48.6% / 78.1% / 89.0% | 50.0% / 78.7% / 89.3% |
| Polling shipper, 16 MB/s, 10 s monitor interval | 2.1% | 0.0% |

The last row differs because, for some seeds, the tick and poll phases put two rotations inside one poll window.

To run a single condition:

```python
import sys; sys.path.insert(0, "sim")
from nodesim_steno import Params, run, MB
print(run(Params(W=48 * MB, line=256, seed=0)))
```

## What these numbers do not show

- Nothing here comes from a running kubelet or a production shipper. The emulator's shipper has no per-record cost unless you set `c_line`, and Table 6 shows that this cost matters.
- The JSON baseline is constructed from Loghub fields; no Loghub system emitted JSON.
- Loghub templates were labeled from the same lines, so template matching is exact by construction.
- The benchmarks cover one event shape on one vCPU and say nothing about counter contention across cores.

The cluster runs that would settle these points are described in Section 7 of the paper. They reuse the companion repository's kind-based kit.

## Data

The Loghub samples come from [logpai/loghub](https://github.com/logpai/loghub) (Zhu et al., ISSRE 2023) and are downloaded at run time under Loghub's own terms. They are not redistributed here.

## Citation

See `CITATION.cff`. Until the paper is published, please cite the repository and the manuscript title above.

## License

MIT. See `LICENSE`.

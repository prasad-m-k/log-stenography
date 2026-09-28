"""Discrete-time emulator of one container's log path on a Kubernetes node.

The node model follows the companion paper (Prasad MK and Achar, 2026,
Section 10.1). A writer appends fixed-size CRI lines. A kubelet actor checks
the active file on monitor ticks and rotates it once it reaches
containerLogMaxSize. A shipper actor tails the active file, keeps rotated files
open for Rotate_Wait, and reads at a rate R with +/-20% jitter per step. It has
two discovery profiles: "event" reopens the active path within 100 ms of a
rotation (Fluent Bit's rotation handler), and "poll" notices new files only on
a fixed refresh interval.

This emulator keeps byte counts in memory instead of performing file
operations, so it needs no byte scaling. Loss is bytes written minus bytes
delivered. All lines in a run have the same on-disk size, so line loss equals
byte loss divided by the line size.
"""
from dataclasses import dataclass
import math
import random

MiB = 1 << 20
MB = 1_000_000


@dataclass
class LogFile:
    fid: int
    created: float
    size: float = 0.0
    read: float = 0.0
    rotated_at: float = math.inf   # when the kubelet renamed it away from 0.log
    opened: bool = False
    closed: bool = False
    deadline: float = math.inf     # when the shipper gives up on it


@dataclass
class Params:
    W: float = 32 * MB             # writer rate, bytes/s on disk (CRI prefix included)
    line: float = 256.0            # bytes per line on disk
    R: float = 32 * MB             # shipper read rate, bytes/s
    c_line: float = 0.0            # extra shipper cost per line, seconds
    S: float = 10 * MiB            # containerLogMaxSize
    I_m: float = 10.0              # containerLogMonitorInterval, s
    rotate_wait: float = 5.0       # Rotate_Wait, s
    mode: str = "event"            # "event" or "poll"
    poll: float = 10.0             # refresh interval of the polling shipper, s
    dt: float = 0.02               # step, s
    active_s: float = 90.0         # writer active time, s
    tail_s: float = 240.0          # extra time allowed for draining, s
    jitter: float = 0.2
    escrow: bool = False
    R_e: float = 16 * MB           # escrow drain rate, bytes/s
    pause: tuple = None            # (start, end): shipper paused by a downstream outage
    evict_at: float = None         # eviction time, s
    seed: int = 0


def run(p: Params) -> dict:
    rng = random.Random(p.seed)
    t = 0.0
    next_tick = rng.uniform(0, p.I_m)
    next_poll = rng.uniform(0, p.poll)
    files = [LogFile(0, 0.0)]
    active = files[0]
    cur = active                   # the file the shipper tails as its active file
    cur.opened = True
    open_rot = []                  # rotated files the shipper still holds open
    written = delivered = 0.0
    lost_abandon = lost_evict = 0.0
    first_loss = None
    largest = 0.0
    rotations = 0
    escrow_q = []                  # [file, next offset to drain]
    escrow_held_max = 0.0
    evicted = False
    # bytes per second the shipper can read once a per-line cost is included
    eff_R = 1.0 / (1.0 / p.R + p.c_line / p.line)

    def abandon(f, now):
        nonlocal lost_abandon, first_loss
        f.closed = True
        unread = f.size - f.read
        if unread > 0:
            if p.escrow:
                escrow_q.append([f, f.read])
            else:
                lost_abandon += unread
                if first_loss is None:
                    first_loss = now

    end_t = p.active_s + p.tail_s
    while t < end_t:
        writing = t < p.active_s and not evicted
        if writing:
            active.size += p.W * p.dt
            written += p.W * p.dt
        # kubelet monitor tick: rotate only if the file has reached the limit
        if t >= next_tick:
            next_tick += p.I_m
            if not evicted and active.size >= p.S:
                largest = max(largest, active.size)
                active.rotated_at = t
                rotations += 1
                active = LogFile(len(files), t)
                files.append(active)
        # shipper discovery of a rotation
        if not evicted and cur is not active:
            seen = (t >= cur.rotated_at + 0.1) if p.mode == "event" else (t >= next_poll)
            if seen:
                cur.deadline = t + p.rotate_wait
                open_rot.append(cur)
                cur = active
                cur.opened = True
        if t >= next_poll:
            next_poll += p.poll
        # eviction removes the pod's log directory with every unread byte
        if p.evict_at is not None and not evicted and t >= p.evict_at:
            evicted = True
            largest = max(largest, active.size)
            for f in files:
                if f.closed:
                    continue
                unread = f.size - f.read
                f.closed = True
                f.opened = True
                if unread > 0:
                    if p.escrow:
                        escrow_q.append([f, f.read])
                    else:
                        lost_evict += unread
                        if first_loss is None:
                            first_loss = t
            open_rot = []
        # shipper reads rotated files oldest first, then its active file
        paused = p.pause is not None and p.pause[0] <= t < p.pause[1]
        if not paused and not evicted:
            budget = eff_R * p.dt * rng.uniform(1 - p.jitter, 1 + p.jitter)
            for f in sorted(open_rot, key=lambda x: x.rotated_at) + [cur]:
                if budget <= 0:
                    break
                n = min(budget, f.size - f.read)
                if n > 0:
                    f.read += n
                    delivered += n
                    budget -= n
            for f in list(open_rot):
                if f.read >= f.size - 1e-6:
                    f.closed = True
                    open_rot.remove(f)
                elif t >= f.deadline:
                    open_rot.remove(f)
                    abandon(f, t)
        # escrow drains held files into a separate shipper input
        if escrow_q:
            eb = p.R_e * p.dt
            while eb > 0 and escrow_q:
                f, off = escrow_q[0]
                n = min(eb, f.size - off)
                escrow_q[0][1] += n
                delivered += n
                eb -= n
                if escrow_q[0][1] >= f.size - 1e-6:
                    escrow_q.pop(0)
            escrow_held_max = max(escrow_held_max, sum(f.size for f, _ in escrow_q))
        t += p.dt
        settled = evicted or (cur.read >= cur.size - 1e-6 and not open_rot)
        if t > p.active_s and settled and not escrow_q:
            break

    # files the shipper never opened (discovery miss of a polling shipper)
    lost_never = 0.0
    never = 0
    for f in files:
        if not f.opened:
            never += 1
            if p.escrow:
                delivered += f.size
                escrow_held_max = max(escrow_held_max, f.size)
            else:
                lost_never += f.size
    lost = max(0.0, written - delivered)
    return dict(
        written_bytes=written, lost_bytes=lost,
        lines_written=written / p.line, lines_lost=lost / p.line,
        loss_ratio=lost / written if written else 0.0,
        lost_abandon_bytes=lost_abandon, lost_never_opened_bytes=lost_never,
        lost_evict_bytes=lost_evict,
        first_loss_s=first_loss, largest_0log_MB=max(largest, active.size) / MB,
        rotations=rotations, escrow_held_max_MB=escrow_held_max / MB,
        never_opened_files=never,
    )

"""Synthetic, reproducible cloudflared facts for the card study.

No tokens, no network, no real process state. ``tick`` shifts the window so the
same card can be looked at twice; ``incident=True`` is the edge-down state, so
failure is designed rather than assumed.

Two things here are *real*, because the card must not lie about its denominators:
the host the panel runs on is read from the machine (``host()``), and a measure's
severity is its share of the budget the service is given (one core, 256 MB) —
never a made-up maximum. Colour comes from that share, so the same number reads
different on a different budget.
"""

from __future__ import annotations

import math
import os
import random
from typing import Any

BASE_CPU_PCT = 17.6
BASE_MEM_MB = 69.4
SAMPLES = 48
INTERVAL = 1.0
CPU_BUDGET = 100.0
MEM_BUDGET_MB = 256.0
# thresholds are percentages of the budget, in order: ok < warm < hot < crit
BANDS = {"cpu": (25.0, 50.0, 80.0), "mem": (30.0, 55.0, 80.0)}
LEVELS = ("ok", "warm", "hot", "crit")
UPTIME_0 = 35 * 86400 + 3600 + 180
VERSION = "2026.10.4"
CONNECTOR = "9f3a2b1c"
PATH = "/path/to/cloudflared/bin/cloudflared"
EDGES = [
    ("sea11", "34.127.33.101", 312),
    ("sea06", "34.127.33.118", 296),
    ("sea09", "34.127.33.124", 341),
    ("pdx04", "34.127.33.130", 388),
]


def level(value: float, metric: str) -> str:
    """Which band a value falls in, measured against that metric's budget."""
    budget = CPU_BUDGET if metric == "cpu" else MEM_BUDGET_MB
    share = 100.0 * value / budget
    for key, edge in zip(LEVELS, BANDS[metric]):
        if share < edge:
            return key
    return "crit"


def host() -> dict[str, Any]:
    """The machine the panel is actually on. Read once, never invented."""
    cores = os.cpu_count() or 1
    total = 0
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError):
        total = 0
    gb = total / 1024**3
    return {
        "cores": cores,
        "ram_gb": gb,
        "ram_text": f"{gb:.1f} GB" if gb else "unknown",
        "text": f"{cores} cores · {gb:.1f} GB" if gb else f"{cores} cores",
    }


def duration(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def ago(seconds: float) -> str:
    if seconds < 90:
        return f"{int(seconds)} s ago"
    if seconds < 5400:
        return f"{int(seconds / 60)} min ago"
    if seconds < 172800:
        return f"{int(seconds / 3600)} h ago"
    return f"{int(seconds / 86400)} d ago"


def history(tick: int, base: float, spread: float, phase: float) -> list[float]:
    rng = random.Random(700 + tick)
    out = []
    for i in range(SAMPLES):
        wave = math.sin((i + tick * 3 + phase) / 5) * spread * 0.45
        pulse = math.exp(-((i - 29) ** 2) / 14) * spread * 0.9
        out.append(max(0.4, base + wave + pulse + rng.uniform(-spread * 0.18, spread * 0.18)))
    return out


def curve(pts: list[tuple[float, float]], step: float) -> list[tuple[tuple[float, float], tuple[float, float], tuple[float, float]]]:
    """A smooth polyline: every sample is the bend of one quadratic, and the
    strokes meet on the midpoints between samples, so the joins have no corners.

    Each entry is (start, control, end). The window is padded by one phantom
    sample on both sides (the local slope carried on), so the first and last
    strokes run past the edge of the box instead of stopping inside it.
    """
    head = (pts[0][0] - step, 2 * pts[0][1] - pts[1][1])
    tail = (pts[-1][0] + step, 2 * pts[-1][1] - pts[-2][1])
    padded = [head, *pts, tail]
    out = []
    for i in range(1, len(padded) - 1):
        a = padded[i - 1]
        b = padded[i]
        c = padded[i + 1]
        out.append(
            (
                ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2),
                b,
                ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2),
            )
        )
    return out


def spark(values: list[float], levels: list[str], width: float = 100.0, height: float = 24.0) -> tuple[str, list[list[float]]]:
    """Shape from the window, colour from the budget.

    The line is normalised to its own window because a spark is a shape; the
    stroke of every segment is the absolute band of that sample, so height says
    "changed" and colour says "how much of the budget". The line is a curve, not
    a zigzag: the browser redraws it every frame from the same rule.
    """
    pad = 3.0
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = width / max(len(values) - 1, 1)
    pts = [
        (round(i * step, 2), round(pad + (1 - (value - lo) / span) * (height - 2 * pad), 2))
        for i, value in enumerate(values)
    ]
    segs = []
    for level, (start, bend, end) in zip(levels, curve(pts, step)):
        segs.append(
            f'<path class="rd-seg lv-{level}" d="M{start[0]:.2f} {start[1]:.2f}'
            f'Q{bend[0]:.2f} {bend[1]:.2f} {end[0]:.2f} {end[1]:.2f}"/>'
        )
    return "".join(segs), [[x, y] for x, y in pts]


def build_state(tick: int = 0, *, incident: bool = False) -> dict[str, Any]:
    cpu = history(tick, BASE_CPU_PCT, 9.4, 0.0)
    mem = history(tick, BASE_MEM_MB, 5.4, 2.0)
    if incident:
        cpu[-1] = 88.4
        mem[-1] = 241.6
    cpu_levels = [level(value, "cpu") for value in cpu]
    mem_levels = [level(value, "mem") for value in mem]
    cpu_segments, cpu_points = spark(cpu, cpu_levels)
    mem_segments, mem_points = spark(mem, mem_levels)

    uptime = UPTIME_0 + tick * 11
    checked = 12 * 60 + tick * 7
    live = 0 if incident else 4

    restarts = (
        [
            ("42 min ago", "tunnel restarted: 3 probes missed"),
            ("3 h ago", "operator restart from the panel"),
            ("2 d ago", "binary updated to " + VERSION),
            ("11 d ago", "connector expired, re-registered"),
        ]
        if not incident
        else [
            ("74 s ago", "watchdog restart, edge unreachable"),
            ("3 h ago", "operator restart from the panel"),
            ("2 d ago", "binary updated to " + VERSION),
            ("11 d ago", "connector expired, re-registered"),
        ]
    )

    return {
        "host": host(),
        "connected": not incident,
        "live": live,
        "edges": [
            {
                "code": code,
                "ip": ip,
                "rtt": "—" if incident else f"{rtt + (tick % 7) * 3} ms",
                "state": "down" if incident else "live",
            }
            for code, ip, rtt in EDGES
        ],
        "edge_names": ", ".join(code for code, _, _ in EDGES),
        "edge_ip": EDGES[0][1],
        "connector": CONNECTOR,
        "cpu": cpu[-1],
        "cpu_text": f"{cpu[-1]:.1f}%",
        "cpu_level": level(cpu[-1], "cpu"),
        "cpu_budget": "one core",
        "cpu_share": f"{100.0 * cpu[-1] / CPU_BUDGET:.0f}% of one core",
        "cpu_peak": f"{max(cpu):.1f}%",
        "cpu_series": [round(value, 2) for value in cpu],
        "cpu_levels": cpu_levels,
        "cpu_segments": cpu_segments,
        "cpu_points": cpu_points,
        "mem": mem[-1],
        "mem_text": f"{mem[-1]:.1f} MB",
        "mem_level": level(mem[-1], "mem"),
        "mem_budget": "256 MB",
        "mem_share": f"{100.0 * mem[-1] / MEM_BUDGET_MB:.0f}% of 256 MB",
        "mem_peak": f"{max(mem):.1f} MB",
        "mem_series": [round(value, 2) for value in mem],
        "mem_levels": mem_levels,
        "mem_segments": mem_segments,
        "mem_points": mem_points,
        "uptime": duration(uptime),
        "started_ago": ago(uptime),
        "proc_state": "replacing" if incident else "running",
        "pid": 2143 + tick,
        "restarts": restarts,
        "restart_count": len(restarts),
        "version": VERSION,
        "available": "2026.10.7" if incident else VERSION,
        "binary_source": "cache",
        "binary_pin": "latest",
        "binary_size": "38.4 MB",
        "policy_verdict": "Update available" if incident else "Up to date",
        "policy_reason": (
            "2026.10.7 is out: updating from 2026.10.4."
            if incident
            else "2026.10.4 is the newest release."
        ),
        "policy_checked": ago(checked),
        "path": PATH,
        "samples": SAMPLES,
        "interval": INTERVAL,
    }

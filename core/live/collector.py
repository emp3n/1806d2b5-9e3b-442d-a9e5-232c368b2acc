"""Live sampling: /proc readers, HTTP/parse helpers and the Collector.

Layer ``collector``. Public names are re-exported flat by
``__init__.py``; put new code in the layer that owns it.
"""
from __future__ import annotations

from __future__ import annotations
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from core import bootstrap, geo, sessions
from resources import available_cores, cores_text, memory_limit_bytes

# constants are repeated in every layer so default arguments evaluate at import
_VERSION_CACHE: dict[str, str] = {}
PROC = Path("/proc")

CLOCK_TICKS = 100  # USER_HZ on Linux/amd64; the /proc contract, not a guess

PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")

SAMPLES = 48

CPU_BUDGET = 100.0

MEM_BUDGET_MB = 256.0

METRICS_URL = os.environ.get("CLOUDFLARED_METRICS", "http://127.0.0.1:20241")

TRACE_URL = "https://1.1.1.1/cdn-cgi/trace"

HTTP_TIMEOUT = 0.8

TRACE_EVERY = 60.0  # the egress IP changes rarely; no need to ask every second

STATE_NAMES = {"R": "running", "S": "sleeping", "D": "io wait", "T": "stopped", "Z": "zombie", "I": "idle"}

EDGE_IP = re.compile(r"\bip=(\d{1,3}(?:\.\d{1,3}){3})\b")

LOCATION = re.compile(r"\blocation=([a-z]{2,4}\d{1,2})\b")

CONN_INDEX = re.compile(r"\bconnIndex=(\d+)\b")

REGISTER_ERROR = re.compile(r'Register tunnel error from server side error="([^"]+)"')

TIME_FIELD = re.compile(r'"time"\s*:\s*"([^"]+)"')

TIME_LEAD = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


class Collector:
    """Samples one connector and remembers the window the card draws."""

    def __init__(self, metrics_url: str = METRICS_URL) -> None:
        self.metrics_url = metrics_url.rstrip("/")
        self.pid: int | None = None
        self.cpu: deque[float] = deque(maxlen=SAMPLES)
        self.mem: deque[float] = deque(maxlen=SAMPLES)
        self._last_cpu: tuple[float, int] | None = None  # (monotonic, ticks)
        self._last_cpu_ns: tuple[float, int] | None = None  # (monotonic, ns)
        self._trace: dict[str, str] = {}
        self._trace_at = 0.0
        self._metrics: dict[str, Any] = {}
        self._ready: dict[str, Any] = {}
        self._log: dict[str, Any] = {"edges": [], "reason": None}
        self._log_file: str | None = None
        # the command line the connector was started with, so the panel can
        # start it again; never serialised (it may carry a --token secret)
        self.launch: list[str] | None = load_launch()
        self._stat: dict[str, Any] | None = None
        self.sampled_at = 0.0

    # -- one turn of the crank
    def sample(self, pid: int | None = None) -> dict[str, Any]:
        self.pid = pid or find_pid() or self.pid
        if not self.pid or not Path(PROC / str(self.pid)).exists():
            self.pid = None
            return {"found": False}

        stat = proc_stat(self.pid)
        if stat and stat.get("state") in ("Z", "X"):
            # a stopped connector lingers in /proc as a zombie until its parent
            # reaps it: that is not a running service, and treating it as one
            # left the card saying "0 of 4 connections live" forever with the
            # power button stuck on "Stop"
            self.pid = None
            self._stat = None
            self._last_cpu = None
            self._last_cpu_ns = None
            return {"found": False}

        # the running process is the truth: adopt its command line, and correct
        # a remembered one that no longer matches (the operator may have
        # restarted it with different flags)
        argv = proc_cmdline(self.pid)
        if is_tunnel_run(argv) and argv != self.launch:
            self.launch = argv
            save_launch(argv, proc_exe(self.pid))
        self._stat = stat
        now = time.monotonic()
        cpu_pct = None
        if stat:
            age = proc_age(self.pid, stat)
            runtime_ns = proc_cpu_ns(self.pid)
            if runtime_ns is not None:
                cpu_pct = cpu_rate_ns(self._last_cpu_ns, runtime_ns, now, age)
                self._last_cpu_ns = (now, runtime_ns)
            else:  # a kernel without schedstat: fall back to 10 ms ticks
                ticks = stat["utime"] + stat["stime"]
                cpu_pct = cpu_rate(self._last_cpu, ticks, now, age)
                self._last_cpu = (now, ticks)

        rss = proc_rss(self.pid)
        mem_mb = rss / 1024**2 if rss is not None else None

        if cpu_pct is not None:
            self.cpu.append(round(cpu_pct, 2))
        if mem_mb is not None:
            self.mem.append(round(mem_mb, 2))

        # follow the connector's own listener unless the operator pinned one
        if not os.environ.get("CLOUDFLARED_METRICS"):
            self.metrics_url = (metrics_url_for(argv) or self.metrics_url).rstrip("/")
        self._metrics = parse_metrics(_http(f"{self.metrics_url}/metrics") or "") if self.metrics_url else {}
        self._ready = parse_ready(_http(f"{self.metrics_url}/ready") or "") if self.metrics_url else {}
        if now - self._trace_at > TRACE_EVERY:
            self._trace = parse_trace(_http(TRACE_URL) or "") or self._trace
            self._trace_at = now
        self._log_file = log_path(self.pid)
        self._log = parse_log(read_tail(self._log_file), since=proc_started_at(self.pid, stat))
        self.sampled_at = now
        return {"found": True}

    # -- helpers
    def _series(self, values: deque[float]) -> list[float]:
        """Pad a young window with its first value: a flat line means "no data
        yet", never a made-up shape."""
        if not values:
            return [0.0] * SAMPLES
        head = list(values)[0]
        return [head] * (SAMPLES - len(values)) + list(values)

    def state(self) -> dict[str, Any]:
        from demo_data import (MEM_BUDGET_MB as DEMO_MEM, CPU_BUDGET as DEMO_CPU,
                               duration, host, level, spark)

        if not self.pid:
            return {"found": False}

        stat = self._stat or proc_stat(self.pid) or {}
        age = proc_age(self.pid) or 0.0
        cpu_series = self._series(self.cpu)
        mem_series = self._series(self.mem)
        cpu_levels = [level(value, "cpu") for value in cpu_series]
        mem_levels = [level(value, "mem") for value in mem_series]
        cpu_segments, cpu_points = spark(cpu_series, cpu_levels)
        mem_segments, mem_points = spark(mem_series, mem_levels)

        cores = available_cores()
        exe = proc_exe(self.pid) or ""
        version = self._metrics.get("version") or ""
        if not version and exe:
            # The metrics endpoint is optional: never freeze on the demo
            # version when the running binary can simply be asked.
            cached = _VERSION_CACHE.get(exe)
            if cached is None:
                try:
                    cached = bootstrap.version_of(Path(exe))
                except Exception:  # noqa: BLE001 - a probe must not break the page
                    cached = "unknown"
                _VERSION_CACHE[exe] = cached
            version = cached
        mem_limit = proc_memory_limit(self.pid)
        # A 3072 MB cgroup is 3.0 GB to a person; printing "3072 MB" reads
        # like a bug next to the card's other GB figures.
        mem_budget = (f"{mem_limit / 1024:.1f} GB" if mem_limit and mem_limit >= 1024
                      else f"{mem_limit:.0f} MB" if mem_limit
                      else f"{DEMO_MEM:.0f} MB")
        live = int(self._ready.get("ready", 0))
        # the edges come from the connector's own log: a registered connection
        # names its colo and address; a failing one at least names the address
        conns = self._log.get("conns") or []
        # Geo is resolved here, in the sampler's background thread, and cached
        # per IP: the card never calls a third-party API and every poll is cheap.
        origin_geo = geo.lookup(self._trace.get("ip") or "")
        edges_geo = []
        for conn in conns[:4]:
            info = geo.lookup(conn.get("ip") or "")
            if not info:
                continue
            code = conn.get("location") or ""
            if any(item["code"] == code for item in edges_geo):
                continue
            edges_geo.append({"code": code, "cc": info["cc"],
                              "region": info["region"], "city": info["city"]})
        # four connections usually land on two edges, and listing the same
        # airport twice ("sea01, sea07, sea07, sea01") reads as a bug in the
        # numbers rather than as two QUIC tunnels to each edge
        codes = list(dict.fromkeys(
            conn["location"] for conn in conns if conn["location"]))
        edges = [conn["ip"] for conn in conns if conn["ip"]]
        note = ""
        if not live:
            reason = self._log.get("reason")
            fails = int(self._metrics.get("register_fail", 0))
            if reason:
                note = reason
            elif fails:
                note = f"{fails} register errors"

        cpu_now = cpu_series[-1]
        mem_now = mem_series[-1]
        return {
            "found": True,
            "host": host(),
            "connected": live > 0,
            "live": live,
            "note": note,
            "edges": list({
                conn.get("location", ""): {
                    "code": conn.get("location", ""), "ip": conn.get("ip", ""),
                    "rtt": "—", "state": "live" if live else "down"}
                for conn in conns if conn.get("location")
            }.values())[:4],
            "edge_names": ", ".join(codes[:4]) if codes else (
                edges[0] if len(edges) == 1 else f"{edges[0]} +{len(edges) - 1}") if edges else "—",
            "edge_ip": self._trace.get("ip") or "—",
            "origin_geo": origin_geo,
            "edges_geo": edges_geo,
            "connector": self._ready.get("connector", "—"),
            "cpu": cpu_now,
            "cpu_text": cpu_text(cpu_now),
            "cpu_level": level(cpu_now, "cpu"),
            "cpu_budget": f"{cores} cores",
            # cpu_now is single-core-equivalent percent (0..100*cores). Show the
            # share of the *container's* allowance, not of an imaginary core.
            "cpu_share": f"{cpu_now / cores:.2f}% of {cores_text(cores)} cores",
            "cpu_peak": f"{max(cpu_series):.1f}%",
            "cpu_series": cpu_series,
            "cpu_levels": cpu_levels,
            "cpu_segments": cpu_segments,
            "cpu_points": cpu_points,
            "mem": mem_now,
            "mem_text": f"{mem_now:.1f} MB",
            "mem_level": level(mem_now, "mem"),
            "mem_budget": mem_budget,
            "mem_scale": float(mem_limit or DEMO_MEM),
            "mem_share": f"{100.0 * mem_now / (mem_limit or DEMO_MEM):.0f}% of {mem_budget}",
            "mem_peak": f"{max(mem_series):.1f} MB",
            "mem_series": mem_series,
            "mem_levels": mem_levels,
            "mem_segments": mem_segments,
            "mem_points": mem_points,
            "uptime": duration(age),
            "started_ago": duration(age),
            "proc_state": STATE_NAMES.get(stat.get("state", "S"), "running"),
            "pid": self.pid,
            "threads": stat.get("threads", 0),
            "restarts": [],
            "restart_count": 0,
            "version": version or "unknown",
            "available": version or "unknown",
            "path": exe or "—",
            "samples": SAMPLES,
            "interval": 1.0,
            "source": "live",
            "source_label": f"live · pid {self.pid}",
            "register_fail": int(self._metrics.get("register_fail", 0)),
            # cloudflared's own gauge of active HA connections, independent of
            # the /ready endpoint; both are published so the card can show the
            # connections actually established right now.
            "ha_connections": int(self._metrics.get("ha_connections", 0)),
            # Viewers through THIS replica: sockets owned by cloudflared to the
            # local ingress, not Cloudflare-side or proxy traffic.
            "connections": sessions.connections(self.pid, bootstrap.ingress_port()),
            "requests": int(self._metrics.get("requests", 0)),
            "gaps": self.gaps(),
        }

    def gaps(self) -> list[str]:
        """What this turn could not read, named rather than silently zeroed."""
        missing = []
        if not self._metrics:
            missing.append("metrics endpoint")
        if not self._ready:
            missing.append("/ready")
        if not self._trace:
            missing.append("egress trace")
        if not self._log_file:
            missing.append("connector log")
        return missing


def idle_state() -> dict[str, Any]:
    """The card's schema with no process in it: dashes, a flat line, no claims.

    Used when the panel is opened while the connector is stopped, so the title
    still says "no cloudflared process" instead of borrowing the lab's sample
    numbers for a service that is not running.
    """
    from demo_data import MEM_BUDGET_MB as MEM, build_state, host, level, spark

    # Origin survives a stopped connector: resolve our own endpoint once.
    own_ip, own_geo = geo.own_endpoint()
    state = build_state(0)
    zeros = [0.0] * SAMPLES
    cpu_levels = [level(0.0, "cpu")] * SAMPLES
    mem_levels = [level(0.0, "mem")] * SAMPLES
    cpu_segments, cpu_points = spark(zeros, cpu_levels)
    mem_segments, mem_points = spark(zeros, mem_levels)
    state.update({
        "found": False,
        "host": host(),
        "connected": False,
        "live": 0,
        "connections": 0,
        "note": "",
        "edges": [],
        "edge_names": "—",
        "edge_ip": own_ip or "—",
        "origin_geo": own_geo,
        "connector": "—",
        "cpu": 0.0,
        "cpu_text": "—",
        "cpu_level": level(0.0, "cpu"),
        "cpu_share": "no process",
        "cpu_peak": "—",
        "cpu_series": zeros,
        "cpu_levels": cpu_levels,
        "cpu_segments": cpu_segments,
        "cpu_points": cpu_points,
        "mem": 0.0,
        "mem_text": "—",
        "mem_level": level(0.0, "mem"),
        "mem_share": "no process",
        "mem_peak": "—",
        "mem_series": zeros,
        "mem_levels": mem_levels,
        "mem_segments": mem_segments,
        "mem_points": mem_points,
        "mem_budget": f"{MEM:.0f} MB",
        "mem_scale": float(MEM),
        "uptime": "—",
        "started_ago": "—",
        "proc_state": "gone",
        "pid": 0,
        "threads": 0,
        "restarts": [],
        "restart_count": 0,
        "version": "—",
        "available": "—",
        "path": "—",
        "samples": SAMPLES,
        "interval": 1.0,
        "source": "live",
        "source_label": "live · no cloudflared process",
        "register_fail": 0,
        "requests": 0,
        "gaps": ["process"],
    })
    return state

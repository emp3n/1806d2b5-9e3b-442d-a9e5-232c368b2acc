"""Where the container's real CPU and memory limits come from.

Nothing here is hard-coded. Streamlit Cloud gives the process 16 CPUs through
cgroups and 3 GiB of RAM even though ``/proc/meminfo`` reports the whole host
(125 GB in the live deployment). Reading ``os.cpu_count()`` or MemTotal would
therefore print the host's numbers on a card that describes a container.

Order of truth:
  1. cgroup v2 ``cpu.max`` (quota / period) and ``cpu.cpuset``/affinity;
  2. when the quota is ``max`` (Streamlit Cloud's case), cgroup v2
     ``cpu.weight`` is the only signal of a shared allocation: weight 14
     against the kernel default 100 means a 0.14 share of every visible CPU,
     so 16 visible CPUs are really about 2.2 cores;
  3. cgroup v1 quota files, if the host still uses them;
  4. ``sched_getaffinity`` / ``os.cpu_count`` as the last fallback.
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

CPU_MAX = Path("/sys/fs/cgroup/cpu.max")
CPU_WEIGHT = Path("/sys/fs/cgroup/cpu.weight")
CPUSET_V2 = Path("/sys/fs/cgroup/cpuset.cpus.effective")
CPU_QUOTA_V1 = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
CPU_PERIOD_V1 = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
MEMORY_LIMIT_V1 = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def _count_cpu_list(value: str) -> int:
    """Count CPUs in a Linux list like ``0-3,8,10-11``."""
    total = 0
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            first, last = part.split("-", 1)
            try:
                total += max(0, int(last) - int(first) + 1)
            except ValueError:
                continue
        else:
            try:
                int(part)
            except ValueError:
                continue
            total += 1
    return total


def _affinity() -> int:
    try:
        return len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def available_cores() -> float:
    """The CPU allowance this process can really count on, not the host's.

    ``cpu.max = max`` does NOT mean the platform gives you all 16 visible CPUs:
    Streamlit Cloud's cgroup sets ``cpu.weight = 14`` (kernel default 100),
    which is a 14 % share of each one. The card used to print 16 cores and
    divide the connector's CPU by 16, making a busy core look like 0.3 % load.
    """
    text = _read(CPU_MAX)
    if text:
        parts = text.split()
        if len(parts) >= 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    return max(0.01, quota / period)
            except ValueError:
                pass
        cpuset = _count_cpu_list(_read(CPUSET_V2)) or _affinity()
        # No hard quota: cpu.weight is the only honest allocation signal.
        weight_text = _read(CPU_WEIGHT)
        if weight_text:
            try:
                weight = int(weight_text)
                if 0 < weight < 100:
                    return round(max(0.01, cpuset * weight / 100), 2)
            except ValueError:
                pass
        return float(max(1, cpuset))
    quota_text, period_text = _read(CPU_QUOTA_V1), _read(CPU_PERIOD_V1)
    if quota_text and period_text:
        try:
            quota, period = int(quota_text), int(period_text)
            if 0 < quota and period > 0:
                return max(0.01, quota / period)
        except ValueError:
            pass
    return float(max(1, _affinity()))


def cores_text(cores: float | None = None) -> str:
    """Human-readable allowance: 2.2 cores, not 2.244396 cores."""
    value = available_cores() if cores is None else cores
    return f"{value:.1f}".rstrip("0").rstrip(".")


def memory_limit_bytes() -> int:
    """The container's memory ceiling, not the host's MemTotal."""
    text = _read(MEMORY_MAX)
    if text and text != "max":
        try:
            value = int(text)
            if value > 0:
                return value
        except ValueError:
            pass
    text = _read(MEMORY_LIMIT_V1)
    if text:
        try:
            value = int(text)
            if 0 < value < (1 << 60):
                return value
        except ValueError:
            pass
    for line in _read(Path("/proc/meminfo")).splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                break
    return 0


def host_info() -> dict[str, Any]:
    """The small dict the card shows in its footer, measured not invented."""
    cores = available_cores()
    total = memory_limit_bytes()
    gb = total / 1024**3 if total else 0.0
    shown = cores_text(cores)
    return {
        "cores": cores,
        "ram_gb": gb,
        "ram_text": f"{gb:.1f} GB" if gb else "unknown",
        "text": f"{shown} cores · {gb:.1f} GB" if gb else f"{shown} cores",
    }

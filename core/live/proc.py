"""Live sampling: /proc readers, HTTP/parse helpers and the Collector.

Layer ``proc``. Public names are re-exported flat by
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


def _text(path: str | Path) -> str | None:
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def cpu_rate(previous: tuple[float, int] | None, ticks: int, now: float, age: float | None) -> float:
    """CPU percent of one core from two (monotonic, ticks) readings.

    The first sample has no interval to divide by, so it reports the lifetime
    average (CPU seconds over process age) rather than a made-up zero; every
    later sample is the true rate between the two reads.
    """
    if previous and now > previous[0]:
        seconds = now - previous[0]
        percent = (ticks - previous[1]) / CLOCK_TICKS / seconds * 100.0
    elif age:
        percent = ticks / CLOCK_TICKS / age * 100.0
    else:
        percent = 0.0
    # A Go connector can use several cores at once: cap against the cgroup's
    # real allowance, not os.cpu_count() (the host's 16 on Streamlit Cloud).
    return max(0.0, min(percent, 100.0 * available_cores()))


def proc_stat(pid: int) -> dict[str, Any] | None:
    """The one line in /proc/<pid>/stat, counted from the last ')'.

    The command field is in parentheses and may hold spaces and brackets, so a
    plain split is wrong: ``(cloudflared)`` happens to be safe, ``(sshd: ...)``
    is not, and the same reader has to survive both.
    """
    raw = _text(PROC / str(pid) / "stat")
    if not raw:
        return None
    close = raw.rfind(")")
    if close < 0:
        return None
    fields = raw[close + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        return {
            "pid": pid,
            "comm": raw[raw.find("(") + 1 : close],
            "state": fields[0],
            "utime": int(fields[11]),
            "stime": int(fields[12]),
            "threads": int(fields[17]),
            "starttime": int(fields[19]),
        }
    except (ValueError, IndexError):
        return None


def proc_rss(pid: int) -> int | None:
    """Resident bytes from statm (field 2) — one short read, not whole status."""
    raw = _text(PROC / str(pid) / "statm")
    if not raw:
        return None
    parts = raw.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1]) * PAGE_SIZE
    except ValueError:
        return None


def proc_exe(pid: int) -> str | None:
    try:
        return os.readlink(PROC / str(pid) / "exe")
    except OSError:
        return None


def boot_seconds() -> float | None:
    raw = _text(PROC / "uptime")
    if not raw:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def proc_age(pid: int, stat: dict[str, Any] | None = None) -> float | None:
    """Seconds since the process started: boot time minus its start tick."""
    stat = stat or proc_stat(pid)
    boot = boot_seconds()
    if not stat or boot is None:
        return None
    age = boot - stat["starttime"] / CLOCK_TICKS
    return age if age >= 0 else None


def proc_cpu_ns(pid: int) -> int | None:
    """CPU nanoseconds burned by every thread of the process.

    ``/proc/<pid>/stat`` counts 10 ms ticks, so a connector idling at a
    fraction of a percent reads as 0 % for whole seconds — honest arithmetic,
    useless reading. ``/proc/<pid>/task/*/schedstat`` is the kernel's own
    nanosecond run-time counter, one file per thread; summed over the threads it
    agrees with the tick counter, and it resolves hundredths of a percent.
    """
    base = PROC / str(pid) / "task"
    try:
        tids = os.listdir(base)
    except OSError:
        return None
    total = 0
    seen = False
    for tid in tids:
        raw = _text(base / tid / "schedstat")
        if not raw:
            continue
        try:
            total += int(raw.split()[0])
        except (ValueError, IndexError):
            continue
        seen = True
    return total if seen else None


def cpu_rate_ns(previous: tuple[float, int] | None, runtime_ns: int | None,
                now: float, age: float | None) -> float:
    """CPU percent of one core from two (monotonic, nanoseconds) readings.

    Same shape as :func:`cpu_rate`: the first sample reports the lifetime
    average rather than a fabricated zero, and a counter that went backwards
    (process replaced, thread gone) is not reported as negative CPU.
    """
    if runtime_ns is None:
        return 0.0
    if previous:
        previous_at, previous_ns = previous
        interval = now - previous_at
        if interval > 0:
            delta = runtime_ns - previous_ns
            return max(0.0, delta / 1e9 / interval * 100.0)
    if age and age > 0:
        return runtime_ns / 1e9 / age * 100.0
    return 0.0


def cpu_text(value: float) -> str:
    """Sub-percent CPU keeps two decimals, so "0.0 %" never means "0.04 %"."""
    return f"{value:.2f}%" if 0 < value < 1 else f"{value:.1f}%"


def token_file_for(exe: str | None) -> Path | None:
    return Path(exe).resolve().with_name("cf-token") if exe else None


def strip_token(argv: list[str], exe: str | None = None,
                token_file: Path | None = None) -> list[str]:
    """``argv`` without its binary, with any inline secret moved into a file.

    ``cloudflared tunnel run --token …`` works, but the secret then sits in
    ``/proc/<pid>/cmdline`` for anyone with the same user, and would be written
    down the first time the panel remembers the command line. The secret goes
    into a ``600`` file next to the binary and travels as ``--token-file``.
    """
    out: list[str] = []
    secret: str | None = None
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--token" and i + 1 < len(argv):
            secret = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--token="):
            secret = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    if secret:
        target = token_file or token_file_for(exe or (argv[0] if argv else None))
        if target:
            target.write_text(secret)
            os.chmod(target, 0o600)
            out.extend(["--token-file", str(target)])
    return out


def launch_file(exe: str | None = None) -> Path:
    """Where the panel remembers how the connector was started.

    ``/proc`` forgets a command line the moment the process exits, so a panel
    opened after a stop would have nothing to start. The recorded argv is kept
    in the user's state directory — not next to the binary, because the panel
    must find it *before* it knows which binary that is. ``CARD_LAUNCH_FILE``
    overrides the path (the tests use it); ``exe`` is accepted and ignored.
    """
    if os.environ.get("CARD_LAUNCH_FILE"):
        return Path(os.environ["CARD_LAUNCH_FILE"])
    state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    return state / "apLLMe" / "cf-launch.json"


def save_launch(argv: list[str], exe: str | None = None) -> None:
    """Remember the command line; a bad path is not worth failing a sample over.

    The directory comes from ``/proc/<pid>/exe`` when it is known, not from
    ``argv[0]``: a connector started as ``./cloudflared`` would otherwise drop
    the file in whatever directory the panel happens to run in.
    """
    target = launch_file(exe or (argv[0] if argv else None))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # the secret goes to a file, the remembered line names that file
        safe = [argv[0]] + strip_token(argv, exe) if argv else []
        if not safe:
            return
        target.write_text(json.dumps(safe))
        os.chmod(target, 0o600)
    except OSError:
        pass


def load_launch(exe: str | None = None) -> list[str] | None:
    target = launch_file(exe)
    try:
        argv = json.loads(target.read_text())
    except (OSError, ValueError):
        return None
    return argv if isinstance(argv, list) and argv else None


def proc_started_at(pid: int, stat: dict[str, Any] | None = None) -> float | None:
    """Wall-clock second this process started, for dating its log lines."""
    age = proc_age(pid, stat)
    return None if age is None else time.time() - age


def proc_state(pid: int) -> str:
    stat = proc_stat(pid)
    return STATE_NAMES.get(stat["state"], "running") if stat else "gone"


def proc_cmdline(pid: int) -> list[str]:
    raw = _text(PROC / str(pid) / "cmdline") or ""
    return [part for part in raw.split("\0") if part]


def proc_memory_limit(pid: int) -> float | None:
    """The cgroup memory limit of the process, if the kernel set one.

    ``memory.max`` is the literal ``max`` when unlimited, which is the case on
    this host; the caller then falls back to the panel's own budget and labels
    it as such.
    """
    raw = _text(PROC / str(pid) / "cgroup")
    if not raw:
        return None
    for line in raw.splitlines():
        if line.startswith("0::"):
            path = line[3:].strip()
            value = _text(Path("/sys/fs/cgroup") / path.lstrip("/") / "memory.max")
            if value and value.strip() not in ("max", ""):
                try:
                    return int(value) / 1024**2
                except ValueError:
                    return None
    return None


def is_tunnel_run(args: Iterable[str]) -> bool:
    """True for a connector: ``tunnel ... run`` or a tokenless quick tunnel.

    Flags may sit before or after the subcommand (cloudflared takes
    ``tunnel --metrics ... run``), so the test is on words, not positions.
    A quick tunnel has no ``run`` at all -- it is ``cloudflared tunnel --url
    http://...`` -- which is exactly what the deployed app starts. Requiring
    ``run`` made a working connector invisible to the sampler and left the card
    saying "no cloudflared process" next to live metrics.
    """
    words = [os.path.basename(arg) for arg in args]
    has_binary = any(word == "cloudflared" or word.startswith("cloudflared-") for word in words[:2])
    joined = f" {' '.join(args)} "
    if not has_binary or " tunnel " not in joined:
        return False
    return re.search(r"(^|\s)run(\s|$)", joined) is not None or "--url" in args


def metrics_url_for(args: list[str]) -> str | None:
    """The /metrics address this connector actually listens on.

    bootstrap.py takes the preferred port only while it is free (a connector
    from the previous script process may still own 20241), so a connector can
    be listening on an ephemeral port. Reading the hard-coded default then made
    a live tunnel look like "0 of 4 connections live" with every gauge at zero.
    """
    value = None
    for i, arg in enumerate(args):
        if arg == "--metrics" and i + 1 < len(args):
            value = args[i + 1]
            break
        if arg.startswith("--metrics="):
            value = arg.split("=", 1)[1]
            break
    if not value:
        return None
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    return f"http://{value}"


def scan_pids() -> list[tuple[int, float]]:
    """Every running connector, newest first, as (pid, start seconds)."""
    found: list[tuple[int, float]] = []
    try:
        entries = os.listdir(PROC)
    except OSError:
        return found
    for entry in entries:
        if not entry.isdigit():
            continue
        pid = int(entry)
        args = proc_cmdline(pid)
        if not args or not is_tunnel_run(args):
            continue
        stat = proc_stat(pid)
        found.append((pid, (stat or {}).get("starttime", 0) / CLOCK_TICKS))
    found.sort(key=lambda item: item[1], reverse=True)
    return found


def find_pid(pid: int | None = None, pidfile: str | None = None) -> int | None:
    """Explicit pid, then $CLOUDFLARED_PID, then the pidfile, then a scan."""
    for candidate in (pid, os.environ.get("CLOUDFLARED_PID")):
        if candidate and Path(PROC / str(candidate)).exists():
            return int(candidate)
    for path in (pidfile or os.environ.get("CLOUDFLARED_PIDFILE"), "/tmp/user/1000/opencode/cf.pid"):
        if not path:
            continue
        raw = _text(path)
        if raw and raw.strip().isdigit() and Path(PROC / raw.strip()).exists():
            return int(raw.strip())
    found = scan_pids()
    return found[0][0] if found else None


def log_path(pid: int | None) -> str | None:
    """Where this connector writes its log, in order of certainty.

    1. ``--logfile`` in its command line — the flag the panel recommends;
    2. ``/proc/<pid>/fd/1`` when stdout was redirected to a real file, which is
       what ``cloudflared tunnel run … > cf.log`` does (the form in the ticket);
    3. ``CLOUDFLARED_LOG``, for a log the process does not own.
    """
    if pid:
        args = proc_cmdline(pid)
        for i, arg in enumerate(args):
            if arg == "--logfile" and i + 1 < len(args):
                return args[i + 1]
            if arg.startswith("--logfile="):
                return arg.split("=", 1)[1]
        try:
            target = os.readlink(f"/proc/{pid}/fd/1")
        except OSError:
            target = ""
        # a terminal or a pipe is not a log; a regular file is
        if target.startswith("/") and os.path.isfile(target):
            return target
    return os.environ.get("CLOUDFLARED_LOG")


def read_tail(path: str | None, lines: int = 400) -> list[str]:
    if not path:
        return []
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 64 * 1024))
            raw = handle.read().decode("utf-8", "replace")
    except OSError:
        return []
    return raw.splitlines()[-lines:]

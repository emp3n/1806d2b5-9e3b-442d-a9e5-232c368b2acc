"""Live numbers for the cloudflared card, every one traced to its source.

The card used to be fed by ``demo_data``; this module replaces that feed with
readings from a real ``cloudflared`` process on this host. Nothing here invents
a value: when a source is missing the field is reported as missing (``None`` or
a dash) and the state carries a ``gaps`` list saying what could not be read.

Provenance, one line per card element (the long form is ``REAL-DATA.md``):

    pid, exe path      scan of /proc/<pid>/cmdline (or CLOUDFLARED_PID, or the
                       --pidfile the connector writes) ; /proc/<pid>/exe
    CPU %              /proc/<pid>/stat fields 14+15 (utime+stime, USER_HZ=100)
                       as a rate between two samples of this collector
    RAM                /proc/<pid>/statm field 2 (resident pages) * page size
    uptime             /proc/<pid>/stat field 22 (starttime) + /proc/uptime
    process state      /proc/<pid>/stat field 3
    connections live   http://127.0.0.1:20241/ready -> readyConnections, connectorId
    version            metrics build_info{version=...}
    register errors    metrics cloudflared_tunnel_tunnel_register_fail
    edges              the colos and addresses in the connector's own log
                       ("Registered tunnel connection ... ip=198.41.200.113
                       location=fra15") — the edges it actually registered
                       with. The colo from https://1.1.1.1/cdn-cgi/trace is
                       *not* used for this: anycast sends that request to a
                       different PoP, which the box saw change between FRA and
                       IST while the tunnel sat in fra08/ist03
    origin IP          https://1.1.1.1/cdn-cgi/trace -> ip= (what the edge sees)
    error reason       the connector's own log, "Register tunnel error ..."

The memory and CPU budgets are the panel's convention (one core, 256 MB): the
process runs in a cgroup with ``memory.max = max`` and no ``cpu.max`` here, so
the kernel has no limit to read and the card says so instead of pretending.
"""

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


# ---------------------------------------------------------------- /proc reads
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
    return max(0.0, min(percent, 100.0 * (os.cpu_count() or 1)))


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


# ------------------------------------------------------------- process finding
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


# ------------------------------------------------------------------- http bits
def _http(url: str) -> str | None:
    """Body of a GET, including the body of an error status.

    ``/ready`` answers 503 when no connection is registered — that is the
    measurement, not a failure — so an HTTPError is read like any other reply.
    """
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as response:
            return response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        try:
            return error.read().decode("utf-8", "replace")
        except OSError:
            return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def parse_metrics(text: str) -> dict[str, Any]:
    """The few series the card needs out of the Prometheus text format."""
    out: dict[str, Any] = {"version": None, "register_fail": 0, "ha_connections": 0,
                           "requests": 0, "request_errors": 0, "labels": {}}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        sample, _, value = line.rpartition(" ")
        name, _, rest = sample.partition("{")
        try:
            number = float(value)
        except ValueError:
            continue
        labels = dict(re.findall(r'(\w+)="([^"]*)"', rest))
        if name == "build_info":
            out["version"] = labels.get("version")
        elif name == "cloudflared_tunnel_tunnel_register_fail":
            out["register_fail"] += int(number)
        elif name == "cloudflared_tunnel_ha_connections":
            out["ha_connections"] = int(number)
        elif name == "cloudflared_tunnel_total_requests":
            out["requests"] = int(number)
        elif name == "cloudflared_tunnel_request_errors":
            out["request_errors"] = int(number)
    return out


def parse_ready(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return {
        "ready": int(data.get("readyConnections", 0) or 0),
        "connector": str(data.get("connectorId") or ""),
        "status": int(data.get("status", 0) or 0),
    }


def parse_trace(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key and value:
            out[key.strip()] = value.strip()
    return out


EDGE_IP = re.compile(r"\bip=(\d{1,3}(?:\.\d{1,3}){3})\b")
LOCATION = re.compile(r"\blocation=([a-z]{2,4}\d{1,2})\b")
CONN_INDEX = re.compile(r"\bconnIndex=(\d+)\b")
REGISTER_ERROR = re.compile(r'Register tunnel error from server side error="([^"]+)"')
TIME_FIELD = re.compile(r'"time"\s*:\s*"([^"]+)"')
TIME_LEAD = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})")


def line_epoch(line: str) -> float | None:
    """The UTC timestamp cloudflared stamps on a log line, JSON or text."""
    found = TIME_FIELD.search(line) or TIME_LEAD.search(line)
    if not found:
        return None
    try:
        stamp = datetime.strptime(found.group(1).rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return stamp.replace(tzinfo=timezone.utc).timestamp()


def parse_log(lines: Iterable[str], since: float | None = None) -> dict[str, Any]:
    """Where the connections landed, and the last registration error.

    A registered connection logs its slot, its edge address and its colo:
    ``Registered tunnel connection connIndex=0 … ip=198.41.200.113
    location=fra15``. The same slot also logs a line *without* a location (the
    curve-preferences line), so the slot — not the line — is the unit: the last
    value seen for each ``connIndex`` wins and an empty field never erases a
    filled one. ``--logfile`` makes cloudflared write JSON lines; the same log
    without the flag is plain text, so both shapes are read.

    ``since`` drops lines written before the connector we are watching started:
    the log file is appended to, so yesterday's ``Invalid tunnel secret`` must
    not be reported as today's reason. The log is *also* cut at its last
    ``Starting tunnel`` line, because some builds write JSON without a ``time``
    field — the marker is then the only evidence of where the current session
    begins.
    """
    lines = list(lines)
    start = None
    for i, line in enumerate(lines):
        if "Starting tunnel" in line:
            start = i
    if start is not None:
        lines = lines[start:]
    slots: dict[int, dict[str, str]] = {}
    loose: list[dict[str, str]] = []
    reason = None
    for line in lines:
        location = ""
        ip = ""
        index: int | None = None
        stamped = line_epoch(line)
        if since is not None and stamped is not None and stamped < since - 2.0:
            continue
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                data = None
            if not isinstance(data, dict):
                continue
            location = str(data.get("location") or "")
            ip = str(data.get("ip") or "")
            if data.get("connIndex") is not None:
                index = int(data["connIndex"])
            if data.get("message") == "Register tunnel error from server side" and data.get("error"):
                reason = str(data["error"])
        else:
            found = EDGE_IP.search(line)
            ip = found.group(1) if found else ""
            colo = LOCATION.search(line)
            location = colo.group(1) if colo else ""
            slot = CONN_INDEX.search(line)
            index = int(slot.group(1)) if slot else None
            error = REGISTER_ERROR.search(line)
            if error:
                reason = error.group(1)
        if index is not None:
            entry = slots.setdefault(index, {"location": "", "ip": ""})
            if location:
                entry["location"] = location
            if ip:
                entry["ip"] = ip
        elif ip or location:
            pair = {"location": location, "ip": ip}
            if pair not in loose:
                loose.append(pair)
    conns = [slots[index] for index in sorted(slots)] if slots else loose
    conns = [conn for conn in conns if conn["location"] or conn["ip"]][-4:]
    return {"conns": conns, "edges": [conn["ip"] for conn in conns if conn["ip"]], "reason": reason}


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


def idle_state() -> dict[str, Any]:
    """The card's schema with no process in it: dashes, a flat line, no claims.

    Used when the panel is opened while the connector is stopped, so the title
    still says "no cloudflared process" instead of borrowing the lab's sample
    numbers for a service that is not running.
    """
    from demo_data import MEM_BUDGET_MB as MEM, build_state, host, level, spark

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
        "note": "",
        "edges": [],
        "edge_names": "—",
        "edge_ip": "—",
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


# -------------------------------------------------------------------- collector
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

        mem_limit = proc_memory_limit(self.pid)
        mem_budget = f"{mem_limit:.0f} MB" if mem_limit else f"{DEMO_MEM:.0f} MB"
        live = int(self._ready.get("ready", 0))
        # the edges come from the connector's own log: a registered connection
        # names its colo and address; a failing one at least names the address
        conns = self._log.get("conns") or []
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
            "connector": self._ready.get("connector", "—"),
            "cpu": cpu_now,
            "cpu_text": cpu_text(cpu_now),
            "cpu_level": level(cpu_now, "cpu"),
            "cpu_budget": "one core",
            "cpu_share": (f"{100.0 * cpu_now / DEMO_CPU:.2f}% of one core"
                          if 0 < cpu_now / DEMO_CPU < 1
                          else f"{100.0 * cpu_now / DEMO_CPU:.0f}% of one core"),
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
            "version": self._metrics.get("version") or "unknown",
            "available": self._metrics.get("version") or "unknown",
            "path": proc_exe(self.pid) or "—",
            "samples": SAMPLES,
            "interval": 1.0,
            "source": "live",
            "source_label": f"live · pid {self.pid}",
            "register_fail": int(self._metrics.get("register_fail", 0)),
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


if __name__ == "__main__":  # a one-shot look at the live connector
    collector = Collector()
    collector.sample()
    time.sleep(1.0)
    collector.sample()
    print(json.dumps(collector.state(), indent=2, ensure_ascii=False))

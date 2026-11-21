"""Live sampling: /proc readers, HTTP/parse helpers and the Collector.

Layer ``sources``. Public names are re-exported flat by
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

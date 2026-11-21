"""Tunnel bootstrap: configuration, binary, probes and process control.

Layer ``probes``. Public names are re-exported flat by
``__init__.py``; put new code in the layer that owns it.
"""
from __future__ import annotations

from __future__ import annotations
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# constants are repeated in every layer so default arguments evaluate at import
_child_procs: list[subprocess.Popen] = []

_public_failures: dict[int, tuple[int, float]] = {}

_cached: Tunnel | None = None
VERSION = "2026.11.1"

BASE = "https://github.com/cloudflare/cloudflared/releases/download"

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

METRICS_PORT = 20241

STATE_PATH = "/app/static/state.json"

CARD_STATE_PATH = "app/static/state.json"

DEFAULT_HOSTNAME = "streamlit.idealis.cc"

URL_KEYS = ("CARD_CLOUD_URL", "STREAMLIT_APP_URL")

STUCK_AFTER = 180.0

CHECK_EVERY = 15.0

PUBLIC_STRIKES = 3

TOKEN_KEYS = ("CLOUDFLARE_TUNNEL_TOKEN", "CARD_TUNNEL_TOKEN")

HOST_KEYS = ("CLOUDFLARE_TUNNEL_HOSTNAME", "CARD_TUNNEL_HOSTNAME")

_lock = threading.Lock()


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """Can something bind ``port`` right now? Used before handing it to
    cloudflared, whose failure mode is an immediate exit with code 255."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def pick_metrics_port(preferred: int = METRICS_PORT) -> int:
    """The preferred port, or any free one when a previous connector holds it.

    Streamlit Cloud reboots the script process without killing the connector it
    started, so 20241 is routinely still bound; starting a second cloudflared on
    it exits with `listen tcp 127.0.0.1:20241: bind: address already in use`.
    """
    if port_is_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _proc_state(pid: int) -> str | None:
    """The process state letter from /proc/<pid>/stat, or None when gone."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    fields = raw.rsplit(")", 1)[-1].split()
    return fields[0] if fields else None


def _proc_alive(pid: int) -> bool:
    """Alive *and doing something*.

    A child that exited but was not reaped by its parent lingers in /proc as a
    zombie. Treating a zombie as running is exactly how the card ended up
    claiming a connector that no longer existed, so "Z" (and "X") count as gone.
    """
    if not pid or pid <= 0:
        return False
    if Path("/proc").is_dir():
        state = _proc_state(pid)
        return state is not None and state not in ("Z", "X")
    try:  # pragma: no cover - non-POSIX fallback
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]


def _flag_value(args: list[str], flag: str) -> str | None:
    for i, arg in enumerate(args):
        if arg == flag and i + 1 < len(args):
            return args[i + 1]
        if arg.startswith(flag + "="):
            return arg.split("=", 1)[1]
    return None


def proc_age(pid: int) -> float:
    """Seconds since the process started, from /proc (no ps, no psutil)."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        uptime = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0
    fields = stat.rsplit(")", 1)[-1].split()
    try:
        started = float(fields[19]) / os.sysconf("SC_CLK_TCK")  # field 22 overall
    except (IndexError, ValueError, OSError):
        return 0.0
    return max(uptime - started, 0.0)


def metrics_answers(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics",
                                    timeout=timeout) as reply:
            return reply.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not ours to reuse"
        return False


def ready_connections(port: int, timeout: float = 1.5) -> int:
    """How many edge connections this connector has registered right now.

    A tunnel prints its URL *before* it is registered; a browser that polls in
    that window (or polls a name whose connection died) gets Cloudflare's 1033
    "tunnel not connected" page while the process looks perfectly healthy.
    Readiness is the only honest signal, and it is what "running" means here.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready",
                                    timeout=timeout) as reply:
            payload = json.loads(reply.read().decode("utf-8", "replace"))
        return int(payload.get("readyConnections") or payload.get("ready") or 0)
    except Exception:  # noqa: BLE001 - an unreadable answer is zero connections
        return 0


def our_tunnels(directory: Path | None = None) -> list[int]:
    """Every cloudflared this app started, from any script generation.

    The binary's directory is the identity: the app keeps its own copy of
    cloudflared in the runtime directory, so a cloudflared someone else started
    (another app, another tunnel, a hand-run debug session) is never touched.
    """
    directory = (directory or runtime_dir()).resolve()
    found: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return found
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        args = _cmdline(pid)
        if not args or "tunnel" not in args:
            continue
        try:
            if Path(args[0]).resolve().parent != directory:
                continue
        except OSError:
            continue
        if "cloudflared" in os.path.basename(args[0]) and _proc_alive(pid):
            found.append(pid)
    return sorted(found)


def public_answers(hostname: str, timeout: float = 5.0) -> bool:
    """Does the operator's hostname really reach this app right now?

    ``/_stcore/health`` is Streamlit's own endpoint: "ok" means DNS, the edge,
    the tunnel and the app all agree. Nothing in this app answers that path by
    accident, which is why it is the end-to-end check.
    """
    host = hostname.removeprefix("https://").removeprefix("http://").strip("/")
    try:
        with urllib.request.urlopen(f"https://{host}/_stcore/health",
                                    timeout=timeout) as reply:
            return reply.status == 200 and reply.read(16).strip() == b"ok"
    except Exception:  # noqa: BLE001 - an unreachable hostname is a normal answer
        return False


def state_answers(url: str, timeout: float = 5.0) -> bool:
    """Does the public hostname serve the card's numbers (not some HTML)?"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as reply:
            body = reply.read(512)
        return reply.status == 200 and b"{" in body
    except Exception:  # noqa: BLE001 - a dead tunnel is a normal answer here
        return False


def _tail_hint(tail: str) -> str:
    """Turn cloudflared's failure modes into a sentence the operator can act on."""
    if "Invalid tunnel secret" in tail or "Unauthorized" in tail:
        return ("the token was rejected (Invalid tunnel secret) — the secret is "
                "wrong, revoked, or belongs to another tunnel")
    if "address already in use" in tail:
        return "the metrics port is taken — the connector would need a free one"
    if "Cannot determine default configuration path" in tail:
        return ""
    return ""

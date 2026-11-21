"""Active viewers currently routed through OUR cloudflared replica.

We deliberately do not use Cloudflare-side metrics here. The question is "how
many connections is this tunnel holding right now", and the replica itself knows
the answer: every browser session proxied through it keeps an ESTABLISHED TCP
socket from the cloudflared process to the local Streamlit ingress port.

Counting sockets owned by that PID (rather than all sockets to the port)
excludes the Streamlit Cloud proxy and any local health checks, so the number
describes only traffic that went through the token-authenticated tunnel.

/proc/net/tcp state 01 == ESTABLISHED (0A is LISTEN, 06 is TIME_WAIT).
"""
from __future__ import annotations

import os
from pathlib import Path

PROC = Path("/proc")
ESTABLISHED = "01"
TABLES = ("net/tcp", "net/tcp6")


def socket_inodes(pid: int) -> set[str]:
    """Socket inodes held by one process, straight from /proc/<pid>/fd."""
    found: set[str] = set()
    try:
        entries = list((PROC / str(pid) / "fd").iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            link = os.readlink(entry)
        except OSError:  # the process may exit while we enumerate
            continue
        if link.startswith("socket:[") and link.endswith("]"):
            found.add(link[8:-1])
    return found


def _rows(table: str):
    try:
        lines = (PROC / table).read_text().splitlines()[1:]
    except OSError:
        return
    for line in lines:
        fields = line.split()
        if len(fields) >= 10:
            yield fields


def active_sessions(pid: int | None, port: int) -> int:
    """Client-side sockets our replica holds to the local app right now."""
    if not pid or port <= 0:
        return 0
    inodes = socket_inodes(pid)
    if not inodes:
        return 0
    count = 0
    for table in TABLES:
        for fields in _rows(table):
            if fields[3] != ESTABLISHED or fields[9] not in inodes:
                continue
            try:
                if int(fields[2].split(":")[1], 16) == port:
                    count += 1
            except (ValueError, IndexError):
                continue
    return count


def ingress_sessions(port: int) -> int:
    """Every live socket accepted on the app's ingress port (server side).

    cloudflared multiplexes many client streams over a small pool of origin
    connections, so counting only its client sockets undercounts badly. The
    server side of the same connections is owned by the app process and sees
    each accepted socket, websockets included.
    """
    if port <= 0:
        return 0
    count = 0
    for table in TABLES:
        for fields in _rows(table):
            if fields[3] != ESTABLISHED:
                continue
            try:
                if int(fields[1].split(":")[1], 16) == port:
                    count += 1
            except (ValueError, IndexError):
                continue
    return count


def connections(pid: int | None, port: int) -> int:
    """The larger of both ends: the live connections routed to the app."""
    return max(active_sessions(pid, port), ingress_sessions(port))

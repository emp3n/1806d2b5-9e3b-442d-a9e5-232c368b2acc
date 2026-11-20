"""Download and run cloudflared for a deployed copy of the card.

The local lab has a connector, a feed on 8598 and a gateway on 8599 already
running next to Streamlit. Streamlit Cloud has none of that: the page is served
from ``<repo>.streamlit.app``, so the card cannot poll a loopback address and
Streamlit Cloud does not proxy an arbitrary ``/api/state`` path.

On ``*.streamlit.app`` this module therefore:

1. downloads the cloudflared binary into ``.temp/`` (rewrite with CARD_RUNTIME),
2. starts a *quick tunnel* (no token needed) aimed at the in-process feed,
3. reads the public ``https://<id>.trycloudflare.com`` URL out of its log,
4. hands that URL to the card as the explicit feed address.

Nothing is written outside the runtime directory, every attempt is cached in
one place, and a connector that dies is reported with its real exit code —
including 255 — instead of leaving a page with silently frozen numbers.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VERSION = "2026.11.1"
BASE = "https://github.com/cloudflare/cloudflared/releases/download"

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def in_cloud_runtime() -> bool:
    """Community Cloud mounts the app at /mount/src/<repo> and does not give
    st.context.url to the script until a session exists. Checking both makes
    the tunnel bootstrap reliable on the very first visit."""
    if os.environ.get("CARD_TUNNEL", "").lower() == "on":
        return True
    return Path("/mount/src").is_dir()


def on_streamlit_cloud(url: str | None) -> bool:
    """A Streamlit Cloud page cannot proxy /api/state to our in-process feed.

    There the app opens a quick tunnel to the feed; a page served from a
    hostname we control keeps using the gateway on its own origin.
    """
    from urllib.parse import urlsplit

    try:
        host = (urlsplit(url).hostname or "").lower() if url else ""
    except ValueError:
        return False
    return host.endswith(".streamlit.app")


@dataclass
class Tunnel:
    ok: bool
    api_url: str | None = None
    public_url: str | None = None
    binary: str | None = None
    pid: int | None = None
    detail: str = ""

    @property
    def message(self) -> str:
        return self.detail if not self.ok else f"tunnel {self.public_url} (pid {self.pid})"


def runtime_dir() -> Path:
    """Where binaries and logs live. ``.temp/`` in the app's directory by
    default, exactly the scratch folder the operator asked for."""
    override = os.environ.get("CARD_RUNTIME")
    if override:
        path = Path(override).expanduser()
    else:
        path = Path(__file__).resolve().parent / ".temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def asset_name() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = machine
    if system == "linux":
        return f"cloudflared-linux-{arch}"
    if system == "darwin":
        return f"cloudflared-darwin-{arch}"
    raise RuntimeError(f"no cloudflared release for {system}/{machine}")


def download_url(version: str = VERSION) -> str:
    return f"{BASE}/{version}/{asset_name()}"


def existing_binary(directory: Path | None = None) -> Path | None:
    """Prefer a binary we already downloaded; accept a system one as a fallback."""
    directory = directory or runtime_dir()
    candidate = directory / "cloudflared"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("cloudflared")
    return Path(found) if found else None


def download(directory: Path | None = None, version: str = VERSION,
             opener=None) -> Path:
    """Fetch cloudflared into .temp/ and make it executable.

    The download goes to a temporary file first: a half-written 40 MB binary
    must never be mistaken for a working one on the next app rerun.
    """
    directory = directory or runtime_dir()
    target = directory / "cloudflared"
    if target.is_file() and os.access(target, os.X_OK):
        return target
    url = download_url(version)
    fetch = opener or urllib.request.urlopen
    partial = directory / "cloudflared.download"
    print(f"cloudflared: downloading {url}", flush=True)
    with fetch(url, timeout=25) as reply, partial.open("wb") as out:
        shutil.copyfileobj(reply, out)
    if partial.stat().st_size < 1_000_000:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"suspiciously small cloudflared download: {url}")
    partial.replace(target)
    target.chmod(0o755)
    print(f"cloudflared: binary ready ({target.stat().st_size} bytes)", flush=True)
    return target


def version_of(binary: Path) -> str:
    result = subprocess.run([str(binary), "--version"], capture_output=True,
                            text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"cloudflared --version exited {result.returncode}: {result.stderr[:200]}")
    return (result.stdout + result.stderr).strip().splitlines()[0]


def _log_tail(path: Path, count: int = 25) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-count:])


def start_quick_tunnel(binary: Path, feed_port: int, directory: Path | None = None,
                       wait_s: float = 20.0, runner=None) -> Tunnel:
    """Run ``cloudflared tunnel --url http://127.0.0.1:<feed_port>``.

    A quick tunnel needs no token, so a fresh Streamlit Cloud deployment works
    without any secret. The public URL is printed in the log; that is what the
    card polls. A process that exits early is reported with its exit code.
    """
    directory = directory or runtime_dir()
    log = directory / "cf.log"
    pidfile = directory / "cf.pid"
    command = [
        str(binary), "tunnel",
        "--url", f"http://127.0.0.1:{feed_port}",
        "--metrics", "127.0.0.1:20241",
        "--logfile", str(log),
        "--pidfile", str(pidfile),
        "--loglevel", "info",
    ]
    print(f"cloudflared: quick tunnel -> 127.0.0.1:{feed_port}", flush=True)
    launch = runner or subprocess.Popen
    try:
        proc = launch(command, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    except OSError as exc:
        return Tunnel(False, detail=f"cannot start cloudflared: {exc}")

    deadline = time.monotonic() + wait_s
    found = None
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            tail = _log_tail(log)
            hint = " (already running? duplicate tunnel? no outbound network?)" if code == 255 else ""
            return Tunnel(False, detail=(
                f"cloudflared exited with code {code}{hint}\n{tail[-900:]}"
            ))
        text = _log_tail(log, 200)
        match = TUNNEL_RE.search(text)
        if match:
            found = match.group(0)
            break
        time.sleep(0.4)

    if not found:
        proc.terminate()
        return Tunnel(False, detail=(
            f"no tunnel URL in {log} after {wait_s:g}s\n{_log_tail(log)[-900:]}"
        ))
    return Tunnel(True, api_url=f"{found}/api/state", public_url=found,
                  binary=str(binary), pid=proc.pid)


_lock = threading.Lock()
_cached: Tunnel | None = None


def ensure_tunnel(feed_port: int, *, force: bool = False,
                  wait_s: float = 20.0) -> Tunnel:
    """One tunnel per Streamlit process, shared across script reruns."""
    global _cached
    with _lock:
        if _cached is not None and not force and _cached.ok:
            return _cached
        try:
            binary = download()
            tunnel = start_quick_tunnel(binary, feed_port, wait_s=wait_s)
        except Exception as exc:  # noqa: BLE001 - the card must name the failure
            tunnel = Tunnel(False, detail=f"{type(exc).__name__}: {exc}")
        print(f"cloudflared: {tunnel.message}", flush=True)
        _cached = tunnel
        return tunnel

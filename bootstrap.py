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
import socket
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

VERSION = "2026.11.1"
BASE = "https://github.com/cloudflare/cloudflared/releases/download"

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# The metrics listener the sampler reads (and that a second instance collides
# with). It is a *preference*, not a constraint: a survivor from the previous
# Streamlit process can still hold it, so a fresh connector asks for a free
# port instead of dying with "bind: address already in use".
METRICS_PORT = 20241


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
    started, so 20241 is routinely still bound; starting a second cloudflared
    on it exits with `listen tcp 127.0.0.1:20241: bind: address already in use`
    and the card reports a dead tunnel while a working one is right there.
    """
    if port_is_free(preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _proc_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if Path("/proc").is_dir():
        return Path(f"/proc/{pid}").exists()
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


def metrics_answers(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics",
                                    timeout=timeout) as reply:
            return reply.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not ours to reuse"
        return False


def feed_answers(url: str, timeout: float = 4.0) -> bool:
    """Does the public tunnel really serve the feed right now?"""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/state",
                                    timeout=timeout) as reply:
            return reply.status == 200 and b"{" in reply.read(256)
    except Exception:  # noqa: BLE001 - a dead tunnel is a normal answer here
        return False


def adopt_running_tunnel(feed_port: int, directory: Path | None = None,
                         verified: bool = True) -> Tunnel | None:
    """Reuse the connector a previous Streamlit process already started.

    A reboot of the script (or a redeploy) leaves the child cloudflared alive
    and still tunnelling to ``127.0.0.1:<feed_port>`` inside the same container:
    the pidfile, the log and the metrics listener all survive. Starting a
    second connector is both unnecessary and the exact thing that fails, so the
    new process reads the old one instead — same pid, same public URL.
    """
    directory = directory or runtime_dir()
    pidfile = directory / "cf.pid"
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _proc_alive(pid):
        return None
    args = _cmdline(pid)
    if not args or "cloudflared" not in os.path.basename(args[0]):
        return None
    if "tunnel" not in args:
        return None
    target = _flag_value(args, "--url") or ""
    if f":{feed_port}" not in target:
        return None
    metrics = _flag_value(args, "--metrics") or ""
    port = metrics.rsplit(":", 1)[-1]
    if not port.isdigit() or not metrics_answers(int(port)):
        return None
    log = Path(_flag_value(args, "--logfile") or (directory / "cf.log"))
    found = TUNNEL_RE.findall(_log_tail(log, 400))
    if not found:
        return None
    public = found[-1]
    api_url = f"{public}/api/state"
    if verified and not feed_answers(api_url):
        return None
    return Tunnel(True, api_url=api_url, public_url=public, binary=args[0],
                  pid=pid, detail=f"reused running tunnel {public} (pid {pid})")


def start_quick_tunnel(binary: Path, feed_port: int, directory: Path | None = None,
                       wait_s: float = 20.0, runner=None,
                       metrics_port: int | None = None) -> Tunnel:
    """Run ``cloudflared tunnel --url http://127.0.0.1:<feed_port>``.

    A quick tunnel needs no token, so a fresh Streamlit Cloud deployment works
    without any secret. The public URL is printed in the log; that is what the
    card polls. A process that exits early is reported with its exit code.

    The metrics listener takes the preferred port only while it is free: a
    connector left over from the previous script process still owns 20241, and
    binding it twice is what made the "second" tunnel die instantly.
    """
    directory = directory or runtime_dir()
    log = directory / "cf.log"
    pidfile = directory / "cf.pid"
    metrics = metrics_port or pick_metrics_port()
    command = [
        str(binary), "tunnel",
        "--url", f"http://127.0.0.1:{feed_port}",
        "--metrics", f"127.0.0.1:{metrics}",
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


def retire_stale_tunnel(directory: Path | None = None) -> str:
    """Stop a pidfile connector that could not be adopted.

    It is ours (its path is the runtime binary), it is alive, and it is not
    serving the feed — so it only holds a metrics port and a dead public name.
    """
    pidfile = (directory or runtime_dir()) / "cf.pid"
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return ""
    if not _proc_alive(pid):
        return ""
    args = _cmdline(pid)
    if not args or "cloudflared" not in os.path.basename(args[0]) or "tunnel" not in args:
        return ""
    try:
        os.kill(pid, 15)  # SIGTERM
    except OSError:
        return ""
    return f"retired stale tunnel pid {pid}"


def ensure_tunnel(feed_port: int, *, force: bool = False,
                  wait_s: float = 20.0, verified: bool = True) -> Tunnel:
    """One tunnel per Streamlit process, shared across script reruns.

    Order matters: reuse a connector that is already tunnelling to this feed,
    and only start a new one when nothing adoptable is left. On Community Cloud
    the script process is rebooted far more often than the container, so the
    reuse path is the common one — and the only one that does not collide on the
    metrics port.
    """
    global _cached
    with _lock:
        if _cached is not None and not force and _cached.ok:
            return _cached
        adopted = adopt_running_tunnel(feed_port, verified=verified) if not force else None
        if adopted is not None:
            print(f"cloudflared: {adopted.message}", flush=True)
            _cached = adopted
            return adopted
        retired = retire_stale_tunnel()
        if retired:
            print(f"cloudflared: {retired}", flush=True)
        try:
            binary = download()
            tunnel = start_quick_tunnel(binary, feed_port, wait_s=wait_s)
        except Exception as exc:  # noqa: BLE001 - the card must name the failure
            tunnel = Tunnel(False, detail=f"{type(exc).__name__}: {exc}")
        print(f"cloudflared: {tunnel.message}", flush=True)
        _cached = tunnel
        return tunnel

"""Run the operator's Cloudflare tunnel for the card — locally and in Cloud.

The route is fixed on Cloudflare's side::

    streamlit.idealis.cc  ->  tunnel  ->  http://localhost:8501

so the app has exactly one requirement: it must listen on 8501 and a connector
must run on the same host. Both are true on Streamlit Community Cloud (its
runner starts ``streamlit run main.py`` on 8501) and locally
(``tools/serve.sh`` starts the same thing). This module is what puts a
connector there:

1. find the tunnel token — ``CLOUDFLARE_TUNNEL_TOKEN`` in Streamlit secrets or
   the environment, else ``.temp/cf-token`` on disk; with no token at all it
   falls back to a quick tunnel so the page is never dead on arrival;
2. adopt the connector a previous script process already started instead of
   starting a second one — the child outlives the script, and two connectors on
   one named tunnel split the traffic between two copies of the app;
3. start it with ``--token-file``, never ``--token``, so the secret never
   appears in ``/proc/<pid>/cmdline`` or in a process listing;
4. hand back a :class:`Tunnel` only after the edge reports at least one
   *registered* connection. A process that is up is not a tunnel that works,
   and a page that trusts the process is the "says it is running, nothing is
   running" bug;
5. stop, restart and describe the connector through the same probes the card
   reads, so the buttons and the numbers can never disagree with reality.

Nothing here writes outside the runtime directory (``.temp/`` by default,
``CARD_RUNTIME`` overrides), and every failure carries the real log tail.
"""

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

VERSION = "2026.11.1"
BASE = "https://github.com/cloudflare/cloudflared/releases/download"

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

# The metrics listener the sampler reads (and that a second instance collides
# with). It is a *preference*, not a constraint: a survivor from the previous
# Streamlit process can still hold it, so a fresh connector asks for a free
# port instead of dying with "bind: address already in use".
METRICS_PORT = 20241

# Where the card's numbers are served from. Streamlit serves ``./static/`` at
# ``/app/static/`` (``server.enableStaticServing``), so this path is the same on
# localhost, on ``*.streamlit.app`` and behind the operator's hostname.
STATE_PATH = "/app/static/state.json"

# The operator's own hostname, i.e. the public hostname the tunnel carries.
DEFAULT_HOSTNAME = "streamlit.idealis.cc"

# How long a registered tunnel may fail to answer before it is replaced.
STUCK_AFTER = 180.0
# A cached tunnel is re-probed at most this often: a rerun must not pay a round
# trip, but the page must not keep polling a tunnel that died either.
CHECK_EVERY = 15.0

# Secrets may arrive as a root-level secret (also injected as an env var) under
# any of these names; the first hit wins.
TOKEN_KEYS = ("CLOUDFLARE_TUNNEL_TOKEN", "CARD_TUNNEL_TOKEN")
HOST_KEYS = ("CLOUDFLARE_TUNNEL_HOSTNAME", "CARD_TUNNEL_HOSTNAME")


@dataclass
class Tunnel:
    """What one connector actually is, as far as the probes can tell."""

    ok: bool
    public_url: str | None = None
    hostname: str | None = None
    pid: int | None = None
    binary: str | None = None
    mode: str = "named"            # "named" | "quick" | "none"
    metrics: int | None = None
    detail: str = ""
    token_source: str = ""
    # when the public name last answered; the cache re-probes at most this often
    checked_at: float = 0.0
    extra: list[int] = field(default_factory=list)

    @property
    def state_url(self) -> str | None:
        """The address the *browser* polls: same origin, Streamlit-served."""
        return f"{self.public_url}{STATE_PATH}" if self.public_url else None

    @property
    def message(self) -> str:
        if not self.ok:
            return self.detail or "no tunnel"
        return (f"{self.mode} tunnel {self.public_url} "
                f"(pid {self.pid}, metrics {self.metrics})")


# --------------------------------------------------------------- settings
def _secret(key: str) -> str | None:
    """One root-level key from Streamlit secrets, if there is a secrets file.

    ``st.secrets`` is where Cloud keeps the tunnel token; outside a script run
    (the CLI tools, the tests) it may raise or be empty, which is not an error.
    """
    try:  # streamlit is optional for the CLI tools and the tests
        import streamlit as st

        value = st.secrets.get(key)
        if value:
            return str(value)
    except Exception:  # noqa: BLE001 - no secrets.toml, no script run, no streamlit
        pass
    return None


def _setting(key: str) -> str | None:
    """Secrets first, then the environment: Cloud injects the same key twice."""
    return _secret(key) or os.environ.get(key) or None


def _lookup(key: str, settings=None) -> str | None:
    """Secrets (or the given mapping) win, the environment is the second source.

    ``settings`` exists so a test can describe "this app's secrets" exactly;
    the environment is still consulted, because a local run exports the same
    key and the operator expects it to be found.
    """
    value = None
    if settings is not None:
        try:
            value = settings.get(key)
        except AttributeError:
            value = None
    else:
        value = _secret(key)
    return str(value) if value else (os.environ.get(key) or None)


def token_files() -> list[Path]:
    """Where a token is allowed to live on disk, in preference order."""
    paths: list[Path] = []
    explicit = os.environ.get("CARD_TOKEN_FILE") or os.environ.get("CLOUDFLARED_TOKEN_FILE")
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.append(runtime_dir() / "cf-token")
    return paths


def intent_path(directory: Path | None = None) -> Path:
    return (directory or runtime_dir()) / "tunnel.intent"


def desired_state(directory: Path | None = None) -> str:
    """What the operator last asked for: ``"on"`` (default) or ``"off"``.

    A stop button that a rerun undoes is not a stop button: every Streamlit
    interaction reruns the script, and ``ensure_tunnel`` runs on every script
    run, so without a remembered intent the connector came back a second after
    it was stopped. The file lives in the runtime directory, so it survives a
    rerun, a reloaded page and a rebooted script process — and disappears with
    the container, which is the right default (a redeploy brings the tunnel
    back instead of leaving the site dark).
    """
    try:
        return "off" if intent_path(directory).read_text().strip() == "off" else "on"
    except OSError:
        return "on"


def set_desired(state: str, directory: Path | None = None) -> str:
    """Remember one of the two verbs. Anything else is refused."""
    if state not in ("on", "off"):
        raise ValueError(f"desired state must be 'on' or 'off', not {state!r}")
    path = intent_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state + "\n", encoding="utf-8")
    return state


def tunnel_token(settings=None) -> tuple[str | None, str]:
    """The tunnel token and where it came from; ``(None, reason)`` without one.

    A token must never be printed, logged or put in a dataclass field: the
    returned text is only ever used to write a ``600`` file or to call
    cloudflared.
    """
    for key in TOKEN_KEYS:
        value = _lookup(key, settings)
        if value:
            return value.strip(), f"secret {key}"
    for path in token_files():
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value, f"file {path}"
    return None, ("no tunnel token: set CLOUDFLARE_TUNNEL_TOKEN in the app secrets "
                  f"(or put one in {token_files()[-1]})")


def public_hostname(settings=None) -> str:
    """The hostname the operator's tunnel is routed to (no scheme, no slash)."""
    raw = ""
    for key in HOST_KEYS:
        raw = _lookup(key, settings) or ""
        if raw:
            break
    raw = raw or DEFAULT_HOSTNAME
    raw = raw.strip().removeprefix("https://").removeprefix("http://").strip("/")
    return raw


def ingress_port(settings=None) -> int:
    """The port the Cloudflare route points at (dashboard-side, so a constant)."""
    raw = _lookup("CLOUDFLARE_TUNNEL_INGRESS_PORT", settings) or "8501"
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 8501


def write_token_file(token: str, directory: Path | None = None) -> Path:
    """Put the token on disk with ``600`` rights, and nowhere else.

    ``--token-file`` keeps the secret out of argv; the file is rewritten on each
    start so a rotated token cannot be shadowed by an old copy.
    """
    directory = directory or runtime_dir()
    path = directory / "cf-token"
    directory.mkdir(parents=True, exist_ok=True)
    previous = None
    try:
        previous = path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if previous != token.strip():
        partial = directory / "cf-token.new"
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(token.strip() + "\n")
        os.replace(partial, path)
    else:
        path.chmod(0o600)
    return path


# --------------------------------------------------------------- the binary
def runtime_dir() -> Path:
    """Where binaries, logs and the token live. ``.temp/`` in the app folder."""
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
        raise RuntimeError(
            f"cloudflared --version exited {result.returncode}: {result.stderr[:200]}")
    return (result.stdout + result.stderr).strip().splitlines()[0]


def _log_tail(path: Path, count: int = 25) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-count:])


# --------------------------------------------------------------- /proc probes
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


# --------------------------------------------------------------- the connector
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


_child_procs: list[subprocess.Popen] = []


def _reap() -> None:
    """Collect our own exited children: an unreaped child stays a zombie."""
    for proc in list(_child_procs):
        if proc.poll() is not None:
            _child_procs.remove(proc)


def _spawn(command: list[str], runner=None) -> subprocess.Popen:
    launch = runner or subprocess.Popen
    proc = launch(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  stdin=subprocess.DEVNULL, start_new_session=True)
    _child_procs.append(proc)
    return proc


def _wait_ready(proc, metrics: int, log: Path, wait_s: float) -> tuple[bool, str]:
    """Block until the edge registers a connection, or say why it never did."""
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if ready_connections(metrics) >= 1:
            return True, ""
        if proc.poll() is not None:
            tail = _log_tail(log)
            hint = _tail_hint(tail)
            return False, (f"cloudflared exited with code {proc.returncode}"
                           + (f": {hint}" if hint else "")
                           + (f"\n{tail[-900:]}" if tail else ""))
        time.sleep(0.3)
    # It is running but not registered: say so, do not pretend it works.
    return False, (f"cloudflared did not register a connection in {wait_s:g}s "
                   f"(pid {proc.pid}, metrics {metrics})\n{_log_tail(log)[-700:]}")


def start_named_tunnel(binary: Path, port: int, token: str,
                       directory: Path | None = None, wait_s: float = 30.0,
                       runner=None, metrics_port: int | None = None,
                       token_source: str = "secret") -> Tunnel:
    """``cloudflared tunnel run --token-file`` against the operator's tunnel.

    The public hostname comes from the dashboard route, not from the log: a
    named tunnel prints nothing about its hostname. What is verified here is the
    half that can fail — registration on the edge.
    """
    directory = directory or runtime_dir()
    log = directory / "cf.log"
    pidfile = directory / "cf.pid"
    metrics = metrics_port or pick_metrics_port()
    token_path = write_token_file(token, directory)
    host = public_hostname()
    command = [
        str(binary), "tunnel",
        "--metrics", f"127.0.0.1:{metrics}",
        "--logfile", str(log),
        "--pidfile", str(pidfile),
        "--loglevel", "info",
        "run", "--token-file", str(token_path),
    ]
    print(f"cloudflared: named tunnel -> {host} (ingress localhost:{port})", flush=True)
    try:
        proc = _spawn(command, runner)
    except OSError as exc:
        return Tunnel(False, mode="named", detail=f"cannot start cloudflared: {exc}",
                      token_source=token_source)
    ok, why = _wait_ready(proc, metrics, log, wait_s)
    if not ok:
        return Tunnel(False, hostname=host, binary=str(binary), mode="named",
                      pid=proc.pid, metrics=metrics, detail=why,
                      token_source=token_source)
    ready = ready_connections(metrics)
    detail = f"named tunnel {host} (pid {proc.pid}, {ready} connections)"
    if port != ingress_port():
        detail += (f" — WARNING: the dashboard route points at localhost:"
                   f"{ingress_port()}, but the app listens on {port}")
    return Tunnel(True, public_url=f"https://{host}", hostname=host, pid=proc.pid,
                  binary=str(binary), mode="named", metrics=metrics, detail=detail,
                  token_source=token_source, checked_at=time.monotonic())


def start_quick_tunnel(binary: Path, port: int, directory: Path | None = None,
                       wait_s: float = 20.0, runner=None,
                       metrics_port: int | None = None) -> Tunnel:
    """``cloudflared tunnel --url http://127.0.0.1:<port>``, no account needed.

    This is the fallback when no token is configured (and what CI or a reviewer
    without the operator's secret gets). The public URL is read out of the log,
    then waited on until the edge registers it.
    """
    directory = directory or runtime_dir()
    log = directory / "cf.log"
    pidfile = directory / "cf.pid"
    metrics = metrics_port or pick_metrics_port()
    command = [
        str(binary), "tunnel",
        "--url", f"http://127.0.0.1:{port}",
        "--metrics", f"127.0.0.1:{metrics}",
        "--logfile", str(log),
        "--pidfile", str(pidfile),
        "--loglevel", "info",
    ]
    print(f"cloudflared: quick tunnel -> 127.0.0.1:{port}", flush=True)
    try:
        proc = _spawn(command, runner)
    except OSError as exc:
        return Tunnel(False, mode="quick", detail=f"cannot start cloudflared: {exc}")

    deadline = time.monotonic() + wait_s
    found = None
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            tail = _log_tail(log)
            hint = " (already running? duplicate tunnel? no outbound network?)" \
                if code == 255 else ""
            return Tunnel(False, mode="quick", detail=(
                f"cloudflared exited with code {code}{hint}\n{tail[-900:]}"))
        match = TUNNEL_RE.search(_log_tail(log, 200))
        if match:
            found = match.group(0)
            break
        time.sleep(0.4)

    if not found:
        proc.terminate()
        return Tunnel(False, mode="quick", detail=(
            f"no tunnel URL in {log} after {wait_s:g}s\n{_log_tail(log)[-900:]}"))

    # A URL is not a connection: the name is printed before the edge accepts
    # traffic, and a page that polls in that window shows Cloudflare's 1033 page.
    ok, why = _wait_ready(proc, metrics, log, min(wait_s, 10.0))
    if not ok:
        return Tunnel(False, public_url=found, mode="quick", pid=proc.pid,
                      binary=str(binary), metrics=metrics, detail=why)
    return Tunnel(True, public_url=found, hostname=found.removeprefix("https://"),
                  pid=proc.pid, binary=str(binary), mode="quick", metrics=metrics,
                  detail=f"quick tunnel {found} (pid {proc.pid})",
                  checked_at=time.monotonic())


def _describe(pid: int, directory: Path | None = None) -> dict:
    """Everything the probes can say about one connector process."""
    args = _cmdline(pid)
    metrics_text = _flag_value(args, "--metrics") or ""
    suffix = metrics_text.rsplit(":", 1)[-1]
    metrics = int(suffix) if suffix.isdigit() else None
    ready = ready_connections(metrics) if metrics else 0
    mode = "quick" if "--url" in args else "named"
    public = None
    if mode == "quick":
        log = Path(_flag_value(args, "--logfile") or (directory or runtime_dir()) / "cf.log")
        found = TUNNEL_RE.findall(_log_tail(log, 400))
        public = found[-1] if found else None
    else:
        public = f"https://{public_hostname()}"
    return {
        "pid": pid,
        "mode": mode,
        "metrics": metrics,
        "ready": ready,
        "public": public,
        "age": round(proc_age(pid), 1),
        "target": _flag_value(args, "--url") or "",
    }


def tunnel_status(port: int | None = None,
                  directory: Path | None = None) -> dict:
    """The connector this app owns, as facts rather than as a memory.

    Used by the sampler (every second, so the card cannot drift from reality),
    by the buttons and by ``tools/connector.sh status``.
    """
    directory = directory or runtime_dir()
    pids = our_tunnels(directory)
    intent = desired_state(directory)
    base = {
        "ok": False, "mode": "none", "pid": None, "ready": 0, "metrics": None,
        "public": None, "age": 0.0, "target": "", "extra": [],
        "ingress": ingress_port(), "port": port, "intent": intent,
        "detail": ("stopped by the operator" if intent == "off"
                   else "no connector of ours is running"),
    }
    if not pids:
        return base
    described = [_describe(pid, directory) for pid in pids]
    # a registered connector beats a young one, and a young one beats an old
    described.sort(key=lambda item: (item["ready"] > 0, -item["age"]), reverse=True)
    best = described[0]
    best = dict(best)
    best["ok"] = best["ready"] > 0
    best["extra"] = [item["pid"] for item in described[1:]]
    best["ingress"] = ingress_port()
    best["port"] = port
    best["intent"] = intent
    if not best["ok"]:
        best["detail"] = (f"connector pid {best['pid']} is not registered with the "
                          f"edge ({best['mode']} tunnel)")
    else:
        best["detail"] = (f"{best['mode']} tunnel {best['public']} "
                          f"(pid {best['pid']}, {best['ready']} connections)")
    if best["extra"]:
        best["detail"] += f" — plus {len(best['extra'])} extra connector(s)"
    return best


def adopt_running_tunnel(port: int, directory: Path | None = None,
                         verified: bool = True) -> Tunnel | None:
    """Reuse the connector a previous script process already started.

    A reboot of the script (or a redeploy) leaves the child cloudflared alive:
    the pidfile, the log and the metrics listener all survive. Starting a second
    connector is unnecessary on a named tunnel and actively harmful — the edge
    splits traffic between them — so the new process reads the old one instead.
    """
    directory = directory or runtime_dir()
    status = tunnel_status(port, directory)
    pid = status.get("pid")
    if not pid or status["ready"] < 1:
        return None
    if not status.get("public"):
        return None
    # An old connector that never answers the public hostname is stuck and gets
    # replaced; a young one is allowed to need a few seconds to be routed.
    if verified and proc_age(pid) > STUCK_AFTER and not public_answers(status["public"]):
        return None
    return Tunnel(
        True, public_url=status["public"],
        hostname=(status["public"] or "").removeprefix("https://"),
        pid=pid, binary=(_cmdline(pid) or [None])[0], mode=status["mode"],
        metrics=status["metrics"],
        detail=f"reused running {status['mode']} tunnel {status['public']} (pid {pid})",
        token_source=tunnel_token()[1], checked_at=time.monotonic(),
        extra=status.get("extra", []),
    )


def retire_stale_tunnel(keep: int | None = None, directory: Path | None = None) -> str:
    """Stop every connector of ours except the one the card will use.

    Two generations racing at boot each start a cloudflared. On a named tunnel
    the loser does not die — it registers too, and the edge then routes half the
    requests to a copy of the app that may be stale or gone. One app, one
    connector.
    """
    pids = [pid for pid in our_tunnels(directory) if keep is None or pid != keep]
    if keep is not None:
        args = _cmdline(keep)
        metrics = _flag_value(args, "--metrics") or ""
        suffix = metrics.rsplit(":", 1)[-1]
        if not suffix.isdigit() or ready_connections(int(suffix)) < 1:
            return ""      # no healthy keeper: never tear down a survivor
    if not pids:
        return ""
    result = stop_tunnel(pids, directory)
    return f"retired connector(s) {result['stopped']}" if result["stopped"] else ""


def stop_tunnel(pids: list[int] | int | None = None,
                directory: Path | None = None, grace: float = 5.0) -> dict:
    """SIGTERM, wait, SIGKILL — and report what is *actually* gone.

    The pidfile is cleared as well: a stale pid there is what makes a page claim
    a connector that does not exist.
    """
    directory = directory or runtime_dir()
    if pids is None:
        targets = our_tunnels(directory)
    elif isinstance(pids, int):
        targets = [pids]
    else:
        targets = list(pids)
    stopped: list[int] = []
    for pid in targets:
        if not _proc_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except OSError:
            continue
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and any(_proc_alive(pid) for pid in stopped):
        time.sleep(0.1)
    for pid in list(stopped):
        if _proc_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    # SIGKILL is not instant: the process is dead a moment later, and checking
    # the same microsecond made a successful stop report "survived SIGKILL".
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        _reap()
        if not any(_proc_alive(pid) for pid in stopped):
            break
        time.sleep(0.05)
    survivors = [pid for pid in stopped if _proc_alive(pid)]
    # The pidfile describes *the* connector. Only clear it when the pid it names
    # is one of the pids that just went away — retiring an extra connector must
    # not erase the keeper's pidfile.
    pidfile = directory / "cf.pid"
    try:
        named = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        named = None
    if (named is not None and not survivors
            and (named in stopped or not _proc_alive(named))):
        try:
            pidfile.unlink()
        except OSError:
            pass
    return {"ok": not survivors, "stopped": [p for p in stopped if p not in survivors],
            "survivors": survivors}


_lock = threading.Lock()
_cached: Tunnel | None = None


def reusable(tunnel: Tunnel) -> bool:
    """Is this tunnel still worth handing to the card?

    The pid must be alive (not a zombie) and its connector must still report a
    registered connection. The public hostname is only consulted for a tunnel
    that has had time to settle: a fresh name 530s for a few seconds, and
    killing a healthy connector over that lag is what produced endless new
    tunnel names and a page stuck on the previous one.
    """
    if not tunnel.ok or not tunnel.pid or not _proc_alive(tunnel.pid):
        return False
    args = _cmdline(tunnel.pid)
    if not args or "cloudflared" not in os.path.basename(args[0]):
        return False
    metrics = (_flag_value(args, "--metrics") or "").rsplit(":", 1)[-1]
    if not metrics.isdigit() or ready_connections(int(metrics)) < 1:
        return False
    if tunnel.hostname and proc_age(tunnel.pid) > STUCK_AFTER:
        if time.monotonic() - tunnel.checked_at > CHECK_EVERY:
            if not public_answers(tunnel.hostname):
                return False
            tunnel.checked_at = time.monotonic()
    return True


def forget_tunnel() -> None:
    """Drop the per-process cache: the next ``ensure_tunnel`` re-reads reality."""
    global _cached
    with _lock:
        _cached = None


def ensure_tunnel(port: int, *, force: bool = False, wait_s: float = 30.0,
                  verified: bool = True) -> Tunnel:
    """One connector per host, shared across script reruns.

    Order matters: reuse a connector that is already registered, and only start
    a new one when nothing adoptable is left. On Community Cloud the script
    process is rebooted far more often than the container, so the reuse path is
    the common one — and the only one that cannot collide on the metrics port.
    """
    global _cached
    with _lock:
        _reap()
        if not force and desired_state() == "off":
            _cached = None
            if our_tunnels():
                # the file says off, a connector is still up: the file wins
                stop_tunnel()
            return Tunnel(False, mode="none",
                          detail="stopped by the operator — press ▶ to start it")
        # A quick tunnel is a fallback, never a destination: if the token has
        # arrived since this connector started (the operator added the secret,
        # or the app booted before it existed) the named tunnel replaces it.
        # Without this the app sat on a trycloudflare.com name forever and the
        # operator's hostname answered 530.
        upgrade = bool(tunnel_token()[0])
        if _cached is not None and not force and reusable(_cached) \
                and not (upgrade and _cached.mode == "quick"):
            return _cached
        _cached = None
        adopted = None if force else adopt_running_tunnel(port, verified=verified)
        if adopted is not None and upgrade and adopted.mode == "quick":
            # returns the named tunnel, or the quick one still serving when the
            # named start failed: either way it is a real connector, not a blank
            swapped = _replace_with_named(port, adopted, wait_s)
            _cached = swapped
            return swapped
        if adopted is not None:
            retired = retire_stale_tunnel(keep=adopted.pid)
            if retired:
                print(f"cloudflared: {retired}", flush=True)
            print(f"cloudflared: {adopted.message}", flush=True)
            _cached = adopted
            return adopted
        retired = retire_stale_tunnel()
        if retired:
            print(f"cloudflared: {retired}", flush=True)
        token, source = tunnel_token()
        try:
            binary = download()
            if token:
                tunnel = start_named_tunnel(binary, port, token, wait_s=wait_s,
                                            token_source=source)
            else:
                print(f"cloudflared: {source}", flush=True)
                tunnel = start_quick_tunnel(binary, port, wait_s=wait_s)
            if tunnel.ok and tunnel.pid:
                retire_stale_tunnel(keep=tunnel.pid)
        except Exception as exc:  # noqa: BLE001 - the card must name the failure
            tunnel = Tunnel(False, mode="named" if token else "quick",
                            detail=f"{type(exc).__name__}: {exc}")
        print(f"cloudflared: {tunnel.message}", flush=True)
        _cached = tunnel
        return tunnel


def _replace_with_named(port: int, old: Tunnel, wait_s: float) -> Tunnel:
    """Start the named tunnel, then retire the quick one — in that order.

    Returns the new tunnel when it registered, or the old one (still running)
    when it did not, so a missing or rejected token can never darken the site.
    """
    global _cached
    token, source = tunnel_token()
    if not token:
        return old
    try:
        binary = download()
        fresh = start_named_tunnel(binary, port, token, wait_s=wait_s,
                                   token_source=source)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        fresh = Tunnel(False, mode="named", detail=f"{type(exc).__name__}: {exc}")
    print(f"cloudflared: {fresh.message}", flush=True)
    if fresh.ok and fresh.pid:
        if old.pid:
            result = stop_tunnel([old.pid])
            print(f"cloudflared: retired quick connector {old.pid} "
                  f"({'ok' if result['ok'] else 'refused'})", flush=True)
        retire_stale_tunnel(keep=fresh.pid)
        _cached = fresh
        return fresh
    # the named tunnel did not come up: the quick one keeps serving
    _cached = old
    return old


def restart_tunnel(port: int, *, wait_s: float = 30.0) -> Tunnel:
    """Replace the connector without a gap: start the new one, then stop the old.

    A named tunnel keeps its hostname, so a restart is invisible to a browser
    once the page reconnects; a quick tunnel gets a new name, which is why the
    named one is the supported path. The old connector is only touched after the
    new one is registered — a failed start must never leave nothing running.
    """
    global _cached
    with _lock:
        _reap()
        set_desired("on")
        before = tunnel_status(port)
        old = [pid for pid in ([before.get("pid")] + list(before.get("extra") or []))
               if pid]
        token, source = tunnel_token()
        binary = download()
        if token:
            tunnel = start_named_tunnel(binary, port, token, wait_s=wait_s,
                                        token_source=source)
        else:
            tunnel = start_quick_tunnel(binary, port, wait_s=wait_s)
        if tunnel.ok:
            survivors = [pid for pid in old if pid != tunnel.pid]
            if survivors:
                stop_tunnel(survivors)
                print(f"cloudflared: retired {survivors} after a clean start", flush=True)
            retire_stale_tunnel(keep=tunnel.pid)
        _cached = tunnel
        return tunnel

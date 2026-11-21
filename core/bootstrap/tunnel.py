"""Tunnel bootstrap: configuration, binary, probes and process control.

Layer ``tunnel``. Public names are re-exported flat by
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
        time.sleep(0.05)
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
    started = time.monotonic()
    try:
        proc = _spawn(command, runner)
    except OSError as exc:
        return Tunnel(False, mode="named", detail=f"cannot start cloudflared: {exc}",
                      token_source=token_source)
    ok, why = _wait_ready(proc, metrics, log, wait_s)
    elapsed = time.monotonic() - started
    if not ok:
        print(f"cloudflared: start failed after {elapsed:.2f}s: {why[:160]}", flush=True)
        return Tunnel(False, hostname=host, binary=str(binary), mode="named",
                      pid=proc.pid, metrics=metrics, detail=why,
                      token_source=token_source)
    ready = ready_connections(metrics)
    print(f"cloudflared: registered in {elapsed:.2f}s (pid {proc.pid}, {ready} edges)",
          flush=True)
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


def public_is_stuck(pid: int, hostname: str) -> bool:
    """Has this connector's public name been failing long enough to mean stuck?

    Only repeated misses count, and only once per CHECK_EVERY seconds, so a
    flaky probe costs nothing. A connector that answers ``/ready`` is left
    alone: the hostname is how the *operator* reaches the page, not what makes
    the connector run.
    """
    if not hostname:
        return False
    now = time.monotonic()
    count, last = _public_failures.get(pid, (0, 0.0))
    if count and now - last < CHECK_EVERY:
        return count >= PUBLIC_STRIKES
    if public_answers(hostname):
        _public_failures[pid] = (0, now)
        return False
    count += 1
    _public_failures[pid] = (count, now)
    return count >= PUBLIC_STRIKES


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
    # The public hostname is NOT a liveness signal. From a Community Cloud
    # container the edge answers 403 to that probe (verified: HTTP 403 in
    # 0.11 s), so treating misses as evidence condemned a healthy connector
    # every few minutes and produced the operator's "it restarts by itself".
    # Registration (ready >= 1) and the process check above are the contract;
    # public_answers() remains a diagnostic, never a kill switch.
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
                directory: Path | None = None, grace: float = 1.0) -> dict:
    """SIGTERM, wait, SIGKILL — and report what is *actually* gone.

    The pidfile is cleared as well: a stale pid there is what makes a page claim
    a connector that does not exist.
    """
    directory = directory or runtime_dir()
    started = time.monotonic()
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
        time.sleep(0.02)
    for pid in list(stopped):
        if _proc_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    # SIGKILL is not instant: the process is dead a moment later, and checking
    # the same microsecond made a successful stop report "survived SIGKILL".
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        _reap()
        if not any(_proc_alive(pid) for pid in stopped):
            break
        time.sleep(0.02)
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
    if stopped:
        print(f"cloudflared: stopped {stopped} in {time.monotonic() - started:.2f}s",
              flush=True)
    return {"ok": not survivors, "stopped": [p for p in stopped if p not in survivors],
            "survivors": survivors}


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
    # A registered connector is reusable, full stop. The public hostname can
    # answer 403 to probes originating in the Cloud container even while all
    # four edges are live, so it must never feed back into this decision.
    return True


def _forget_strikes(pid: int | None) -> None:
    if pid:
        _public_failures.pop(pid, None)


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
    started = time.monotonic()
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
        print(f"cloudflared: restart finished in {time.monotonic() - started:.2f}s",
              flush=True)
        return tunnel

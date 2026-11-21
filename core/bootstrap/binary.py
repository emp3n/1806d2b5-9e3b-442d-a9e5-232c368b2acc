"""Tunnel bootstrap: configuration, binary, probes and process control.

Layer ``binary``. Public names are re-exported flat by
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


def binary_path(directory: Path, version: str) -> Path:
    """One executable per release: replacing it cannot hit a running process.

    ETXTBSY ("Text file busy") came from writing a new release over the path of
    the binary cloudflared was executing. Versioned names give each download a
    fresh inode, so choosing a version never touches the running connector.
    """
    return directory / f"cloudflared-{version}"


def installed_binary(directory: Path, version: str) -> Path | None:
    path = binary_path(directory, version)
    return path if path.is_file() and os.access(path, os.X_OK) else None


def existing_binary(directory: Path | None = None, version: str | None = None) -> Path | None:
    """The release-specific executable, else the legacy single-file binary."""
    directory = directory or runtime_dir()
    if version:
        found = installed_binary(directory, version)
        if found:
            return found
    candidate = directory / "cloudflared"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("cloudflared")
    return Path(found) if found else None


_DL_LOCK = threading.Lock()


def _copy_with_progress(reply, out, update) -> int:
    """Stream a response in chunks, publishing exact byte progress."""
    total = int(reply.headers.get("Content-Length") or 0) if hasattr(reply, "headers") else 0
    done = 0
    update(0, total)
    while True:
        chunk = reply.read(64 * 1024)
        if not chunk:
            break
        out.write(chunk)
        done += len(chunk)
        update(done, total)
    return done


def download(directory: Path | None = None, version: str = VERSION,
             opener=None, force: bool = False) -> Path:
    """Fetch one release into its own file, with exact byte progress.

    The lock plus the post-lock existence check mean two script sessions that
    reboot together fetch the same release once, not twice. The temporary file
    is only moved into place after the whole body arrived and passed the size
    check, so a reader never sees a partial binary.
    """
    directory = directory or runtime_dir()
    target = binary_path(directory, version)
    if not force and installed_binary(directory, version):
        return target

    def publish(read: int, total: int) -> None:
        with _install_lock:
            _INSTALL["read"] = read
            _INSTALL["total"] = total

    with _DL_LOCK:
        if not force and installed_binary(directory, version):
            return target
        url = download_url(version)
        fetch = opener or urllib.request.urlopen
        partial = directory / f"cloudflared-{version}.download"
        print(f"cloudflared: downloading {url}", flush=True)
        try:
            with fetch(url, timeout=25) as reply, partial.open("wb") as out:
                size = _copy_with_progress(reply, out, publish)
            if size < 1_000_000:
                raise RuntimeError(f"suspiciously small cloudflared download: {url}")
            partial.replace(target)
            target.chmod(0o755)
        finally:
            if partial.exists():
                partial.unlink()
    print(f"cloudflared: binary ready v{version} ({target.stat().st_size} bytes)", flush=True)
    return target


def supports_token_tunnels(binary: Path) -> bool:
    """Old releases use `--cred-file`, not our `--token-file` launch mode."""
    try:
        result = subprocess.run([str(binary), "tunnel", "run", "--help"],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return "--token-file" in (result.stdout + result.stderr)


# A version choice downloads in the background. The connector keeps serving
# until the operator presses Start; only then is the installed binary used.
_INSTALL: dict[str, object] = {"version": "", "state": "idle", "detail": "",
                             "read": 0, "total": 0}
_install_lock = threading.Lock()


def install_status() -> dict[str, str]:
    with _install_lock:
        return dict(_INSTALL)


def start_install(version: str | None = None) -> dict[str, str]:
    """Fetch a release on a daemon thread; return the state immediately.

    A second click on the same version while a download is running is a no-op,
    so the card cannot stack 40 MB downloads behind each other.
    """
    target = version or resolved_version()
    with _install_lock:
        if _INSTALL["state"] == "downloading" and _INSTALL["version"] == target:
            return dict(_INSTALL)
        _INSTALL.update(version=target, state="downloading", detail="")

    def job() -> None:
        try:
            path = download(version=target, force=True)
            if not supports_token_tunnels(path):
                raise RuntimeError(
                    f"{target} predates token-based tunnels (--token-file); "
                    "choose a newer release")
            with _install_lock:
                _INSTALL.update(state="ready", detail="")
        except Exception as exc:  # noqa: BLE001 - surfaced in the card, never raised here
            with _install_lock:
                _INSTALL.update(state="error",
                                detail=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=job, name="cf-version-download", daemon=True).start()
    return install_status()


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

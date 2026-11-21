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


def existing_binary(directory: Path | None = None) -> Path | None:
    """Prefer a binary we already downloaded; accept a system one as a fallback."""
    directory = directory or runtime_dir()
    candidate = directory / "cloudflared"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    found = shutil.which("cloudflared")
    return Path(found) if found else None


def download(directory: Path | None = None, version: str = VERSION,
             opener=None, force: bool = False) -> Path:
    """Fetch one cloudflared release into .temp/ and make it executable.

    The download goes to a temporary file first: a half-written 40 MB binary
    must never be mistaken for a working one on the next app rerun. The small
    marker next to it says which release the binary is, so picking another
    version in the card really replaces it instead of silently reusing the old
    one forever.
    """
    directory = directory or runtime_dir()
    target = directory / "cloudflared"
    marker = directory / "cloudflared.installed"
    installed = ""
    try:
        installed = marker.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    if (not force and installed == version and target.is_file()
            and os.access(target, os.X_OK)):
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
    marker.write_text(version + "\n", encoding="utf-8")
    print(f"cloudflared: binary ready v{version} ({target.stat().st_size} bytes)", flush=True)
    return target


# A version choice downloads in the background. The connector keeps serving
# until the operator presses Start; only then is the installed binary used.
_INSTALL: dict[str, str] = {"version": "", "state": "idle", "detail": ""}
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
            download(version=target, force=True)
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

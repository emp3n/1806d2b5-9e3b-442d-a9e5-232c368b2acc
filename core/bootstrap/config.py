"""Tunnel bootstrap: configuration, binary, probes and process control.

Layer ``config``. Public names are re-exported flat by
``__init__.py``; put new code in the layer that owns it.
"""
from __future__ import annotations

from __future__ import annotations
import functools
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

# The remembered choice lives beside the intent and the token: one small file
# that survives a Streamlit rerun, a page reload and a script reboot, and is
# wiped with the container. "latest" means "the newest published tag".
VERSION_FILE = "cloudflared.version"
VERSIONS_API = "https://api.github.com/repos/cloudflare/cloudflared/tags?per_page=100"
VERSIONS_TIMEOUT = 2.0
TAG_RE = re.compile(r"^\d{4}\.\d{1,2}\.\d{1,2}$")
FALLBACK_VERSIONS = (VERSION, "2026.11.0", "2026.10.7", "2026.10.4", "2026.10.3")

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


def version_path(directory: Path | None = None) -> Path:
    return (directory or runtime_dir()) / VERSION_FILE


def desired_version(directory: Path | None = None) -> str:
    """The version the operator picked, or ``latest`` before any pick."""
    try:
        value = version_path(directory).read_text(encoding="utf-8").strip()
    except OSError:
        return "latest"
    if value == "latest" or TAG_RE.fullmatch(value):
        return value
    return "latest"  # a corrupt file must never turn into a bad download URL


def set_desired_version(value: str, directory: Path | None = None) -> str:
    """Persist a version tag after validating it as a URL path component."""
    name = str(value).strip()
    if name != "latest" and not TAG_RE.fullmatch(name):
        raise ValueError(f"not a cloudflared version tag: {name!r}")
    path = version_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(name + "\n", encoding="utf-8")
    return name


@functools.lru_cache(maxsize=1)
def _versions_cached() -> tuple[str, ...]:
    """Published tags, newest first. One call per process; no browser access."""
    try:
        request = urllib.request.Request(
            VERSIONS_API,
            headers={"User-Agent": "apllme-card",
                     "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(request, timeout=VERSIONS_TIMEOUT) as reply:
            data = json.loads(reply.read(256_000))
        tags = [str(item.get("name") or "") for item in data
                if isinstance(item, dict)]
        good = tuple(tag for tag in tags if TAG_RE.fullmatch(tag))
        return good or FALLBACK_VERSIONS
    except Exception:  # noqa: BLE001 - a blocked API still leaves a usable list
        return FALLBACK_VERSIONS


def versions() -> list[str]:
    return list(_versions_cached())


versions.cache_clear = _versions_cached.cache_clear


def resolved_version() -> str:
    """Turn the remembered choice into a concrete release tag."""
    chosen = desired_version()
    if chosen == "latest":
        available = versions()
        return available[0] if available else VERSION
    return chosen


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


def cloud_url(settings=None) -> str:
    """The deployment's own ``*.streamlit.app`` address, if it is configured.

    Stopping the connector takes the tunnel hostname down with it, so the page
    has to name an address that survives that — otherwise "stopped by you" is a
    dead end and the operator is left with a 530 and no way back in.
    """
    for key in URL_KEYS:
        value = _lookup(key, settings)
        if value:
            return str(value).strip().rstrip("/")
    return ""


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


def runtime_dir() -> Path:
    """Where binaries, logs and the token live. ``.temp/`` in the app folder."""
    override = os.environ.get("CARD_RUNTIME")
    if override:
        path = Path(override).expanduser()
    else:
        path = Path(__file__).resolve().parents[1] / ".temp"
    path.mkdir(parents=True, exist_ok=True)
    return path

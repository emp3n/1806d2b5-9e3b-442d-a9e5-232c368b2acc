"""The card's one channel: a JSON file Streamlit itself serves.

The operator's Cloudflare route is fixed::

    streamlit.idealis.cc -> tunnel -> http://localhost:8501

so the only thing a browser can reach is Streamlit itself. ``server.
enableStaticServing`` publishes ``./static/`` at ``/app/static/``, which makes
the numbers *same origin* on ``localhost:8501``, on ``*.streamlit.app`` and
behind the operator's hostname — one path, no second port, no CORS, no gateway,
and no extra tunnel that has to stay alive just for the data.

A daemon thread rewrites the file once a second. Every write goes to a temporary
file and is then ``os.replace``d onto the real one, so a reader never sees half
a payload. The payload carries a ``ts`` heartbeat: a card that finds an old one
says "stalled" instead of freezing on numbers nobody is refreshing.

The sampler reads the connector through :mod:`bootstrap` (the one owner of the
process) and through :mod:`live` (the /proc and metrics reader), so the footer,
the gauges and the buttons all describe the same process.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import bootstrap
from live import Collector

INTERVAL = 1.0
FILENAME = "state.json"


def static_dir(directory: Path | None = None) -> Path:
    """The folder Streamlit publishes; it lives next to the app, in the repo."""
    return (directory or Path(__file__).resolve().parent) / "static"


def state_path(directory: Path | None = None) -> Path:
    return static_dir(directory) / FILENAME


def write_state(payload: dict[str, Any], path: Path | None = None) -> Path:
    """Replace the state file atomically; a reader gets old or new, never half."""
    target = path or state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".new")
    text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, target)
    return target


def read_state(path: Path | None = None) -> dict[str, Any]:
    """The last payload, or an empty one when the file is missing or broken."""
    try:
        return json.loads((path or state_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def state_text(state: dict[str, Any]) -> str:
    """The header line: how many edges are live. The reason is its own field."""
    return f'{state.get("live", 0)} of 4 connections live'


def to_payload(state: dict[str, Any], last_action: dict[str, Any] | None = None,
               tunnel: dict[str, Any] | None = None) -> dict[str, Any]:
    """The compact message the page needs, no markup, no spark geometry."""
    tunnel = tunnel or {}
    if not state.get("found"):
        payload: dict[str, Any] = {"found": False}
        if last_action:
            payload["last_action"] = last_action
        if tunnel:
            payload["tunnel"] = {
                "ok": bool(tunnel.get("ok")),
                "mode": tunnel.get("mode") or "none",
                "pid": tunnel.get("pid"),
                "ready": int(tunnel.get("ready") or 0),
                "public": tunnel.get("public"),
                "intent": tunnel.get("intent") or "on",
            }
        return payload
    payload = {
        "found": True,
        "pid": state["pid"],
        "live": state["live"],
        "note": state.get("note", ""),
        "status_text": state_text(state),
        "status_state": "up" if state["connected"] else "down",
        "cpu": round(float(state["cpu"]), 2),
        "mem": round(float(state["mem"]), 2),
        "mem_scale": float(state.get("mem_scale", 256.0)),
        "uptime": state["uptime"],
        "version": state["version"],
        "path": state["path"],
        "origin": state["edge_ip"],
        "edges": state["edge_names"],
        "proc_state": state["proc_state"],
        "source_label": state["source_label"],
        "register_fail": state.get("register_fail", 0),
        "tunnel": {
            "ok": bool(tunnel.get("ok")),
            "mode": tunnel.get("mode") or "none",
            "ready": int(tunnel.get("ready") or state["live"]),
            "public": tunnel.get("public"),
            "intent": tunnel.get("intent") or "on",
        },
    }
    if last_action:
        payload["last_action"] = last_action
    return payload


class Sampler:
    """Samples the connector on a thread and publishes it as ``state.json``."""

    def __init__(self, port: int | None = None, interval: float = INTERVAL,
                 directory: Path | None = None) -> None:
        self.port = port
        self.interval = interval
        self.directory = directory
        self.collector = Collector()
        self.tunnel: dict[str, Any] = {}
        self.last_action: dict[str, Any] | None = None
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {"found": False}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="card-sampler",
                                        daemon=True)
        self.last_error = ""

    # -- lifecycle
    def start(self) -> "Sampler":
        if not self._thread.is_alive():
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.refresh()
            except Exception as exc:  # noqa: BLE001 - a sample must never kill the feed
                self.last_error = f"{type(exc).__name__}: {exc}"
                print(f"card: sampler error {self.last_error}", flush=True)
            self._stop.wait(self.interval)

    # -- one turn of the crank
    def refresh(self) -> dict[str, Any]:
        """Probe the connector, build the payload, publish it."""
        status = bootstrap.tunnel_status(self.port, self.directory) if self.port else {}
        self.tunnel = status
        pid = status.get("pid")
        if pid:
            self.collector.sample(pid=pid)
            state = self.collector.state()
        else:
            # Nothing is running. Say that, instead of reading whatever
            # cloudflared happens to be on the host: the card owns one process.
            self.collector.pid = None
            state = {"found": False}
        payload = to_payload(state, self.last_action, status)
        payload["ts"] = round(time.time(), 3)
        with self._lock:
            self._payload = payload
        write_state(payload, state_path(self.directory))
        return payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._payload)

    # -- the buttons
    def act(self, action: str) -> dict[str, Any]:
        """Perform one verb on the connector and report what actually happened."""
        if action not in ("stop", "start", "restart", "start_or_restart"):
            return {"ok": False, "action": action, "error": f"unknown action {action!r}"}
        result: dict[str, Any]
        if action == "stop":
            # remember it: a rerun must not undo the operator's decision
            bootstrap.set_desired("off", self.directory)
            status = bootstrap.tunnel_status(self.port, self.directory)
            pids = [pid for pid in ([status.get("pid")] + list(status.get("extra") or []))
                    if pid]
            stopped = bootstrap.stop_tunnel(pids, self.directory) if pids else {
                "ok": True, "stopped": [], "survivors": []}
            bootstrap.forget_tunnel()
            result = {"ok": stopped["ok"], "action": "stop",
                      "stopped": stopped["stopped"],
                      "error": "" if stopped["ok"] else
                      f"these pids survived SIGKILL: {stopped['survivors']}"}
        elif action == "restart":
            bootstrap.set_desired("on", self.directory)
            tunnel = bootstrap.restart_tunnel(self.port) if self.port else None
            result = {"ok": bool(tunnel and tunnel.ok), "action": "restart",
                      "pid": getattr(tunnel, "pid", None),
                      "detail": getattr(tunnel, "detail", "no port")}
        else:  # start / start_or_restart
            bootstrap.set_desired("on", self.directory)
            tunnel = bootstrap.ensure_tunnel(self.port, force=True) if self.port else None
            result = {"ok": bool(tunnel and tunnel.ok), "action": "start",
                      "pid": getattr(tunnel, "pid", None),
                      "detail": getattr(tunnel, "detail", "no port")}
        result["at"] = round(time.time())
        self.last_action = result
        try:
            self.refresh()          # the next poll tells the truth immediately
        except Exception as exc:  # noqa: BLE001 - the reply must survive a bad probe
            self.last_error = f"{type(exc).__name__}: {exc}"
        return result


_sampler: Sampler | None = None
_sampler_lock = threading.Lock()


def sampler(port: int | None = None, directory: Path | None = None) -> Sampler:
    """The one sampler for this Streamlit process (a rerun must not add another)."""
    global _sampler
    with _sampler_lock:
        if _sampler is None:
            _sampler = Sampler(port, directory=directory)
        elif port and _sampler.port != port:
            _sampler.port = port
        return _sampler.start()


def idle_payload() -> dict[str, Any]:
    """What ``state.json`` looks like before the first sample (and after a stop)."""
    return {"found": False, "ts": round(time.time(), 3)}


__all__ = ["Sampler", "sampler", "to_payload", "state_text", "write_state",
           "read_state", "state_path", "static_dir", "idle_payload"]

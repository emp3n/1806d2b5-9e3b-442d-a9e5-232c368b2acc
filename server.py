"""One sampler thread, the JSON the card polls, and the two buttons.

The card is a static page inside Streamlit's iframe, so it cannot read /proc or
signal a process. This module is the only bridge: a thread samples the connector
once a second through :mod:`live`, and a tiny HTTP server hands the last payload
to the page at ``/api/state`` (CORS ``*``, because the iframe document has an
opaque origin).

It also carries the card's power buttons. ``POST /api/action`` with
``{"action": "stop" | "start" | "restart"}`` stops the connector the sampler is
reading (SIGTERM, then SIGKILL after 5 s), starts it again from the command line
it was found with, or does both. Three limits keep that honest:

* it only touches the pid it is already reading — never a pid from the request;
* it only ever launches the recorded argv, with ``--token <secret>`` rewritten to
  ``--token-file`` so restarting does not put the secret back into argv;
* the route demands ``X-RD-Action``, a key that is printed into the page and
  *not* into ``/api/state``, so a page that can only read the feed cannot act.

Run it alone to watch the numbers without Streamlit::

    python3 server.py --port 8598
    curl -s localhost:8598/api/state | python3 -m json.tool
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from live import Collector, proc_stat

import live

DEFAULT_PORT = 8598
# addresses that mean "this machine" to whoever is looking at them
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
INTERVAL = 1.0
STOP_GRACE = 5.0     # SIGTERM -> SIGKILL
START_GRACE = 12.0   # a connector needs a moment to appear in /proc
ACTIONS = ("stop", "start", "restart")

# printed into the page, never into /api/state: a cross-site page can read the
# feed (CORS *) but cannot learn this, so it cannot press the buttons
ACTION_KEY = secrets.token_urlsafe(18)


def action_key() -> str:
    return ACTION_KEY


def feed_url(page_url: str | None, api_port: int = DEFAULT_PORT,
             explicit: str | None = None) -> str:
    """The address the *browser* should poll — not the one this host uses.

    ``127.0.0.1`` only means this machine to a browser running on this machine.
    A phone opening the page through a tunnel resolves it to the phone, the
    fetch is refused, and the card keeps whatever the server rendered (that was
    the "data does not update on my phone" report). So:

    * an explicit address (``CARD_API_URL`` / ``?api=``) wins, for a feed exposed
      under its own hostname;
    * a page served from anywhere but loopback polls its own origin, which is
      where ``gateway.py`` puts ``/api/state``;
    * a page served from loopback on this machine keeps talking to the feed's
      own port, exactly as before.
    """
    explicit = (explicit or "").strip()
    if explicit.startswith(("http://", "https://")):
        return explicit
    parts = urlsplit(page_url or "")
    if not parts.hostname or parts.hostname.lower() in LOOPBACK:
        return f"http://127.0.0.1:{api_port}/api/state"
    return f"{parts.scheme or 'http'}://{parts.netloc}/api/state"


def state_text(state: dict[str, Any]) -> str:
    """The header line: how many edges are live. The reason is its own field."""
    return f'{state.get("live", 0)} of 4 connections live'


def to_payload(state: dict[str, Any], last_action: dict[str, Any] | None = None) -> dict[str, Any]:
    """The compact message the page needs, no markup, no spark geometry."""
    if not state.get("found"):
        payload: dict[str, Any] = {"found": False}
        if last_action:
            payload["last_action"] = last_action
        return payload
    return {
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
    }


def alive(pid: int | None) -> bool:
    """True only for a process that is still doing something.

    A child that has exited but not been reaped by its parent lingers in /proc
    as a zombie; the connector is gone the moment it stops running, so the
    panel must not report a zombie as "still up".
    """
    stat = proc_stat(pid) if pid else None
    return bool(stat) and stat.get("state") != "Z"


def launch_argv(argv: list[str], exe: str | None = None, token_file: Path | None = None) -> list[str]:
    """The recorded command line, with any inline secret moved into a file.

    ``cloudflared tunnel run --token …`` works, but relaunching it that way would
    put the secret back into ``/proc/<pid>/cmdline``. The secret is written to a
    ``600`` file next to the binary and passed with ``--token-file`` instead.
    """
    binary = exe or argv[0]
    return [binary] + live.strip_token(argv, exe=binary, token_file=token_file)


class Supervisor:
    """Stop, start and restart the one connector the sampler is reading."""

    def __init__(self, sampler: "Sampler") -> None:
        self.sampler = sampler
        self._lock = threading.Lock()
        self.last_action: dict[str, Any] | None = None
        # the child we launched, kept so its exit is reaped: an unreaped child
        # stays a zombie in /proc, and every restart used to leave one behind
        self._child: subprocess.Popen | None = None

    def _reap(self) -> None:
        if self._child is not None:
            self._child.poll()  # waitpid(WNOHANG): collects the zombie

    # -- reading the target
    @property
    def collector(self) -> Collector:
        return self.sampler.collector

    def pid(self) -> int | None:
        return self.collector.pid

    def command(self) -> list[str] | None:
        if not self.collector.launch:
            return None
        return launch_argv(self.collector.launch, exe=self.collector.launch[0])

    # -- the three verbs
    def stop(self) -> dict[str, Any]:
        pid = self.pid()
        if not alive(pid):
            return {"ok": False, "error": "no cloudflared process to stop"}
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return {"ok": False, "error": "process already gone"}
        except PermissionError:
            return {"ok": False, "error": f"not allowed to signal pid {pid}"}
        deadline = time.monotonic() + STOP_GRACE
        while time.monotonic() < deadline:
            if not alive(pid):
                break
            time.sleep(0.1)
        if alive(pid):  # it ignored SIGTERM
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and alive(pid):
                time.sleep(0.05)
        self._reap()
        if alive(pid):
            return {"ok": False, "error": f"pid {pid} survived SIGKILL"}
        return {"ok": True, "action": "stop", "pid": pid, "forced": False}

    def start(self) -> dict[str, Any]:
        command = self.command()
        if not command:
            return {"ok": False, "error": "no recorded command line to start"}
        if alive(self.pid()):
            return {"ok": False, "error": f"already running as pid {self.pid()}"}
        self._reap()  # a previous stop may have left a zombie with this name
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # outlives the API thread's session
            )
        except OSError as error:
            return {"ok": False, "error": f"cannot start: {error.strerror or error}"}
        self._child = process

        deadline = time.monotonic() + START_GRACE
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return {"ok": False, "error": f"exited at once (code {process.returncode})"}
            if alive(process.pid):
                time.sleep(0.3)  # a connector that is going to die dies now
                if process.poll() is not None:
                    return {"ok": False, "error": f"exited at once (code {process.returncode})"}
                self.collector.pid = process.pid
                return {"ok": True, "action": "start", "pid": process.pid}
            time.sleep(0.1)
        return {"ok": False, "error": "the process never appeared in /proc"}

    def restart(self) -> dict[str, Any]:
        stopped = self.stop()
        if not stopped["ok"]:
            return stopped
        started = self.start()
        if not started["ok"]:
            return started
        started["action"] = "restart"
        started["stopped_pid"] = stopped["pid"]
        return started

    def perform(self, action: str) -> dict[str, Any]:
        if action not in ACTIONS:
            return {"ok": False, "error": f"unknown action {action!r}"}
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "error": "another action is still running"}
        try:
            result = getattr(self, action)()
        finally:
            self._lock.release()
        self.last_action = {"action": action, "ok": result["ok"], "at": round(time.time())}
        # make the next poll tell the truth immediately, not in a second
        try:
            self.sampler.refresh()
        except Exception:  # a half-dead process must not break the reply
            pass
        return result


class Sampler:
    """Samples on a background thread so the series is real while nobody looks."""

    def __init__(self, interval: float = INTERVAL) -> None:
        self.collector = Collector()
        self.interval = interval
        self._lock = threading.Lock()
        self._payload: dict[str, Any] = {"found": False}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="cloudflared-sampler", daemon=True)
        self.supervisor = Supervisor(self)
        self.started_at = time.time()

    def start(self) -> "Sampler":
        if not self._thread.is_alive():
            self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.refresh()
            self._stop.wait(self.interval)

    def refresh(self) -> dict[str, Any]:
        self.collector.sample()
        payload = to_payload(self.collector.state(), self.supervisor.last_action)
        with self._lock:
            self._payload = payload
        return payload

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._payload)

    def stop(self) -> None:
        self._stop.set()


def make_handler(sampler: Sampler) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "card-live/2"
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, body: bytes, kind: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            # the card lives in an srcdoc iframe: its origin is opaque
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-RD-Action")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
            path = self.path.split("?", 1)[0]
            if path == "/api/state":
                self._send(200, json.dumps(sampler.snapshot(), ensure_ascii=False).encode())
            elif path == "/api/action":
                self._send(405, b'{"ok":false,"error":"use POST"}')
            elif path == "/health":
                body = json.dumps({"ok": True, "uptime": round(time.time() - sampler.started_at, 1),
                                   "pid": sampler.collector.pid})
                self._send(200, body.encode())
            else:
                self._send(404, b'{"error":"not found"}')

        def do_OPTIONS(self) -> None:  # noqa: N802 - CORS preflight
            # a 204 carries no body and, per RFC 7230, no Content-Length: the
            # keep-alive socket is reused by the real POST, and a body header
            # on a bodiless reply desynchronises it (Chrome then fails the POST
            # with ERR_FAILED, which reads as "API unreachable")
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-RD-Action")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802 - the name http.server calls
            if self.path.split("?", 1)[0] != "/api/action":
                self._send(404, b'{"error":"not found"}')
                return
            if self.headers.get("X-RD-Action") != ACTION_KEY:
                self._send(403, b'{"ok":false,"error":"missing or wrong X-RD-Action"}')
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                action = str(body.get("action", ""))
            except (ValueError, TypeError):
                self._send(400, b'{"ok":false,"error":"body must be JSON with an action"}')
                return
            if action not in ACTIONS:
                self._send(400, json.dumps({"ok": False, "error": f"unknown action {action!r}"}).encode())
                return
            result = sampler.supervisor.perform(action)
            self._send(200 if result.get("ok") else 409,
                       json.dumps(result, ensure_ascii=False).encode())

        def log_message(self, *args: Any) -> None:  # keep Streamlit's log readable
            return

    return Handler


def serve(sampler: Sampler, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Bind the API and answer in a daemon thread."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(sampler))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="card-api", daemon=True).start()
    return httpd


def main() -> None:
    parser = argparse.ArgumentParser(description="live feed for the cloudflared card")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--once", action="store_true", help="print one payload and exit")
    parser.add_argument("--action", choices=ACTIONS, help="stop, start or restart the connector")
    args = parser.parse_args()

    sampler = Sampler()
    if args.once:
        print(json.dumps(sampler.refresh(), indent=2, ensure_ascii=False))
        return
    if args.action:
        sampler.refresh()
        print(json.dumps(sampler.supervisor.perform(args.action), indent=2, ensure_ascii=False))
        return
    sampler.start()
    serve(sampler, args.port)
    print(f"card live feed on http://127.0.0.1:{args.port}/api/state")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        sampler.stop()


if __name__ == "__main__":
    main()

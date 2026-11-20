"""One origin for the page and the feed.

The card polls ``/api/state``. The feed binds ``127.0.0.1:8598`` — the
*server's* loopback — and that address means the same thing only on the server.
Open the page from a phone and ``127.0.0.1`` is the phone: the fetch is
refused, the card keeps whatever the server rendered at first paint, and
nothing on the page says so. A cloudflared tunnel carries one port, so the page
and the feed have to share one.

This process is that port. It reads the request line, sends ``/api/*`` to the
feed and everything else to Streamlit, then copies bytes in both directions
until the connection closes — which is also how Streamlit's
``/_stcore/stream`` websocket gets through, unchanged and unwrapped.

    python3 gateway.py                       # 0.0.0.0:8600 -> app 8599, api 8598
    cloudflared tunnel --url http://localhost:8600

``/api/gateway`` is answered here rather than relayed, so a health check can
tell the gateway apart from the app it fronts.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import socket
import socketserver
import sys
import urllib.request
from typing import Any

DEFAULT_PORT = 8600
DEFAULT_APP = 8599
DEFAULT_API = 8598
HEAD_LIMIT = 128 * 1024
CLIENT_TIMEOUT = 30.0
IDLE = 600.0            # a quiet websocket is not a dead one

# answered by this process; /api/* goes to the feed, everything else to the app
API_PREFIX = "/api/"
GATEWAY_PATH = "/api/gateway"
MARK = {"ok": True, "gateway": True, "version": 1}


def read_head(sock: socket.socket) -> bytes:
    """Bytes up to and including the blank line that ends the request head.

    Anything already read past it belongs to the body and is returned in the
    same buffer: the caller forwards the whole buffer untouched.
    """
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < HEAD_LIMIT:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf


def relay(a: socket.socket, b: socket.socket) -> None:
    """Copy both ways until both sides have finished talking."""
    open_sides = {a, b}
    while open_sides:
        try:
            ready, _, _ = select.select(list(open_sides), [], [], IDLE)
        except (OSError, ValueError):
            return
        if not ready:
            continue
        for side in ready:
            other = b if side is a else a
            try:
                chunk = side.recv(65536)
            except OSError:
                chunk = b""
            if not chunk:
                open_sides.discard(side)
                try:
                    other.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            try:
                other.sendall(chunk)
            except OSError:
                return


class Handler(socketserver.BaseRequestHandler):
    server: "Gateway"

    def handle(self) -> None:
        client = self.request
        try:
            client.settimeout(CLIENT_TIMEOUT)
            buf = read_head(client)
        except OSError:
            return
        if not buf:
            return
        try:
            line = buf.split(b"\r\n", 1)[0].decode("latin-1")
            _method, target = line.split(" ")[:2]
        except (ValueError, UnicodeDecodeError):
            return
        path = target.split("?", 1)[0]

        if path == GATEWAY_PATH:
            self._reply(client, 200, json.dumps(MARK).encode())
            return

        port = self.server.api_port if path.startswith(API_PREFIX) else self.server.app_port
        if self.server.verbose:
            print(f"{path} -> {port}", flush=True)
        try:
            upstream = socket.create_connection(("127.0.0.1", port), timeout=10)
        except OSError:
            self._unreachable(client, path, port)
            return
        try:
            client.settimeout(None)
            upstream.sendall(buf)
            relay(client, upstream)
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    def _reply(self, sock: socket.socket, code: int, body: bytes) -> None:
        head = (
            f"HTTP/1.1 {code} {'OK' if code == 200 else 'Bad Gateway'}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        try:
            sock.sendall(head + body)
        except OSError:
            pass

    def _unreachable(self, sock: socket.socket, path: str, port: int) -> None:
        """Say which port is missing: the page must not read as a feed error."""
        if path.startswith(API_PREFIX):
            self._reply(sock, 502, json.dumps(
                {"ok": False, "error": f"the feed is not running on 127.0.0.1:{port}"}
            ).encode())
            return
        body = f"the app is not running on 127.0.0.1:{port}\n".encode()
        head = (
            "HTTP/1.1 502 Bad Gateway\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        try:
            sock.sendall(head + body)
        except OSError:
            pass


class Gateway(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], api_port: int, app_port: int,
                 verbose: bool = False) -> None:
        self.api_port = api_port
        self.app_port = app_port
        # CARD_GATEWAY_LOG=1 names every route it takes: the first thing worth
        # knowing when a page says "feed unreachable"
        self.verbose = verbose or bool(os.environ.get("CARD_GATEWAY_LOG"))
        super().__init__(address, Handler)


class Gateway6(Gateway):
    """The same gateway, but one socket for both families.

    ``cloudflared`` resolves ``localhost`` to ``::1`` first and only then to
    127.0.0.1, so a gateway bound to 0.0.0.0 alone answers nothing to the first
    attempt — the tunnel logs "connection refused", the visitor gets an error
    page, and the card looks frozen again for a reason that has nothing to do
    with the card. Bound as ``::`` with V6ONLY off, one socket takes both.
    """

    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except OSError:
            pass
        super().server_bind()


def make(port: int = DEFAULT_PORT, api_port: int = DEFAULT_API,
         app_port: int = DEFAULT_APP, host: str = "::") -> Gateway:
    """Dual-stack by default; a host without a colon stays IPv4 for tests."""
    if ":" in host and socket.has_ipv6:
        try:
            return Gateway6((host, port), api_port=api_port, app_port=app_port)
        except OSError:
            pass                       # no IPv6 here: fall back, do not fail
    return Gateway((host if ":" not in host else "0.0.0.0", port),
                   api_port=api_port, app_port=app_port)


def probe(port: int = DEFAULT_PORT, timeout: float = 0.4) -> dict[str, Any] | None:
    """Is a gateway listening on loopback? Used by tests and by the docs check."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/gateway",
                                    timeout=timeout) as reply:
            return json.loads(reply.read() or b"{}")
    except Exception:                     # noqa: BLE001 - "not there" is the answer
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="one origin for the card and its feed")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CARD_GATEWAY_PORT", DEFAULT_PORT)))
    parser.add_argument("--api", type=int, default=int(os.environ.get("CARD_API_PORT", DEFAULT_API)))
    parser.add_argument("--app", type=int, default=int(os.environ.get("CARD_APP_PORT", DEFAULT_APP)))
    parser.add_argument("--host", default=os.environ.get("CARD_GATEWAY_HOST", "::"))
    args = parser.parse_args()

    server = make(args.port, api_port=args.api, app_port=args.app, host=args.host)
    print(f"gateway on http://{args.host}:{args.port}  ->  /api/* : {args.api},  everything else : {args.app}")
    print(f"point the tunnel at it:  cloudflared tunnel --url http://localhost:{args.port}")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

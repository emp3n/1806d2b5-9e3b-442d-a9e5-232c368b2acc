"""The feed's address, and the one process that puts it on the page's origin.

The bug these tests exist for: the card was handed
``http://127.0.0.1:8598/api/state``. That address means "this machine" only to
a browser running on this machine — a phone opening the tunneled page resolved
it to the phone, the fetch was refused, and the card kept its first-paint
numbers while looking perfectly alive. ``feed_url`` is the decision, ``gateway``
is what makes the page's own origin a real answer.
"""

from __future__ import annotations

import json
import socket
import socketserver
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pathlib import Path

import gateway
import server

ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------ the address
@pytest.mark.parametrize("page, explicit, expected", [
    # no context at all (AppTest, a script run): the feed is local
    (None, None, "http://127.0.0.1:8598/api/state"),
    # opened on the server itself: loopback still means the feed's own port
    ("http://localhost:8599/?theme=dark", None, "http://127.0.0.1:8598/api/state"),
    ("http://127.0.0.1:8599/", None, "http://127.0.0.1:8598/api/state"),
    # opened from anywhere else: only the page's own origin can carry it
    ("https://streamlit.idealis.cc/", None, "https://streamlit.idealis.cc/api/state"),
    ("https://streamlit.idealis.cc/?theme=dark", None, "https://streamlit.idealis.cc/api/state"),
    ("http://192.168.1.7:8600/", None, "http://192.168.1.7:8600/api/state"),
    # an explicit address wins, for a feed exposed under its own hostname
    ("https://streamlit.idealis.cc/", "https://feed.idealis.cc/api/state",
     "https://feed.idealis.cc/api/state"),
    # anything that is not a URL is ignored rather than handed to the browser
    ("http://localhost:8599/", "ftp://nope", "http://127.0.0.1:8598/api/state"),
    ("http://localhost:8599/", "  ", "http://127.0.0.1:8598/api/state"),
])
def test_the_browser_gets_an_address_it_can_actually_reach(page, explicit, expected):
    assert server.feed_url(page, 8598, explicit) == expected


def test_a_remote_page_never_gets_loopback():
    """The whole bug in one assertion."""
    url = server.feed_url("https://streamlit.idealis.cc/", 8598)
    assert "127.0.0.1" not in url and "localhost" not in url


# ------------------------------------------------------------ the gateway
class Upstream:
    """A stand-in for one of the two services behind the gateway."""

    def __init__(self, body: bytes, status: int = 200, kind: str = "application/json"):
        self.seen: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _answer(self, method: str) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                payload = self.rfile.read(length) if length else b""
                record = {"method": method, "path": self.path, "headers": dict(self.headers),
                          "body": payload.decode("latin-1")}
                outer.seen.append(record)
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                self._answer("GET")

            def do_POST(self):  # noqa: N802
                self._answer("POST")

            def log_message(self, *args):  # keep the suite quiet
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02},
                             daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def behind():
    app = Upstream(b'{"from":"app"}')
    api = Upstream(b'{"from":"api"}')
    server_ = gateway.make(0, api_port=api.port, app_port=app.port, host="127.0.0.1")
    threading.Thread(target=server_.serve_forever, kwargs={"poll_interval": 0.02},
                     daemon=True).start()
    yield app, api, server_.server_address[1]
    server_.shutdown()
    server_.server_close()
    app.close()
    api.close()


def call(port: int, request: bytes, wait: float = 0.25) -> bytes:
    """One request on a fresh connection; whatever came back before the quiet."""
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as sock:
        sock.sendall(request)
        sock.settimeout(wait)
        return drain(sock)


def drain(sock: socket.socket) -> bytes:
    chunks = []
    while True:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def read_until(sock: socket.socket, marker: bytes, wait: float = 2.0) -> bytes:
    """Reads until `marker` shows up: two frames can arrive in one packet."""
    buf = b""
    sock.settimeout(wait)
    while marker not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    return buf


def test_api_paths_go_to_the_feed_and_everything_else_to_the_app(behind):
    app, api, port = behind
    assert b'"from":"api"' in call(port, b"GET /api/state HTTP/1.1\r\nHost: x\r\n\r\n")
    assert b'"from":"app"' in call(port, b"GET /?theme=dark HTTP/1.1\r\nHost: x\r\n\r\n")
    assert b'"from":"api"' in call(port, b"GET /api/health HTTP/1.1\r\nHost: x\r\n\r\n")
    # Streamlit's own health lives on /_stcore/health, so bare /health is its
    # business, not the feed's
    assert b'"from":"app"' in call(port, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")
    assert [r["path"] for r in api.seen] == ["/api/state", "/api/health"]
    assert [r["path"] for r in app.seen] == ["/?theme=dark", "/health"]


def test_the_gateway_answers_for_itself(behind):
    app, api, port = behind
    reply = call(port, b"GET /api/gateway HTTP/1.1\r\nHost: x\r\n\r\n")
    assert b"200" in reply.split(b"\r\n")[0]
    assert json.loads(reply.split(b"\r\n\r\n", 1)[1])["gateway"] is True
    assert app.seen == [] and api.seen == []      # nothing was relayed


def test_a_post_keeps_its_body_and_its_action_header(behind):
    app, api, port = behind
    body = b'{"action":"stop"}'
    request = (
        b"POST /api/action HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        b"X-RD-Action: secret\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    )
    call(port, request)
    seen = api.seen[0]
    assert seen["method"] == "POST"
    assert seen["body"] == body.decode()
    assert seen["headers"].get("X-RD-Action") == "secret"


def test_the_streamlit_websocket_gets_through(behind):
    """Streamlit's session is a websocket on /_stcore/stream. The gateway does
    not speak websocket; it copies bytes, which is enough — the handshake and
    the frames both arrive."""
    app, api, port = behind

    class WS(socketserver.BaseRequestHandler):
        def handle(self):
            buf = b""
            while b"\r\n\r\n" not in buf:
                buf += self.request.recv(4096)
            self.request.sendall(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\n\r\n")
            self.request.sendall(b"\x81\x05hello")      # one frame
            while True:                                  # then echo
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                self.request.sendall(b"\x81" + bytes([len(chunk)]) + chunk)

    ws = socketserver.ThreadingTCPServer(("127.0.0.1", 0), WS)
    ws.daemon_threads = True
    threading.Thread(target=ws.serve_forever, kwargs={"poll_interval": 0.02},
                     daemon=True).start()
    server_ = gateway.make(0, api_port=api.port, app_port=ws.server_address[1], host="127.0.0.1")
    threading.Thread(target=server_.serve_forever, kwargs={"poll_interval": 0.02},
                     daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", server_.server_address[1]), timeout=2) as sock:
            sock.sendall(
                b"GET /_stcore/stream HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                b"Connection: Upgrade\r\n\r\n")
            assert b"101 Switching Protocols" in read_until(sock, b"hello")
            sock.sendall(b"ping-from-page")
            assert b"ping-from-page" in read_until(sock, b"ping-from-page")
    finally:
        server_.shutdown()
        server_.server_close()
        ws.shutdown()
        ws.server_close()


def test_a_missing_feed_says_so_in_json(behind):
    app, api, port = behind
    api.close()                      # the feed is down, the page is not
    reply = call(port, b"GET /api/state HTTP/1.1\r\nHost: x\r\n\r\n")
    assert b"502" in reply.split(b"\r\n")[0]
    assert b"the feed is not running" in reply
    # and it stays CORS-readable, because the page is cross-origin to a tunnel
    assert b"Access-Control-Allow-Origin: *" in reply


def test_a_missing_app_says_which_port(behind):
    app, api, port = behind
    app.close()
    reply = call(port, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    assert b"502" in reply.split(b"\r\n")[0]
    assert b"the app is not running" in reply


def test_two_requests_share_one_connection(behind):
    app, api, port = behind
    request = b"GET /api/state HTTP/1.1\r\nHost: x\r\n\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.sendall(request)
        first = read_until(sock, b'"from":"api"')
        sock.sendall(request)
        second = read_until(sock, b'"from":"api"')
    assert b"200" in first and b"200" in second
    assert len(api.seen) == 2


def test_probe_recognises_a_gateway_and_a_closed_port(behind):
    _app, _api, port = behind
    assert gateway.probe(port)["gateway"] is True
    assert gateway.probe(port + 1, timeout=0.2) is None


def test_the_gateway_does_not_need_the_operator_to_start_it_by_hand():
    """The module is runnable: one command is the whole deployment."""
    assert callable(gateway.main)
    assert gateway.DEFAULT_PORT == 8600 and gateway.DEFAULT_APP == 8599


def test_the_app_no_longer_hardcodes_the_servers_loopback():
    """The one line that broke the phone: api_url = f"http://127.0.0.1:{port}…"."""
    source = (ROOT / "app.py").read_text()
    assert "feed_url(" in source
    assert 'http://127.0.0.1:{port}/api/state' not in source
    # and the two ways to point it somewhere else really reach it
    assert "CARD_API_URL" in source
    assert 'params.get("api"' in source


# --- the launcher: two processes, one address -------------------------------

def test_the_launcher_puts_the_gateway_on_the_public_port():
    """The tunnel keeps its target; the gateway takes that port and Streamlit
    moves behind it. That is the whole deployment."""
    script = (ROOT / "tools" / "serve.sh").read_text()
    assert 'PUBLIC=${CARD_PUBLIC_PORT:-8599}' in script
    assert 'APP=$((PUBLIC + 1))' in script
    assert '--port "$PUBLIC" --api "$API" --app "$APP"' in script
    # the app has to serve its feed on the same port the gateway relays to
    assert 'CARD_API_PORT="$API"' in script
    subprocess.run(["sh", "-n", str(ROOT / "tools" / "serve.sh")], check=True)


def test_the_launcher_takes_its_defaults_from_the_server():
    """A default that drifts from server.DEFAULT_PORT is a 502 nobody expects."""
    from server import DEFAULT_PORT

    script = (ROOT / "tools" / "serve.sh").read_text()
    assert f"API=${{CARD_API_PORT:-{DEFAULT_PORT}}}" in script
    assert (ROOT / "tools" / "serve.sh").stat().st_mode & 0o111


# --- the power button when the page itself rides the tunnel -----------------

def test_public_page_is_told_it_travels_over_the_tunnel():
    from card import tunnel_borne

    assert tunnel_borne("https://streamlit.idealis.cc/api/state") is True
    assert tunnel_borne("http://127.0.0.1:8598/api/state") is False
    assert tunnel_borne("http://localhost:8600/api/state") is False
    assert tunnel_borne("http://card.localhost:8600/api/state") is False
    # a LAN page is direct: stopping the connector does not cut it
    assert tunnel_borne("http://192.168.1.7:8600/api/state") is False
    assert tunnel_borne("http://10.0.0.5:8600/api/state") is False
    assert tunnel_borne("http://172.20.0.2:8600/api/state") is False
    # 172.32 is public, and so is anything that is not a dotted quad
    assert tunnel_borne("http://172.32.0.2:8600/api/state") is True
    assert tunnel_borne(None) is False and tunnel_borne("") is False


# --- the origin the tunnel actually dials -----------------------------------

def test_the_gateway_answers_on_both_loopback_families():
    """cloudflared resolves localhost to ::1 first. Bound to 0.0.0.0 only, the
    gateway answered nothing to that first attempt: 52 "connection refused" in
    the tunnel's log, and a page that looked frozen for a reason that was never
    the card's."""
    if not socket.has_ipv6:
        pytest.skip("no IPv6 on this host")
    from gateway import make

    server = make(0, api_port=1, app_port=1, host="::")
    port = server.server_address[1]
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for family, host in ((socket.AF_INET6, "::1"), (socket.AF_INET, "127.0.0.1")):
            with socket.create_connection((host, port), timeout=2) as sock:
                sock.sendall(b"GET /api/gateway HTTP/1.1\r\nHost: localhost\r\n\r\n")
                reply = sock.recv(4096)
            assert b'"gateway": true' in reply, (family, reply[:120])
    finally:
        server.shutdown()
        server.server_close()


def test_the_launcher_waits_for_the_mark_not_just_a_reply():
    """Streamlit answers 200 with its own HTML to every unknown path, so a
    readiness check that accepts any reply declares a non-gateway ready."""
    script = (ROOT / "tools" / "serve.sh").read_text()
    assert '\'"gateway": true\'' in script
    assert 'if [ "$name" = gateway ] && healthy "$port" "$path" \'"gateway": true\'' in script

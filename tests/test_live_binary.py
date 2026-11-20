"""The real thing: run cloudflared, read it back through the card's own code.

These tests start the downloaded binary with a tunnel token, wait for its
metrics endpoint, and then assert that every number the card shows can be taken
from the process itself. They skip when the binary or the token file is absent,
so the suite stays honest on a machine without them.

    CLOUDFLARED_BIN=/tmp/user/1000/opencode/cloudflared \\
    CLOUDFLARED_TOKEN_FILE=/tmp/user/1000/opencode/cf-token \\
    python3 -m pytest tests -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

import pytest

import live
import server

BIN = os.environ.get("CLOUDFLARED_BIN") or shutil.which("cloudflared") or "/tmp/user/1000/opencode/cloudflared"
TOKEN_FILE = os.environ.get("CLOUDFLARED_TOKEN_FILE") or "/tmp/user/1000/opencode/cf-token"

pytestmark = pytest.mark.integration


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(url: str, seconds: float = 25.0) -> str:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:  # /ready answers 503 with a body
            return error.read().decode()
        except OSError:
            time.sleep(0.3)
    raise AssertionError(f"{url} never answered")


def start_connector(tmp_path, name: str = "tunnel"):
    """Launch the connector the way the panel would: token in a file, not argv."""
    port = free_port()
    log = tmp_path / f"{name}.log"
    pidfile = tmp_path / f"{name}.pid"
    process = subprocess.Popen(
        [
            BIN, "tunnel",
            "--metrics", f"127.0.0.1:{port}",
            "--logfile", str(log),
            "--pidfile", str(pidfile),
            "--loglevel", "info",
            "run", "--token-file", TOKEN_FILE,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_for(f"http://127.0.0.1:{port}/metrics")
    return process, port, log, pidfile


@pytest.fixture
def real_binary():
    """The three tests that call ``start_connector`` directly need the same
    guard the module fixture has: without a token file the connector exits
    before its metrics port opens, and the failure looks like a broken card."""
    if not (os.path.isfile(BIN) and os.access(BIN, os.X_OK)):
        pytest.skip(f"no cloudflared binary at {BIN}")
    if not os.path.isfile(TOKEN_FILE):
        pytest.skip(f"no token file at {TOKEN_FILE}")


@pytest.fixture(scope="module")
def connector(tmp_path_factory):
    if not (os.path.isfile(BIN) and os.access(BIN, os.X_OK)):
        pytest.skip(f"no cloudflared binary at {BIN}")
    if not os.path.isfile(TOKEN_FILE):
        pytest.skip(f"no token file at {TOKEN_FILE}")
    tmp_path = tmp_path_factory.mktemp("connector")
    process, port, log, pidfile = start_connector(tmp_path)
    yield {"proc": process, "port": port, "log": log, "pidfile": pidfile}
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_binary_reports_the_version_the_card_shows(connector):
    out = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=10).stdout
    match = re.search(r"version (\S+)", out)
    assert match, out

    collector = live.Collector(metrics_url=f"http://127.0.0.1:{connector['port']}")
    collector.sample(pid=connector["proc"].pid)
    assert collector.state()["version"] == match.group(1)


def test_the_card_finds_the_process_by_scanning_proc(connector):
    pids = [pid for pid, _ in live.scan_pids()]
    assert connector["proc"].pid in pids


def test_exe_path_and_uptime_come_from_the_kernel(connector):
    pid = connector["proc"].pid
    assert os.path.realpath(live.proc_exe(pid)) == os.path.realpath(BIN)
    first = live.proc_age(pid)
    time.sleep(2.0)
    second = live.proc_age(pid)
    assert first is not None and second is not None
    assert 1.5 <= second - first <= 3.5


def test_memory_and_threads_are_read_not_guessed(connector):
    pid = connector["proc"].pid
    rss = live.proc_rss(pid)
    assert rss is not None and rss > 5 * 1024**2
    stat = live.proc_stat(pid)
    assert stat["threads"] >= 2
    assert stat["comm"] == "cloudflared"


def test_two_samples_make_a_real_cpu_rate(connector):
    collector = live.Collector(metrics_url=f"http://127.0.0.1:{connector['port']}")
    collector.sample(pid=connector["proc"].pid)
    time.sleep(1.0)
    collector.sample(pid=connector["proc"].pid)
    state = collector.state()
    assert 0.0 <= state["cpu"] <= 100.0 * (os.cpu_count() or 1)
    assert state["cpu_text"].endswith("%")
    assert len(state["cpu_series"]) == live.SAMPLES
    assert state["mem"] > 5.0  # MB, a Go binary does not live in less


def test_connections_and_edges_come_from_ready_and_the_log(connector):
    collector = live.Collector(metrics_url=f"http://127.0.0.1:{connector['port']}")
    collector.sample(pid=connector["proc"].pid)
    state = collector.state()
    assert 0 <= state["live"] <= 4
    assert re.fullmatch(r"[0-9a-f-]{36}|—", state["connector"]), state["connector"]
    # the connector's log names the colos it registered with; the card shows those
    assert state["edge_names"] == "—" or re.fullmatch(
        r"[a-z]{2,4}\d{1,2}(, [a-z]{2,4}\d{1,2})*|\d{1,3}(\.\d{1,3}){3}( \+\d+)?", state["edge_names"]
    ), state["edge_names"]


def test_origin_ip_is_the_egress_address_the_edge_sees(connector):
    collector = live.Collector(metrics_url=f"http://127.0.0.1:{connector['port']}")
    collector.sample(pid=connector["proc"].pid)
    state = collector.state()
    assert re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", state["edge_ip"]), state["edge_ip"]


def test_the_token_stays_out_of_the_command_line_and_the_payload(connector, tmp_path):
    pid = connector["proc"].pid
    args = live.proc_cmdline(pid)
    assert "--token-file" in args
    assert not any(arg.startswith("eyJ") for arg in args), "the secret is in argv"

    token = open(TOKEN_FILE, encoding="utf-8").read().strip()
    collector = live.Collector(metrics_url=f"http://127.0.0.1:{connector['port']}")
    collector.sample(pid=pid)
    state = collector.state()
    assert token not in json.dumps(state)
    assert token not in json.dumps(server.to_payload(state))


def test_the_api_serves_the_running_connector(connector):
    sampler = server.Sampler(interval=3600)
    sampler.collector.sample(pid=connector["proc"].pid)
    with sampler._lock:
        sampler._payload = server.to_payload(sampler.collector.state())
    httpd = server.serve(sampler, port=0)
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/state")
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
            assert response.headers["Access-Control-Allow-Origin"] == "*"
        assert payload["found"] is True
        assert payload["pid"] == connector["proc"].pid
        assert payload["status_text"].endswith("of 4 connections live")
        assert payload["path"].endswith("cloudflared")
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_buttons_stop_and_start_the_real_connector(tmp_path, monkeypatch, real_binary):
    """The panel's power button, end to end: SIGTERM, wait, relaunch the
    recorded command line, and the new process registers again.

    This host also runs the panel's own connector, so discovery is pinned off:
    otherwise the sampler would adopt *that* one the moment ours stops and the
    start would (correctly) refuse as "already running".
    """
    monkeypatch.setattr(live, "find_pid", lambda *args, **kwargs: None)
    process, port, log, _ = start_connector(tmp_path, name="ctl")
    sampler = server.Sampler(interval=3600)
    # read this connector's own metrics port, not whatever is on the default one
    sampler.collector.metrics_url = f"http://127.0.0.1:{port}"
    sampler.collector.sample(pid=process.pid)
    started_pid = None
    try:
        assert sampler.collector.launch and "--token-file" in sampler.collector.launch
        stopped = sampler.supervisor.perform("stop")
        assert stopped["ok"] is True and stopped["pid"] == process.pid
        assert not server.alive(process.pid)
        assert process.wait(timeout=10) is not None  # SIGTERM was enough

        sampler.collector.launch = [
            BIN, "tunnel",
            "--metrics", f"127.0.0.1:{port}",
            "--logfile", str(log),
            "--loglevel", "info",
            "run", "--token-file", TOKEN_FILE,
        ]
        started = sampler.supervisor.perform("start")
        assert started["ok"] is True, started
        started_pid = started["pid"]
        assert server.alive(started_pid)

        # it comes back all the way: the collector reads the new pid, metrics
        # answer, and the version is the binary's own (not the "unknown" a
        # process reports in its first second, before build_info is served)
        wait_for(f"http://127.0.0.1:{port}/metrics")
        ready = live.parse_ready(wait_for(f"http://127.0.0.1:{port}/ready"))
        assert 0 <= ready["ready"] <= 4
        deadline = time.monotonic() + 15
        state = sampler.collector.state()
        while state["version"] == "unknown" and time.monotonic() < deadline:
            time.sleep(0.5)
            sampler.collector.sample()
            state = sampler.collector.state()
        assert state["found"] is True
        assert state["pid"] == started_pid
        assert state["version"] == live.parse_metrics(
            live._http(f"http://127.0.0.1:{port}/metrics") or ""
        )["version"]
        assert state["version"] != "unknown"
    finally:
        if started_pid and server.alive(started_pid):
            os.kill(started_pid, 15)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_restart_in_one_step_replaces_the_pid(tmp_path, monkeypatch, real_binary):
    monkeypatch.setattr(live, "find_pid", lambda *args, **kwargs: None)
    process, port, log, _ = start_connector(tmp_path, name="rst")
    sampler = server.Sampler(interval=3600)
    sampler.collector.sample(pid=process.pid)
    new_pid = None
    try:
        sampler.collector.launch = [
            BIN, "tunnel",
            "--metrics", f"127.0.0.1:{port}",
            "--logfile", str(log),
            "--loglevel", "info",
            "run", "--token-file", TOKEN_FILE,
        ]
        result = sampler.supervisor.perform("restart")
        assert result["ok"] is True, result
        new_pid = result["pid"]
        assert result["stopped_pid"] == process.pid
        assert new_pid != process.pid and server.alive(new_pid)
        assert not server.alive(process.pid)
    finally:
        if new_pid and server.alive(new_pid):
            os.kill(new_pid, 15)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_a_dead_process_is_reported_as_gone(tmp_path, real_binary):
    process, port, _, _ = start_connector(tmp_path, name="short")
    try:
        collector = live.Collector(metrics_url=f"http://127.0.0.1:{port}")
        assert collector.sample(pid=process.pid)["found"] is True
        assert collector.state()["pid"] == process.pid
    finally:
        process.terminate()
        process.wait(timeout=10)
    time.sleep(0.5)
    assert live.proc_stat(process.pid) is None
    gone = live.Collector(metrics_url=f"http://127.0.0.1:{port}")
    assert gone.sample(pid=process.pid)["found"] is False
    assert gone.state() == {"found": False}

"""The tunnel this app owns: where the token comes from, and what "running" means.

These tests are about the two failures the operator actually hit:

* the page said the connector was already running when nothing was (a zombie,
  a stale pidfile or a survivor whose name no longer answered);
* the connector could not be restarted, because the only thing that could start
  it again was a command line replayed from memory.

So: a Tunnel is only ``ok`` after ``/ready`` reports a registered connection, a
zombie is not alive, and a restart starts the new connector *before* it touches
the old one. No network and no real connector here — the binary, the clock and
the probes are all fakes.
"""

from __future__ import annotations

import io
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

import bootstrap

TOKEN = "eyJhIjoiMDAwIiwiayI6InRlc3QifQ"


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class Proc:
    """Just enough of subprocess.Popen for the start paths."""

    def __init__(self, code=None, pid=4242):
        self.code = code
        self.returncode = code
        self.pid = pid

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15
        self.returncode = -15


# --------------------------------------------------------------- the binary
def test_asset_and_release_url_are_explicit():
    url = bootstrap.download_url()
    assert url.startswith("https://github.com/cloudflare/cloudflared/releases/download/")
    assert url.endswith(f"/{bootstrap.asset_name()}")
    assert bootstrap.VERSION in url


def test_download_lands_in_the_temp_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path / "rt"))
    called = []

    def fake_open(url, timeout):
        called.append((url, timeout))
        return Reply(b"x" * 1_100_000)

    binary = bootstrap.download(opener=fake_open)
    assert binary == bootstrap.runtime_dir() / "cloudflared"
    assert binary.stat().st_size == 1_100_000
    assert binary.stat().st_mode & 0o111
    assert called[0][1] == 25
    assert bootstrap.download(opener=fake_open) == binary
    assert len(called) == 1


# --------------------------------------------------------------- the token
def test_the_token_comes_from_the_secret_before_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    (tmp_path / "cf-token").write_text("from-file\n")
    monkeypatch.setenv("CLOUDFLARE_TUNNEL_TOKEN", "from-env")

    # nothing in secrets: the environment is the next stop
    token, source = bootstrap.tunnel_token({})
    assert token == "from-env" and "CLOUDFLARE_TUNNEL_TOKEN" in source

    # a secret wins over the environment
    token, source = bootstrap.tunnel_token({"CLOUDFLARE_TUNNEL_TOKEN": "from-secret"})
    assert token == "from-secret" and "secret" in source

    # neither: the file on disk
    monkeypatch.delenv("CLOUDFLARE_TUNNEL_TOKEN")
    token, source = bootstrap.tunnel_token({})
    assert token == "from-file" and "cf-token" in source


def test_no_token_is_named_not_invented(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    monkeypatch.delenv("CLOUDFLARE_TUNNEL_TOKEN", raising=False)
    monkeypatch.delenv("CARD_TUNNEL_TOKEN", raising=False)
    token, reason = bootstrap.tunnel_token({})
    assert token is None
    assert "CLOUDFLARE_TUNNEL_TOKEN" in reason


def test_the_token_file_is_private_and_rewritten(tmp_path):
    path = bootstrap.write_token_file("first", tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text().strip() == "first"
    bootstrap.write_token_file("second", tmp_path)
    assert path.read_text().strip() == "second"


def test_the_hostname_defaults_to_the_route_the_operator_made():
    assert bootstrap.public_hostname({}) == "streamlit.idealis.cc"
    assert bootstrap.public_hostname(
        {"CLOUDFLARE_TUNNEL_HOSTNAME": "https://Example.test/"}
    ) == "Example.test"


# --------------------------------------------------------------- the connector
def test_the_named_tunnel_never_puts_the_token_in_argv(tmp_path):
    seen = {}

    class Fake(Proc):
        running = True

        def poll(self):
            return None

    def launch(command, **kwargs):
        seen["command"] = command
        (tmp_path / "cf.log").write_text('{"message":"registered"}\n')
        return Fake()

    original_ready = bootstrap.ready_connections
    bootstrap.ready_connections = lambda port, timeout=1.5: 1
    try:
        tunnel = bootstrap.start_named_tunnel(
            pathlib.Path("/bin/cloudflared"), 8501, TOKEN, directory=tmp_path,
            wait_s=0.5, runner=launch, metrics_port=20299)
    finally:
        bootstrap.ready_connections = original_ready

    command = seen["command"]
    assert tunnel.ok, tunnel.detail
    assert "run" in command and "--token-file" in command
    assert not any(TOKEN in part for part in command), "the secret is in argv"
    assert command[command.index("--metrics") + 1] == "127.0.0.1:20299"
    token_path = pathlib.Path(command[command.index("--token-file") + 1])
    assert token_path.read_text().strip() == TOKEN
    assert token_path.stat().st_mode & 0o777 == 0o600
    assert tunnel.mode == "named"
    assert tunnel.public_url == "https://streamlit.idealis.cc"
    assert tunnel.state_url == "https://streamlit.idealis.cc/app/static/state.json"


def test_a_rejected_token_is_named_as_such(tmp_path, monkeypatch):
    def launch(command, **kwargs):
        (tmp_path / "cf.log").write_text(
            '{"level":"error","error":"Unauthorized: Invalid tunnel secret",'
            '"message":"Register tunnel error from server side"}\n')
        return Proc(code=1)

    tunnel = bootstrap.start_named_tunnel(
        pathlib.Path("/bin/cloudflared"), 8501, TOKEN, directory=tmp_path,
        wait_s=1.0, runner=launch, metrics_port=20298)
    assert tunnel.ok is False
    assert "Invalid tunnel secret" in tunnel.detail
    assert "another tunnel" in tunnel.detail


def test_a_connector_that_never_registers_is_not_called_running(tmp_path):
    """It is up; it is not a tunnel. Reporting it as one is the whole bug."""
    def launch(command, **kwargs):
        return Proc()          # alive forever, never registers

    tunnel = bootstrap.start_named_tunnel(
        pathlib.Path("/bin/cloudflared"), 8501, TOKEN, directory=tmp_path,
        wait_s=0.4, runner=launch, metrics_port=20297)
    assert tunnel.ok is False
    assert "did not register" in tunnel.detail


def test_a_second_connector_takes_a_free_metrics_port(tmp_path):
    """20241 is still held by the connector that survived the last script."""
    def launch(command, **kwargs):
        (tmp_path / "cf.log").write_text('{"message":"https://p.trycloudflare.com"}\n')
        return Proc(pid=11)

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    taken = holder.getsockname()[1]
    try:
        assert not bootstrap.port_is_free(taken)
        port = bootstrap.pick_metrics_port(taken)
        assert port != taken and bootstrap.port_is_free(port)
    finally:
        holder.close()


def test_quick_tunnel_takes_the_metrics_port_it_is_given(tmp_path):
    seen = {}

    def launch(command, **kwargs):
        seen["command"] = command
        (tmp_path / "cf.log").write_text(
            '{"message":"https://port-check.trycloudflare.com"}\n')
        return Proc(pid=11)

    original_ready = bootstrap.ready_connections
    bootstrap.ready_connections = lambda port, timeout=1.5: 1
    try:
        tunnel = bootstrap.start_quick_tunnel(
            pathlib.Path("/bin/cloudflared"), 8501, directory=tmp_path,
            wait_s=1.0, runner=launch, metrics_port=20299)
    finally:
        bootstrap.ready_connections = original_ready
    command = seen["command"]
    assert command[command.index("--metrics") + 1] == "127.0.0.1:20299"
    assert tunnel.ok and tunnel.mode == "quick"
    assert tunnel.state_url == "https://port-check.trycloudflare.com/app/static/state.json"


# --------------------------------------------------------------- alive means alive
def test_a_zombie_is_not_a_running_connector():
    """An unreaped child lingers in /proc. The card used to call that "running"
    and offered to stop a process that no longer existed."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        time.sleep(0.3)
        assert pathlib.Path(f"/proc/{child.pid}").exists(), "no zombie to test"
        assert bootstrap._proc_alive(child.pid) is False
    finally:
        child.poll()
        child.wait(timeout=5)


# --------------------------------------------------------------- status & adopt
def _fake_runtime(tmp_path, pid=4242, metrics=20241, mode="named",
                  ready=True, public="https://streamlit.idealis.cc"):
    """A pidfile, a cmdline and a log, as a surviving connector leaves them."""
    binary = tmp_path / "cloudflared"
    binary.write_text("")
    binary.chmod(0o755)
    if mode == "named":
        args = [str(binary), "tunnel", "--metrics", f"127.0.0.1:{metrics}",
                "--pidfile", str(tmp_path / "cf.pid"), "run",
                "--token-file", str(tmp_path / "cf-token")]
    else:
        args = [str(binary), "tunnel", "--url", "http://127.0.0.1:8501",
                "--metrics", f"127.0.0.1:{metrics}",
                "--logfile", str(tmp_path / "cf.log"),
                "--pidfile", str(tmp_path / "cf.pid")]
    (tmp_path / "cf.pid").write_text(str(pid))
    (tmp_path / "cf.log").write_text(
        '{"message":"https://survivor.trycloudflare.com"}\n')
    return args


def _pin(monkeypatch, args, alive=True, ready=1, age=30.0, public=True):
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: alive)
    monkeypatch.setattr(bootstrap, "_cmdline", lambda pid: args)
    monkeypatch.setattr(bootstrap, "metrics_answers", lambda port, timeout=1.5: True)
    monkeypatch.setattr(bootstrap, "ready_connections", lambda port, timeout=1.5: ready)
    monkeypatch.setattr(bootstrap, "proc_age", lambda pid: age)
    monkeypatch.setattr(bootstrap, "public_answers", lambda host, timeout=5.0: public)
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda directory=None: [int(
        pathlib.Path(args[0]).parent.joinpath("cf.pid").read_text())])


def test_status_reads_a_named_connector_back(tmp_path, monkeypatch):
    args = _fake_runtime(tmp_path)
    _pin(monkeypatch, args)
    status = bootstrap.tunnel_status(8501, tmp_path)
    assert status["ok"] is True
    assert status["mode"] == "named"
    assert status["pid"] == 4242 and status["ready"] == 1
    assert status["public"] == "https://streamlit.idealis.cc"


def test_status_says_not_ok_when_the_edge_has_no_connection(tmp_path, monkeypatch):
    args = _fake_runtime(tmp_path)
    _pin(monkeypatch, args, ready=0)
    status = bootstrap.tunnel_status(8501, tmp_path)
    assert status["ok"] is False
    assert "not registered" in status["detail"]


def test_status_without_a_connector_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda directory=None: [])
    status = bootstrap.tunnel_status(8501, tmp_path)
    assert status["ok"] is False and status["mode"] == "none"
    assert "no connector" in status["detail"]


def test_adopt_reuses_the_surviving_named_connector(tmp_path, monkeypatch):
    args = _fake_runtime(tmp_path)
    _pin(monkeypatch, args)
    tunnel = bootstrap.adopt_running_tunnel(8501, directory=tmp_path)
    assert tunnel is not None and tunnel.ok
    assert tunnel.pid == 4242 and tunnel.mode == "named"
    assert tunnel.public_url == "https://streamlit.idealis.cc"
    assert "reused" in tunnel.detail


def test_adopt_refuses_a_connector_that_never_registered(tmp_path, monkeypatch):
    args = _fake_runtime(tmp_path)
    _pin(monkeypatch, args, ready=0)
    assert bootstrap.adopt_running_tunnel(8501, directory=tmp_path) is None


def test_a_young_connector_is_adopted_before_the_hostname_answers(tmp_path, monkeypatch):
    """A fresh connector 530s at the edge for a few seconds. Killing it and
    starting another is how the page ended up on a name that never answered."""
    args = _fake_runtime(tmp_path)
    _pin(monkeypatch, args, age=5.0, public=False)
    assert bootstrap.adopt_running_tunnel(8501, directory=tmp_path) is not None


def test_an_old_connector_that_never_answers_is_replaced(tmp_path, monkeypatch):
    args = _fake_runtime(tmp_path)
    _pin(monkeypatch, args, age=bootstrap.STUCK_AFTER + 10, public=False)
    assert bootstrap.adopt_running_tunnel(8501, directory=tmp_path) is None


def test_a_quick_connector_is_recognised_by_its_url_flag(tmp_path, monkeypatch):
    args = _fake_runtime(tmp_path, mode="quick")
    _pin(monkeypatch, args)
    tunnel = bootstrap.adopt_running_tunnel(8501, directory=tmp_path)
    assert tunnel is not None and tunnel.mode == "quick"
    assert tunnel.public_url == "https://survivor.trycloudflare.com"


# --------------------------------------------------------------- stop & restart
def test_stop_clears_the_pidfile_it_actually_stopped(tmp_path):
    (tmp_path / "cf.pid").write_text("4242")
    killed = []

    original = bootstrap._proc_alive
    bootstrap._proc_alive = lambda pid: False
    try:
        bootstrap.stop_tunnel(4242, tmp_path)
    finally:
        bootstrap._proc_alive = original
    assert not (tmp_path / "cf.pid").exists()


def test_stop_cannot_erase_a_living_keepers_pidfile(tmp_path):
    """Retiring an extra connector must leave the keeper discoverable."""
    (tmp_path / "cf.pid").write_text("999")
    original = bootstrap._proc_alive
    bootstrap._proc_alive = lambda pid: pid == 999
    try:
        bootstrap.stop_tunnel(4242, tmp_path)
    finally:
        bootstrap._proc_alive = original
    assert (tmp_path / "cf.pid").read_text() == "999"


def test_restart_starts_the_new_connector_before_stopping_the_old_one(monkeypatch):
    """The order is the promise: a failed start must leave the old one serving."""
    calls = []
    new = bootstrap.Tunnel(True, public_url="https://streamlit.idealis.cc",
                           hostname="streamlit.idealis.cc", pid=8000, mode="named")
    monkeypatch.setattr(bootstrap, "_cached", new)
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda: (TOKEN, "secret TEST"))
    monkeypatch.setattr(bootstrap, "download", lambda: pathlib.Path("/bin/cloudflared"))
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda port, directory=None: {
        "pid": 7000, "extra": [], "ready": 1, "public": "https://streamlit.idealis.cc"})

    def start(*args, **kwargs):
        calls.append("start")
        return new

    monkeypatch.setattr(bootstrap, "start_named_tunnel", start)
    monkeypatch.setattr(bootstrap, "stop_tunnel", lambda pids, directory=None: (
        calls.append(("stop", list(pids))) or {"ok": True, "stopped": list(pids)}))
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel", lambda keep=None, directory=None: "")

    tunnel = bootstrap.restart_tunnel(8501)
    assert tunnel is new and tunnel.ok
    assert calls == ["start", ("stop", [7000])], calls


def test_a_failed_restart_leaves_the_old_connector_running(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda: (TOKEN, "secret TEST"))
    monkeypatch.setattr(bootstrap, "download", lambda: pathlib.Path("/bin/cloudflared"))
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda port, directory=None: {
        "pid": 7000, "extra": [], "ready": 1, "public": "https://streamlit.idealis.cc"})
    monkeypatch.setattr(bootstrap, "start_named_tunnel",
                        lambda *a, **k: bootstrap.Tunnel(
                            False, mode="named", detail="did not register"))
    monkeypatch.setattr(bootstrap, "stop_tunnel",
                        lambda pids, directory=None: calls.append("stop"))
    tunnel = bootstrap.restart_tunnel(8501)
    assert tunnel.ok is False
    assert calls == [], "nothing may be stopped when the replacement failed"


# --------------------------------------------------------------- intent
def test_the_intent_defaults_to_on(tmp_path):
    assert bootstrap.desired_state(tmp_path) == "on"


def test_the_intent_survives_a_process(tmp_path):
    bootstrap.set_desired("off", tmp_path)
    assert (tmp_path / "tunnel.intent").read_text().strip() == "off"
    assert bootstrap.desired_state(tmp_path) == "off"
    bootstrap.set_desired("on", tmp_path)
    assert bootstrap.desired_state(tmp_path) == "on"
    with pytest.raises(ValueError):
        bootstrap.set_desired("maybe", tmp_path)


def test_a_stopped_tunnel_is_not_restarted_by_a_rerun(tmp_path, monkeypatch):
    """The bug the operator reported: press stop, and the connector is back on
    the next rerun because ``ensure_tunnel`` runs on every script run."""
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "_cached", None)
    bootstrap.set_desired("off")
    started = []
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda directory=None: [])
    monkeypatch.setattr(bootstrap, "start_named_tunnel",
                        lambda *a, **k: started.append("named"))
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: started.append("quick"))
    monkeypatch.setattr(bootstrap, "download", lambda: started.append("download"))
    tunnel = bootstrap.ensure_tunnel(8501)
    assert tunnel.ok is False
    assert "stopped by the operator" in tunnel.detail
    assert started == [], "a rerun must not undo the stop"


def test_an_explicit_start_overrides_the_remembered_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "_cached", None)
    bootstrap.set_desired("off")
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda directory=None: [])
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel", lambda keep=None, directory=None: "")
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda: (TOKEN, "secret TEST"))
    monkeypatch.setattr(bootstrap, "download", lambda: pathlib.Path("/bin/cloudflared"))
    monkeypatch.setattr(bootstrap, "start_named_tunnel",
                        lambda *a, **k: bootstrap.Tunnel(True, pid=31, mode="named"))
    tunnel = bootstrap.ensure_tunnel(8501, force=True)
    assert tunnel.ok and tunnel.pid == 31


# --------------------------------------------------------------- ensure
def test_ensure_tunnel_adopts_before_it_starts_anything(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "_cached", None)
    monkeypatch.setattr(bootstrap, "adopt_running_tunnel",
                        lambda port, verified=True: bootstrap.Tunnel(
                            True, public_url="https://streamlit.idealis.cc",
                            hostname="streamlit.idealis.cc", pid=9, mode="named"))
    started = []
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel",
                        lambda keep=None, directory=None: "")
    monkeypatch.setattr(bootstrap, "start_named_tunnel",
                        lambda *a, **k: started.append("named"))
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: started.append("quick"))
    monkeypatch.setattr(bootstrap, "download", lambda: started.append("download"))
    tunnel = bootstrap.ensure_tunnel(8501)
    assert tunnel.ok and tunnel.pid == 9
    assert started == [], "an adoptable connector must not be replaced"


def test_ensure_tunnel_uses_the_token_when_there_is_one(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "_cached", None)
    monkeypatch.setattr(bootstrap, "adopt_running_tunnel", lambda port, verified=True: None)
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel", lambda keep=None, directory=None: "")
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda: (TOKEN, "secret TEST"))
    monkeypatch.setattr(bootstrap, "download", lambda: pathlib.Path("/bin/cloudflared"))
    seen = {}

    def named(binary, port, token, **kwargs):
        seen["named"] = (port, token)
        return bootstrap.Tunnel(True, public_url="https://streamlit.idealis.cc", pid=5)

    monkeypatch.setattr(bootstrap, "start_named_tunnel", named)
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("quick")))
    tunnel = bootstrap.ensure_tunnel(8501)
    assert tunnel.ok and seen["named"] == (8501, TOKEN)


def test_ensure_tunnel_falls_back_to_a_quick_tunnel_without_a_token(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "_cached", None)
    monkeypatch.setattr(bootstrap, "adopt_running_tunnel", lambda port, verified=True: None)
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel", lambda keep=None, directory=None: "")
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda: (None, "no tunnel token"))
    monkeypatch.setattr(bootstrap, "download", lambda: pathlib.Path("/bin/cloudflared"))
    monkeypatch.setattr(bootstrap, "start_named_tunnel",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("named")))
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: bootstrap.Tunnel(True, mode="quick", pid=6))
    tunnel = bootstrap.ensure_tunnel(8501)
    assert tunnel.ok and tunnel.mode == "quick"


def test_ensure_tunnel_names_a_download_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    monkeypatch.setattr(bootstrap, "_cached", None)
    monkeypatch.setattr(bootstrap, "adopt_running_tunnel", lambda port, verified=True: None)
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel", lambda keep=None, directory=None: "")

    def boom():
        raise OSError("no route to GitHub")

    monkeypatch.setattr(bootstrap, "download", boom)
    tunnel = bootstrap.ensure_tunnel(8501)
    assert tunnel.ok is False
    assert "no route to GitHub" in tunnel.message

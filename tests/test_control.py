"""The power buttons: does the panel stop and start the process it is reading.

The unit tests here run against a stand-in process (``python3 -c 'sleep'``), so
they can prove the mechanics — SIGTERM, the wait, the relaunch from the recorded
argv, the secret moved back into a file — without needing a tunnel. The real
binary is exercised in ``test_live_binary.py``.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import live
import server


def stand_in() -> subprocess.Popen:
    """A process with the same shape as the connector: long-lived, killable."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class FakeSampler:
    """A sampler around a real pid, without the /proc of a real connector."""

    def __init__(self, pid: int, launch: list[str]) -> None:
        self.collector = live.Collector(metrics_url="")
        self.collector.pid = pid
        self.collector.launch = launch
        self.supervisor = server.Supervisor(self)
        self.refreshed = 0
        self.started_at = time.time()

    def refresh(self):
        self.refreshed += 1
        return {}

    def snapshot(self):
        return {"found": False}


def kill(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)


# ------------------------------------------------------------------ the verbs
def test_stop_signals_the_pid_the_sampler_reads():
    process = stand_in()
    try:
        sampler = FakeSampler(process.pid, [sys.executable, "-c", "x"])
        result = sampler.supervisor.perform("stop")
        assert result["ok"] is True and result["pid"] == process.pid
        # the child is a zombie until its parent reaps it: not "alive" is the
        # honest test, /proc still has the entry
        assert not server.alive(process.pid)
        assert process.wait(timeout=10) is not None  # went down on SIGTERM alone
        assert live.proc_stat(process.pid) is None
    finally:
        kill(process)


def test_stop_without_a_process_says_so():
    sampler = FakeSampler(999_999, [sys.executable, "-c", "x"])
    result = sampler.supervisor.perform("stop")
    assert result["ok"] is False and "to stop" in result["error"]


def test_start_uses_the_recorded_command_line():
    launch = [sys.executable, "-c", "import time; time.sleep(120)"]
    sampler = FakeSampler(0, launch)
    result = sampler.supervisor.perform("start")
    try:
        assert result["ok"] is True, result
        assert server.alive(result["pid"])
        assert result["pid"] != 0
        assert sampler.collector.pid == result["pid"]
    finally:
        os.kill(result["pid"], 9) if result.get("ok") else None


def test_restart_gives_a_new_pid_and_leaves_one_process():
    process = stand_in()
    launch = [sys.executable, "-c", "import time; time.sleep(120)"]
    sampler = FakeSampler(process.pid, launch)
    result = sampler.supervisor.perform("restart")
    try:
        assert result["ok"] is True, result
        assert result["stopped_pid"] == process.pid
        assert result["pid"] != process.pid
        assert not server.alive(process.pid)
        assert server.alive(result["pid"])
    finally:
        kill(process)
        if result.get("ok"):
            os.kill(result["pid"], 9)


def test_start_refuses_when_something_is_already_running():
    process = stand_in()
    try:
        sampler = FakeSampler(process.pid, [sys.executable, "-c", "x"])
        result = sampler.supervisor.perform("start")
        assert result["ok"] is False and "already running" in result["error"]
    finally:
        kill(process)


def test_a_command_that_dies_at_once_is_reported():
    sampler = FakeSampler(0, [sys.executable, "-c", "raise SystemExit(3)"])
    result = sampler.supervisor.perform("start")
    assert result["ok"] is False and "exited at once" in result["error"]


def test_a_launched_child_is_reaped_not_left_as_a_zombie():
    """Every restart used to leave a `Z` entry in /proc for the API process to
    carry; the supervisor now keeps the Popen and collects it."""
    launch = [sys.executable, "-c", "import time; time.sleep(120)"]
    sampler = FakeSampler(0, launch)
    started = sampler.supervisor.perform("start")
    assert started["ok"] is True, started
    pid = started["pid"]
    stopped = sampler.supervisor.perform("stop")
    assert stopped["ok"] is True and stopped["pid"] == pid
    assert live.proc_stat(pid) is None  # reaped, not a lingering zombie


def test_unknown_action_is_refused():
    sampler = FakeSampler(0, [sys.executable, "-c", "x"])
    assert sampler.supervisor.perform("rm -rf /")["ok"] is False


# --------------------------------------------------- the secret stays a secret
def test_launch_moves_an_inline_token_into_a_600_file(tmp_path):
    secret = "eyJhIjoiMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwIiwidCI6IngifQ"
    argv = ["/opt/bin/cloudflared", "tunnel", "run", "--token", secret]
    target = tmp_path / "cf-token"
    out = server.launch_argv(argv, exe="/opt/bin/cloudflared", token_file=target)

    assert out == ["/opt/bin/cloudflared", "tunnel", "run", "--token-file", str(target)]
    assert secret not in " ".join(out)
    assert target.read_text() == secret
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_launch_keeps_a_token_file_as_it_is(tmp_path):
    target = tmp_path / "cf-token"
    target.write_text("secret")
    argv = ["/opt/bin/cloudflared", "tunnel", "--metrics", "127.0.0.1:20241", "run",
            "--token-file", str(target)]
    assert server.launch_argv(argv) == argv


# ------------------------------------------------------- remembering the line
def test_the_command_line_is_remembered_across_a_restart(fake_proc, monkeypatch, tmp_path):
    """A stopped connector leaves no /proc entry; the panel must still know how
    to start it after a reload."""
    remembered = tmp_path / "launch.json"
    monkeypatch.setenv("CARD_LAUNCH_FILE", str(remembered))
    first = live.Collector(metrics_url="")
    first.sample()
    assert first.launch and remembered.exists()
    assert live.load_launch() == first.launch

    again = live.Collector(metrics_url="")
    assert again.launch == first.launch          # loaded before any process is seen


def test_the_remembered_line_carries_no_secret(fake_proc, monkeypatch, tmp_path):
    remembered = tmp_path / "launch.json"
    monkeypatch.setenv("CARD_LAUNCH_FILE", str(remembered))
    secret = "eyJhIjoiMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwIiwidCI6IngifQ"
    (fake_proc / "4242" / "cmdline").write_text(
        f"cloudflared\0tunnel\0run\0--token\0{secret}\0"
    )
    live.Collector(metrics_url="").sample()
    assert secret not in remembered.read_text()
    assert stat.S_IMODE(remembered.stat().st_mode) == 0o600
    # the secret is not lost: it moved to a 600 file and the line names it
    moved = json.loads(remembered.read_text())
    assert "--token-file" in moved and not any(part.startswith("eyJ") for part in moved)
    token_path = moved[moved.index("--token-file") + 1]
    assert Path(token_path).read_text() == secret
    assert stat.S_IMODE(Path(token_path).stat().st_mode) == 0o600


def test_a_remembered_line_starts_the_process_without_any_discovery(monkeypatch, tmp_path):
    """The reload path: nothing in /proc, no pid, only the remembered argv."""
    remembered = tmp_path / "launch.json"
    monkeypatch.setenv("CARD_LAUNCH_FILE", str(remembered))
    argv = [sys.executable, "-c", "import time; time.sleep(120)"]
    remembered.write_text(json.dumps(argv))
    monkeypatch.setattr(live, "find_pid", lambda *args, **kwargs: None)

    sampler = server.Sampler(interval=3600)
    assert sampler.collector.launch == argv
    result = sampler.supervisor.perform("start")
    try:
        assert result["ok"] is True, result
        assert server.alive(result["pid"])
    finally:
        if result.get("ok"):
            os.kill(result["pid"], 9)


# ----------------------------------------------------------------- the route
@pytest.fixture()
def api(monkeypatch):
    sampler = server.Sampler(interval=3600)
    calls: list[int] = []
    sampler.refreshed = calls  # len(calls) is how often the feed re-sampled

    def refresh():
        calls.append(1)
        return {"found": False}

    monkeypatch.setattr(sampler, "refresh", refresh)
    process = stand_in()
    sampler.collector.pid = process.pid
    sampler.collector.launch = [sys.executable, "-c", "import time; time.sleep(120)"]
    httpd = server.serve(sampler, port=0)
    port = httpd.server_address[1]
    yield port, sampler, process
    httpd.shutdown()
    httpd.server_close()
    kill(process)


def post(port: int, body: dict, key: str | None):
    data = json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/action", data=data, method="POST",
        headers={"Content-Type": "application/json",
                 **({"X-RD-Action": key} if key else {})},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.status, dict(response.headers), json.loads(response.read())


def test_action_requires_the_key(api):
    port, _, process = api
    with pytest.raises(urllib.error.HTTPError) as info:
        post(port, {"action": "stop"}, None)
    assert info.value.code == 403
    assert server.alive(process.pid)  # nothing happened

    with pytest.raises(urllib.error.HTTPError) as info:
        post(port, {"action": "stop"}, "wrong-key")
    assert info.value.code == 403
    assert server.alive(process.pid)


def test_action_rejects_an_unknown_verb(api):
    port, _, process = api
    with pytest.raises(urllib.error.HTTPError) as info:
        post(port, {"action": "kill -9 1"}, server.action_key())
    assert info.value.code == 400


def test_get_on_the_action_route_is_a_405(api):
    port, _, _ = api
    with pytest.raises(urllib.error.HTTPError) as info:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/action", timeout=5)
    assert info.value.code == 405


def test_stop_through_the_route_ends_the_process(api):
    port, sampler, process = api
    status, headers, body = post(port, {"action": "stop"}, server.action_key())
    assert status == 200 and body["ok"] is True and body["pid"] == process.pid
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert not server.alive(process.pid)
    assert sampler.supervisor.last_action["action"] == "stop"
    assert len(sampler.refreshed) > 0  # the reply is followed by a fresh sample


def test_start_through_the_route_brings_it_back(api):
    port, sampler, process = api
    os.kill(process.pid, 9)
    process.wait(timeout=10)
    status, _, body = post(port, {"action": "start"}, server.action_key())
    try:
        assert status == 200 and body["ok"] is True, body
        assert body["pid"] != process.pid
        assert server.alive(body["pid"])
    finally:
        if body.get("ok"):
            os.kill(body["pid"], 9)


def test_a_stopped_connector_is_reported_with_the_reason(monkeypatch):
    """``found: false`` alone would look like a crash; the panel says who did it."""
    payload = server.to_payload({"found": False}, {"action": "stop", "ok": True, "at": 1})
    assert payload == {"found": False, "last_action": {"action": "stop", "ok": True, "at": 1}}
    assert server.to_payload({"found": False}) == {"found": False}


def test_the_key_is_in_the_page_and_not_in_the_feed(fake_proc, monkeypatch):
    """The page can press the buttons; anything that only reads /api/state cannot."""
    import card
    from demo_data import build_state

    state = build_state(0)
    page = card.render(state, theme="dark", api="http://127.0.0.1:8598/api/state",
                       action_key=server.action_key())
    assert server.action_key() in page
    assert "X-RD-Action" in page
    assert server.action_key() not in json.dumps(server.to_payload(state))

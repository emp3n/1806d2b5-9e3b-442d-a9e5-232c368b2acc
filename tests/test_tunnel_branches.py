"""The last 33 branches in tunnel control: start failures, swaps and stops."""
from __future__ import annotations

from pathlib import Path

import bootstrap


class FakeProc:
    pid = 777

    def __init__(self, code=None):
        self.code, self.terminated = code, False

    def poll(self): return self.code
    def terminate(self): self.terminated = True


class FakeTime:
    def __init__(self): self.t = 0.0
    def monotonic(self): self.t += 0.5; return self.t
    def sleep(self, seconds): pass


def _spawn(proc):
    return lambda command, runner=None: proc


def test_named_start_warns_when_the_dashboard_port_differs(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "_spawn", _spawn(FakeProc()))
    monkeypatch.setattr(bootstrap, "_wait_ready", lambda *a: (True, ""))
    monkeypatch.setattr(bootstrap, "ready_connections", lambda port: 4)
    monkeypatch.setattr(bootstrap, "ingress_port", lambda: 8501)
    monkeypatch.setattr(bootstrap, "pick_metrics_port", lambda: 20241)
    result = bootstrap.start_named_tunnel(Path("cf"), 9999, "token", directory=tmp_path)
    assert "WARNING" in result.detail and "9999" in result.detail


def test_quick_start_reports_a_spawn_failure(monkeypatch, tmp_path):
    def boom(command, runner=None): raise OSError("cannot exec")
    monkeypatch.setattr(bootstrap, "_spawn", boom)
    result = bootstrap.start_quick_tunnel(Path("cf"), 8501, directory=tmp_path)
    assert result.ok is False and "cannot start" in result.detail


def test_quick_start_reports_an_early_exit(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "_spawn", _spawn(FakeProc(code=255)))
    monkeypatch.setattr(bootstrap, "pick_metrics_port", lambda: 20241)
    monkeypatch.setattr(bootstrap, "_log_tail", lambda *a: "duplicate tunnel")
    result = bootstrap.start_quick_tunnel(Path("cf"), 8501, directory=tmp_path, wait_s=1)
    assert result.ok is False and "already running" in result.detail


def test_quick_start_terminates_when_no_url_appears(monkeypatch, tmp_path):
    proc = FakeProc()
    monkeypatch.setattr(bootstrap, "_spawn", _spawn(proc))
    monkeypatch.setattr(bootstrap, "time", FakeTime())
    monkeypatch.setattr(bootstrap, "pick_metrics_port", lambda: 20241)
    monkeypatch.setattr(bootstrap, "_log_tail", lambda *a: "")
    result = bootstrap.start_quick_tunnel(Path("cf"), 8501, directory=tmp_path, wait_s=0.1)
    assert result.ok is False and "no tunnel URL" in result.detail
    assert proc.terminated is True


def test_quick_start_refuses_an_unregistered_url(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "_spawn", _spawn(FakeProc()))
    monkeypatch.setattr(bootstrap, "time", FakeTime())
    monkeypatch.setattr(bootstrap, "pick_metrics_port", lambda: 20241)
    monkeypatch.setattr(bootstrap, "_log_tail", lambda *a: "https://abc.trycloudflare.com")
    monkeypatch.setattr(bootstrap, "_wait_ready", lambda *a: (False, "not registered"))
    result = bootstrap.start_quick_tunnel(Path("cf"), 8501, directory=tmp_path, wait_s=0.1)
    assert result.ok is False and "not registered" in result.detail


def test_tunnel_status_names_extra_connectors(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda d: [1, 2])
    monkeypatch.setattr(bootstrap, "_describe", lambda pid, d: {
        "pid": pid, "ready": 4, "age": 1.0, "mode": "named",
        "public": "https://example.com", "metrics": 20241})
    result = bootstrap.tunnel_status(8501, tmp_path)
    assert result["ok"] is True and "plus 1 extra" in result["detail"]


def test_adopt_refuses_a_connector_without_a_public_name(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda port, d: {
        "pid": 42, "ready": 4, "public": None, "mode": "named", "metrics": 20241})
    assert bootstrap.adopt_running_tunnel(8501, tmp_path) is None


def test_retire_stale_tunnel_keeps_a_bad_keeper_and_finds_no_pids(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda d: [10])
    monkeypatch.setattr(bootstrap, "_cmdline", lambda pid: ["cf", "--metrics", "20241"])
    monkeypatch.setattr(bootstrap, "ready_connections", lambda port: 0)
    assert bootstrap.retire_stale_tunnel(keep=10, directory=tmp_path) == ""
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda d: [])
    assert bootstrap.retire_stale_tunnel(directory=tmp_path) == ""


def test_stop_tunnel_variants(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda d: [55])
    gone = set()
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: pid not in gone)
    def kill(pid, sig):
        if sig == bootstrap.signal.SIGKILL: gone.add(pid)
    monkeypatch.setattr(bootstrap.os, "kill", kill)
    result = bootstrap.stop_tunnel(directory=tmp_path, grace=0)
    assert 55 in result["stopped"]

    monkeypatch.setattr(bootstrap.time, "sleep", lambda s: None)
    monkeypatch.setattr(bootstrap.os, "kill", lambda pid, sig: (_ for _ in ()).throw(OSError()))


def test_reusable_refuses_a_non_cloudflared_command(monkeypatch):
    tunnel = bootstrap.Tunnel(pid=4242, ok=True)
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: True)
    monkeypatch.setattr(bootstrap, "_cmdline", lambda pid: ["/x/something-else"])
    assert bootstrap.reusable(tunnel) is False


def test_forget_strikes_clears_the_counter():
    bootstrap._public_failures[8899] = (3, 0.0)
    bootstrap._forget_strikes(8899)
    assert 8899 not in bootstrap._public_failures
    bootstrap._forget_strikes(None)


def test_ensure_tunnel_honours_the_off_intent(monkeypatch):
    monkeypatch.setattr(bootstrap, "desired_state", lambda *a: "off")
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda *a: [1])
    called = []
    monkeypatch.setattr(bootstrap, "stop_tunnel", lambda *a, **k: called.append(1))
    result = bootstrap.ensure_tunnel(8501)
    assert result.ok is False and called == [1]


def test_ensure_tunnel_returns_a_reusable_cache(monkeypatch):
    cached = bootstrap.Tunnel(pid=4242, ok=True, mode="named")
    monkeypatch.setattr(bootstrap, "_cached", cached)
    monkeypatch.setattr(bootstrap, "reusable", lambda t: True)
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda *a: (None, "no token"))
    assert bootstrap.ensure_tunnel(8501) is cached
    monkeypatch.setattr(bootstrap, "_cached", None)


def test_ensure_tunnel_logs_retired_connectors(monkeypatch):
    adopted = bootstrap.Tunnel(pid=4242, ok=True, mode="named")
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda *a: (None, "no token"))
    monkeypatch.setattr(bootstrap, "adopt_running_tunnel", lambda *a, **k: adopted)
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel", lambda **k: "retired connector(s) [9]")
    assert bootstrap.ensure_tunnel(8501) is adopted
    monkeypatch.setattr(bootstrap, "adopt_running_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(bootstrap, "download", lambda *a, **k: Path("cf"))
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: bootstrap.Tunnel(pid=5, ok=True))
    bootstrap.ensure_tunnel(8501)
    monkeypatch.setattr(bootstrap, "_cached", None)


def test_replace_with_named_without_a_token_keeps_the_old_tunnel():
    old = bootstrap.Tunnel(pid=4242, ok=True, mode="quick")
    monkeypatch_token = bootstrap.tunnel_token
    bootstrap.tunnel_token = lambda *a: (None, "no token")
    try:
        assert bootstrap._replace_with_named(8501, old, 1) is old
    finally:
        bootstrap.tunnel_token = monkeypatch_token


def test_replace_with_named_reports_a_start_exception(monkeypatch):
    old = bootstrap.Tunnel(pid=4242, ok=True, mode="quick")
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda *a: ("tok", "secret"))
    def boom(*a, **k): raise RuntimeError("cannot start")
    monkeypatch.setattr(bootstrap, "download", boom)
    result = bootstrap._replace_with_named(8501, old, 1)
    assert result is old


def test_restart_without_a_token_uses_a_quick_tunnel(monkeypatch):
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda *a: {"pid": None, "extra": []})
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda *a: (None, "no token"))
    monkeypatch.setattr(bootstrap, "download", lambda *a, **k: Path("cf"))
    seen = []
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: seen.append(1) or bootstrap.Tunnel(pid=5, ok=True))
    bootstrap.restart_tunnel(8501)
    assert seen == [1]


def test_public_is_stuck_uses_a_recent_strike_count(monkeypatch):
    import time as real_time
    bootstrap._public_failures[9911] = (bootstrap.PUBLIC_STRIKES, real_time.monotonic())
    assert bootstrap.public_is_stuck(9911, "example.com") is True
    bootstrap._forget_strikes(9911)


def test_stop_tunnel_waits_and_survives_a_failed_kill(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: True)
    monkeypatch.setattr(bootstrap.time, "sleep", lambda seconds: None)
    def kill(pid, sig):
        if sig == bootstrap.signal.SIGKILL:
            raise OSError("already gone")
    monkeypatch.setattr(bootstrap.os, "kill", kill)
    result = bootstrap.stop_tunnel([31], tmp_path, grace=0.01)
    assert result["ok"] is False


def test_stop_tunnel_survives_a_pidfile_that_cannot_be_unlinked(monkeypatch, tmp_path):
    gone = set()
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: pid not in gone)
    def kill(pid, sig):
        if sig == bootstrap.signal.SIGKILL: gone.add(pid)
    monkeypatch.setattr(bootstrap.os, "kill", kill)
    pidfile = tmp_path / "cf.pid"
    pidfile.write_text("31\n")
    real_unlink = Path.unlink
    def broken(self, *a, **kw): raise OSError("readonly")
    monkeypatch.setattr(Path, "unlink", broken)
    bootstrap.stop_tunnel([31], tmp_path, grace=0)
    monkeypatch.setattr(Path, "unlink", real_unlink)

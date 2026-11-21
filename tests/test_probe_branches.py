"""Probe success paths and OS fallbacks, driven by a fake /proc tree."""
from __future__ import annotations

import io
import json
from pathlib import Path

import bootstrap
import resources


class Reply:
    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status = body, status

    def read(self, n=-1): return self.body if n < 0 else self.body[:n]
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _fake_proc(tmp_path):
    def factory(arg):
        text = str(arg)
        return tmp_path / text[len("/proc/"):] if text.startswith("/proc/") else Path(arg)
    return factory


def test_metrics_and_ready_answers_accept_a_live_connector(monkeypatch):
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda url, timeout: Reply(b"ok"))
    assert bootstrap.metrics_answers(20241) is True

    def ready(url, timeout):
        return Reply(json.dumps({"readyConnections": 4}).encode())

    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", ready)
    assert bootstrap.ready_connections(20241) == 4


def test_public_and_state_answers_accept_a_healthy_page(monkeypatch):
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda url, timeout: Reply(b"ok"))
    assert bootstrap.public_answers("https://example.com/") is True
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda url, timeout: Reply(b'{"found":true}'))
    assert bootstrap.state_answers("https://example.com/state.json") is True
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda url, timeout: Reply(b"<html>"))
    assert bootstrap.state_answers("https://example.com/state.json") is False


def test_proc_age_reads_a_fake_proc_stat(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "Path", _fake_proc(tmp_path))
    stat = tmp_path / "4242"
    stat.mkdir()
    fields = ["S"] * 22
    fields[19] = "10"  # start time in clock ticks
    (stat / "stat").write_text("(cloudflared) " + " ".join(fields))
    (tmp_path / "uptime").write_text("1000.0 0.0\n")
    age = bootstrap.proc_age(4242)
    assert 999 < age < 1000


def test_our_tunnels_discovers_only_our_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "Path", _fake_proc(tmp_path))
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: True)
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "cloudflared"
    binary.write_bytes(b"x")
    pid_dir = tmp_path / "77"
    pid_dir.mkdir()
    (pid_dir / "cmdline").write_bytes(
        b"/redesign/bin/cloudflared\0tunnel\0--metrics\0127.0.0.1:20241\0")
    # the fake Path maps /redesign/bin/... onto tmp_path/bin/... only when the
    # absolute prefix matches, so point cmdline at the real binary directory
    (pid_dir / "cmdline").write_bytes(
        str(binary).encode() + b"\0tunnel\0--metrics\0127.0.0.1:20241\0")
    (pid_dir / "stat").write_text("(cloudflared) S " + " ".join(["1"] * 22))
    found = bootstrap.our_tunnels(binary_dir)
    assert found == [77]

    # a process from another directory is never adopted
    (pid_dir / "cmdline").write_bytes(b"/elsewhere/cloudflared\0tunnel\0")
    assert bootstrap.our_tunnels(binary_dir) == []


def test_non_posix_proc_alive_fallback(tmp_path, monkeypatch):
    class NoProcPath:
        def __call__(self, arg): return self
        def is_dir(self): return False

    monkeypatch.setattr(bootstrap, "Path", NoProcPath())
    assert bootstrap._proc_alive(4242) is True
    def gone(pid, sig): raise OSError("no such process")
    monkeypatch.setattr(bootstrap.os, "kill", gone)
    assert bootstrap._proc_alive(4242) is False


def test_tail_hint_has_a_quiet_default():
    assert bootstrap._tail_hint("nothing special") == ""


# ---------------------------------------------------------------- resources
def test_cgroup_v2_cpu_weight_and_memory_edges(tmp_path, monkeypatch):
    cpu = tmp_path / "cpu.max"
    cpus = tmp_path / "cpuset"
    weight = tmp_path / "weight"
    cpu.write_text("max 100000")
    cpus.write_text("0-3")
    weight.write_text("25")
    monkeypatch.setattr(resources, "CPU_MAX", cpu)
    monkeypatch.setattr(resources, "CPUSET_V2", cpus)
    monkeypatch.setattr(resources, "CPU_WEIGHT", weight)
    assert resources.available_cores() == 1.0

    weight.write_text("0")  # invalid weight: fall back to the visible set
    assert resources.available_cores() == 4.0
    cpu.write_text("garbage 100000")
    assert resources.available_cores() == 4.0


def test_proc_age_ignores_a_malformed_start_field(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "Path", _fake_proc(tmp_path))
    pid = tmp_path / "55"
    pid.mkdir()
    fields = ["S"] * 22
    fields[19] = "not-a-number"
    (pid / "stat").write_text("(x) " + " ".join(fields))
    (tmp_path / "uptime").write_text("10.0 0.0\n")
    assert bootstrap.proc_age(55) == 0.0


def test_our_tunnels_with_no_proc_directory(tmp_path, monkeypatch):
    class NoProc:
        def __call__(self, arg): return _fake_proc(tmp_path)(arg)
        def is_dir(self): return False

    monkeypatch.setattr(bootstrap, "Path", NoProc())
    assert bootstrap.our_tunnels(tmp_path) == []


def test_our_tunnels_skips_a_path_that_cannot_be_resolved(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "Path", _fake_proc(tmp_path))
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: True)
    pid = tmp_path / "66"
    pid.mkdir()
    (pid / "cmdline").write_bytes(b"/x/cloudflared\0tunnel\0")
    real_resolve = Path.resolve
    def broken(self, *a, **kw):
        if str(self).startswith("/x/"): raise OSError("gone")
        return real_resolve(self, *a, **kw)
    monkeypatch.setattr(Path, "resolve", broken)
    assert bootstrap.our_tunnels(tmp_path) == []

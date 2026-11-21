"""Malformed /proc files and defensive returns in the live readers."""
from __future__ import annotations

import json
import os
from pathlib import Path

import live
import resources


def test_proc_stat_rejects_malformed_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "PROC", tmp_path)
    pid = tmp_path / "10"
    pid.mkdir()
    (pid / "stat").write_text("no closing bracket")
    assert live.proc_stat(10) is None
    (pid / "stat").write_text("(x) S")
    assert live.proc_stat(10) is None
    fields = ["S"] + ["x"] * 21
    (pid / "stat").write_text("(x) " + " ".join(fields))
    assert live.proc_stat(10) is None


def test_proc_rss_and_uptime_reject_bad_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "PROC", tmp_path)
    (tmp_path / "11").mkdir()
    (tmp_path / "11" / "statm").write_text("only-one")
    assert live.proc_rss(11) is None
    (tmp_path / "11" / "statm").write_text("1 nope")
    assert live.proc_rss(11) is None
    (tmp_path / "uptime").write_text("garbage")
    assert live.boot_seconds() is None


def test_proc_cpu_ns_skips_bad_threads(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "PROC", tmp_path)
    tasks = tmp_path / "12" / "task"
    (tasks / "a").mkdir(parents=True)
    (tasks / "a" / "schedstat").write_text("not a number 0 0")
    assert live.proc_cpu_ns(12) is None


def test_strip_token_handles_inline_equals(monkeypatch):
    argv = ["cloudflared", "tunnel", "run", "--token=secret"]
    assert "secret" not in " ".join(live.strip_token(argv))


def test_launch_file_respects_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CARD_LAUNCH_FILE", str(tmp_path / "launch.json"))
    assert live.launch_file() == tmp_path / "launch.json"
    monkeypatch.delenv("CARD_LAUNCH_FILE")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert str(live.launch_file()).startswith(str(tmp_path / "state"))


def test_save_and_load_launch_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("CARD_LAUNCH_FILE", str(tmp_path / "launch.json"))
    live.save_launch([])  # empty argv is ignored, no file written
    assert not (tmp_path / "launch.json").exists()
    live.save_launch(["cloudflared", "tunnel", "run"])
    assert live.load_launch() == ["cloudflared", "tunnel", "run"]
    (tmp_path / "launch.json").write_text("not json")
    assert live.load_launch() is None
    (tmp_path / "launch.json").write_text('{"a":1}')
    assert live.load_launch() is None


def test_proc_memory_limit_reads_cgroup_v2(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "PROC", tmp_path)
    (tmp_path / "13").mkdir()
    (tmp_path / "13" / "cgroup").write_text("0::/mygroup\n")
    cgroup = tmp_path / "sys" / "fs" / "cgroup" / "mygroup"
    cgroup.mkdir(parents=True)
    (cgroup / "memory.max").write_text("1073741824\n")
    monkeypatch.setattr(live.Path, "read_text", Path.read_text)
    import pathlib
    real = pathlib.Path.read_text
    def patched(self, *a, **kw):
        if str(self).startswith("/sys/fs/cgroup"):
            return (tmp_path / "sys" / "fs" / "cgroup" / str(self).split("/sys/fs/cgroup/", 1)[1]).read_text()
        return real(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "read_text", patched)
    assert live.proc_memory_limit(13) == 1024.0


def test_metrics_url_accepts_a_full_url():
    assert live.metrics_url_for(["cf", "tunnel", "--metrics", "https://x:20241/"]) \
           == "https://x:20241"


def test_scan_pids_survives_a_missing_proc(monkeypatch):
    monkeypatch.setattr(live, "PROC", "/definitely/not/here")
    assert live.scan_pids() == []


def test_log_path_equals_form_and_environment_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "PROC", tmp_path)
    (tmp_path / "14").mkdir()
    monkeypatch.setattr(live, "proc_cmdline", lambda pid:
                        ["cf", "tunnel", "--logfile=/var/log/cf.log"])
    assert live.log_path(14) == "/var/log/cf.log"
    monkeypatch.setattr(live, "proc_cmdline", lambda pid: ["cf"])
    monkeypatch.setattr(live.os, "readlink", lambda path: "pipe:[1]")
    monkeypatch.setenv("CLOUDFLARED_LOG", str(tmp_path / "env.log"))
    assert live.log_path(14) == str(tmp_path / "env.log")


def test_read_tail_handles_a_large_file_and_a_missing_path(tmp_path):
    assert live.read_tail(str(tmp_path / "missing")) == []
    path = tmp_path / "big.log"
    path.write_text("\n".join(f"line{i}" for i in range(10000)))
    tail = live.read_tail(str(path), 5)
    assert len(tail) == 5 and tail[-1] == "line9999"


def test_resources_rejects_garbage_in_every_source(tmp_path, monkeypatch):
    cpu = tmp_path / "cpu.max"
    cpus = tmp_path / "cpuset"
    weight = tmp_path / "weight"
    cpu.write_text("max 100000")
    cpus.write_text("0-1")
    weight.write_text("nope")
    monkeypatch.setattr(resources, "CPU_MAX", cpu)
    monkeypatch.setattr(resources, "CPUSET_V2", cpus)
    monkeypatch.setattr(resources, "CPU_WEIGHT", weight)
    monkeypatch.setattr(resources, "CPU_QUOTA_V1", tmp_path / "v1q")
    monkeypatch.setattr(resources, "CPU_PERIOD_V1", tmp_path / "v1p")
    assert resources.available_cores() == 2.0
    cpu.write_text("bad 100000")
    (tmp_path / "v1q").write_text("bad")
    (tmp_path / "v1p").write_text("100000")
    assert resources.available_cores() == 2.0


def test_memory_limit_ignores_invalid_v2_v1_and_meminfo(tmp_path, monkeypatch):
    v2, v1 = tmp_path / "max", tmp_path / "v1"
    v2.write_text("garbage")
    v1.write_text("not-a-number")
    monkeypatch.setattr(resources, "MEMORY_MAX", v2)
    monkeypatch.setattr(resources, "MEMORY_LIMIT_V1", v1)
    import pathlib
    real = pathlib.Path.read_text
    def patched(self, *a, **kw):
        if str(self) == "/proc/meminfo": return "MemTotal: nope kB\n"
        return real(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "read_text", patched)
    assert resources.memory_limit_bytes() == 0


def test_save_launch_survives_a_readonly_target(monkeypatch, tmp_path):
    monkeypatch.setenv("CARD_LAUNCH_FILE", str(tmp_path / "no" / "launch.json"))
    live.save_launch(["cloudflared", "tunnel"])  # no exception


def test_proc_memory_limit_ignores_a_bad_cgroup_value(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "PROC", tmp_path)
    (tmp_path / "15").mkdir()
    (tmp_path / "15" / "cgroup").write_text("0::/g\n")
    import pathlib
    real = pathlib.Path.read_text
    def patched(self, *a, **kw):
        if str(self).startswith("/sys/fs/cgroup"): return "garbage\n"
        return real(self, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "read_text", patched)
    assert live.proc_memory_limit(15) is None


def test_log_path_survives_a_missing_fd(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "PROC", tmp_path)
    (tmp_path / "16").mkdir()
    monkeypatch.setattr(live, "proc_cmdline", lambda pid: ["cf"])
    def boom(path): raise OSError("gone")
    monkeypatch.setattr(live.os, "readlink", boom)
    monkeypatch.delenv("CLOUDFLARED_LOG", raising=False)
    assert live.log_path(16) is None

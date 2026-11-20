"""The card must describe the CONTAINER, not the host it runs on."""
from __future__ import annotations

import resources


def test_cgroup_v2_quota_is_converted_to_cores(tmp_path, monkeypatch):
    cpu = tmp_path / "cpu.max"
    cpu.write_text("250000 100000")  # 2.5 cores -> ceil to 3
    monkeypatch.setattr(resources, "CPU_MAX", cpu)
    monkeypatch.setattr(resources, "CPUSET_V2", tmp_path / "missing")
    assert resources.available_cores() == 3


def test_cpuset_limits_an_unlimited_quota(tmp_path, monkeypatch):
    cpu = tmp_path / "cpu.max"
    cpu.write_text("max 100000")
    cpus = tmp_path / "cpuset"
    cpus.write_text("2-5,8")  # 5 CPUs visible
    monkeypatch.setattr(resources, "CPU_MAX", cpu)
    monkeypatch.setattr(resources, "CPUSET_V2", cpus)
    monkeypatch.setattr(resources, "_affinity", lambda: 16)
    assert resources.available_cores() == 5


def test_v1_quota_fallback(tmp_path, monkeypatch):
    quota = tmp_path / "quota"
    period = tmp_path / "period"
    quota.write_text("200000")
    period.write_text("100000")
    monkeypatch.setattr(resources, "CPU_MAX", tmp_path / "missing")
    monkeypatch.setattr(resources, "CPU_QUOTA_V1", quota)
    monkeypatch.setattr(resources, "CPU_PERIOD_V1", period)
    assert resources.available_cores() == 2


def test_memory_uses_cgroup_not_meminfo(tmp_path, monkeypatch):
    limit = tmp_path / "memory.max"
    limit.write_text("3221225472")
    monkeypatch.setattr(resources, "MEMORY_MAX", limit)
    info = resources.host_info()
    assert info["cores"] >= 1
    assert info["ram_gb"] == 3.0
    assert "3.0 GB" in info["text"]


def test_host_info_never_reports_the_host_core_count(monkeypatch):
    monkeypatch.setattr(resources, "available_cores", lambda: 4)
    monkeypatch.setattr(resources, "memory_limit_bytes", lambda: 1024**3)
    assert resources.host_info()["text"].startswith("4 cores")

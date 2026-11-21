"""Malformed inputs and dead-end paths — the lines the happy path never sees."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import bootstrap
import live
import resources
import sessions


# --------------------------------------------------------------- http body
def test_http_reads_an_error_status_body(monkeypatch):
    class Reply:
        status = 503
        def read(self): return b'{"status":503,"readyConnections":0}'
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(live.urllib.request, "urlopen", lambda *a, **k: Reply())
    assert json.loads(live._http("http://x/ready"))["readyConnections"] == 0


def test_http_drops_an_unreadable_error_body(monkeypatch):
    class BadError(urllib.error.HTTPError):
        def read(self): raise OSError("gone")

    def boom(*a, **k):
        raise BadError("u", 503, "no", {}, io.BytesIO())

    monkeypatch.setattr(live.urllib.request, "urlopen", boom)
    assert live._http("http://x/ready") is None


def test_http_returns_none_on_a_network_error(monkeypatch):
    def boom(*a, **k): raise urllib.error.URLError("offline")
    monkeypatch.setattr(live.urllib.request, "urlopen", boom)
    assert live._http("http://x/ready") is None


# ------------------------------------------------------------- parsers
def test_parse_metrics_ignores_noise_and_reads_labels():
    text = """\
# comment
build_info{version="2026.11.1"} 1
garbage_line not-a-number
cloudflared_tunnel_request_errors 3
cloudflared_tunnel_tunnel_register_fail{error="timeout"} 2
cloudflared_tunnel_ha_connections 4
"""
    out = live.parse_metrics(text)
    assert out["version"] == "2026.11.1"
    assert out["request_errors"] == 3
    assert out["register_fail"] == 2
    assert out["ha_connections"] == 4


def test_parse_ready_survives_broken_and_partial_json():
    assert live.parse_ready("not json") == {}
    data = live.parse_ready('{"readyConnections":null,"status":"2"}')
    assert data == {"ready": 0, "connector": "", "status": 2}


def test_parse_log_ignores_broken_json_and_records_text_reasons():
    lines = [
        '{"time":"2026-11-21T00:00:00Z","broken":',
        'Register tunnel error from server side error="bad token"',
        'not json but ip=1.2.3.4 location=sea01 connIndex=1',
    ]
    result = live.parse_log(lines)
    assert result["reason"] == "bad token"
    assert result["conns"][0]["location"] == "sea01"
    assert result["conns"][0]["ip"] == "1.2.3.4"


def test_line_epoch_handles_both_timestamp_shapes():
    assert live.line_epoch('{"time":"2026-11-21T00:00:00Z"}') > 0
    assert live.line_epoch("2026-11-21T00:00:00Z connecting") > 0
    assert live.line_epoch("no timestamp") is None


# ---------------------------------------------------------------- /proc
def test_proc_readers_return_none_for_a_missing_pid(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "PROC", tmp_path)
    assert live.proc_stat(999) is None
    assert live.proc_rss(999) is None
    assert live.proc_exe(999) is None
    assert live.proc_cmdline(999) == []
    assert live.proc_memory_limit(999) is None
    assert live.proc_cpu_ns(999) is None
    assert live.proc_started_at(999) is None


def test_boot_seconds_and_affinity_fallbacks(monkeypatch):
    monkeypatch.setattr(live, "PROC", Path("/definitely/not/here"))
    assert live.boot_seconds() is None
    monkeypatch.setattr(resources.os, "sched_getaffinity", lambda x: (_ for _ in ()).throw(OSError()), raising=False)
    assert resources._affinity() >= 1


# -------------------------------------------------------------- flags/SVG
def test_flag_lookup_rejects_a_path_traversal_attempt():
    from ui.card import flag_img
    assert flag_img("../../etc/passwd") == ""
    assert flag_img("US").startswith('<img class="rd-flag"')
    assert "app/static/flags/us.svg" in flag_img("US")


def test_sessions_are_zero_without_a_pid_or_port():
    assert sessions.connections(None, 0) == 0
    assert sessions.ingress_sessions(0) == 0


# --------------------------------------------------------------- binary layer
def test_asset_name_supports_arm_and_macos(monkeypatch):
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "aarch64")
    assert bootstrap.asset_name() == "cloudflared-linux-arm64"
    monkeypatch.setattr(bootstrap.platform, "system", lambda: "Darwin")
    assert bootstrap.asset_name() == "cloudflared-darwin-arm64"


def test_existing_binary_prefers_the_local_copy(tmp_path):
    local = tmp_path / "cloudflared"
    local.write_bytes(b"x")
    local.chmod(0o755)
    assert bootstrap.existing_binary(tmp_path) == local
    local.chmod(0o644)
    assert bootstrap.existing_binary(tmp_path) is None


def test_download_rejects_a_truncated_payload(tmp_path):
    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    fetch = lambda url, timeout: Reply(b"tiny")
    try:
        bootstrap.download(tmp_path, opener=fetch)
    except RuntimeError as exc:
        assert "suspiciously small" in str(exc)
    else:
        raise AssertionError("a truncated download must fail")
    assert not (tmp_path / "cloudflared.download").exists()


def test_download_accepts_a_full_payload(tmp_path):
    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    path = bootstrap.download(tmp_path, opener=lambda u, timeout: Reply(b"x" * 1_100_000))
    assert path.exists() and path.stat().st_size == 1_100_000
    assert path.stat().st_mode & 0o111
    # a second call reuses it without fetching
    path.write_bytes(b"x" * 1_100_000)
    assert bootstrap.download(tmp_path, opener=lambda u, t: None) == path


def test_version_of_reports_a_nonzero_exit(tmp_path):
    fake = tmp_path / "bad-cloudflared"
    fake.write_text("#!/bin/sh\nexit 3\n")
    fake.chmod(0o755)
    try:
        bootstrap.version_of(fake)
    except RuntimeError as exc:
        assert "exited 3" in str(exc)
    else:
        raise AssertionError("a failing --version must raise")


# ------------------------------------------------------------------- probes
def test_proc_age_and_state_for_a_missing_process():
    assert bootstrap.proc_age(999999) == 0.0
    assert bootstrap._proc_state(999999) is None
    assert bootstrap._proc_alive(0) is False
    assert bootstrap._proc_alive(-1) is False


def test_public_is_stuck_needs_repeated_misses(monkeypatch):
    bootstrap._public_failures.clear()
    monkeypatch.setattr(bootstrap, "public_answers", lambda host: False)
    assert bootstrap.public_is_stuck(1, "example.com") is False
    # three strikes, with enough time between probes to count as separate checks
    bootstrap._public_failures[1] = (bootstrap.PUBLIC_STRIKES - 1, 0.0)
    assert bootstrap.public_is_stuck(1, "example.com") is True
    bootstrap._public_failures.clear()
    monkeypatch.setattr(bootstrap, "public_answers", lambda host: True)
    assert bootstrap.public_is_stuck(1, "example.com") is False
    assert bootstrap.public_is_stuck(1, "") is False


def test_reusable_refuses_a_tunnel_with_no_registration(monkeypatch):
    tunnel = bootstrap.Tunnel(pid=4242, ok=True)
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: False)
    assert bootstrap.reusable(tunnel) is False
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: True)
    monkeypatch.setattr(bootstrap, "_cmdline", lambda pid: ["/x/cloudflared"])
    assert bootstrap.reusable(tunnel) is False
    monkeypatch.setattr(bootstrap, "_cmdline", lambda pid: ["/x/cloudflared", "--metrics", "127.0.0.1:20241"])
    monkeypatch.setattr(bootstrap, "ready_connections", lambda port: 0)
    assert bootstrap.reusable(tunnel) is False


# ----------------------------------------------------------- tunnel control
def test_stop_tunnel_reports_what_was_signalled(monkeypatch, tmp_path):
    killed, gone = [], set()
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: pid not in gone)
    def record(pid, sig):
        killed.append((pid, sig))
        if sig == bootstrap.signal.SIGKILL: gone.add(pid)
    monkeypatch.setattr(bootstrap.os, "kill", record)
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    result = bootstrap.stop_tunnel([101, 102], tmp_path, grace=0)
    assert result["stopped"] == [101, 102]
    assert [s for _, s in killed][:2] == [bootstrap.signal.SIGTERM] * 2


def test_stop_tunnel_survives_a_process_that_vanishes(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "_proc_alive", lambda pid: False)
    result = bootstrap.stop_tunnel(999, tmp_path, grace=0)
    assert result["ok"] is True and result["stopped"] == []


def test_start_named_tunnel_reports_a_spawn_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    def boom(*a, **k): raise OSError("cannot exec")
    monkeypatch.setattr(bootstrap, "_spawn", boom)
    result = bootstrap.start_named_tunnel(tmp_path / "cf", 8501, "token", directory=tmp_path, wait_s=0)
    assert result.ok is False and "cannot start" in result.detail


def test_retire_stale_tunnel_keeps_a_healthy_keeper(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda d: [10, 11])
    monkeypatch.setattr(bootstrap, "_cmdline", lambda pid: ["cf", "--metrics", "127.0.0.1:20241"])
    monkeypatch.setattr(bootstrap, "ready_connections", lambda port: 4)
    assert bootstrap.retire_stale_tunnel(keep=10, directory=tmp_path) == ""


def test_forget_tunnel_clears_the_cached_connector():
    import core.bootstrap.tunnel as tunnel_layer
    tunnel_layer._cached = object()
    bootstrap.forget_tunnel()
    assert tunnel_layer._cached is None

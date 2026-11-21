"""Pure helpers: no network, no process, no browser — just deterministic logic."""
from __future__ import annotations

import platform
from pathlib import Path

import bootstrap
import live
import resources

# ---------------------------------------------------------------- cgroups
def test_cpu_list_parser_handles_ranges_and_garbage():
    assert resources._count_cpu_list("0-3,8,10-11") == 7
    assert resources._count_cpu_list("0") == 1
    assert resources._count_cpu_list("") == 0
    assert resources._count_cpu_list("x-y,z") == 0
    assert resources._count_cpu_list("3-1") == 0


def test_memory_limit_falls_back_through_all_sources(tmp_path, monkeypatch):
    v2, v1, info = tmp_path / "max", tmp_path / "v1", tmp_path / "info"
    monkeypatch.setattr(resources, "MEMORY_MAX", v2)
    monkeypatch.setattr(resources, "MEMORY_LIMIT_V1", v1)
    monkeypatch.setattr(resources.Path, "read_text", lambda self, **kw: "")
    import builtins
    real_read = Path.read_text

    def fake(self, *a, **kw):
        if self == v2: return "2147483648\n"
        if self == v1: return "99999999999999999999999\n"
        if str(self) == "/proc/meminfo": return "MemTotal: 1000 kB\n"
        return real_read(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", fake)
    assert resources.memory_limit_bytes() == 2147483648
    v2.write_text("")
    fake_v2 = fake
    def no_v2(self, *a, **kw):
        if self == v2: return ""
        return fake_v2(self, *a, **kw)
    monkeypatch.setattr(Path, "read_text", no_v2)
    assert resources.memory_limit_bytes() == 1024000  # /proc/meminfo fallback


# ---------------------------------------------------------------- bootstrap
def test_flag_value_accepts_both_forms():
    args = ["tunnel", "--logfile", "/tmp/a", "--metrics=127.0.0.1:20241"]
    assert bootstrap._flag_value(args, "--logfile") == "/tmp/a"
    assert bootstrap._flag_value(args, "--metrics") == "127.0.0.1:20241"
    assert bootstrap._flag_value(args, "--pidfile") is None


def test_download_url_is_versioned_and_platform_specific(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    assert bootstrap.asset_name() == "cloudflared-linux-amd64"
    assert bootstrap.download_url("1.2.3").endswith("/1.2.3/cloudflared-linux-amd64")
    monkeypatch.setattr(platform, "system", lambda: "Plan9")
    try:
        bootstrap.asset_name()
    except RuntimeError:
        pass
    else:
        raise AssertionError("unsupported platform must raise")


def test_tunnel_hint_names_the_actionable_failure():
    assert "token" in bootstrap._tail_hint("Invalid tunnel secret")
    assert "port" in bootstrap._tail_hint("address already in use")
    assert bootstrap._tail_hint("Cannot determine default configuration path") == ""


def test_desired_state_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    assert bootstrap.desired_state() == "on"
    assert bootstrap.set_desired("off") == "off"
    assert bootstrap.desired_state() == "off"


def test_port_helpers_are_bounded():
    assert 1 <= bootstrap.pick_metrics_port() <= 65535
    assert bootstrap.port_is_free(0) is True  # ephemeral port is always free


# ---------------------------------------------------------------- live
def test_cpu_text_keeps_small_numbers_and_drops_noise():
    assert live.cpu_text(0.04) == "0.04%"
    assert live.cpu_text(0.0) == "0.0%"
    assert live.cpu_text(12.34) == "12.3%"


def test_parse_trace_reads_key_value_lines():
    assert live.parse_trace("ip=1.2.3.4\nlocation=sea01\n\n=x\n") == {
        "ip": "1.2.3.4", "location": "sea01"}


def test_token_is_stripped_from_logged_argv(tmp_path):
    token = tmp_path / "cf-token"
    argv = ["cloudflared", "tunnel", "run", "--token-file", str(token)]
    out = live.strip_token(argv)
    assert "--token-file" in out and str(token) in out
    inline = ["cloudflared", "tunnel", "run", "--token", "secret123"]
    assert "secret123" not in " ".join(live.strip_token(inline))


def test_metrics_url_reads_the_flag_only_for_a_real_tunnel():
    assert live.metrics_url_for(["cloudflared", "tunnel", "--metrics", "127.0.0.1:9999"]) \
           == "http://127.0.0.1:9999"
    assert live.metrics_url_for(["cloudflared", "proxy"]) is None


# ---------------------------------------------------------- configuration
def test_lookup_prefers_settings_then_environment(monkeypatch):
    monkeypatch.setattr(bootstrap, "_secret", lambda key: None)
    monkeypatch.setenv("RD_TEST_KEY", "from-env")
    assert bootstrap._lookup("RD_TEST_KEY", {"RD_TEST_KEY": "from-settings"}) == "from-settings"
    assert bootstrap._lookup("RD_TEST_KEY", {}) == "from-env"
    assert bootstrap._lookup("MISSING_KEY", object()) is None


def test_setting_uses_secret_before_environment(monkeypatch):
    monkeypatch.setattr(bootstrap, "_secret", lambda key: "secret-value")
    monkeypatch.setenv("RD_TEST_KEY", "env-value")
    assert bootstrap._setting("RD_TEST_KEY") == "secret-value"


def test_host_and_port_are_normalized(monkeypatch):
    monkeypatch.setattr(bootstrap, "_secret", lambda key: None)
    monkeypatch.delenv("CLOUDFLARE_TUNNEL_HOSTNAME", raising=False)
    default = bootstrap.public_hostname()
    assert default and "://" not in default and "/" not in default
    settings = {"CLOUDFLARE_TUNNEL_HOSTNAME": "https://Example.COM/"}
    assert bootstrap.public_hostname(settings) == "Example.COM"
    assert bootstrap.ingress_port({"CLOUDFLARE_TUNNEL_INGRESS_PORT": "9000"}) == 9000
    assert bootstrap.ingress_port({"CLOUDFLARE_TUNNEL_INGRESS_PORT": "bad"}) == 8501
    assert bootstrap.cloud_url({"STREAMLIT_APP_URL": "https://x.streamlit.app/"}) \
           == "https://x.streamlit.app"


def test_tunnel_token_never_comes_back_empty_without_a_source(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "_secret", lambda key: None)
    monkeypatch.delenv("CLOUDFLARE_TUNNEL_TOKEN", raising=False)
    monkeypatch.setattr(bootstrap, "token_files", lambda: [tmp_path / "cf-token"])
    token, source = bootstrap.tunnel_token()
    assert token is None and "no tunnel token" in source
    (tmp_path / "cf-token").write_text("abc123\n")
    token, source = bootstrap.tunnel_token()
    assert token == "abc123" and "file" in source
    token, source = bootstrap.tunnel_token({"CLOUDFLARE_TUNNEL_TOKEN": " xyz "})
    assert token == "xyz" and source.startswith("secret")


def test_write_token_file_is_private(tmp_path):
    path = bootstrap.write_token_file("s3cr3t", tmp_path)
    assert path.read_text().strip() == "s3cr3t"
    assert path.stat().st_mode & 0o077 == 0


def test_log_tail_returns_only_the_last_lines(tmp_path):
    path = tmp_path / "log"
    path.write_text("\n".join(str(i) for i in range(10)))
    assert bootstrap._log_tail(path, 3) == "7\n8\n9"
    assert bootstrap._log_tail(tmp_path / "missing") == ""


def test_proc_helpers_are_safe_for_a_missing_pid():
    assert bootstrap._proc_state(999999) is None
    assert bootstrap._proc_alive(999999) is False
    assert bootstrap._cmdline(999999) == []
    assert bootstrap.our_tunnels(Path("/nonexistent")) == []


# --------------------------------------------------------- sampler lifecycle
def test_sampler_loop_survives_a_broken_sample_and_stops(tmp_path, monkeypatch):
    import statefeed
    sam = statefeed.Sampler(directory=tmp_path, interval=0.001)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("probe exploded")
        sam._stop.set()

    monkeypatch.setattr(sam, "refresh", flaky)
    sam.start()
    sam._thread.join(timeout=2)
    assert not sam._thread.is_alive()
    assert "RuntimeError" in sam.last_error
    assert sam.snapshot() == {"found": False}


def test_act_refreshes_and_reports_the_real_result(tmp_path, monkeypatch):
    import statefeed
    sam = statefeed.Sampler(directory=tmp_path)
    monkeypatch.setattr(statefeed.bootstrap, "set_desired", lambda *a: "on")
    monkeypatch.setattr(statefeed.bootstrap, "ensure_tunnel", lambda *a, **k: None)
    monkeypatch.setattr(sam, "refresh", lambda: {"found": True})
    result = sam.act("start")
    assert "at" in result and result["pending"] is True
    import time
    for _ in range(60):
        if not sam.last_action.get("pending"): break
        time.sleep(0.01)
    assert sam.last_action["action"] == "start"
    assert sam.act("bogus")["error"]


def test_available_cores_v1_quota_and_fallback(tmp_path, monkeypatch):
    cpu, period = tmp_path / "quota", tmp_path / "period"
    monkeypatch.setattr(resources, "CPU_MAX", tmp_path / "missing")
    monkeypatch.setattr(resources, "CPU_QUOTA_V1", cpu)
    monkeypatch.setattr(resources, "CPU_PERIOD_V1", period)
    monkeypatch.setattr(resources, "_affinity", lambda: 8)
    cpu.write_text("250000"), period.write_text("100000")
    assert resources.available_cores() == 2.5
    cpu.write_text("bad")
    assert resources.available_cores() == 8.0

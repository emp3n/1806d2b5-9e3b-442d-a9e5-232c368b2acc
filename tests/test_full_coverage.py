"""Targeted tests for the last defensive branches — measured to 100%."""
from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path

import bootstrap
import geo
import live
import resources
import sessions
import statefeed


# ------------------------------------------------------------------ binary
def test_asset_name_uses_unknown_machine_verbatim(monkeypatch):
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "ppc64le")
    assert bootstrap.asset_name() == "cloudflared-linux-ppc64le"


# ------------------------------------------------------------------ config
def test_secret_reader_survives_a_broken_secrets_store(monkeypatch):
    real_import = builtins.__import__
    def failing(name, *a, **kw):
        if name == "streamlit": raise RuntimeError("no script run")
        return real_import(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", failing)
    assert bootstrap._secret("ANY") is None


def test_token_files_honours_an_explicit_environment_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CARD_TOKEN_FILE", str(tmp_path / "custom"))
    assert Path(tmp_path / "custom") in bootstrap.token_files()


def test_write_token_file_overwrites_an_existing_file(tmp_path):
    path = bootstrap.write_token_file("one", tmp_path)
    path.write_text("two")  # identical -> the chmod branch
    path = bootstrap.write_token_file("two", tmp_path)
    assert path.read_text().strip() == "two"


# ------------------------------------------------------------------ probes
def test_our_tunnels_returns_empty_without_a_proc_mount(monkeypatch):
    import core.bootstrap.probes as layer
    class NoProc:
        def is_dir(self): return False
    monkeypatch.setattr(layer, "Path", lambda arg: NoProc())
    assert layer.our_tunnels(Path("/tmp")) == []


# ------------------------------------------------------------------- geo
def test_shard_survives_a_network_failure(monkeypatch):
    def broken(url): raise OSError("offline")
    monkeypatch.setattr(geo, "_fetch", broken)
    geo.shard.cache_clear()
    assert geo.shard("https://example.streamlit.app") == ""


# ------------------------------------------------------------ demo formats
def test_duration_and_ago_edge_branches():
    from demo_data import ago, duration
    assert "h" in duration(2 * 3600 + 5 * 60)
    assert "s ago" in ago(30)
    assert "h ago" in ago(3600 * 3)
    assert "d ago" in ago(3600 * 48)


# --------------------------------------------------------------- collector
def test_collector_sample_without_a_pid_reports_not_found(monkeypatch):
    monkeypatch.setattr(live, "find_pid", lambda: None)
    col = live.Collector()
    assert col.sample() == {"found": False}
    col.pid = 999999
    assert col.sample() == {"found": False}


def test_window_pads_an_empty_series():
    col = live.Collector()
    assert col._series([]) == [0.0] * live.SAMPLES


def test_state_notes_register_errors_and_skips_geo_misses(monkeypatch):
    col = live.Collector()
    col.pid = 4242
    col._metrics = {"register_fail": 3}
    col._ready = {"ready": 0}
    col._log = {"conns": [{"ip": "127.0.0.1", "location": ""}], "reason": ""}
    col._trace = {}
    monkeypatch.setattr(geo, "lookup", lambda ip: {})
    state = col.state()
    assert "register errors" in state["note"]
    assert state["edges_geo"] == []
    col._log = {"conns": [], "reason": "token rejected"}
    assert col.state()["note"] == "token rejected"


# ------------------------------------------------------------------ proc
def test_save_launch_swallows_a_write_failure(monkeypatch, tmp_path):
    import core.live.proc as layer
    monkeypatch.setenv("CARD_LAUNCH_FILE", str(tmp_path / "launch.json"))
    real = Path.write_text
    def broken(self, *a, **kw): raise OSError("readonly")
    monkeypatch.setattr(layer.Path, "write_text", broken)
    layer.save_launch(["cloudflared", "tunnel", "run"])
    monkeypatch.setattr(Path, "write_text", real)


# --------------------------------------------------------------- sources
def test_line_epoch_rejects_a_bad_timestamp():
    assert live.line_epoch('{"time":"nonsense"}') is None


def test_parse_log_keeps_loose_pairs_without_a_slot():
    result = live.parse_log(["ip=1.2.3.4 location=sea01"])
    assert result["conns"][0]["location"] == "sea01"


# ------------------------------------------------------------- resources
def test_memory_limit_accepts_cgroup_v1(tmp_path, monkeypatch):
    v2, v1 = tmp_path / "max", tmp_path / "v1"
    v2.write_text("garbage")
    v1.write_text("536870912")
    monkeypatch.setattr(resources, "MEMORY_MAX", v2)
    monkeypatch.setattr(resources, "MEMORY_LIMIT_V1", v1)
    assert resources.memory_limit_bytes() == 536870912


# -------------------------------------------------------------- sessions
def test_session_counters_ignore_malformed_proc_rows(tmp_path, monkeypatch):
    table = tmp_path / "tcp"
    table.write_text("""\
  sl  local_address rem_address   st queue tr tm ret uid timeout inode
   1: bad bad 01 0 0 0 0 0 111
   2: bad 0100007F:2135 01 0 0 0 0 0 111
""")
    import core.sessions as layer
    monkeypatch.setattr(layer, "PROC", tmp_path)
    monkeypatch.setattr(layer, "TABLES", ("tcp",))
    monkeypatch.setattr(layer, "socket_inodes", lambda pid: {"111"})
    assert layer.active_sessions(7, 8501) == 1  # one malformed row skipped
    assert layer.ingress_sessions(8501) == 0


def test_session_rows_are_skipped_when_the_table_is_missing(tmp_path, monkeypatch):
    import core.sessions as layer
    monkeypatch.setattr(layer, "PROC", tmp_path)
    assert list(layer._rows("net/tcp")) == []


# ------------------------------------------------------------- statefeed
def test_write_state_refuses_an_oversized_payload(tmp_path):
    try:
        statefeed.write_state({"blob": "x" * (statefeed.STATE_SIZE + 10)}, tmp_path / "s.json")
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("oversized payload must be refused")


def test_sampler_stop_and_idle_payload(tmp_path):
    sam = statefeed.Sampler(directory=tmp_path)
    sam.stop()
    assert statefeed.idle_payload()["found"] is False


def test_act_survives_a_failing_refresh(monkeypatch, tmp_path):
    sam = statefeed.Sampler(directory=tmp_path)
    def boom(): raise RuntimeError("bad probe")
    monkeypatch.setattr(sam, "refresh", boom)
    result = sam.act("start_or_restart")
    assert "RuntimeError" in sam.last_error and "ok" in result


def test_sampler_singleton_updates_its_port(monkeypatch):
    statefeed._sampler = None
    first = statefeed.sampler(8501)
    second = statefeed.sampler(8600)
    assert second is first and first.port == 8600
    first.stop()
    statefeed._sampler = None


def test_write_state_cleans_up_a_partial_file(tmp_path, monkeypatch):
    import statefeed as feed
    real = os.replace
    def broken(a, b): raise OSError("disk full")
    monkeypatch.setattr(feed.os, "replace", broken)
    try:
        feed.write_state({"found": False}, tmp_path / "state.json")
    except OSError:
        pass
    assert list(tmp_path.glob("state.json.*.new")) == []
    monkeypatch.setattr(feed.os, "replace", real)

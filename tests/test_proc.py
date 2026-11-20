"""What the card reads, read back: /proc parsing, discovery, rate maths.

Every test here answers "does this field mean what the card says it means", with
the expected numbers written out rather than compared against the code.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

import demo_data
import live
from conftest import stat_line

REDESIGN = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ /proc/stat
def test_stat_fields_follow_proc_naming(fake_proc):
    stat = live.proc_stat(4242)
    assert stat == {
        "pid": 4242,
        "comm": "cloudflared",
        "state": "S",
        "utime": 7,
        "stime": 4,
        "threads": 13,
        "starttime": 2606026,
    }


def test_stat_survives_a_command_with_spaces_and_brackets(fake_proc):
    """(sshd: /usr/sbin/sshd -D [listener]) is legal and breaks a naive split."""
    (fake_proc / "4242" / "stat").write_text(
        stat_line(comm="sshd: /usr/sbin/sshd -D [listener]", utime=11, stime=22)
    )
    stat = live.proc_stat(4242)
    assert stat["comm"] == "sshd: /usr/sbin/sshd -D [listener]"
    assert stat["utime"] == 11 and stat["stime"] == 22


def test_rss_is_resident_pages_times_page_size(fake_proc):
    assert live.proc_rss(4242) == 10990 * live.PAGE_SIZE
    assert round(live.proc_rss(4242) / 1024**2, 1) == pytest.approx(
        round(10990 * 4096 / 1024**2, 1)
    )


def test_age_is_boot_time_minus_start_tick(fake_proc):
    # 26151.83 s of uptime minus 2606026 ticks / 100 = 26060.26 s of age
    assert live.proc_age(4242) == pytest.approx(91.57, abs=0.01)


def test_missing_process_reads_as_none(fake_proc):
    assert live.proc_stat(999) is None
    assert live.proc_rss(999) is None
    assert live.proc_exe(999) is None
    assert live.proc_age(999) is None


def test_state_letter_becomes_a_word(fake_proc):
    assert live.proc_state(4242) == "sleeping"
    (fake_proc / "4242" / "stat").write_text(stat_line(state="R"))
    assert live.proc_state(4242) == "running"


# ------------------------------------------------------------------ CPU rate
@pytest.mark.parametrize(
    "previous,ticks,now,age,expected",
    [
        ((100.0, 10), 20, 101.0, 50.0, 10.0),   # 10 ticks = 0.1 s of CPU in 1 s
        ((100.0, 10), 110, 101.0, 50.0, 100.0),  # a full core for one second
        ((100.0, 10), 10, 100.5, 50.0, 0.0),     # idle
        (None, 11, 100.0, 100.0, 0.11),          # first sample: lifetime average
        (None, 11, 100.0, None, 0.0),            # no age, no claim
    ],
)
def test_cpu_rate(previous, ticks, now, age, expected):
    assert live.cpu_rate(previous, ticks, now, age) == pytest.approx(expected, abs=0.01)


def test_cpu_rate_counts_utime_and_stime(fake_proc):
    """A process that burns user and system time is one number, not two."""
    collector = live.Collector(metrics_url="")
    collector.sample()
    (fake_proc / "4242" / "stat").write_text(stat_line(utime=107, stime=104))
    import time as _time

    collector._last_cpu = (_time.monotonic() - 1.0, 11)
    collector.sample()
    assert collector.cpu[-1] == pytest.approx(200.0, abs=0.5)  # 100+100 ticks in 1 s


# ------------------------------------------------------------- CPU in nanos
def _thread(fake_proc, tid: int, run_ns: int, wait_ns: int = 0) -> None:
    task = fake_proc / "4242" / "task" / str(tid)
    task.mkdir(parents=True, exist_ok=True)
    (task / "schedstat").write_text(f"{run_ns} {wait_ns} 42\n")


def test_cpu_ns_sums_every_thread(fake_proc):
    """Ticks in /proc/stat count all threads, so the nanosecond counter must
    sum them too — the main thread alone would report a fourteenth of the cost."""
    _thread(fake_proc, 4242, 1_500_000)
    _thread(fake_proc, 4243, 2_250_000)
    assert live.proc_cpu_ns(4242) == 3_750_000


def test_cpu_ns_without_task_tree_is_none(fake_proc):
    assert live.proc_cpu_ns(4242) is None
    assert live.proc_cpu_ns(999) is None


def test_cpu_ns_skips_a_thread_that_vanished(fake_proc):
    _thread(fake_proc, 4242, 1_000_000)
    _thread(fake_proc, 4243, 500_000)
    (fake_proc / "4242" / "task" / "4243" / "schedstat").unlink()
    assert live.proc_cpu_ns(4242) == 1_000_000


@pytest.mark.parametrize(
    "previous,ns,now,age,expected",
    [
        ((100.0, 0), 5_000_000, 101.0, 50.0, 0.5),      # 5 ms in 1 s: half a percent
        ((100.0, 0), 0, 101.0, 50.0, 0.0),              # idle
        ((100.0, 1_000_000_000), 1_100_000_000, 101.0, 50.0, 10.0),  # 100 ms in 1 s
        ((100.0, 5_000_000), 4_000_000, 101.0, 50.0, 0.0),  # counter went back
        (None, 2_500_000_000, 100.0, 500.0, 0.5),       # first sample: lifetime
        (None, 2_500_000_000, 100.0, None, 0.0),        # no age, no claim
        (None, None, 100.0, 10.0, 0.0),                 # no counter, no claim
    ],
)
def test_cpu_rate_ns(previous, ns, now, age, expected):
    assert live.cpu_rate_ns(previous, ns, now, age) == pytest.approx(expected, abs=0.001)


def test_nanoseconds_resolve_what_ticks_cannot(fake_proc):
    """0.3 % is invisible to a 10 ms tick counter and plain in schedstat."""
    _thread(fake_proc, 4242, 1_000_000)
    collector = live.Collector(metrics_url="")
    collector.sample()
    _thread(fake_proc, 4242, 4_000_000)          # 3 ms more
    import time as _time

    collector._last_cpu_ns = (_time.monotonic() - 1.0, 1_000_000)
    collector.sample()
    assert collector.cpu[-1] == pytest.approx(0.3, abs=0.02)   # not 0.0, not 1.0


def test_cpu_text_keeps_two_decimals_below_one():
    assert live.cpu_text(0.32) == "0.32%"
    assert live.cpu_text(0.04) == "0.04%"
    assert live.cpu_text(0.0) == "0.0%"
    assert live.cpu_text(2.5) == "2.5%"


def test_a_sub_percent_reading_reaches_the_card(fake_proc, monkeypatch):
    """The tile, its share and the sparkline all keep the small number."""
    import card

    ready = '{"status":200,"readyConnections":4,"connectorId":"d5ca80e2-6bbd-49b4-812d-3fa1466eb06b"}'
    monkeypatch.setattr(live, "_http", lambda url: ready if url.endswith("/ready") else None)
    _thread(fake_proc, 4242, 1_000_000)
    collector = live.Collector(metrics_url="http://127.0.0.1:20241")
    collector.sample()
    _thread(fake_proc, 4242, 4_000_000)
    import time as _time

    collector._last_cpu_ns = (_time.monotonic() - 1.0, 1_000_000)
    collector.sample()
    state = collector.state()
    assert state["cpu_text"] == "0.30%"
    cores = live.available_cores()
    assert state["cpu_share"] == f"{0.30 / cores:.2f}% of {cores} cores"
    page = card.render(state, theme="dark")
    assert "0.30%" in page and re.search(r"of [\d.]+ cores", page)


# --------------------------------------------------------------- process find
def test_is_tunnel_run_accepts_both_flag_orders():
    assert live.is_tunnel_run(["cloudflared", "tunnel", "run", "--token", "x"])
    assert live.is_tunnel_run(
        ["/tmp/user/1000/opencode/cloudflared", "tunnel", "--metrics", "127.0.0.1:1", "run", "--token-file", "t"]
    )


@pytest.mark.parametrize(
    "args",
    [
        ["cloudflared", "tunnel", "create", "x"],
        ["cloudflared", "tunnel", "info", "7bd4"],
        ["cloudflared", "version"],
        ["python3", "-m", "streamlit", "run", "main.py"],
        ["zsh", "-c", "cloudflared tunnel run"],
    ],
)
def test_is_tunnel_run_rejects_other_commands(args):
    assert not live.is_tunnel_run(args)


def test_scan_finds_the_connector_and_ignores_the_shell(fake_proc):
    (fake_proc / "1").mkdir()
    (fake_proc / "1" / "cmdline").write_text("/usr/bin/zsh\0-c\0cloudflared tunnel run\0")
    assert [pid for pid, _ in live.scan_pids()] == [4242]


def test_find_pid_prefers_an_explicit_pid(fake_proc):
    assert live.find_pid(pid=4242) == 4242
    assert live.find_pid(pid=999) == 4242  # falls through to the scan
    pidfile = fake_proc.parent / "cf.pid"
    pidfile.write_text("4242\n")
    assert live.find_pid(pid=999, pidfile=str(pidfile)) == 4242


# ---------------------------------------------------------------- http sources
METRICS = """\
# HELP build_info Build and version information
# TYPE build_info gauge
build_info{goversion="go1.27.4",revision="2026-11-17-15:04 UTC",type="",version="2026.11.1"} 1
# HELP cloudflared_tunnel_ha_connections Number of active ha connections
# TYPE cloudflared_tunnel_ha_connections gauge
cloudflared_tunnel_ha_connections 1
# TYPE cloudflared_tunnel_total_requests counter
cloudflared_tunnel_total_requests 12
# TYPE cloudflared_tunnel_tunnel_register_fail counter
cloudflared_tunnel_tunnel_register_fail{error="server_error",rpcName="registerConnection"} 5
cloudflared_tunnel_tunnel_register_fail{error="timeout",rpcName="registerConnection"} 2
"""


def test_metrics_parser_reads_version_and_sums_register_failures():
    parsed = live.parse_metrics(METRICS)
    assert parsed["version"] == "2026.11.1"
    assert parsed["register_fail"] == 7  # both label sets, summed
    assert parsed["ha_connections"] == 1
    assert parsed["requests"] == 12


def test_ready_parser_reads_the_503_body():
    """/ready answers 503 with a body; that body is the measurement."""
    parsed = live.parse_ready('{"status":503,"readyConnections":0,"connectorId":"db0c87c0-461a"}')
    assert parsed == {"ready": 0, "connector": "db0c87c0-461a", "status": 503}


def test_http_reads_the_body_of_an_error_status(monkeypatch):
    """urllib raises on 503; the collector needs the body anyway."""
    import io
    import urllib.error

    class FakeError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("u", 503, "no", {}, io.BytesIO(b'{"readyConnections":0}'))

    def boom(url, timeout=0):
        raise FakeError()

    monkeypatch.setattr(live.urllib.request, "urlopen", boom)
    assert live._http("http://127.0.0.1:1/ready") == '{"readyConnections":0}'


def test_trace_parser():
    trace = "fl=1\nip=91.247.168.135\ncolo=FRA\nloc=TR\n"
    assert live.parse_trace(trace)["ip"] == "91.247.168.135"
    assert live.parse_trace(trace)["colo"] == "FRA"


def test_log_json_lines_give_edges_and_the_reason():
    lines = [
        '{"level":"info","message":"Starting tunnel"}',
        '{"level":"error","event":0,"ip":"198.41.192.27","connIndex":0,'
        '"error":"Unauthorized: Invalid tunnel secret","message":"Register tunnel error from server side"}',
        '{"level":"info","event":0,"ip":"198.41.192.27","connIndex":0}',
        '{"level":"info","event":0,"ip":"198.41.200.13","connIndex":1}',
    ]
    parsed = live.parse_log(lines)
    assert parsed["edges"] == ["198.41.192.27", "198.41.200.13"]
    assert parsed["reason"] == "Unauthorized: Invalid tunnel secret"


def test_log_text_lines_still_parse():
    lines = [
        '2026-11-19T22:26:12Z ERR Register tunnel error from server side '
        'error="Unauthorized: Invalid tunnel secret" connIndex=0 ip=198.41.200.13'
    ]
    parsed = live.parse_log(lines)
    assert parsed["edges"] == ["198.41.200.13"]
    assert parsed["reason"] == "Unauthorized: Invalid tunnel secret"


def test_slots_do_not_become_phantom_connections():
    """Each connection logs a line with a location and one without; the slot is
    the unit, so four connections never read as eight."""
    lines = [
        "2026-11-19T22:50:58Z INF Tunnel connection curve preferences: [X25519] connIndex=0 event=0 ip=198.41.200.113",
        "2026-11-19T22:50:58Z INF Registered tunnel connection connIndex=0 connection=a event=0 ip=198.41.200.113 location=fra15 protocol=quic",
        "2026-11-19T22:50:58Z INF Tunnel connection curve preferences: [X25519] connIndex=1 event=0 ip=198.41.192.227",
        "2026-11-19T22:50:58Z INF Registered tunnel connection connIndex=1 connection=b event=0 ip=198.41.192.227 location=fra08 protocol=quic",
        "2026-11-19T22:51:00Z INF Tunnel connection curve preferences: [X25519] connIndex=2 event=0 ip=198.41.192.167",
        "2026-11-19T22:51:00Z INF Registered tunnel connection connIndex=2 connection=c event=0 ip=198.41.192.167 location=fra03 protocol=quic",
        "2026-11-19T22:51:00Z INF Tunnel connection curve preferences: [X25519] connIndex=3 event=0 ip=198.41.200.193",
        "2026-11-19T22:51:00Z INF Registered tunnel connection connIndex=3 connection=d event=0 ip=198.41.200.193 location=fra16 protocol=quic",
    ]
    parsed = live.parse_log(lines)
    assert [conn["location"] for conn in parsed["conns"]] == ["fra15", "fra08", "fra03", "fra16"]
    assert [conn["ip"] for conn in parsed["conns"]] == [
        "198.41.200.113", "198.41.192.227", "198.41.192.167", "198.41.200.193"
    ]
    assert parsed["reason"] is None


def test_the_chip_shows_the_real_colos(fake_proc, monkeypatch):
    """The mockup had sea11, sea06…; the real chip is the connector's own colos."""
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    lines = [
        f"{now}Z INF Starting tunnel tunnelID=7bd4235b-ec9b-434d-8478-18aec2ecb0c2",
        f"{now}Z INF Registered tunnel connection connIndex=0 connection=a event=0 ip=198.41.200.113 location=fra15 protocol=quic",
        f"{now}Z INF Registered tunnel connection connIndex=2 connection=c event=0 ip=198.41.192.167 location=fra03 protocol=quic",
    ]
    monkeypatch.setattr(live, "read_tail", lambda path, lines=400: lines_[:])
    lines_ = lines
    monkeypatch.setattr(live, "log_path", lambda pid: "/dev/null")
    monkeypatch.setattr(live, "_http", lambda url: '{"status":200,"readyConnections":4,"connectorId":"fe34ed81-c118-49ad-8105-05f79ff4c4c3"}' if url.endswith("/ready") else None)
    collector = live.Collector(metrics_url="http://127.0.0.1:20241")
    collector.sample()
    state = collector.state()
    assert state["edge_names"] == "fra15, fra03"
    assert state["live"] == 4 and state["connected"] is True
    assert [edge["code"] for edge in state["edges"]] == ["fra15", "fra03"]


def test_a_previous_sessions_error_is_not_the_current_reason():
    """cf.log is appended to across runs: the last `Starting tunnel` is the line
    between another process's failure and ours."""
    lines = [
        '{"level":"error","event":0,"ip":"198.41.192.227","connIndex":0,'
        '"error":"Unauthorized: Invalid tunnel secret","message":"Register tunnel error from server side"}',
        "2026-11-19T22:55:46Z INF Starting tunnel tunnelID=7bd4235b-ec9b-434d-8478-18aec2ecb0c2",
        "2026-11-19T22:55:47Z INF Registered tunnel connection connIndex=0 connection=a event=0 ip=198.41.192.107 location=fra08 protocol=quic",
        "2026-11-19T22:55:47Z INF Registered tunnel connection connIndex=1 connection=b event=0 ip=198.41.200.113 location=ist03 protocol=quic",
    ]
    parsed = live.parse_log(lines)
    assert parsed["reason"] is None
    assert [conn["location"] for conn in parsed["conns"]] == ["fra08", "ist03"]


def test_since_drops_lines_from_before_the_process_started():
    lines = [
        '{"level":"error","ip":"198.41.192.227","connIndex":0,'
        '"error":"Unauthorized: Invalid tunnel secret","message":"Register tunnel error from server side",'
        '"time":"2026-11-19T22:26:30Z"}',
        '{"level":"info","ip":"198.41.200.113","connIndex":1,"location":"ist03",'
        '"time":"2026-11-19T22:55:47Z","message":"Registered tunnel connection"}',
    ]
    since = live.line_epoch('{"time":"2026-11-19T22:55:46Z"}')
    parsed = live.parse_log(lines, since=since)
    assert parsed["reason"] is None
    assert parsed["conns"] == [{"location": "ist03", "ip": "198.41.200.113"}]


def test_line_epoch_reads_json_and_text_stamps():
    assert live.line_epoch('{"time":"2026-11-19T22:55:47Z","message":"x"}') == live.line_epoch(
        "2026-11-19T22:55:47Z INF x"
    )
    assert live.line_epoch("no stamp here") is None


def test_log_path_is_read_from_the_command_line(monkeypatch, tmp_path):
    log = tmp_path / "cf.log"
    log.write_text("x\n")
    monkeypatch.setattr(live, "proc_cmdline", lambda pid: ["cloudflared", "tunnel", "--logfile", str(log), "run"])
    assert live.log_path(1) == str(log)


def test_log_path_falls_back_to_stdout_when_redirected(monkeypatch, tmp_path):
    """`cloudflared tunnel run … > cf.log` carries no --logfile: the fd does."""
    log = tmp_path / "cf-plain.log"
    log.write_text("x\n")
    monkeypatch.setattr(live, "proc_cmdline", lambda pid: ["cloudflared", "tunnel", "run", "--token", "…"])
    monkeypatch.setattr(live.os, "readlink", lambda path: str(log))
    assert live.log_path(525123) == str(log)


def test_a_pipe_is_not_a_log(monkeypatch):
    monkeypatch.setattr(live, "proc_cmdline", lambda pid: ["cloudflared", "tunnel", "run"])
    monkeypatch.setattr(live.os, "readlink", lambda path: "pipe:[123456]")
    monkeypatch.delenv("CLOUDFLARED_LOG", raising=False)
    assert live.log_path(525123) is None


# ------------------------------------------------------------------- the state
def test_collector_builds_a_state_the_card_can_render(fake_proc, monkeypatch):
    monkeypatch.setattr(live, "_http", lambda url: None)
    collector = live.Collector(metrics_url="")
    assert collector.sample()["found"] is True
    state = collector.state()

    assert state["pid"] == 4242
    assert state["path"].endswith("/cloudflared")
    assert state["mem_text"] == f"{10990 * live.PAGE_SIZE / 1024**2:.1f} MB"
    assert state["uptime"].endswith("s")  # 91.57 s -> "1m 31s"
    assert state["live"] == 0 and state["connected"] is False
    assert state["source"] == "live"
    assert len(state["cpu_series"]) == live.SAMPLES == len(state["mem_series"])
    assert all(level in ("ok", "warm", "hot", "crit") for level in state["cpu_levels"])


def test_card_keys_exist_in_both_feeds(fake_proc, monkeypatch):
    """A guard against drift: what card.py reads must exist in both states."""
    text = (REDESIGN / "card.py").read_text()
    literal = set(re.findall(r'state\["([a-z_]+)"\]', text))
    templated = set(re.findall(r"state\[f['\"]([a-z_]+)\{key\}", text))
    required = literal | {f"{key}_{suffix}" for key in ("cpu", "mem") for suffix in templated}

    sample = demo_data.build_state(0)
    monkeypatch.setattr(live, "_http", lambda url: None)
    collector = live.Collector(metrics_url="")
    collector.sample()
    real = collector.state()

    assert required <= set(sample), required - set(sample)
    assert required <= set(real), required - set(real)


def test_state_and_payload_never_carry_the_token(fake_proc, monkeypatch):
    """The collector reads cmdlines; a token passed with --token must not leak
    into the state file, the footer or the HTML."""
    import statefeed

    token = "eyJhIjogIjAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwIiwgInQiOiAidGVzdC10dW5uZWwiLCAicyI6ICJ0ZXN0In0"  # a token-shaped string, not the real one
    (fake_proc / "4242" / "cmdline").write_text(f"cloudflared\0tunnel\0run\0--token\0{token}\0")
    monkeypatch.setattr(live, "_http", lambda url: None)
    collector = live.Collector(metrics_url="")
    collector.sample()
    state = collector.state()
    payload = statefeed.to_payload(state)

    import card

    page = card.render(state, theme="dark")
    for blob in (json.dumps(state), json.dumps(payload), page):
        assert token not in blob


def test_window_pads_with_its_first_value_not_noise(fake_proc, monkeypatch):
    """A young window is a flat line: 'no data yet' is not a shape."""
    monkeypatch.setattr(live, "_http", lambda url: None)
    collector = live.Collector(metrics_url="")
    collector.sample()
    series = collector.state()["cpu_series"]
    assert series[-1] == pytest.approx(series[0])


def test_four_ready_edges_render_as_a_healthy_card(fake_proc, monkeypatch):
    """The success path, without a valid token: a /ready body with 4 connections."""
    import card

    ready = '{"status":200,"readyConnections":4,"connectorId":"31563bf4-b6db-4774-b5f8-8590356ddf17"}'
    metrics = 'build_info{goversion="go1.27.4",revision="x",type="",version="2026.11.1"} 1\n'

    def http(url):
        if url.endswith("/ready"):
            return ready
        if url.endswith("/metrics"):
            return metrics
        return None

    monkeypatch.setattr(live, "_http", http)
    collector = live.Collector(metrics_url="http://127.0.0.1:20241")
    collector.sample()
    state = collector.state()

    assert state["live"] == 4 and state["connected"] is True
    assert state["note"] == ""
    assert state["connector"].startswith("31563bf4")

    page = card.render(state, theme="dark")
    assert "4 of 4 connections live" in page
    assert 'data-status="up"' in page
    assert "data-note hidden" in page
    assert re.search(r"of [\d.]+ cores", page) and "MB" in page


def test_a_zombie_reads_as_gone_not_as_a_running_connector(fake_proc, monkeypatch):
    """A stopped connector stays in /proc until it is reaped.

    Reading that entry as "found" is how the panel froze at "0 of 4 connections
    live" with the power button stuck on "Stop": the process was dead, but
    ``/proc/<pid>`` was still there.
    """
    from conftest import stat_line

    collector = live.Collector(metrics_url="")
    assert collector.sample()["found"] is True
    assert collector.pid == 4242

    (fake_proc / "4242" / "stat").write_text(stat_line(state="Z"))
    assert collector.sample(pid=4242)["found"] is False
    assert collector.pid is None
    assert collector.state()["found"] is False

    # and a fresh process is picked up again, with counters that start over
    (fake_proc / "4242" / "stat").write_text(stat_line(state="S", utime=1, stime=0))
    assert collector.sample(pid=4242)["found"] is True
    assert collector.pid == 4242


def test_idle_state_claims_nothing():
    """No process: dashes and a flat line, never the sample numbers."""
    import card

    state = live.idle_state()
    assert state["found"] is False and state["live"] == 0
    assert state["uptime"] == "—" and state["version"] == "—" and state["path"] == "—"
    assert state["cpu_text"] == "—" and state["mem_text"] == "—"
    assert state["source_label"] == "live · no cloudflared process"
    assert state["gaps"] == ["process"]
    assert set(state["cpu_series"]) == {0.0} and len(state["cpu_series"]) == live.SAMPLES

    page = card.render(state, theme="dark")
    assert "no cloudflared process" in page
    assert ">17.4%<" not in page  # the sample workload is not borrowed


def test_missing_sources_are_named_as_gaps(fake_proc, monkeypatch):
    monkeypatch.setattr(live, "_http", lambda url: None)
    collector = live.Collector(metrics_url="")
    collector.sample()
    gaps = collector.state()["gaps"]
    assert "metrics endpoint" in gaps and "/ready" in gaps


def test_memory_budget_falls_back_to_the_panel_convention(fake_proc, monkeypatch):
    monkeypatch.setattr(live, "_http", lambda url: None)
    collector = live.Collector(metrics_url="")
    collector.sample()
    state = collector.state()
    assert state["mem_budget"] == "256 MB"  # the cgroup here says "max"
    assert state["mem_scale"] == 256.0


def test_a_tokenless_quick_tunnel_is_a_real_connector():
    """The deployed app starts `cloudflared tunnel --url ...`, with no `run`
    subcommand. It is still the connector the card must read."""
    assert live.is_tunnel_run([
        "/mount/src/app/.temp/cloudflared", "tunnel",
        "--url", "http://127.0.0.1:8598",
        "--metrics", "127.0.0.1:20241",
    ])
    # and the old token form still counts
    assert live.is_tunnel_run([
        "cloudflared", "tunnel", "--metrics", "127.0.0.1:20241", "run",
        "--token-file", "/tmp/cf-token",
    ])
    # an unrelated cloudflared command is not a connector
    assert not live.is_tunnel_run(["cloudflared", "--version"])


def test_metrics_url_follows_the_connectors_own_listener():
    """bootstrap takes a free port when 20241 is held, so the sampler must read
    the address from the cmdline instead of the default."""
    assert live.metrics_url_for(
        ["/x/cloudflared", "tunnel", "--url", "http://127.0.0.1:8598",
         "--metrics", "127.0.0.1:39921"]) == "http://127.0.0.1:39921"
    assert live.metrics_url_for(
        ["/x/cloudflared", "tunnel", "--metrics=127.0.0.1:20241", "run"]) == \
        "http://127.0.0.1:20241"
    assert live.metrics_url_for(["/x/cloudflared", "tunnel", "run"]) is None


def test_each_edge_is_named_once():
    """Four connections on two edges must not read "sea01, sea07, sea07, sea01".

    The edges are read from the connector's own log (which lists one connection
    per line), so the seam under test is the parsed log, not HTTP.
    """
    collector = live.Collector(metrics_url="")
    # a real pid this test process owns, so state() takes its live branch
    collector.pid = os.getpid()
    collector._ready = {"status": 200, "ready": 4, "connector": "c-1"}
    collector._log = {"conns": [
        {"location": "sea01", "ip": "1.1.1.1"}, {"location": "sea07", "ip": "2.2.2.2"},
        {"location": "sea07", "ip": "2.2.2.2"}, {"location": "sea01", "ip": "1.1.1.1"},
    ]}
    collector.cpu.append(0.4)
    collector.mem.append(41.0)
    state = collector.state()
    assert state["live"] == 4
    assert state["edge_names"] == "sea01, sea07", state["edge_names"]
    assert [edge["code"] for edge in state["edges"]] == ["sea01", "sea07"]

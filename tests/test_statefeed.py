"""The card's data channel: one JSON file Streamlit serves, one writer.

Nothing here talks to a network. What is pinned down:

* the payload is compact and carries a heartbeat, so the page can tell "live"
  from "the sampler stopped" instead of freezing on the last numbers;
* a write replaces the file atomically — a reader never sees half a payload;
* with no connector the payload says ``found: false`` rather than reading
  whatever cloudflared happens to be on the host;
* the verbs go through :mod:`bootstrap`, and a failed verb is reported as
  failed (the old buttons answered "ok" and left the card stale).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import bootstrap
import statefeed


def full_state(**over):
    state = {
        "found": True,
        "host": {"text": "16 cores"},
        "connected": True,
        "live": 4,
        "note": "",
        "edges": [],
        "edge_names": "sea10",
        "edge_ip": "203.0.113.9",
        "connector": "bc7454a2-82d2-437b-8254-538fde5c1b34",
        "cpu": 0.53,
        "cpu_text": "0.53%",
        "cpu_share": "0.53% of one core",
        "mem": 41.8,
        "mem_text": "41.8 MB",
        "mem_scale": 256.0,
        "uptime": "4m 3s",
        "proc_state": "running",
        "pid": 920,
        "version": "2026.11.1",
        "path": "/app/.temp/cloudflared",
        "source": "live",
        "source_label": "live · pid 920",
        "register_fail": 0,
        "samples": 48,
    }
    state.update(over)
    return state


TUNNEL = {"ok": True, "mode": "named", "pid": 920, "ready": 4,
          "public": "https://streamlit.idealis.cc"}


# --------------------------------------------------------------- the payload
def test_the_payload_carries_what_the_card_paints():
    payload = statefeed.to_payload(full_state(), None, TUNNEL)
    assert payload["found"] is True
    assert payload["pid"] == 920
    assert payload["status_text"] == "4 of 4 connections live"
    assert payload["status_state"] == "up"
    assert payload["cpu"] == 0.53 and payload["mem"] == 41.8
    assert payload["tunnel"] == {"ok": True, "mode": "named", "ready": 4,
                                 "public": "https://streamlit.idealis.cc",
                                 "intent": "on"}
    # nothing markup-shaped or secret-shaped can ride along
    assert "eyJ" not in json.dumps(payload)
    assert "<" not in json.dumps(payload)


def test_a_down_connector_says_down_not_up():
    payload = statefeed.to_payload(full_state(live=0, connected=False),
                                   None, dict(TUNNEL, ok=False, ready=0))
    assert payload["status_text"] == "0 of 4 connections live"
    assert payload["status_state"] == "down"


def test_no_connector_is_a_state_not_a_guess():
    payload = statefeed.to_payload({"found": False}, None,
                                   {"ok": False, "mode": "none", "ready": 0,
                                    "public": None})
    assert payload == {"found": False, "tunnel": {"ok": False, "mode": "none",
                                                  "pid": None, "ready": 0,
                                                  "public": None,
                                                  "intent": "on"}}


def test_the_last_action_rides_with_the_payload():
    action = {"ok": True, "action": "stop", "stopped": [920]}
    payload = statefeed.to_payload({"found": False}, action, None)
    assert payload["last_action"] == action


# --------------------------------------------------------------- the file
def test_a_write_is_atomic_and_readable(tmp_path):
    target = statefeed.state_path(tmp_path)
    statefeed.write_state({"found": True, "ts": 1.5}, target)
    assert json.loads(target.read_text()) == {"found": True, "ts": 1.5}
    statefeed.write_state({"found": False, "ts": 2.5}, target)
    assert statefeed.read_state(target) == {"found": False, "ts": 2.5}
    # the temporary file is renamed, not left behind
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_concurrent_writers_never_share_a_temporary_file(tmp_path, monkeypatch):
    """During a Cloud reboot two script processes can write the same second.
    A fixed "state.json.new" made the loser crash with FileNotFoundError in
    os.replace; every write must get its own inode."""
    seen = []
    original = statefeed.os.replace
    monkeypatch.setattr(statefeed.os, "replace",
                        lambda src, dst: (seen.append(str(src)), original(src, dst))[1])
    target = statefeed.state_path(tmp_path)
    statefeed.write_state({"found": True, "n": 1}, target)
    statefeed.write_state({"found": True, "n": 2}, target)
    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert all("state.json." in name and name.endswith(".new") for name in seen)
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_geo_and_placement_reach_the_browser_payload():
    state = full_state()
    state["origin_geo"] = {"cc": "US", "region": "Missouri"}
    state["edges_geo"] = [{"code": "sea01", "cc": "US"}]
    state["placement"] = "s4a-prod/us-west1-b"
    payload = statefeed.to_payload(state)
    assert payload["origin_geo"]["cc"] == "US"
    assert payload["edges_geo"][0]["cc"] == "US"
    assert payload["placement"] == "s4a-prod/us-west1-b"


def test_the_file_length_never_changes_between_replacements(tmp_path):
    """Starlette takes Content-Length from stat() before it streams the file.

    With a variable-length payload an os.replace landing in between made it
    answer "Response content shorter/longer than Content-Length" (reproduced:
    176 failures in 1200 GETs). Every write must be the same byte count, and
    the trailing padding must stay invisible to json.loads.
    """
    target = statefeed.state_path(tmp_path)
    sizes = set()
    for n in range(20):
        payload = {"found": True, "ts": n, "pad": "x" * (n % 37)}
        statefeed.write_state(payload, target)
        sizes.add(target.stat().st_size)
        assert statefeed.read_state(target) == payload
    assert sizes == {statefeed.STATE_SIZE}
    assert statefeed.STATE_SIZE < 65536, "a padded 1 Hz file must stay small"


def test_a_missing_file_reads_as_empty(tmp_path):
    assert statefeed.read_state(tmp_path / "nope.json") == {}


# --------------------------------------------------------------- the sampler
@pytest.fixture()
def sampler(tmp_path, monkeypatch):
    """A sampler wired to a canned process, writing into a temporary folder."""
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda port, directory=None: {
        "ok": True, "mode": "named", "pid": 920, "ready": 4,
        "public": "https://streamlit.idealis.cc", "extra": []})
    made = statefeed.Sampler(8501, interval=3600, directory=tmp_path)
    monkeypatch.setattr(made.collector, "sample", lambda pid=None: {"found": True})
    monkeypatch.setattr(made.collector, "state", lambda: full_state())
    return made


def test_one_refresh_publishes_a_fresh_file(sampler, tmp_path):
    before = time.time()
    payload = sampler.refresh()
    path = statefeed.state_path(tmp_path)
    on_disk = json.loads(path.read_text())
    assert on_disk == payload
    assert on_disk["pid"] == 920
    assert abs(on_disk["ts"] - before) < 5, "the heartbeat is the write time"
    assert on_disk["tunnel"]["public"] == "https://streamlit.idealis.cc"


def test_the_heartbeat_moves_forward(sampler):
    first = sampler.refresh()["ts"]
    time.sleep(0.01)
    second = sampler.refresh()["ts"]
    assert second > first


def test_no_connector_publishes_dashes_not_stale_numbers(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap, "tunnel_status",
                        lambda port, directory=None: {"ok": False, "mode": "none",
                                                      "pid": None, "ready": 0})
    made = statefeed.Sampler(8501, interval=3600, directory=tmp_path)
    payload = made.refresh()
    assert payload["found"] is False
    assert "cpu" not in payload and "status_text" not in payload


def test_the_sampler_never_takes_over_a_foreign_connector(sampler, monkeypatch):
    """An empty status must not fall back to scanning /proc: the app owns one
    process, and showing someone else's would be worse than showing nothing."""
    seen = []
    monkeypatch.setattr(sampler.collector, "sample", lambda pid=None: seen.append(pid))
    monkeypatch.setattr(bootstrap, "tunnel_status",
                        lambda port, directory=None: {"ok": False, "mode": "none",
                                                      "pid": None, "ready": 0})
    sampler.refresh()
    assert seen == []


# --------------------------------------------------------------- the verbs
def test_stop_stops_the_pids_the_status_reports(sampler, monkeypatch):
    stopped = []
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda port, directory=None: {
        "ok": True, "pid": 920, "ready": 4, "extra": [921], "public": "x"})
    monkeypatch.setattr(bootstrap, "stop_tunnel", lambda pids, directory=None: (
        stopped.extend(pids) or {"ok": True, "stopped": list(pids), "survivors": []}))
    result = sampler.act("stop")
    assert result["ok"] is True
    assert stopped == [920, 921]
    assert sampler.last_action["action"] == "stop"


def test_a_stop_that_does_not_stop_is_a_failure(sampler, monkeypatch):
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda port, directory=None: {
        "ok": True, "pid": 920, "ready": 4, "extra": [], "public": "x"})
    monkeypatch.setattr(bootstrap, "stop_tunnel", lambda pids, directory=None: {
        "ok": False, "stopped": [], "survivors": [920]})
    result = sampler.act("stop")
    assert result["ok"] is False
    assert "survived" in result["error"]


def test_restart_goes_through_the_start_verify_swap(sampler, monkeypatch):
    monkeypatch.setattr(bootstrap, "tunnel_status",
                        lambda port, directory=None: {"ok": False, "pid": None, "ready": 0})
    monkeypatch.setattr(bootstrap, "restart_tunnel",
                        lambda port, wait_s=30.0: bootstrap.Tunnel(
                            True, pid=1234, mode="named",
                            public_url="https://streamlit.idealis.cc"))
    result = sampler.act("restart")
    assert result["ok"] is True and result["pid"] == 1234


def test_start_uses_ensure_tunnel_with_force(sampler, monkeypatch):
    seen = {}

    def ensure(port, force=False, wait_s=30.0, verified=True):
        seen["force"] = force
        return bootstrap.Tunnel(True, pid=77, mode="named")

    monkeypatch.setattr(bootstrap, "tunnel_status",
                        lambda port, directory=None: {"ok": False, "pid": None, "ready": 0})
    monkeypatch.setattr(bootstrap, "ensure_tunnel", ensure)
    result = sampler.act("start")
    assert result["ok"] is True and result["pid"] == 77
    assert seen["force"] is True


def test_stop_is_remembered_so_a_rerun_cannot_undo_it(sampler, monkeypatch, tmp_path):
    """Every Streamlit interaction reruns the script and ``ensure_tunnel`` runs
    on every rerun — so an explicit stop has to be written down or the
    connector is back a second later."""
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda port, directory=None: {
        "ok": True, "pid": 920, "ready": 4, "extra": [], "public": "x"})
    monkeypatch.setattr(bootstrap, "stop_tunnel", lambda pids, directory=None: {
        "ok": True, "stopped": list(pids), "survivors": []})
    sampler.act("stop")
    assert bootstrap.desired_state(tmp_path) == "off"


def test_start_clears_the_remembered_stop(sampler, monkeypatch, tmp_path):
    bootstrap.set_desired("off", tmp_path)
    monkeypatch.setattr(bootstrap, "tunnel_status",
                        lambda port, directory=None: {"ok": False, "pid": None, "ready": 0})
    monkeypatch.setattr(bootstrap, "ensure_tunnel",
                        lambda port, force=False, wait_s=30.0, verified=True:
                        bootstrap.Tunnel(True, pid=5, mode="named"))
    sampler.act("start")
    assert bootstrap.desired_state(tmp_path) == "on"


def test_an_unknown_verb_is_refused(sampler):
    result = sampler.act("kill -9 1")
    assert result["ok"] is False
    assert "unknown action" in result["error"]


def test_the_process_has_one_sampler(monkeypatch, tmp_path):
    monkeypatch.setattr(statefeed, "_sampler", None)
    monkeypatch.setattr(statefeed.Sampler, "start", lambda self: self)
    first = statefeed.sampler(8501, directory=tmp_path)
    second = statefeed.sampler(8501, directory=tmp_path)
    assert first is second
    monkeypatch.setattr(statefeed, "_sampler", None)

"""The feed the page polls: payload shape, headers, and the two routes.

The card runs in an srcdoc iframe, whose origin is opaque, so the CORS header is
part of the contract and is tested as such.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import server


def sample_state(**overrides):
    state = {
        "found": True,
        "pid": 496320,
        "live": 0,
        "note": "Unauthorized: Invalid tunnel secret",
        "connected": False,
        "cpu": 0.08,
        "mem": 45.4,
        "mem_scale": 256.0,
        "uptime": "4m 31s",
        "version": "2026.11.1",
        "path": "/tmp/user/1000/opencode/cloudflared",
        "edge_ip": "91.247.168.135",
        "edge_names": "198.41.200.13 +1",
        "proc_state": "sleeping",
        "source_label": "live · pid 496320",
        "register_fail": 12,
    }
    state.update(overrides)
    return state


def test_payload_carries_what_the_page_paints():
    payload = server.to_payload(sample_state())
    assert payload["found"] is True
    assert payload["status_text"] == "0 of 4 connections live"
    assert payload["note"] == "Unauthorized: Invalid tunnel secret"
    assert payload["status_state"] == "down"
    assert payload["cpu"] == 0.08 and payload["mem"] == 45.4
    assert payload["edges"] == "198.41.200.13 +1"
    assert payload["origin"] == "91.247.168.135"
    assert payload["mem_scale"] == 256.0


def test_payload_reports_up_when_edges_are_live():
    payload = server.to_payload(sample_state(live=4, connected=True, note=""))
    assert payload["status_text"] == "4 of 4 connections live"
    assert payload["status_state"] == "up"
    assert payload["note"] == ""


def test_payload_without_a_process_says_so():
    assert server.to_payload({"found": False}) == {"found": False}


@pytest.fixture()
def api(monkeypatch):
    """A sampler whose collector never touches the machine."""
    sampler = server.Sampler(interval=3600)
    monkeypatch.setattr(sampler, "refresh", lambda: {"found": False})
    with sampler._lock:
        sampler._payload = server.to_payload(sample_state())
    httpd = server.serve(sampler, port=0)
    port = httpd.server_address[1]
    yield port
    httpd.shutdown()


def get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
        return response.status, dict(response.headers), response.read()


def test_api_state_is_json_with_cors(api):
    status, headers, body = get(api, "/api/state")
    assert status == 200
    assert headers["Content-Type"] == "application/json"
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body)["pid"] == 496320


def test_health_route(api):
    status, _, body = get(api, "/health")
    assert status == 200 and json.loads(body)["ok"] is True


def test_unknown_route_is_404(api):
    with pytest.raises(urllib.error.HTTPError) as info:
        get(api, "/etc/passwd")
    assert info.value.code == 404

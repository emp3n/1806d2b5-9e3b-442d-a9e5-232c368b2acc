"""A compact card: no footer under RAM, no empty bubbles when stopped.

These tests are the specification for the three changes:
  1. nothing is rendered after the RAM tile;
  2. the shard/edges/connections chips exist only while there is data — Origin
     stays, because it is resolved from our own IP, not from the connector;
  3. the not-found payload carries a real Origin (public IP + region), resolved
     server-side once through api.ip.sb/geoip and cached.
"""
from __future__ import annotations

import re
from pathlib import Path

import geo
import statefeed

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "ui" / "card"
RENDER = (CARD / "render.py").read_text()
SCRIPT = (CARD / "script.py").read_text()
THEME = (ROOT / "ui" / "theme.py").read_text()


def _render(state):
    from card import render
    return render(state, theme="dark")


def _idle():
    from live import idle_state
    return idle_state()


def test_nothing_is_rendered_after_the_ram_tile():
    from demo_data import build_state
    page = _render(build_state(0))
    assert "rd-foot" not in page
    assert "data-source" not in page
    assert 'name="source"' not in page
    # the RAM tile is the last element of the card, right before </article>
    tail = page[page.index('data-value="mem"'):]
    assert "</article>" in tail and "<footer" not in tail


def test_no_footer_css_or_js_writes_remain():
    assert ".rd-foot" not in THEME
    assert ":not(.rd-foot)" not in THEME
    assert "set('[data-source]'" not in SCRIPT
    assert "[data-source]" not in SCRIPT


def test_stopped_card_hides_the_empty_bubbles_but_keeps_origin():
    page = _render(_idle())
    facts = page[page.index('<div class="rd-facts">'):page.index('<div class="rd-measures">')]
    assert 'data-tone="origin"' in facts
    # the shard and the whole second facts row (edges + connections) are absent
    assert re.search(r'data-tone="shard"[^>]*\shidden', facts)
    assert re.search(r'data-fact-row="2"[^>]*\shidden', facts)
    assert re.search(r'data-tone="connections"[^>]*\shidden', facts)
    assert re.search(r'data-tone="edges"[^>]*\shidden', facts)


def test_running_card_shows_all_the_chips():
    from demo_data import build_state
    state = build_state(0)
    state["shard"] = "shard-3"
    state["connections"] = 4
    page = _render(state)
    facts = page[page.index('<div class="rd-facts">'):page.index('<div class="rd-measures">')]
    # row 3 (the picker) is hidden while running; rows 1-2 must be fully shown
    head = facts[:facts.index('data-fact-row="3"')]
    assert ' hidden' not in head
    assert 'data-tone="shard"' in head
    assert 'data-tone="connections"' in facts
    assert 'data-fact-row="2"' in facts


def test_the_browser_toggles_presence_from_the_payload():
    assert "function paintPresence(data)" in SCRIPT
    assert "paintPresence(data);" in SCRIPT
    assert SCRIPT.count("paintPresence(data);") >= 2  # blank() and apply()
    # the chips are addressed by stable hooks, never by position
    assert 'data-fact-row="2"' in SCRIPT
    assert '[data-tone="shard"]' in SCRIPT
    assert '[data-tone="connections"]' in SCRIPT


def test_geo_own_endpoint_contract(monkeypatch):
    geo.OWN_CACHE.clear()
    monkeypatch.setattr(geo, "_fetch", lambda url: {
        "ip": "8.8.8.8", "country_code": "us", "country": "United States",
        "region": "Oregon", "city": "Portland"})
    ip, info = geo.own_endpoint()
    assert ip == "8.8.8.8"
    assert info == {"cc": "US", "country": "United States", "region": "Oregon",
                    "city": "Portland"}
    # cached: a second call must not hit the network again
    monkeypatch.setattr(geo, "_fetch", lambda url: (_ for _ in ()).throw(AssertionError()))
    assert geo.own_endpoint() == (ip, info)
    geo.OWN_CACHE.clear()


def test_geo_own_endpoint_rejects_bad_ip_and_bad_country(monkeypatch):
    geo.OWN_CACHE.clear()
    monkeypatch.setattr(geo, "_fetch", lambda url: {"ip": "not-an-ip", "country_code": "???", "city": "X"})
    assert geo.own_endpoint() == ("", {})
    geo.OWN_CACHE.clear()
    monkeypatch.setattr(geo, "_fetch", lambda url: {"ip": "127.0.0.1", "country_code": "US", "city": "X"})
    assert geo.own_endpoint() == ("", {})  # a private address is never Origin
    geo.OWN_CACHE.clear()
    monkeypatch.setattr(geo, "_fetch", lambda url: (_ for _ in ()).throw(OSError("offline")))
    assert geo.own_endpoint() == ("", {})
    geo.OWN_CACHE.clear()


def test_not_found_payload_carries_a_real_origin(monkeypatch):
    monkeypatch.setattr(geo, "own_endpoint", lambda: ("198.51.100.7", {"cc": "DE", "city": "Berlin"}))
    payload = statefeed.to_payload({"found": False})
    assert payload["origin"] == "198.51.100.7"
    assert payload["origin_geo"]["city"] == "Berlin"


def test_idle_state_keeps_origin_instead_of_a_dash(monkeypatch):
    monkeypatch.setattr(geo, "own_endpoint", lambda: ("198.51.100.7", {"cc": "DE", "city": "Berlin"}))
    state = _idle()
    assert state["edge_ip"] == "198.51.100.7"
    assert state["origin_geo"]["city"] == "Berlin"


def test_stopped_card_drops_the_empty_side_facts_and_path_row():
    page = _render(_idle())
    assert re.search(r'class="rd-side"[^>]*\shidden', page)
    assert re.search(r'class="rd-path"[^>]*\shidden', page)
    # the measures themselves stay: "no process" is a real answer
    assert 'data-value="cpu"' in page and 'data-value="mem"' in page


def test_running_card_keeps_the_side_facts_and_path_row():
    from demo_data import build_state
    page = _render(build_state(0))
    assert re.search(r'class="rd-side"(?![^>]*\shidden)', page)
    assert re.search(r'class="rd-path"(?![^>]*\shidden)', page)


def test_the_browser_hides_the_empty_side_facts_and_path_row():
    assert "'[data-tone=\"connections\"]', live" in SCRIPT
    assert "['.rd-side', hasSide]" in SCRIPT
    assert "['.rd-path', hasPath]" in SCRIPT
    assert "hasSide = live &&" in SCRIPT and "hasPath = live &&" in SCRIPT
    assert ".rd-side[hidden], .rd-path[hidden] { display: none !important; }" in THEME


def test_geo_own_endpoint_falls_back_when_the_first_resolver_fails(monkeypatch):
    geo.OWN_CACHE.clear()
    calls = []
    def fetch(url):
        calls.append(url)
        if url == geo.API_OWN:
            raise OSError("403 from this datacentre")
        return {"ip": "8.8.4.4", "country_code": "DE", "country_name": "Germany",
                "region": "Hesse", "city": "Frankfurt"}
    monkeypatch.setattr(geo, "_fetch", fetch)
    assert geo.own_endpoint() == ("8.8.4.4", {"cc": "DE", "country": "Germany",
                                              "region": "Hesse", "city": "Frankfurt"})
    assert calls == [geo.API_OWN, geo.OWN_FALLBACKS[0]]
    geo.OWN_CACHE.clear()

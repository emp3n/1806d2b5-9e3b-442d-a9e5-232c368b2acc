"""Shard is a deployment fact; edges are unique colos with honest multiplicity."""
from __future__ import annotations

import re
from pathlib import Path

import live
import statefeed

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "ui" / "card"
RENDER = (CARD / "render.py").read_text()
SCRIPT = (CARD / "script.py").read_text()


def test_shard_stays_when_the_connector_is_stopped(monkeypatch):
    import geo
    from live import idle_state
    from card import render
    monkeypatch.setattr(geo, "shard", lambda url: "shard-3")
    state = idle_state()
    assert state["shard"] == "shard-3"
    page = render(state, theme="dark")
    facts = page[page.index('<div class="rd-facts">'):page.index('<div class="rd-measures"')]
    chip = re.search(r'data-tone="shard"[^>]*', facts).group(0)
    assert "hidden" not in chip
    assert ">shard-3<" in facts


def test_the_browser_keeps_the_shard_without_a_process():
    assert "var hasShard = !!((data && data.shard) || '');" in SCRIPT
    assert "hasShard = live &&" not in SCRIPT


def test_duplicate_colos_collapse_into_one_chip_with_a_count(fake_proc, monkeypatch):
    # Two of the four connections landed on the same airport; Cloudflare logs
    # that honestly, so the card must show one cea11 with ×2, not four chips.
    monkeypatch.setattr(live, "parse_log", lambda *a, **k: {"conns": [
        {"location": "cea11", "ip": "1.1.1.1"},
        {"location": "cea11", "ip": "1.0.0.1"},
        {"location": "fra05", "ip": "8.8.8.8"},
        {"location": "fra05", "ip": "8.8.4.4"},
    ], "edges": [], "reason": ""})
    monkeypatch.setattr(live.geo, "lookup",
                        lambda ip: {"cc": "US", "country": "United States",
                                    "region": "Ohio", "city": "Columbus"})
    col = live.Collector(metrics_url="")
    col.pid = 4242
    col._ready = {"ready": 4, "connector": "x"}
    col._log = live.parse_log([])
    state = col.state()
    assert state["edges_geo"] == [
        {"code": "cea11", "cc": "US", "region": "Ohio", "city": "Columbus", "count": 2},
        {"code": "fra05", "cc": "US", "region": "Ohio", "city": "Columbus", "count": 2},
    ]
    assert state["edge_names"] == "cea11, fra05"


def test_the_multiplicity_badge_is_rendered_in_html_and_js():
    from card.markup import edge_items
    html = edge_items([{"code": "cea11", "cc": "US", "count": 2}], "")
    assert "cea11" in html and "×2" in html
    assert "rd-edge-count" in html
    plain = edge_items([{"code": "sea01", "cc": "US", "count": 1}], "")
    assert "×" not in plain
    assert "rd-edge-count" in SCRIPT
    assert "item.count" in SCRIPT

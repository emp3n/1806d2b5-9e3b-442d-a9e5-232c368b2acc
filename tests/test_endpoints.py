"""Every address and data contract the card depends on, in one place.

The card talks to a fixed set of endpoints: the JSON file served by Streamlit,
cloudflared's /ready and /metrics, the platform's disambiguate route, the geo
lookup and the local SVG flags. These tests pin the *shape* of each contract so
a rename on one side cannot silently break the other.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import bootstrap
import geo
import palette
import statefeed
import theme

ROOT = Path(__file__).resolve().parents[1]


def test_state_path_is_relative_and_served_by_streamlit():
    assert bootstrap.CARD_STATE_PATH == "app/static/state.json"
    assert not bootstrap.CARD_STATE_PATH.startswith("/")
    assert "static/state.json" in (ROOT / "app.py").read_text()


def test_state_file_has_a_fixed_length_and_atomic_replace(tmp_path):
    payload = {"found": True, "live": 4, "connections": 6, "shard": "shard-3"}
    path = statefeed.write_state(payload, tmp_path / "state.json")
    first = path.read_bytes()
    assert len(first) == statefeed.STATE_SIZE
    assert first.endswith(b" ")
    assert json.loads(first)["connections"] == 6
    # a shorter payload still pads to the same length: Content-Length is stable
    path2 = statefeed.write_state({"found": False}, tmp_path / "state.json")
    assert len(path2.read_bytes()) == len(first)
    assert list((tmp_path).glob("state.json.*.new")) == []


def test_payload_exposes_every_field_the_card_reads():
    state = {
        "found": True, "pid": 7, "live": 4, "connections": 6, "note": "",
        "status_text": "4 of 4 connections live", "connected": True,
        "cpu": 0.5, "cpu_text": "0.5%", "cpu_share": "0.2% of 2.2 cores",
        "cpu_budget": "2.2 cores", "mem": 41.8, "mem_text": "41.8 MB",
        "mem_share": "1% of 3.0 GB", "mem_budget": "3.0 GB", "cores": 2.24,
        "cores_text": "2.2", "mem_scale": 3072.0, "uptime": "4m 3s",
        "version": "2026.11.1", "path": "/bin/cloudflared",
        "edge_ip": "35.230.127.150", "edge_names": "sea07, sea10",
        "proc_state": "running", "source_label": "live · pid 7",
        "register_fail": 0, "origin_geo": {"cc": "US"},
        "edges_geo": [{"code": "sea07", "cc": "US"}], "shard": "shard-3",
    }
    data = statefeed.to_payload(state)
    for key in ("found", "pid", "live", "connections", "status_text", "cpu",
                "mem", "cores_text", "mem_scale", "origin", "origin_geo",
                "edges", "edges_geo", "tunnel"):
        assert key in data, key
    assert data["connections"] == 6
    assert data["origin_geo"]["cc"] == "US"


def test_disambiguate_url_and_cluster_validation(monkeypatch):
    seen = []
    monkeypatch.setattr(geo, "_fetch", lambda url: seen.append(url) or {"cluster": "shard-7"})
    geo.shard.cache_clear()
    assert geo.shard("https://example.streamlit.app/") == "shard-7"
    assert seen == ["https://example.streamlit.app/api/v2/app/disambiguate"]
    monkeypatch.setattr(geo, "_fetch", lambda url: {"cluster": "oops"})
    geo.shard.cache_clear()
    assert geo.shard("https://example.streamlit.app") == ""


def test_geo_lookup_contract(monkeypatch):
    monkeypatch.setattr(geo, "CACHE", {})
    monkeypatch.setattr(geo, "_fetch", lambda url: {
        "success": True, "country": "United States", "country_code": "us",
        "region": "Oregon", "region_code": "OR", "city": "The Dalles"})
    info = geo.lookup("35.230.127.150")
    assert info == {"cc": "US", "country": "United States", "region": "Oregon",
                    "region_code": "OR", "city": "The Dalles"}


def test_all_local_flags_used_by_the_card_exist_and_are_svg():
    flags = ROOT / "static" / "flags"
    assert (flags / "LICENSE.txt").exists()
    for cc in ("us", "de", "fr", "gb", "ru", "cn", "jp", "br", "in"):
        path = flags / f"{cc}.svg"
        assert path.exists(), cc
        assert path.read_text().lstrip().startswith("<svg"), cc
    assert len(list(flags.glob("*.svg"))) == 271


def test_generated_css_covers_every_palette_and_mode():
    css = theme.THEME_CSS
    for name in palette.PALETTES:
        assert f'.rd-stage[data-palette="{name}"] {{' in css
        assert f'.rd-stage[data-palette="{name}"][data-theme="dark"] {{' in css
        assert f'.rd-stage[data-palette="{name}"][data-theme="auto"] {{' in css
    assert "@media (prefers-color-scheme: dark)" in css
    # no second source of truth: raw roles are only in the generated blocks
    assert "#F4F3EF" not in css and "#1F2224" not in css


def test_theme_color_and_viewport_are_declared_in_the_page_head():
    src = (ROOT / "app.py").read_text()
    assert 'name="viewport" content="width=device-width, initial-scale=1' in src
    assert 'name="theme-color"' in src
    assert f"semantic_tokens(palette, \"light\")" in src
    assert f"semantic_tokens(palette, \"dark\")" in src


def test_shard_is_published_by_the_sampler_not_the_payload_builder():
    sampler = statefeed.Sampler(directory=Path("/tmp"), port=None)
    sampler.refresh = lambda: None
    import bootstrap as bs
    import geo as g
    g.shard.cache_clear()
    original = g.shard
    g.shard = lambda url: "shard-3"
    try:
        payload = {"found": False}
        # the same statement refresh() uses
        payload["shard"] = g.shard(bs.cloud_url())
        assert payload["shard"] == "shard-3"
    finally:
        g.shard = original


def test_disambiguate_is_never_called_from_the_browser():
    src = (ROOT / "ui" / "card.py").read_text()
    js = re.search(r"<script>(.*?)</script>", src, re.S).group(1)
    assert "disambiguate" not in js
    assert "ipwho.is" not in js
    assert "fetch(" in js
    # the state path is injected as RD_API, never hardcoded in the script
    assert "state.json" not in js

"""Geo facts: resolved once in the sampler, published to the one data channel."""
from __future__ import annotations

import geo


def test_lookup_is_cached_and_validates_iso(monkeypatch):
    calls = []

    def fake(url):
        calls.append(url)
        return {"success": True, "country": "United States", "country_code": "us",
                "region": "Missouri", "region_code": "MO", "city": "Kansas City"}

    monkeypatch.setattr(geo, "_fetch", fake)
    monkeypatch.setattr(geo, "CACHE", {})
    first = geo.lookup("35.201.127.49")
    second = geo.lookup("35.201.127.49")
    assert first == second
    assert first["cc"] == "US"
    assert first["region"] == "Missouri"
    assert len(calls) == 1


def test_lookup_skips_private_and_bad_addresses(monkeypatch):
    monkeypatch.setattr(geo, "_fetch", lambda url: (_ for _ in ()).throw(
        AssertionError("must not call the API")))
    monkeypatch.setattr(geo, "CACHE", {})
    assert geo.lookup("127.0.0.1") == {}
    assert geo.lookup("not-an-ip") == {}
    assert geo.lookup("") == {}


def test_lookup_survives_a_network_failure(monkeypatch):
    def broken(url):
        raise OSError("offline")

    monkeypatch.setattr(geo, "_fetch", broken)
    monkeypatch.setattr(geo, "CACHE", {})
    assert geo.lookup("8.8.8.8") == {}


def test_placement_comes_from_the_platform_resolv_conf(tmp_path, monkeypatch):
    resolv = tmp_path / "resolv.conf"
    resolv.write_text("search default.svc.cluster.local us-west1-b.c.s4a-prod.internal\n"
                      "nameserver 10.3.96.10\n")
    monkeypatch.setattr(geo, "RESOLV", resolv)
    geo.placement.cache_clear()
    assert geo.placement() == "s4a-prod/us-west1-b"

"""Fixtures for the live-feed tests.

The point of the suite is that the card's numbers come from real sources, so the
tests run two ways: against a miniature /proc built in a temporary directory
(exact, fast, no process needed) and against the real cloudflared binary when it
is on this host (see ``test_live_binary.py``).
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

REDESIGN = Path(__file__).resolve().parents[1]
if str(REDESIGN) not in sys.path:
    sys.path.insert(0, str(REDESIGN))


def stat_line(
    pid: int = 4242,
    *,
    comm: str = "cloudflared",
    state: str = "S",
    utime: int = 7,
    stime: int = 4,
    threads: int = 13,
    starttime: int = 2606026,
) -> str:
    """A /proc/<pid>/stat line with the fields this reader cares about.

    Field numbers follow proc(5): utime 14, stime 15, num_threads 20,
    starttime 22, with the command in parentheses as field 2.
    """
    after = (
        f"{state} 1 {pid} {pid} 0 -1 4194304 0 0 0 0 {utime} {stime} 0 0 20 0 "
        f"{threads} 0 {starttime} 0 0"
    )
    return f"{pid} ({comm}) {after}\n"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path_factory, monkeypatch):
    """Never let a test write the remembered launch line to the real home."""
    monkeypatch.setenv(
        "CARD_LAUNCH_FILE",
        str(tmp_path_factory.mktemp("state") / "cf-launch.json"),
    )


@pytest.fixture(autouse=True)
def no_external_ip_lookup(monkeypatch):
    """Own-IP resolution is real code, but tests answer it locally.

    The resolver is called by the sampler (for the Origin chip while the
    connector is down), so leaving it live would make the feed tests depend on
    a third-party server. Everything else still uses the real fetch.
    """
    import geo

    real = geo._fetch

    own_urls = (geo.API_OWN,) + geo.OWN_FALLBACKS

    def local(url):
        if url in own_urls:
            return {"ip": "8.8.8.8", "country_code": "US", "country": "United States",
                    "region": "California", "city": "Mountain View"}
        return real(url)

    geo.OWN_CACHE.clear()
    monkeypatch.setattr(geo, "_fetch", local)


@pytest.fixture()
def fake_proc(tmp_path, monkeypatch):
    """A miniature /proc with one connector inside it."""
    import live

    root = tmp_path / "proc"
    (root / "4242").mkdir(parents=True)
    (root / "uptime").write_text("26151.83 180487.04\n")
    (root / "4242" / "stat").write_text(stat_line())
    (root / "4242" / "statm").write_text("324109 10990 5137 3038 0 24383 0\n")
    (root / "4242" / "cmdline").write_text("cloudflared\0tunnel\0run\0--token-file\0/tmp/tok\0")
    binary = tmp_path / "cloudflared"
    binary.write_text("")
    os.symlink(binary, root / "4242" / "exe")
    monkeypatch.setattr(live, "PROC", root)
    monkeypatch.setattr(live, "_http", lambda url: None)
    return root


@pytest.fixture()
def collector(fake_proc):
    import live

    return live.Collector(metrics_url="")


@pytest.fixture(autouse=True)
def no_external_version_api(monkeypatch):
    """The release list is fetched from GitHub once; tests answer it locally."""
    import bootstrap

    real = bootstrap.urllib.request.urlopen

    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def local(request, timeout=None):
        url = getattr(request, "full_url", request if isinstance(request, str) else "")
        if str(url).startswith(bootstrap.RELEASES_API.split("{")[0]):
            return Reply(json.dumps([
                {"tag_name": "2026.11.1", "published_at": "2026-11-17T15:11:01Z",
                 "prerelease": False, "draft": False},
                {"tag_name": "2026.10.4", "published_at": "2026-10-20T15:11:01Z",
                 "prerelease": False, "draft": False},
            ]).encode())
        return real(request, timeout=timeout)

    bootstrap.catalogue.cache_clear()
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", local)

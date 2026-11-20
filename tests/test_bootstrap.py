"""The deployed app has to fetch cloudflared itself, and tell the truth when
it cannot. No network and no real connector in these tests."""

from __future__ import annotations

import io
import bootstrap


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


class Proc:
    def __init__(self, code=None, pid=4242):
        self.code = code
        self.pid = pid

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15


def test_asset_and_release_url_are_explicit():
    url = bootstrap.download_url()
    assert url.startswith("https://github.com/cloudflare/cloudflared/releases/download/")
    assert url.endswith(f"/{bootstrap.asset_name()}")
    assert bootstrap.VERSION in url


def test_download_lands_in_the_temp_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path / "rt"))
    called = []

    def fake_open(url, timeout):
        called.append((url, timeout))
        return Reply(b"x" * 1_100_000)

    binary = bootstrap.download(opener=fake_open)
    assert binary == bootstrap.runtime_dir() / "cloudflared"
    assert binary.stat().st_size == 1_100_000
    assert binary.stat().st_mode & 0o111
    assert called[0][1] == 25
    # a second call reuses the binary; nothing is fetched twice
    assert bootstrap.download(opener=fake_open) == binary
    assert len(called) == 1


def test_tunnel_url_is_read_from_the_log(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    log = tmp_path / "cf.log"
    log.write_text(
        "INF Generated a Cloudflare Tunnel token\n"
        "INF +--------------------------------------------------------------------------------------------+\n"
        "INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |\n"
        "INF |  https://funny-elephant-42.trycloudflare.com                                               |\n"
    )
    binary = tmp_path / "cloudflared"
    binary.write_text("#!/bin/sh\n")
    tunnel = bootstrap.start_quick_tunnel(binary, 8598, runner=lambda *a, **k: Proc())
    assert tunnel.ok is True
    assert tunnel.public_url == "https://funny-elephant-42.trycloudflare.com"
    assert tunnel.api_url == "https://funny-elephant-42.trycloudflare.com/api/state"
    assert tunnel.pid == 4242


def test_exit_255_is_named_instead_of_looking_like_a_frozen_card(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    (tmp_path / "cf.log").write_text("ERR failed to create tunnel: no outbound network\n")
    binary = tmp_path / "cloudflared"
    binary.write_text("#!/bin/sh\n")
    tunnel = bootstrap.start_quick_tunnel(binary, 8598, runner=lambda *a, **k: Proc(code=255))
    assert tunnel.ok is False
    assert "code 255" in tunnel.message
    assert "no outbound network" in tunnel.message


def test_missing_url_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("CARD_RUNTIME", str(tmp_path))
    (tmp_path / "cf.log").write_text("INF connecting to Cloudflare edge\n")
    binary = tmp_path / "cloudflared"
    binary.write_text("#!/bin/sh\n")
    tunnel = bootstrap.start_quick_tunnel(binary, 8598, wait_s=0.1,
                                          runner=lambda *a, **k: Proc())
    assert tunnel.ok is False
    assert "no tunnel URL" in tunnel.detail


def test_ensure_tunnel_starts_only_once(monkeypatch):
    monkeypatch.setattr(bootstrap, "_cached", None)
    good = bootstrap.Tunnel(True, api_url="https://x.trycloudflare.com/api/state",
                            public_url="https://x.trycloudflare.com", pid=7)
    calls = []
    monkeypatch.setattr(bootstrap, "download", lambda: calls.append("download") or "bin")
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: calls.append("start") or good)
    assert bootstrap.ensure_tunnel(8598) is good
    assert bootstrap.ensure_tunnel(8598) is good
    assert calls.count("start") == 1, "a rerun must not spawn a second connector"


def test_ensure_tunnel_turns_exceptions_into_a_named_failure(monkeypatch):
    monkeypatch.setattr(bootstrap, "_cached", None)

    def boom():
        raise OSError("no route to GitHub")

    monkeypatch.setattr(bootstrap, "download", boom)
    tunnel = bootstrap.ensure_tunnel(8598)
    assert tunnel.ok is False
    assert "no route to GitHub" in tunnel.message


def test_a_cloud_page_is_detected_from_its_host():
    on_streamlit_cloud = bootstrap.on_streamlit_cloud

    assert on_streamlit_cloud("https://funny-name.streamlit.app/?theme=dark")
    assert not on_streamlit_cloud("http://localhost:8599/")
    assert not on_streamlit_cloud("https://streamlit.idealis.cc/")
    assert not on_streamlit_cloud(None)

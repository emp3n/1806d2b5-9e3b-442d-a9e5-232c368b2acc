"""A remembered cloudflared version: default latest, chosen once, used on start."""
from __future__ import annotations

import io
import json
from pathlib import Path

import bootstrap


def test_version_file_defaults_to_latest(tmp_path):
    assert bootstrap.desired_version(tmp_path) == "latest"
    assert bootstrap.version_path(tmp_path).name == "cloudflared.version"


def test_version_choice_is_validated_and_remembered(tmp_path):
    assert bootstrap.set_desired_version("2026.10.4", tmp_path) == "2026.10.4"
    assert bootstrap.desired_version(tmp_path) == "2026.10.4"
    assert bootstrap.set_desired_version("latest", tmp_path) == "latest"
    for bad in ("../../etc/passwd", "v1.2.3", "", "2026.10.4;rm -rf /"):
        try:
            bootstrap.set_desired_version(bad, tmp_path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad!r} must be refused")


def test_a_corrupt_version_file_falls_back_to_latest(tmp_path):
    bootstrap.version_path(tmp_path).write_text("garbage\n")
    assert bootstrap.desired_version(tmp_path) == "latest"


def test_versions_are_read_from_the_github_tags_api(monkeypatch):
    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    payload = json.dumps([
        {"tag_name": "2026.11.1", "published_at": "2026-11-17T15:11:01Z",
         "prerelease": False, "draft": False},
        {"tag_name": "2026.10.4", "published_at": "2026-10-20T15:11:01Z",
         "prerelease": False, "draft": False},
    ]).encode()
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda req, timeout: Reply(payload))
    bootstrap.catalogue.cache_clear()
    assert bootstrap.tags() == ["2026.11.1", "2026.10.4"]
    assert "releases" in bootstrap.RELEASES_API
    bootstrap.catalogue.cache_clear()


def test_versions_fall_back_when_the_api_is_offline(monkeypatch):
    def down(req, timeout): raise OSError("offline")
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", down)
    bootstrap.catalogue.cache_clear()
    assert bootstrap.tags() and bootstrap.tags()[0].startswith("20")
    bootstrap.catalogue.cache_clear()


def test_latest_resolves_to_the_newest_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "tags", lambda: ["2026.12.0", "2026.11.1"])
    monkeypatch.setattr(bootstrap, "version_path", lambda d=None: tmp_path / "v")
    assert bootstrap.resolved_version() == "2026.12.0"
    bootstrap.set_desired_version("2026.10.4", tmp_path)
    assert bootstrap.resolved_version() == "2026.10.4"
    monkeypatch.setattr(bootstrap, "tags", lambda: [])
    bootstrap.version_path(tmp_path).unlink()
    assert bootstrap.resolved_version() == bootstrap.VERSION


def test_download_switches_binary_when_the_version_changes(tmp_path):
    class Reply(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fetch(url, timeout):
        assert "/2026.10.4/" in url
        return Reply(b"x" * 1_100_000)

    target = bootstrap.download(tmp_path, version="2026.10.4", opener=fetch)
    assert target.stat().st_size == 1_100_000
    assert (tmp_path / "cloudflared.installed").read_text().strip() == "2026.10.4"
    # the same version is reused without a fetch
    assert bootstrap.download(tmp_path, version="2026.10.4",
                              opener=lambda u, t: None) == target
    # a different version is fetched even though the old binary is executable
    def fetch_new(url, timeout):
        assert "/2026.9.1/" in url
        return Reply(b"y" * 1_100_000)
    bootstrap.download(tmp_path, version="2026.9.1", opener=fetch_new)
    assert (tmp_path / "cloudflared.installed").read_text().strip() == "2026.9.1"


def test_choosing_a_version_downloads_without_touching_the_connector(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda *a, **k: {"pid": 42, "extra": []})
    monkeypatch.setattr(bootstrap, "stop_tunnel", lambda *a, **k: seen.append("stop"))
    monkeypatch.setattr(bootstrap, "ensure_tunnel", lambda *a, **k: seen.append("start"))
    monkeypatch.setattr(bootstrap, "start_install", lambda v: {
        "version": v, "state": "downloading", "detail": ""})
    result = bootstrap.use_version("2026.10.4", 8501)
    assert result["ok"] is True and result["install"] == "downloading"
    assert seen == [] and bootstrap.desired_version() == "2026.10.4"


def test_a_failed_download_is_reported_without_stopping_anything(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "start_install", lambda v: {
        "version": v, "state": "error", "detail": "RuntimeError: offline"})
    result = bootstrap.use_version("2026.10.4", 8501)
    assert result["ok"] is False and "offline" in result["detail"]


def test_ensure_tunnel_downloads_the_chosen_release(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "version_path", lambda d=None: tmp_path / "v")
    monkeypatch.setattr(bootstrap, "desired_version", lambda *a: "2026.9.1")
    monkeypatch.setattr(bootstrap, "our_tunnels", lambda *a: [])
    monkeypatch.setattr(bootstrap, "tunnel_token", lambda *a: (None, "no token"))
    monkeypatch.setattr(bootstrap, "retire_stale_tunnel", lambda **k: "")
    monkeypatch.setattr(bootstrap, "download",
                        lambda *a, **k: seen.append(k["version"]) or Path("bin/cf"))
    monkeypatch.setattr(bootstrap, "start_quick_tunnel",
                        lambda *a, **k: bootstrap.Tunnel(True, pid=5))
    bootstrap.ensure_tunnel(8501, force=True)
    assert seen == ["2026.9.1"]

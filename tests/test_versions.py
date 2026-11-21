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

    payload = json.dumps([{"name": "2026.11.1"}, {"name": "not-a-version"},
                          {"name": "2026.10.4"}]).encode()
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda req, timeout: Reply(payload))
    bootstrap.versions.cache_clear()
    assert bootstrap.versions() == ["2026.11.1", "2026.10.4"]
    assert "tags" in bootstrap.VERSIONS_API
    bootstrap.versions.cache_clear()


def test_versions_fall_back_when_the_api_is_offline(monkeypatch):
    def down(req, timeout): raise OSError("offline")
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", down)
    bootstrap.versions.cache_clear()
    assert bootstrap.versions() and bootstrap.versions()[0].startswith("20")
    bootstrap.versions.cache_clear()


def test_latest_resolves_to_the_newest_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "versions", lambda: ["2026.12.0", "2026.11.1"])
    monkeypatch.setattr(bootstrap, "version_path", lambda d=None: tmp_path / "v")
    assert bootstrap.resolved_version() == "2026.12.0"
    bootstrap.set_desired_version("2026.10.4", tmp_path)
    assert bootstrap.resolved_version() == "2026.10.4"
    monkeypatch.setattr(bootstrap, "versions", lambda: [])
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


def test_selecting_a_version_downloads_it_and_starts_the_tunnel(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "tunnel_status",
                        lambda port, directory=None: {"pid": 42, "extra": []})
    monkeypatch.setattr(bootstrap, "stop_tunnel",
                        lambda *a, **k: {"ok": True, "stopped": [42], "survivors": []})
    monkeypatch.setattr(bootstrap, "ensure_tunnel",
                        lambda *a, **k: started.append(k) or
                        bootstrap.Tunnel(True, pid=77, mode="named"))
    result = bootstrap.use_version("2026.10.4", 8501)
    assert result["ok"] is True and result["version"] == "2026.10.4"
    assert started and started[0]["force"] is True
    assert bootstrap.desired_version() == "2026.10.4"


def test_a_version_change_that_cannot_stop_the_old_connector_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda *a, **k: {"pid": 42, "extra": []})
    monkeypatch.setattr(bootstrap, "stop_tunnel",
                        lambda *a, **k: {"ok": False, "stopped": [], "survivors": [42]})
    monkeypatch.setattr(bootstrap, "ensure_tunnel", lambda *a, **k: None)
    result = bootstrap.use_version("2026.10.4", 8501)
    assert result["ok"] is False and "42" in result["error"]


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


def test_a_version_change_reports_a_failed_start(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda *a, **k: {"pid": None, "extra": []})
    def boom(*a, **k): raise RuntimeError("no route to GitHub")
    monkeypatch.setattr(bootstrap, "ensure_tunnel", boom)
    result = bootstrap.use_version("2026.10.4", 8501)
    assert result["ok"] is False and "no route to GitHub" in result["error"]

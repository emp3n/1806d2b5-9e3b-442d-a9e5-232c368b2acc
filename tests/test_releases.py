"""The release catalogue: every version, its age, and its stability flag."""
from __future__ import annotations

import io
import json
import time

import bootstrap


class Reply(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _releases():
    now = time.time()
    return [
        {"tag_name": "2026.11.1", "published_at": "2026-11-17T15:11:01Z",
         "prerelease": False, "draft": False},
        {"tag_name": "2026.12.0-rc1", "published_at": "2026-11-20T15:11:01Z",
         "prerelease": True, "draft": False},
        {"tag_name": "wip-draft", "published_at": "2026-11-21T15:11:01Z",
         "prerelease": True, "draft": True},
    ]


def test_all_releases_are_kept_including_prereleases(monkeypatch):
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda req, timeout: Reply(json.dumps(_releases()).encode()))
    bootstrap.catalogue.cache_clear()
    data = bootstrap.catalogue()
    assert [x["tag"] for x in data] == ["2026.11.1", "2026.12.0-rc1"]
    assert data[0]["pre"] is False and data[1]["pre"] is True
    assert bootstrap.tags() == ["2026.11.1", "2026.12.0-rc1"]
    bootstrap.catalogue.cache_clear()


def test_relative_commit_time_is_human(monkeypatch):
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda req, timeout: Reply(json.dumps(_releases()).encode()))
    from datetime import datetime, timezone
    now = 1_700_000_000.0
    stamp = datetime.fromtimestamp(now - 3 * 86400, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda req, timeout: Reply(json.dumps([{
                            "tag_name": "2026.11.1", "published_at": stamp,
                            "prerelease": False, "draft": False}]).encode()))
    bootstrap.catalogue.cache_clear()
    data = bootstrap.catalogue(now=now)
    assert data[0]["age"] == "3 days ago"
    bootstrap.catalogue.cache_clear()


def test_age_text_boundaries():
    f = bootstrap.age_text
    now = 1_700_000_000.0
    assert f(0, now) == ""
    assert f(now, now) == "just now"
    assert f(now - 90, now) == "1 minute ago"
    assert f(now - 3 * 60, now) == "3 minutes ago"
    assert f(now - 2 * 3600, now) == "2 hours ago"
    assert f(now - 86400, now) == "1 day ago"
    assert f(now - 5 * 86400, now) == "5 days ago"
    assert f(now - 14 * 86400, now) == "2 weeks ago"
    assert f(now - 60 * 86400, now) == "2 months ago"
    assert f(now - 400 * 86400, now) == "1 year ago"


def test_release_pages_stop_when_the_api_runs_out(monkeypatch):
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        if "page=2" in req.full_url:
            return Reply(b"[]")
        return Reply(json.dumps(_releases()).encode())

    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", fake)
    bootstrap.catalogue.cache_clear()
    bootstrap.tags()
    assert len(calls) == 2 and "page=1" in calls[0]
    bootstrap.catalogue.cache_clear()


def test_a_blocked_api_still_yields_a_usable_catalogue(monkeypatch):
    def down(req, timeout): raise OSError("offline")
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", down)
    bootstrap.catalogue.cache_clear()
    assert bootstrap.tags() and bootstrap.tags()[0] == bootstrap.VERSION
    bootstrap.catalogue.cache_clear()


def test_a_missing_or_broken_publish_date_has_no_age():
    from core.bootstrap.releases import _published_at
    assert _published_at("") == 0.0
    assert _published_at("2026-99-99T00:00:00Z") == 0.0  # impossible month
    assert bootstrap.age_text(0) == ""


def test_back_compatible_tag_list(monkeypatch):
    monkeypatch.setattr(bootstrap.urllib.request, "urlopen",
                        lambda req, timeout: Reply(json.dumps(_releases()).encode()))
    bootstrap.catalogue.cache_clear()
    assert bootstrap.versions() == ["2026.11.1", "2026.12.0-rc1"]
    bootstrap.catalogue.cache_clear()

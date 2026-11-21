"""Picking a version downloads it; only the Start button launches anything."""
from __future__ import annotations

import time
from pathlib import Path

import bootstrap
import statefeed

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "ui" / "card"
RENDER = (CARD / "render.py").read_text()
SCRIPT = (CARD / "script.py").read_text()
THEME = (ROOT / "ui" / "theme.py").read_text()


def test_choice_never_stops_or_starts_the_connector(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    called = []
    monkeypatch.setattr(bootstrap, "stop_tunnel", lambda *a, **k: called.append("stop"))
    monkeypatch.setattr(bootstrap, "ensure_tunnel", lambda *a, **k: called.append("start"))
    monkeypatch.setattr(bootstrap, "start_install", lambda v: {
        "version": v, "state": "downloading", "detail": ""})
    result = bootstrap.use_version("2026.10.4", 8501)
    assert result["ok"] is True and result["install"] == "downloading"
    assert called == [] and bootstrap.desired_version() == "2026.10.4"


def test_background_install_reaches_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "download",
                        lambda *a, **k: Path("bin/cloudflared"))
    bootstrap._INSTALL.update(version="", state="idle", detail="")
    status = bootstrap.start_install("2026.10.4")
    assert status["state"] in ("downloading", "ready")
    for _ in range(40):
        if bootstrap.install_status()["state"] != "downloading":
            break
        time.sleep(0.01)
    assert bootstrap.install_status()["state"] == "ready"


def test_background_install_reports_a_failure(monkeypatch):
    def boom(**k): raise RuntimeError("no route")
    monkeypatch.setattr(bootstrap, "download", boom)
    bootstrap._INSTALL.update(version="", state="idle", detail="")
    bootstrap.start_install("2026.10.4")
    for _ in range(40):
        if bootstrap.install_status()["state"] != "downloading":
            break
        time.sleep(0.01)
    state = bootstrap.install_status()
    assert state["state"] == "error" and "no route" in state["detail"]


def test_install_status_is_published_in_the_payload(monkeypatch):
    monkeypatch.setattr(bootstrap, "install_status",
                        lambda: {"version": "2026.10.4", "state": "ready", "detail": ""})
    payload = statefeed.to_payload({"found": False})
    assert payload["install"]["state"] == "ready"


def test_secret_or_environment_sets_the_initial_version(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap, "_setting", lambda key: "2026.9.1" if key == "CLOUDFLARE_VERSION" else None)
    assert bootstrap.version_override() == "2026.9.1"
    assert bootstrap.desired_version() == "2026.9.1"
    bootstrap.set_desired_version("2026.10.4", tmp_path)
    assert bootstrap.desired_version() == "2026.10.4"  # the file wins once written


def test_picker_head_has_no_word_version_and_has_progress(monkeypatch):
    import geo
    from live import idle_state
    from card import render
    monkeypatch.setattr(geo, "shard", lambda url: "shard-3")
    page = render(idle_state(), theme="dark")
    facts = page[page.index('<div class="rd-version-box"'):page.index('<div class="rd-measures"')]
    assert '<em>version</em>' not in facts
    assert 'data-version-progress' in facts and 'data-version-note' not in facts


def test_styling_matches_the_chips_and_the_scroll_is_thin():
    assert ".rd-version-box {" in THEME and "background: var(--chip-tint)" in THEME
    assert "font-family: \"RD Russo\"" in THEME
    assert "::-webkit-scrollbar { width: 4px; }" in THEME
    assert "padding-left: 12px" not in THEME  # no protruding selected bubble
    assert "cubic-bezier" in THEME


def test_js_reacts_to_the_background_install():
    assert "data.install" in SCRIPT
    assert "data-version-progress" in SCRIPT
    assert "data-version-note" not in SCRIPT
    assert "is-downloading" in SCRIPT and "is-ready" in SCRIPT
    assert "Ready" not in SCRIPT and "Downloading" not in SCRIPT


def test_a_second_click_while_downloading_does_not_stack_jobs(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "download", lambda *a, **k: calls.append(1))
    bootstrap._INSTALL.update(version="2026.10.4", state="downloading", detail="")
    status = bootstrap.start_install("2026.10.4")
    assert status["state"] == "downloading" and calls == []
    bootstrap._INSTALL.update(version="", state="idle", detail="")

"""The stopped card offers a real version picker instead of empty edge bubbles."""
from __future__ import annotations

import re
from pathlib import Path

import statefeed

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "ui" / "card"
RENDER = (CARD / "render.py").read_text()
SCRIPT = (CARD / "script.py").read_text()
THEME = (ROOT / "ui" / "theme.py").read_text()
APP = (ROOT / "app.py").read_text()


def _render(state):
    from card import render
    return render(state, theme="dark")


def test_payload_carries_the_choice_and_the_full_list(monkeypatch):
    import bootstrap
    monkeypatch.setattr(bootstrap, "desired_version", lambda *a: "2026.10.4")
    monkeypatch.setattr(bootstrap, "versions", lambda: ["2026.11.1", "2026.10.4"])
    live = statefeed.to_payload({"found": False})
    assert live["chosen_version"] == "2026.10.4"
    assert live["versions"][0]["tag"] == "2026.11.1"


def test_stopped_card_swaps_edges_for_the_version_picker(monkeypatch):
    import geo
    from live import idle_state
    monkeypatch.setattr(geo, "shard", lambda url: "shard-3")
    page = _render(idle_state())
    assert re.search(r'data-fact-row="2"[^>]*\shidden', page)
    assert re.search(r'data-fact-row="3"(?![^>]*\shidden)', page)
    assert 'data-version-toggle' in page and 'data-version-list' in page
    assert 'data-release="latest"' in page


def test_running_card_keeps_edges_and_hides_the_picker():
    from demo_data import build_state
    state = build_state(0)
    state["chosen_version"] = "latest"
    state["versions"] = ["2026.11.1", "2026.10.4"]
    page = _render(state)
    assert re.search(r'data-fact-row="2"(?![^>]*\shidden)', page)
    assert re.search(r'data-fact-row="3"[^>]*\shidden', page)


def test_the_dropdown_has_its_own_scroll_and_animations():
    for hook in (".rd-version-list", "::-webkit-scrollbar", "scrollbar-width",
                 "@keyframes rd-menu-in", ".rd-chevron", "rd-version-progress",
                 "overscroll-behavior", "transform-origin"):
        assert hook in THEME, hook


def test_the_browser_rebuilds_options_and_posts_the_choice():
    assert "function paintVersion(data)" in SCRIPT
    assert "paintVersion(data);" in SCRIPT
    assert "data-version-list" in SCRIPT
    assert "aria-expanded" in SCRIPT
    assert "rd: 'version'" in SCRIPT
    assert "version: value" in SCRIPT
    assert "Escape" in SCRIPT
    assert "document.addEventListener('click'" in SCRIPT


def test_the_app_accepts_the_version_verb_and_persists_it():
    assert "st-key-rd_version_" in APP
    assert "pick.replace(/\\\\./g, '-')" in APP
    assert "rd_version_" in APP
    assert "use_version" in APP
    assert "verb === 'version'" in APP
    assert "event.data.version" in APP

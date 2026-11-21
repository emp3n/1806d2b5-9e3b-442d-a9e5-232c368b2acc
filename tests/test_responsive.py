"""The layout contract for phone, tablet and desktop, checked without a browser.

Browser measurements for 320/360/390/430/600/768/1024/1440 px live in
``docs/STRUCTURE.md``. These tests fail cheaply when a selector or breakpoint
drifts so the measured layout cannot silently regress.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "theme.py").read_text()
CARD = (ROOT / "card.py").read_text()
APP = (ROOT / "app.py").read_text()
JS = re.search(r"<script>(.*?)</script>", CARD, re.S).group(1)


def _media(query: str) -> str:
    start = CSS.index(f"@media {query} {{")
    depth, i = 0, start
    while i < len(CSS):
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start:i + 1]
        i += 1
    raise AssertionError(f"unterminated media query {query}")


def test_the_page_declares_a_mobile_viewport():
    assert 'name="viewport"' in APP
    assert "width=device-width, initial-scale=1" in APP
    assert "viewport-fit=cover" in APP  # notches and the safe-area dock


def test_phone_stacks_every_fact_chip():
    phone = _media("(max-width: 520px)")
    assert ".rd-fact-row { flex-direction: column; align-items: flex-start;" in phone
    assert ".rd-fact-row > .rd-fact:last-child { flex: 0 0 auto;" in phone
    assert max_width(phone) is True


def max_width(block: str) -> bool:
    return "max-width: 100%" in block


def test_tablet_and_phone_share_the_bottom_dock():
    dock = _media("(max-width: 760px)")
    assert "position: fixed" in dock
    assert "flex-direction: row" in dock
    assert "env(safe-area-inset-bottom)" in dock
    assert "backdrop-filter: blur(16px)" in dock
    assert ".rd-tab-name { display: none; }" in dock
    assert "touch-action: pan-y" in CSS


def test_small_phone_shrinks_only_the_dock():
    small = _media("(max-width: 430px)")
    assert "--dock: 72px" in small
    assert ".rd-tab { width: 52px; height: 52px; }" in small


def test_desktop_pairs_are_not_stacked():
    # the stacking rule must live ONLY in the phone media query
    outside = CSS[:CSS.index("@media (max-width: 520px)")]
    assert ".rd-fact-row { flex-direction: column" not in outside
    assert ".rd-fact-row { display: flex; align-items: flex-start; gap: 6px; }" in outside


def test_fit_never_scales_a_phone_below_readable_size():
    assert "var phone = window.matchMedia('(max-width: 760px)')" in JS
    assert "if (phone.matches) return;" in JS
    assert "var FLOOR = 0.62" in JS
    assert "stage.classList.add('rd-scroll')" in JS


def test_the_stage_scrolls_on_a_phone_and_uses_dvh():
    assert "@supports (height: 100dvh)" in CSS
    assert "height: 100dvh" in CSS
    stage = CSS[CSS.index(".rd-stage {"):CSS.index("}", CSS.index(".rd-stage {"))]
    assert "overflow-y: auto" in _media("(max-width: 760px)")
    assert "box-sizing: border-box" in stage


def test_motion_and_hover_are_toned_down_for_touch():
    assert _media("(prefers-reduced-motion: reduce)")
    assert "animation-duration: .001ms" in _media("(prefers-reduced-motion: reduce)")
    assert _media("(hover: none)")
    assert ".rd-tab:hover .rd-tab-dot" in _media("(hover: none)")

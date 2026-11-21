"""The phone layout, checked without a browser.

The desktop card lives on one screen and is scaled to fit. A phone cannot do
that: 0.7 of a 13 px label is not text, and the scaled card still overflowed the
stage, whose padding was added outside ``100vh`` — so the bottom of the card sat
behind the bottom of the window with no way to reach it (``overflow: hidden``).
These tests pin the three decisions that fixed it:

* on a phone the rail is a frosted dock at the bottom, where the thumb is, and
  it holds still (the name plate that a tap left open is gone);
* the stage scrolls, with a scrollbar styled from the same tokens, and it only
  scrolls when the content is taller than the window;
* a window too short even for the smallest readable scale scrolls too, instead
  of being cropped at both ends.

The measured browser runs are in ``REAL-DATA.md``; this file is the cheap net
that fails when the CSS or the fit function drifts back.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "ui" / "theme.py").read_text()
CARD = (ROOT / "ui" / "card.py").read_text()
SCRIPT = re.search(r"<script>(.*?)</script>", CARD, re.S).group(1)


def media(query: str) -> str:
    """The body of one @media block, by its query."""
    start = CSS.index("@media " + query)
    depth = 0
    for i in range(start, len(CSS)):
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start:i + 1]
    raise AssertionError(f"unclosed {query}")


PHONE = media("(max-width: 760px)")


# ---------------------------------------------------------------- the dock
def test_the_phone_rail_is_a_dock_at_the_bottom():
    rail = re.search(r"\.rd-rail \{(.*?)\}", PHONE, re.S).group(1)
    assert "position: fixed" in rail
    assert "bottom: 0" in rail
    assert "left: 0" in rail and "right: 0" in rail
    assert "flex-direction: row" in rail


def test_the_dock_is_frosted_and_clears_the_home_indicator():
    rail = re.search(r"\.rd-rail \{(.*?)\}", PHONE, re.S).group(1)
    assert "backdrop-filter" in rail
    assert "env(safe-area-inset-bottom)" in rail
    stage = re.search(r"\.rd-stage \{(.*?)\}", PHONE, re.S).group(1)
    # the card must be able to scroll clear of the dock, and the clearance is
    # the dock's own height (a token), not a hand-tuned number
    assert "safe-area-inset-bottom" in stage
    assert "calc(var(--dock) + 4px + env(safe-area-inset-bottom))" in stage
    assert re.search(r"--dock: 76px", CSS)


def test_the_rail_holds_still_on_touch():
    """No circle slides out from under a fingertip, and no plate stays open."""
    assert re.search(r"\.rd-tab-name \{ display: none; \}", PHONE)
    no_hover = media("(hover: none)")
    assert "transform: none" in no_hover
    # the pressed state survives the hover rule
    assert '.rd-tab[aria-pressed="true"] .rd-tab-dot' in no_hover


def test_the_circle_keeps_its_name_without_the_plate():
    """The plate is hidden on phones, so the button carries its own name."""
    import card

    labels = dict(card.VIEWS)
    assert set(labels.values()) == {"Overview", "Services", "Database", "Shell", "Logs"}
    # the rail template puts the name on the button, not only in the plate
    assert 'aria-label="{label}" aria-pressed=' in CARD


# ------------------------------------------------------------- the scrolling
def test_the_stage_scrolls_and_only_when_it_must():
    stage = re.search(r"\.rd-stage \{(.*?)\}", PHONE, re.S).group(1)
    assert "overflow-y: auto" in stage            # auto, not scroll: no bar when it fits
    assert "-webkit-overflow-scrolling: touch" in stage
    assert "display: flex" in stage and "flex-direction: column" in stage
    # a column flex container centres on the main axis, which clips the head
    assert "justify-content: flex-start" in stage


def test_the_stage_padding_comes_out_of_the_window():
    """The bug behind "I cannot scroll to the end": padding was added to 100vh."""
    base = re.search(r"\.rd-stage \{(.*?)\}", CSS, re.S).group(1)
    assert "box-sizing: border-box" in base
    assert "height: 100vh" in base


def test_the_stage_follows_the_real_phone_viewport():
    """100vh is taller than the visible area while the URL bar is out, which
    would push the dock below the reachable edge."""
    assert "@supports (height: 100dvh)" in CSS
    assert re.search(r"@supports \(height: 100dvh\) \{\s*\.rd-stage \{ height: 100dvh; \}", CSS)


def test_the_scrollbar_is_styled_from_the_card_tokens():
    assert "scrollbar-width: thin" in CSS
    assert "scrollbar-color:" in CSS
    assert ".rd-stage::-webkit-scrollbar-thumb" in CSS
    thumb = re.search(r"\.rd-stage::-webkit-scrollbar-thumb \{(.*?)\}", CSS, re.S).group(1)
    # a pill (999 px) reads as an extreme card radius; the thumb reuses one of
    # the card's own corner sizes instead
    radius = re.search(r"border-radius: ([^;]+);", thumb).group(1).strip()
    assert radius in {"4px", "8px", "14px", "20px", "50%"}, radius
    thumb = re.search(r"\.rd-stage::-webkit-scrollbar-thumb \{(.*?)\}", CSS, re.S).group(1)
    assert "var(--text)" in thumb and "background-clip: padding-box" in thumb


def test_a_window_too_short_to_read_scrolls_instead_of_cropping():
    scroll = re.search(r"\.rd-stage\.rd-scroll \{(.*?)\}", CSS, re.S).group(1)
    assert "overflow-y: auto" in scroll
    frame = re.search(r"\.rd-stage\.rd-scroll \.rd-frame \{(.*?)\}", CSS, re.S).group(1)
    # auto margins are what keep both ends reachable, and they must not leak
    # into the scaled mode: there the box is centred and scaled about its
    # centre, which is what makes the picture fit
    assert "margin-block: auto" in frame
    base = re.search(r"^\.rd-frame \{(.*?)\}", CSS, re.S | re.M).group(1)
    assert "margin-block" not in base
    assert "align-items: center" in base


# ------------------------------------------------------------------ the script
def test_phones_are_never_scaled_down():
    assert "phone.matches" in SCRIPT
    assert "window.matchMedia('(max-width: 760px)')" in SCRIPT
    body = SCRIPT[SCRIPT.index("function fit()"):]
    body = body[: body.index("\n    }")]
    assert "if (phone.matches) return;" in body


def test_the_fit_floor_scrolls_rather_than_shrinking_further():
    body = SCRIPT[SCRIPT.index("function fit()"):]
    body = body[: body.index("\n    }")]
    assert "scale >= FLOOR" in body
    assert "stage.classList.add('rd-scroll')" in body
    assert "Math.max(0.62" not in SCRIPT          # the old clamp that cropped


def test_a_section_switch_starts_at_the_top():
    assert "box.scrollTop = 0" in SCRIPT


def test_a_finger_can_scrub_and_let_go():
    assert "addEventListener('pointerdown'" in SCRIPT
    assert "addEventListener('pointerup'" in SCRIPT
    assert "addEventListener('pointercancel', release)" in SCRIPT
    assert "if (event.pointerType !== 'mouse') release();" in SCRIPT
    # the vertical drag belongs to the page, not to the graph
    assert "touch-action: pan-y" in CSS

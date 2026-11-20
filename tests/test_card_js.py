"""Invariants of the card's own script, checked without a browser.

These are cheap structural tests for bugs that no /proc fixture can catch: the
page is one IIFE, and a name declared twice in it silently breaks a feature.
The bug that prompted this file: the fit-to-window code declared
``var frame = document.querySelector('.rd-frame')`` while ``function frame`` was
the animation loop, so hoisting made ``requestAnimationFrame(frame)`` receive a
DOM node — the loop died on its first frame and every number on the card froze
at its server-rendered value (CPU stuck at 0.0 %, no hover cursor).
"""

from __future__ import annotations

import re
from pathlib import Path

CARD = Path(__file__).resolve().parents[1] / "card.py"
THEME = Path(__file__).resolve().parents[1] / "theme.py"

SCRIPT = re.search(r"<script>(.*?)</script>", CARD.read_text(), re.S).group(1)


def declared_functions() -> list[str]:
    return re.findall(r"^\s*function ([A-Za-z_$][\w$]*)\s*\(", SCRIPT, re.M)


def functions() -> set[str]:
    return set(declared_functions())


def variables() -> set[str]:
    names: set[str] = set()
    for line in re.findall(r"\bvar ([^;]+);", SCRIPT):
        for part in line.split(","):
            name = part.split("=")[0].strip()
            if re.fullmatch(r"[A-Za-z_$][\w$]*", name):
                names.add(name)
    return names


def test_no_variable_shadows_a_function():
    """A `var` with the same name as a `function` wins after its assignment and
    takes the whole loop down with it."""
    both = functions() & variables()
    assert not both, f"these names are both a function and a variable: {sorted(both)}"


def test_no_function_is_declared_twice():
    """A nested `function set(...)` shadowed the shared one, so the power
    button's stop callback threw a ReferenceError outside the helper's scope
    and the panel reported a successful stop as "API unreachable"."""
    names = declared_functions()
    duplicates = {name for name in names if names.count(name) > 1}
    assert not duplicates, f"declared more than once: {sorted(duplicates)}"


def test_the_power_buttons_do_not_reach_for_a_scoped_helper():
    """`set` and `show` must be IIFE-level: callbacks run outside the block
    that declares them."""
    for helper in ("set", "say"):
        declarations = [m.start() for m in re.finditer(rf"function {helper}\(", SCRIPT)]
        assert len(declarations) == 1, helper
        # the one declaration sits at four-space indentation (IIFE body level)
        line = SCRIPT[: declarations[0]].count("\n")
        assert SCRIPT.splitlines()[line].startswith("    function "), helper


def test_the_animation_loop_is_a_function_at_call_time():
    """The loop re-arms itself; if the name is reassigned the page freezes."""
    assert re.search(r"window\.requestAnimationFrame\(frame\);", SCRIPT)
    assert "function frame(" in SCRIPT
    assert not re.search(r"\bvar frame\b", SCRIPT)


def test_the_hover_cursor_is_reachable_and_visible():
    """Hover must add `.on` to a dot that exists in the markup and is painted."""
    assert "spark.cursor.classList.add('on')" in SCRIPT
    assert "spark.cursor.style.left" in SCRIPT and "spark.cursor.style.top" in SCRIPT
    assert '<i class="rd-cursor" aria-hidden="true"></i>' in CARD.read_text()
    css = THEME.read_text()
    assert ".rd-cursor.on { opacity: 1; }" in css
    assert ".rd-cursor" in css and "border-radius: 50%" in css


def test_pointer_and_keyboard_scrub_share_one_path():
    for handler in ("pointermove", "pointerleave", "keydown"):
        assert handler in SCRIPT, handler
    # the number under the pointer is the sample, not an interpolation
    assert "show(spark, spark.buf[index])" in SCRIPT


def test_sub_percent_values_are_formatted_not_rounded_to_zero():
    assert "value.toFixed(2)" in SCRIPT  # CPU below 1 %
    assert "share.toFixed(2)" in SCRIPT  # its "% of one core"


def test_a_dead_feed_is_named_instead_of_frozen():
    """The phone bug: a refused fetch left the last numbers standing and said
    nothing, so a frozen panel looked exactly like a live one."""
    assert "function lose()" in SCRIPT
    assert "'feed unreachable'" in SCRIPT
    assert "card.setAttribute('data-feed', 'lost')" in SCRIPT
    # the footer names the address that failed, which is how "127.0.0.1" on a
    # phone stops being a mystery
    assert "set('[data-source]', 'no feed · '" in SCRIPT
    # and the old silent catch is gone
    assert "catch(function () {{ card.setAttribute('data-link', 'lost'); }})" not in SCRIPT


def test_the_poll_loop_cannot_stack_up_and_backs_off():
    """A hanging request on a phone network used to pile up behind the next
    one every second; the loop is a chain with a deadline now."""
    assert "AbortController" in SCRIPT
    assert "window.clearTimeout(cut)" in SCRIPT
    assert "Math.pow(2, misses - 1)" in SCRIPT and "CEILING" in SCRIPT
    assert "setInterval(poll" not in SCRIPT


def test_a_phone_resumes_the_loop_when_it_comes_back():
    """Browsers throttled the old interval while the tab was hidden, so the
    first thing a returning user saw was a stale card."""
    for event in ("visibilitychange", "pageshow", "online"):
        assert event in SCRIPT, event
    assert "document.hidden" in SCRIPT


def test_the_stale_numbers_step_back():
    css = THEME.read_text()
    assert '.rd-card[data-feed="lost"] > :not(.rd-head):not(.rd-foot) { opacity: .45; }' in css


def test_the_card_only_promises_a_feed_when_one_exists():
    from card import render
    from demo_data import build_state

    live = render(build_state(0), api="http://127.0.0.1:8598/api/state", action_key="k")
    sample = render(build_state(0))

    def tag(html: str) -> str:
        return re.search(r'<article class="rd-card"[^>]*>', html).group(0)

    assert 'data-feed="ok"' in tag(live)
    # the sample card has no feed to lose, so it must not promise one
    assert "data-feed" not in tag(sample)


def test_stop_asks_twice_when_the_page_rides_the_tunnel():
    """Stopping the connector from a page that arrives over its tunnel closes
    the page too: the second press is the point of no return, and it is
    announced before anything is sent."""
    assert "var RD_SEVER" in SCRIPT
    assert "This page comes through this tunnel" in SCRIPT
    arm = SCRIPT.index("RD_SEVER && !power.classList.contains('is-armed')")
    send = SCRIPT.index("act(gone ? 'start' : 'stop'")
    assert arm < send, "the second press must come after the warning, not instead of it"
    assert "Click again" in SCRIPT
    # and starting is never gated: it is the way back
    assert "if (!gone) {" in SCRIPT

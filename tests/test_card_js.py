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
    assert "'state unreachable'" in SCRIPT
    assert "card.setAttribute('data-feed', 'lost')" in SCRIPT
    # the footer names the path that failed: it is a file this page's own
    # server publishes, so a failure means the app under it is gone
    assert "set('[data-source]', 'no state · '" in SCRIPT
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
    assert '.rd-card[data-feed="lost"] > :not(.rd-head):not(.rd-foot),' in css
    assert '.rd-card[data-feed="stalled"] > :not(.rd-head):not(.rd-foot) { opacity: .45; }' in css


def test_the_heartbeat_separates_stalled_from_live():
    """A state file nobody rewrites leaves the last numbers looking live."""
    assert "function beat(data)" in SCRIPT
    assert "typeof data.ts !== 'number'" in SCRIPT
    assert "RD_STALE" in SCRIPT and "card.setAttribute('data-feed', 'stalled')" in SCRIPT
    assert "beat(data)" in SCRIPT.split("function beat(data)")[1], "beat() is never used"


def test_staleness_is_observed_not_subtracted_from_the_clock():
    """A phone whose clock is minutes ahead must not see a live file as stalled:
    the page compares the stamp to the last stamp it saw, not to Date.now()."""
    body = SCRIPT.split("function beat(data)")[1].split("function apply(")[0]
    assert "Date.now() / 1000 - data.ts" not in body, "clocks are subtracted again"
    assert "beatSeen = now" in body and "data.ts !== beatTs" in body
    assert "now - beatSeen" in body


def test_the_poll_defeats_a_heuristic_cache():
    """The static file has no Cache-Control, so a plain GET may be served from
    the browser's heuristic cache and freeze the card for minutes."""
    assert "RD_API + '?t=' + Date.now()" in SCRIPT


def test_the_card_only_promises_a_feed_when_one_exists():
    from card import render
    from demo_data import build_state

    live = render(build_state(0), state_url="/app/static/state.json")
    sample = render(build_state(0))

    def tag(html: str) -> str:
        return re.search(r'<article class="rd-card"[^>]*>', html).group(0)

    assert 'data-feed="ok"' in tag(live)
    # the sample card has no feed to lose, so it must not promise one
    assert "data-feed" not in tag(sample)


def test_the_verbs_are_on_the_card_where_the_design_puts_them():
    """Stop and Restart belong in the card's own header (they were there, and
    they were moved out to the page corner by mistake)."""
    markup = CARD.read_text()
    assert '<div class="rd-verbs">' in markup
    assert 'data-verb="power"' in markup and 'data-verb="restart"' in markup
    assert markup.count('class="rd-round"') == 2
    # the header order: mark, identity, verbs — the buttons sit at its right end
    head = markup[markup.index('<header class="rd-head">'):markup.index("</header>")]
    assert head.index("rd-id") < head.index("rd-verbs")
    css = THEME.read_text()
    assert ".rd-verbs { display: flex;" in css
    assert ".rd-round.is-armed" in css and ".rd-round[aria-pressed=\"true\"]" in css


def test_a_card_button_asks_the_app_instead_of_acting_itself():
    """An iframe cannot start a process. The button must not pretend to: it asks
    the app (?rd=<verb>), and the app is the only thing that performs a verb."""
    assert "function fire(verb)" in SCRIPT
    assert "url.searchParams.set('rd', verb)" in SCRIPT
    assert "window.top.location.replace" in SCRIPT
    # no second port, no action route, no key
    assert "X-RD-Action" not in SCRIPT and "/api/action" not in SCRIPT
    assert "data-action=" not in CARD.read_text()
    # the app consumes the request and clears it, so a reload cannot replay it
    shell = (CARD.parent / "app.py").read_text()
    assert 'st.query_params.get("rd")' in shell
    assert "st.query_params.clear()" in shell
    assert "statefeed.sampler(port).act(pending)" in shell


def test_stopping_from_the_tunnel_page_warns_first_and_names_the_way_back():
    """Stopping the connector closes the page it was asked from, and the stop is
    remembered — so the card has to say which name goes dark and where to return."""
    assert "var RD_HOST" in SCRIPT and "var RD_BACK" in SCRIPT
    assert "Stopping closes " in SCRIPT and "click again" in SCRIPT
    assert "is-armed" in SCRIPT and "armTimer" in SCRIPT
    # an armed button forgets itself; a confirmation left open is not a stop
    assert "window.setTimeout(disarm, 5000)" in SCRIPT
    shell = (CARD.parent / "app.py").read_text()
    assert "served_through_tunnel" in shell
    assert "back_in=back_in_url" in shell and "bootstrap.cloud_url()" in shell


def test_the_glyph_follows_the_process_not_the_click():
    assert "function retitle(up)" in SCRIPT
    # painted from the state the app reports, in both directions
    assert SCRIPT.count("retitle(true)") == 1 and SCRIPT.count("retitle(false)") == 1


def test_the_card_has_no_http_action_endpoint():
    """One channel. The card asks; the app acts; the state file answers."""
    assert "act(" not in SCRIPT
    assert "POST" not in SCRIPT.upper().replace("POSTER", "")
    assert "data-action=" not in CARD.read_text()

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

CARD = Path(__file__).resolve().parents[1] / "ui" / "card"
CARD_SRC = "\n".join(p.read_text() for p in CARD.glob("*.py"))
THEME = Path(__file__).resolve().parents[1] / "ui" / "theme.py"

SCRIPT = re.search(r"<script>(.*?)</script>", CARD_SRC, re.S).group(1)


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
    assert '<i class="rd-cursor" aria-hidden="true"></i>' in CARD_SRC
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
    # with no footer, the status line itself is the one honest place that
    # names the failure — the card never pretends the old numbers are live
    assert "set('[data-status-text]', 'state unreachable')" in SCRIPT
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
    assert '.rd-card[data-feed="lost"] > :not(.rd-head),' in css
    assert '.rd-card[data-feed="stalled"] > :not(.rd-head) { opacity: .45; }' in css


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


def test_the_app_installs_a_postmessage_bridge():
    """The card cannot navigate an ancestor of the sandboxed Cloud viewer. The
    bridge presses one of three native Streamlit buttons, so the action is a
    rerun over the open socket — no page reload, no corner toast."""
    shell = (CARD.parent.parent / "app.py").read_text()
    assert "window.__rdVerbBridge" in shell
    assert "window.addEventListener('message'" in shell
    assert "'.st-key-rd_verb_' + verb + ' button'" in shell
    assert "button.click()" in shell
    assert "unsafe_allow_javascript=True" in shell
    assert "st.toast(" not in shell
    assert 'st.query_params.get("rd")' not in shell


def test_the_result_is_shown_once_in_the_cards_own_bottom_toast():
    """No Streamlit toast in the corner and no page reload: the outcome travels
    in state.json and the card renders it in its own bottom notification."""
    assert "lastActionAt" in SCRIPT
    assert "act.at !== lastActionAt" in SCRIPT
    assert "say(act.ok ?" in SCRIPT
    assert "act.ok ? 'ok' : 'crit'" in SCRIPT
    # the notice is installed BEFORE the found/empty split, so a Stop (which
    # makes found:false) still shows its result
    assert SCRIPT.index("var act = data.last_action") < SCRIPT.index("if (!data || !data.found)")
    shell = (CARD.parent.parent / "app.py").read_text()
    assert "st.toast(" not in shell
    assert 'outcome["at"] = int(time.time())' in shell
    assert "sampler.last_action = outcome" in shell


def test_the_native_verb_buttons_are_present_but_invisible():
    shell = (CARD.parent.parent / "app.py").read_text()
    assert 'with st.container(key=f"rd_verb_{_verb}")' in shell
    assert "on_click=_run_verb" in shell
    assert ".st-key-rd_verb_stop" in shell
    assert "left: -9999px" in shell


def test_sample_mode_does_not_poll_a_stale_live_file():
    shell = (CARD.parent.parent / "app.py").read_text()
    assert "state_url = bootstrap.CARD_STATE_PATH if use_live else None" in shell


def test_the_chosen_memory_unit_survives_every_poll():
    """The server text is always MB. Writing it on each poll used to flip the
    display back to MB while the GB toggle stayed pressed."""
    assert "spark.key === 'mem'" in SCRIPT
    assert "? fmtMem(data.mem)" in SCRIPT
    assert "if (spark.key === 'mem') show(spark, spark.shown)" in SCRIPT


def test_shard_is_its_own_chip_beside_origin_not_inside_it():
    from card import render
    from demo_data import build_state

    state = build_state(0)
    state["origin_geo"] = {"cc": "US", "country": "United States",
                           "region": "Missouri", "city": "Kansas City"}
    state["edges_geo"] = [{"code": "sea01", "cc": "US"},
                          {"code": "fra04", "cc": "DE"}]
    state["shard"] = "shard-3"
    page = render(state, theme="dark")
    start = page.index('<div class="rd-facts">')
    facts = page[start:page.index('<div class="rd-measures"')]
    origin_chip = facts[facts.index('data-tone="origin"'):facts.index('data-tone="shard"')]

    # origin keeps its flag + city, and no shard inside it
    assert origin_chip.index('data-fact="origin"') < origin_chip.index('data-origin-flag')
    assert "app/static/flags/us.svg" in origin_chip
    assert "Kansas City" in origin_chip and "Missouri" not in origin_chip
    assert "data-shard" not in origin_chip

    # the shard is a sibling chip of the same .rd-fact class, containing only
    # the cluster value — no label, no "shard - " duplication
    shard_chip = '<span class="rd-fact" data-tone="shard"><span data-shard>shard-3</span></span>'
    assert shard_chip in facts
    assert facts.index('data-tone="origin"') < facts.index('data-tone="shard"') \
           < facts.index('data-edge-list')
    assert "rd-shard-line" not in page and "rd-shard-chip" not in page

    assert '<span class="rd-edge">sea01' in page
    assert 'class="rd-flag rd-flag--edge"' in page
    css = (CARD.parent / "theme.py").read_text()
    assert ".rd-flag--edge {" in css and "translateY(-3px)" in css
    assert '.rd-fact[data-tone="shard"]' in css
    assert ".rd-fact { gap: 7px; }" in css
    assert "margin-right: 6px" in css
    assert "set('[data-shard]', (data && data.shard) || '')" in SCRIPT
    assert "shard - " not in page and "shard - " not in SCRIPT


def test_connections_chip_sits_in_the_edges_row():
    from card import render
    from demo_data import build_state

    state = build_state(0)
    state["shard"] = "shard-3"
    state["connections"] = 5
    page = render(state, theme="dark")
    start = page.index('<div class="rd-facts">')
    facts = page[start:page.index('<div class="rd-measures"')]
    assert facts.index('data-edge-list') < facts.index('data-tone="connections"')
    # the Edges label is gone so every code + mini flag fits on one line
    assert '<em>Edges</em>' not in facts
    edge_chip = facts[facts.index('data-edge-list') - 40:facts.index('data-tone="connections"')]
    assert '<span class="rd-edge-list"' in edge_chip
    assert "set('[data-fact=\"edges\"]'" not in SCRIPT
    assert '<em>connections</em><span data-connections>5</span>' in facts
    assert 'data-tone="connections"' not in facts[:facts.index('data-tone="shard"')]
    assert '<em>connections</em><span data-connections>0</span>' in render(
        build_state(0, incident=True), theme="dark")
    assert "function paintConnections(data)" in SCRIPT
    assert "paintConnections(data);" in SCRIPT and "paintConnections({{}});" in SCRIPT
    assert "set('[data-connections]', String((data && data.connections) || 0))" in SCRIPT
    assert "var BEAT = 500" in SCRIPT


def test_rows_size_the_origin_not_the_shard():
    css = THEME.read_text()
    assert ".rd-facts { display: flex; flex-direction: column;" in css
    assert ".rd-fact-row > .rd-fact:first-child { flex: 1 1 auto; }" in css
    assert ".rd-fact-row > .rd-fact:last-child { flex: 0 0 auto; }" in css
    facts_rule = css[css.index('.rd-facts {'):css.index('.rd-fact-row {')]
    assert "grid" not in facts_rule


def test_paint_geo_does_not_rebuild_flags_on_every_poll():
    assert "var flagSig = '', edgeSig = ''" in SCRIPT
    assert "if (cc !== flagSig)" in SCRIPT
    assert "if (sig === edgeSig) return;" in SCRIPT


def test_geo_paint_is_guarded_against_injected_codes():
    assert "function paintGeo(data)" in SCRIPT
    assert "/^[A-Z]{{2}}$/.test(g.cc || '')" in SCRIPT
    assert "'app/static/flags/' + cc.toLowerCase() + '.svg'" in SCRIPT
    assert "slot.textContent = ''" in SCRIPT


def test_the_card_polls_a_relative_path():
    """The srcdoc frame resolves it against the app document: "/" on a plain
    host, "/~/+/" on Cloud. A leading slash lost the viewer prefix."""
    shell = (CARD.parent.parent / "app.py").read_text()
    assert "state_url = bootstrap.CARD_STATE_PATH" in shell
    assert "state_url = bootstrap.STATE_PATH" not in shell


def test_a_verb_reloads_the_app_document_not_the_outer_wrapper():
    """On Community Cloud window.top is the wrapper at "/", while the app runs
    in an inner frame at "/~/+/". Navigating top dropped the ?rd= verb, and a
    sandbox forbids navigating an intermediate ancestor at all."""
    assert "{{rd: verb}}, '*'" in SCRIPT and "{{rd: 'version', version: value}}" in SCRIPT
    assert "window.parent.location" not in SCRIPT
    assert "window.top.location" not in SCRIPT
    shell = (CARD.parent.parent / "app.py").read_text()
    assert "sandbox=" not in shell, "a sandbox blocks ancestor navigation"


def test_the_card_document_is_escaped_before_it_is_interpolated():
    """Without a sandbox the interpolated values must not become markup."""
    assert "def esc(value: object) -> str" in CARD_SRC
    assert "html.escape(str(value), quote=True)" in CARD_SRC


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
    markup = CARD_SRC
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
    assert "function fire(verb, value)" in SCRIPT
    assert "{{rd: verb}}, '*'" in SCRIPT and "{{rd: 'version', version: value}}" in SCRIPT
    # no second port, no action route, no key
    assert "X-RD-Action" not in SCRIPT and "/api/action" not in SCRIPT
    assert "data-action=" not in CARD_SRC
    # a native button callback owns the verb; no URL round trip at all
    shell = (CARD.parent.parent / "app.py").read_text()
    assert "def _run_verb(verb: str)" in shell
    assert "sampler.act(verb)" in shell
    assert "on_click=_run_verb" in shell


def test_stopping_from_the_tunnel_page_warns_first_and_names_the_way_back():
    """Stopping the connector closes the page it was asked from, and the stop is
    remembered — so the card has to say which name goes dark and where to return."""
    assert "var RD_HOST" in SCRIPT and "var RD_BACK" in SCRIPT
    assert "Stopping closes " in SCRIPT and "click again" in SCRIPT
    assert "is-armed" in SCRIPT and "armTimer" in SCRIPT
    # an armed button forgets itself; a confirmation left open is not a stop
    assert "window.setTimeout(disarm, 5000)" in SCRIPT
    shell = (CARD.parent.parent / "app.py").read_text()
    assert "served_through_tunnel" in shell
    assert "back_in=back_in_url" in shell and "bootstrap.cloud_url()" in shell


def test_the_glyph_follows_the_process_not_the_click():
    assert "function retitle(up)" in SCRIPT
    # painted from the state the app reports, in both directions
    assert SCRIPT.count("retitle(true)") == 1 and SCRIPT.count("retitle(false)") == 1


def test_the_card_has_no_http_action_endpoint():
    """One channel. The card asks; the app acts; the state file answers."""
    assert "act(" not in SCRIPT
    assert "method:" not in SCRIPT and "X-RD-Action" not in SCRIPT
    assert "data-action=" not in CARD_SRC

def test_the_tunnel_hostname_reaches_the_card_as_a_string():
    """The card's own ``host`` is the machine's cores and RAM. When the render
    parameter was also called ``host`` it shadowed that local, and the browser got
    RD_HOST as an object: stopping from the tunnel page warned "Stopping closes
    [object Object]" instead of naming the hostname that goes dark."""
    from card import render
    from demo_data import build_state

    page = render(build_state(0), tunnel_host="streamlit.idealis.cc",
                  back_in="https://example.streamlit.app")
    assert 'var RD_HOST = "streamlit.idealis.cc";' in page
    assert "[object Object]" not in page
    assert 'var RD_BACK = "https://example.streamlit.app";' in page
    # no render parameter may collide with a name the card already declares:
    # the signature may take ``tunnel_host``, never a bare ``host``
    assert "\n    host: str = \"\"," not in CARD_SRC
    assert "\n    tunnel_host: str = \"\"," in CARD_SRC

"""The console shell: five sections, one built, and the connector behind it.

A vertical rail on the right — Overview, Services, Database, Shell, Logs — and
the cloudflared card inside Services. The design brief lives in
``.uimix/DESIGN-BRIEF.json`` (recorded through the uimixes skill).

Three things happen before the card is drawn, and they are the whole point of
this file:

1. **the tunnel** — ``bootstrap.ensure_tunnel`` adopts the connector a previous
   script process left running, or starts the operator's named tunnel from the
   ``CLOUDFLARE_TUNNEL_TOKEN`` secret. The route on Cloudflare's side is
   ``streamlit.idealis.cc -> http://localhost:8501``, so the app must be the
   thing on 8501, and it is: Streamlit Cloud runs it there, and
   ``tools/serve.sh`` does the same locally;
2. **the numbers** — ``statefeed`` samples the connector once a second into
   ``static/state.json``, which Streamlit serves itself at
   ``/app/static/state.json``. Same origin everywhere, so the card can poll it
   on a phone, from ``*.streamlit.app`` and from the operator's hostname;
3. **the buttons** — Stop and Restart are the card's own round buttons, in the
   card's header where the design puts them. A click asks this process for the
   verb (``?rd=``), the verb runs here against the one connector the sampler
   reads, and the card shows the outcome a second later because it reads the
   same probe. A button never reports a result it did not get.

``?source=live`` (default) is the real connector; ``?source=sample`` keeps the
lab's synthetic series and touches nothing.

Query parameters: ``section=overview|services|database|shell|logs``,
``theme=light|dark|auto`` (auto follows the OS), ``palette=linen|slate|moss``,
``incident=0|1``, ``tick=N``,
``source=live|sample``.
"""

from __future__ import annotations

import html
import os
import time

import streamlit as st

import bootstrap
import statefeed
from card import render
from demo_data import build_state
from live import idle_state
from palette import DEFAULT_PALETTE, PALETTES, semantic_tokens
from theme import THEME_CSS

VIEWS = ("overview", "services", "database", "shell", "logs")

st.set_page_config(page_title="cloudflared — card study", layout="wide")

# The study is one full-bleed surface: no Streamlit chrome, no page scroll, the
# frame takes the whole window. The Stop and Restart buttons are drawn by the
# card itself (it is the design's own header), so nothing is docked over it.
st.markdown(
    """<style>
    [data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
    #MainMenu, footer { display: none !important; }
    [data-testid="stAppViewContainer"], [data-testid="stMain"],
    [data-testid="stMainBlockContainer"], [data-testid="stAppViewBlockContainer"],
    [data-testid="stVerticalBlock"] {
      padding: 0 !important; margin: 0 !important; gap: 0 !important;
      max-width: none !important; height: 100vh; overflow: hidden !important;
    }
    html, body { overflow: hidden !important; height: 100vh; }
    iframe { display: block; border: 0 !important; width: 100vw !important; height: 100vh !important; }
    /* a phone's real viewport is smaller than 100vh while the URL bar is out */
    @supports (height: 100dvh) {
      [data-testid="stVerticalBlock"], html, body { height: 100dvh; }
      iframe { height: 100dvh !important; }
    }

    /* a real tunnel failure has to be visible over the full-bleed frame */
    .rd-alert {
      position: fixed; left: 16px; bottom: 16px; z-index: 100000;
      max-width: min(560px, calc(100vw - 32px));
      padding: 10px 14px; border-radius: 12px; font-size: 13px; line-height: 1.45;
      background: rgba(60, 20, 20, .92); color: #F3DEDE;
      border: 1px solid rgba(220, 120, 120, .5);
      box-shadow: 0 6px 24px rgba(0, 0, 0, .35); white-space: pre-wrap;
    }
    .rd-alert b { display: block; margin-bottom: 2px; font-weight: 600; }
    /* the native buttons are only an input channel for the card's postMessage;
       they must stay in the DOM but never take space or be seen */
    .st-key-rd_verb_stop, .st-key-rd_verb_start, .st-key-rd_verb_restart {
      position: absolute !important; left: -9999px !important; top: 0 !important;
      width: 1px !important; height: 1px !important; overflow: hidden !important;
      opacity: 0 !important; pointer-events: none !important;
    }

    </style>""",
    unsafe_allow_html=True,
)

print("card: app.py started", flush=True)

params = st.query_params

theme = str(params.get("theme", "dark")).lower()
if theme not in ("light", "dark", "auto"):
    theme = "dark"
palette = str(params.get("palette", DEFAULT_PALETTE)).lower()
if palette not in PALETTES:
    palette = DEFAULT_PALETTE
meta_light = semantic_tokens(palette, "light")["--card"]
meta_dark = semantic_tokens(palette, "dark")["--card"]
incident = str(params.get("incident", "0")) == "1"
try:
    tick = max(0, min(1000, int(str(params.get("tick", "0")))))
except ValueError:
    tick = 0
view = str(params.get("section", "services"))
if view not in VIEWS:
    view = "services"

# ---- which port is this app on ---------------------------------------------
# The Cloudflare route points at localhost:8501, so that is the port the tunnel
# has to feed. Streamlit Cloud runs the app there; tools/serve.sh does the same.
def app_port() -> int:
    raw = os.environ.get("CARD_PORT")
    if raw and str(raw).isdigit():
        return int(raw)
    try:
        value = st.get_option("server.port")
        if value:
            return int(value)
    except Exception:  # noqa: BLE001 - no config, no problem: 8501 is the contract
        pass
    return bootstrap.ingress_port()


port = app_port()

# ---- where the numbers come from -------------------------------------------
# ?source=live (default) reads the connector on this host; ?source=sample keeps
# the lab data and starts nothing.
use_live = str(params.get("source", "live")).lower() != "sample"
page_host = ""
try:
    page_host = (st.context.url or "")
except Exception:  # noqa: BLE001 - no request context outside a script run
    page_host = ""
served_through_tunnel = bool(page_host) and bootstrap.public_hostname() in page_host

# The card's buttons never navigate the page. A click is a postMessage to this
# document, which programmatically presses one of three invisible native
# Streamlit buttons. A native button reruns the script over the existing
# WebSocket — no navigation, no fresh render of the whole page, no 10-second
# flash — and its callback performs the verb in this process, the one that owns
# the connector. The result goes into state.json, and the card shows it in its
# own bottom notification. ``CARD_TUNNEL=off`` means this process does not manage
# a connector, so a verb is refused rather than starting one quietly.
tunnel_managed = os.environ.get("CARD_TUNNEL", "on").lower() != "off"
back_in_url = bootstrap.cloud_url()


def _run_verb(verb: str) -> None:
    sampler = statefeed.sampler(port)
    if not use_live:
        outcome = {"ok": False, "action": verb,
                   "error": "the live source is off (?source=sample)"}
    elif not tunnel_managed:
        outcome = {"ok": False, "action": verb,
                   "error": "this process does not manage the tunnel (CARD_TUNNEL=off)"}
    else:
        outcome = sampler.act(verb)
    # The card reads state.json, not session_state: even a refusal must reach
    # that one channel so the bottom notification can name the reason. ``at``
    # is the card's "show this once" key (Sampler.act adds it on the live path).
    # A cached outcome from an older session can survive a rerun; the notice
    # must describe THIS click. Re-read the actual connector for start/restart
    # and stamp it now, before it reaches state.json.
    outcome["at"] = int(time.time())
    if outcome.get("ok") and outcome.get("action") in ("start", "restart"):
        status = bootstrap.tunnel_status(port)
        if status.get("pid"):
            outcome["pid"] = status["pid"]
    sampler.last_action = outcome
    sampler.refresh()
    st.session_state.last_action = outcome


# One native button per verb. Pressed by the bridge below, never by a person:
# a native click is a Streamlit rerun over the open socket, so the page does not
# navigate and only the parts that changed are redrawn.
for _verb in ("stop", "start", "restart"):
    with st.container(key=f"rd_verb_{_verb}"):
        st.button(_verb, key=f"rd_button_{_verb}", on_click=_run_verb,
                  args=(_verb,))

state: dict = {}
feed: statefeed.Sampler | None = None
tunnel: bootstrap.Tunnel | None = None
tunnel_problem = ""

if use_live:
    if tunnel_managed:
        tunnel = bootstrap.ensure_tunnel(port)
        # A connector the operator stopped on purpose is not a failure: the card
        # says "stopped by you" and the ▶ button is the way back. Anything else
        # is a real failure, and it must not be silent: a page with frozen
        # numbers that never says why is exactly what this rewrite is about.
        if not tunnel.ok and bootstrap.desired_state() != "off":
            tunnel_problem = tunnel.detail or tunnel.message
    feed = statefeed.sampler(port)
    # One synchronous sample before the page is built: the card and the buttons
    # are drawn from the same probe, so a page can never show "live" numbers next
    # to a Start button (the first script run happens before the thread's first
    # tick, and that is exactly how the controls used to lie for a second).
    payload = feed.refresh()
    if payload.get("found"):
        state = feed.collector.state()
    else:
        state = idle_state()
else:
    state = build_state(tick, incident=incident)

# ---- what the page says about the last verb -------------------------------
last = st.session_state.get("last_action")

# ---- the card --------------------------------------------------------------
# Same origin, no CORS, no second port. The path is RELATIVE on purpose: the
# card is a srcdoc iframe and the browser resolves it against the app document.
# On a plain host that is "/" and on Community Cloud it is "/~/+/", so one
# string covers both; a leading slash skipped /~/+/ and served the HTML shell.
# In sample mode there is no sampler writing state.json, so polling it would
# overwrite the demo numbers with a stale "no process" payload from a previous
# live run. No feed => the card keeps the server-rendered sample series.
state_url = bootstrap.CARD_STATE_PATH if use_live else None

page = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    f'<meta name="theme-color" content="{meta_light}" media="(prefers-color-scheme: light)">'
    f'<meta name="theme-color" content="{meta_dark}" media="(prefers-color-scheme: dark)">'
    f"<style>{THEME_CSS}</style></head><body>"
    + render(state, theme=theme, palette=palette, view=view, state_url=state_url,
             tunnel_host=bootstrap.public_hostname() if served_through_tunnel else "",
             back_in=back_in_url)
    + "</body></html>"
)
# The card is an iframe this file writes itself, from our own escaped template.
# It has NO sandbox on purpose. A sandboxed frame may navigate the top window
# with allow-top-navigation, but it may NEVER navigate an intermediate ancestor
# — and on Community Cloud the app itself runs inside the viewer frame at
# /~/+/, so the card's parent IS that intermediate ancestor. With a sandbox the
# buttons armed, said "Restarting...", and the console said "Unsafe attempt to
# initiate navigation ... disallowed from navigating its ancestors". Removing
# the sandbox is safe here: every interpolated value is HTML-escaped in card.py
# and the document contains nothing we did not render ourselves.


def _attribute(document: str) -> str:
    """Escape the card into one line so markdown keeps the tag whole.

    A raw HTML block in markdown ends at a blank line, and the card's own
    template has them: written literally, the tag was cut in half and the rest
    of the document spilled onto the page as text. Newlines (and tabs) travel as
    character references, which the HTML parser decodes back before the iframe
    ever sees the document — the card is byte-for-byte the same inside.
    """
    return (document.replace("&", "&amp;").replace("'", "&#39;")
            .replace("\r", "").replace("\n", "&#10;").replace("\t", "&#9;"))


# The card is a descendant of a sandboxed viewer frame, so it cannot navigate
# ANY ancestor. It sends a postMessage; this document presses the matching
# native Streamlit button, which reruns over the existing socket instead of
# reloading the page. One guarded listener per script run.
st.html(
    """<script>
(function () {
  if (window.__rdVerbBridge) return;
  window.__rdVerbBridge = true;
  window.addEventListener('message', function (event) {
    var verb = event.data && event.data.rd;
    if (verb !== 'stop' && verb !== 'start' && verb !== 'restart') return;
    var button = document.querySelector('.st-key-rd_verb_' + verb + ' button');
    if (button) button.click();
  });
})();
</script>""",
    unsafe_allow_javascript=True,
)

st.markdown(
    f'<iframe title="cloudflared card" srcdoc=\'{_attribute(page)}\' '
    f'style="width:100vw;height:100vh;border:0;'
    f'display:block"></iframe>',
    unsafe_allow_html=True,
)

if tunnel_problem:
    st.markdown(
        '<div class="rd-alert"><b>tunnel not running</b>'
        + html.escape(tunnel_problem[:300])
        + "</div>",
        unsafe_allow_html=True,
    )


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
``theme=light|dark|auto`` (auto follows the OS), ``incident=0|1``, ``tick=N``,
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

    </style>""",
    unsafe_allow_html=True,
)

print("card: app.py started", flush=True)

params = st.query_params

theme = str(params.get("theme", "dark")).lower()
if theme not in ("light", "dark", "auto"):
    theme = "dark"
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

# The buttons are back on the card, and the card is an iframe: it cannot call
# into this process. So a click reloads the page with ``?rd=<verb>`` and the verb
# runs *here*, in the process that owns the connector — after which the card
# paints whatever is true. The parameter is cleared first, so a reload can never
# replay an action, and the app is the only thing that ever performs one.
# ``CARD_TUNNEL=off`` means this process does not manage a connector (a test
# run, a build). It must also mean it refuses to start one: the buttons ask the
# app, and an app that has been told not to touch the tunnel answers "no" rather
# than quietly registering a second connector on the named tunnel.
tunnel_managed = os.environ.get("CARD_TUNNEL", "on").lower() != "off"

back_in_url = bootstrap.cloud_url()
pending = (st.query_params.get("rd") or "").strip().lower()
if pending:
    st.query_params.clear()
    if not use_live:
        outcome = {"ok": False, "action": pending,
                   "error": "the live source is off (?source=sample)"}
    elif not tunnel_managed:
        outcome = {"ok": False, "action": pending,
                   "error": "this process does not manage the tunnel (CARD_TUNNEL=off)"}
    else:
        outcome = statefeed.sampler(port).act(pending)
    st.session_state.last_action = outcome
    verb_pid = outcome.get("pid") or outcome.get("stopped")
    if outcome.get("ok"):
        st.toast(f"{pending} ok · pid {verb_pid}", icon="✅")
    else:
        st.toast(f"{pending} failed — "
                 f"{outcome.get('error') or outcome.get('detail') or 'no detail'}",
                 icon="⚠️")

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
# The address is relative on purpose: the card resolves it against its own
# document, which is the app on whatever hostname the browser used (localhost,
# *.streamlit.app, streamlit.idealis.cc). One path, no CORS, no second port.
state_url = bootstrap.STATE_PATH

page = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="theme-color" content="#E3E2DE" media="(prefers-color-scheme: light)">'
    '<meta name="theme-color" content="#161819" media="(prefers-color-scheme: dark)">'
    f"<style>{THEME_CSS}</style></head><body>"
    + render(state, theme=theme, view=view, state_url=state_url,
             host=bootstrap.public_hostname() if served_through_tunnel else "",
             back_in=back_in_url)
    + "</body></html>"
)
# The card is an iframe this file writes itself. ``st.iframe`` would be tidier,
# but its sandbox omits ``allow-top-navigation`` — and the card's Stop and
# Restart buttons are in the card, so they have to be able to ask this page for
# a verb (``?rd=``). This is our own document, rendered from our own probe, so
# it gets exactly that one permission on top of the defaults.
FRAME_SANDBOX = ("allow-scripts allow-same-origin allow-forms allow-modals "
                 "allow-popups allow-downloads allow-top-navigation")


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


st.markdown(
    f'<iframe title="cloudflared card" srcdoc=\'{_attribute(page)}\' '
    f'sandbox="{FRAME_SANDBOX}" style="width:100vw;height:100vh;border:0;'
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

if last and not last.get("ok"):
    st.markdown(
        '<div class="rd-alert"><b>' + html.escape(str(last.get("action")))
        + " failed</b>"
        + html.escape(str(last.get("error") or last.get("detail") or "")[:300])
        + "</div>",
        unsafe_allow_html=True,
    )

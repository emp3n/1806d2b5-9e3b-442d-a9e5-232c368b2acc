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
3. **the buttons** — Stop and Restart are real Streamlit buttons in this
   process, not a POST to a port that may not exist. They act on the one
   connector the sampler is reading, and the card shows the result a second
   later because it reads the same probe.

``?source=live`` (default) is the real connector; ``?source=sample`` keeps the
lab's synthetic series and touches nothing.

Query parameters: ``section=overview|services|database|shell|logs``,
``theme=light|dark|auto`` (auto follows the OS), ``incident=0|1``, ``tick=N``,
``source=live|sample``.
"""

from __future__ import annotations

import html
import os

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
# frame takes the whole window. The two controls are the exception — they are
# positioned over the card's header, where the card has its own buttons drawn in
# the design, so the page reads as one card and the buttons are real widgets.
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

    /* ---- the two controls, docked over the card's header ---- */
    div[data-testid="stHorizontalBlock"] {
      position: fixed !important; top: 16px; right: 20px; z-index: 100000;
      display: flex !important; width: auto !important; gap: 8px !important;
      align-items: center !important; padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
    div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
      width: auto !important; flex: 0 0 auto !important; min-width: 0 !important;
      padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"] div[data-testid="stButton"] {
      width: auto !important; margin: 0 !important;
    }
    div[data-testid="stHorizontalBlock"] button {
      width: 40px !important; height: 40px !important; min-height: 40px !important;
      padding: 0 !important; border-radius: 999px !important;
      font-size: 15px !important; line-height: 1 !important;
      display: flex !important; align-items: center !important; justify-content: center !important;
      background: rgba(22, 24, 25, .55) !important; color: #E6E4DE !important;
      border: 1px solid rgba(230, 228, 222, .22) !important;
      backdrop-filter: blur(12px) saturate(120%);
      box-shadow: 0 2px 10px rgba(0, 0, 0, .22) !important;
      transition: border-color .14s ease, background .14s ease, transform .14s ease;
    }
    div[data-testid="stHorizontalBlock"] button:hover {
      border-color: rgba(230, 228, 222, .55) !important;
      background: rgba(22, 24, 25, .8) !important; transform: translateY(-1px);
    }
    div[data-testid="stHorizontalBlock"] button:focus-visible {
      outline: 2px solid #E3E2DE !important; outline-offset: 2px !important;
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

    @media (prefers-color-scheme: light) {
      div[data-testid="stHorizontalBlock"] button {
        background: rgba(255, 255, 255, .72) !important; color: #161819 !important;
        border-color: rgba(22, 24, 25, .18) !important;
      }
    }
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

state: dict = {}
feed: statefeed.Sampler | None = None
tunnel: bootstrap.Tunnel | None = None
tunnel_problem = ""

if use_live:
    if os.environ.get("CARD_TUNNEL", "on").lower() != "off":
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

# ---- the controls ----------------------------------------------------------
# Two round buttons over the card's header. Stop asks once when the page itself
# arrives through the tunnel it would close (a reload then brings the connector
# back, because ensure_tunnel starts it); everything else is one click.
last = st.session_state.get("last_action")


def act(action: str) -> None:
    if feed is None:
        st.toast("live source is off: ?source=sample starts no process", icon="ℹ️")
        return
    result = feed.act(action)
    st.session_state.last_action = result
    st.session_state.arm = None
    if result.get("ok"):
        st.toast(f"{action} ok · pid {result.get('pid') or result.get('stopped')}",
                 icon="✅")
    else:
        detail = result.get("error") or result.get("detail") or "no detail"
        st.toast(f"{action} failed — {detail}", icon="⚠️")


running = bool(state.get("found"))
arm = st.session_state.get("arm")

with st.container():
    if arm == "stop":
        power, cancel = st.columns(2, gap="small")
        with power:
            if st.button("⏻", key="power_confirm", help="Yes — stop the connector now"):
                act("stop")
                st.rerun()
        with cancel:
            if st.button("✕", key="power_cancel", help="Keep it running"):
                st.session_state.arm = None
                st.rerun()
    else:
        power, restart = st.columns(2, gap="small")
        with power:
            if running:
                if st.button("⏻", key="power_stop",
                             help="Stop the connector (this page dies with it if you "
                                  "are on the tunnel hostname)"):
                    if served_through_tunnel:
                        st.session_state.arm = "stop"
                        st.toast("This page travels through that tunnel — press ⏻ "
                                 "again to close it, or ✕ to keep it.", icon="⚠️")
                        st.rerun()
                    else:
                        act("stop")
                        st.rerun()
            else:
                if st.button("▶", key="power_start", help="Start the connector"):
                    act("start")
                    st.rerun()
        with restart:
            if st.button("⟳", key="power_restart",
                         help="Replace the connector: the new one must register "
                              "before the old one is stopped"):
                act("restart")
                st.rerun()

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
    + render(state, theme=theme, view=view, state_url=state_url)
    + "</body></html>"
)
st.iframe(page, height=1000)

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

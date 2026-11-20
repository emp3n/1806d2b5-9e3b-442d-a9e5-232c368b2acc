"""The console shell: five sections, one built.

A vertical rail on the right — Overview, Services, Database, Shell, Logs — and
the cloudflared card inside Services. The design brief lives
in ``.uimix/DESIGN-BRIEF.json`` (recorded through the uimixes skill).

Two feeds, one card:

- ``?source=live`` (default) reads the real connector on this host through
  ``live.py`` and hands the page one payload a second from ``server.py``
  (provenance for every number is in ``REAL-DATA.md``). The address handed to
  the *browser* is derived from the page, not from this host: 127.0.0.1 means
  "this machine" only to this machine, so a phone gets the page's own origin and
  ``gateway.py`` serves ``/api/state`` there (see ``server.feed_url``);
- ``?source=sample`` keeps the lab's synthetic series and its browser-side
  generator.

Query parameters: ``section=overview|services|database|shell|logs``,
``theme=light|dark|auto`` (auto follows the OS), ``incident=0|1``, ``tick=N``,
``source=live|sample``.
"""

from __future__ import annotations

import os

import streamlit as st

from card import render
from demo_data import build_state
from theme import THEME_CSS

VIEWS = ("overview", "services", "database", "shell", "logs")

st.set_page_config(page_title="cloudflared — card study", layout="wide")

# the study is one full-bleed surface: no Streamlit chrome, no page scroll, the
# frame takes the whole window
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
    </style>""",
    unsafe_allow_html=True,
)

print("card: app.py started", flush=True)

params = st.query_params

# Authenticated one-shot execution used by the MCP `cloud_exec` tool. The
# token lives in Streamlit secrets (or CARD_EXEC_TOKEN), never in a URL that is
# logged: the query carries token:base64(code) only for this one request, and
# the output is rendered into the session that requested it.
exec_param = str(params.get("exec", ""))
if exec_param and ":" in exec_param:
    import base64
    import hmac
    import subprocess
    import sys

    supplied, encoded = exec_param.split(":", 1)
    token = os.environ.get("CARD_EXEC_TOKEN", "")
    if not token:
        try:
            from streamlit import secrets as st_secrets

            token = str(st_secrets.get("CARD_EXEC_TOKEN", ""))
        except Exception:  # noqa: BLE001 - no secrets file locally is normal
            pass
    if token and hmac.compare_digest(supplied, token):
        try:
            source = base64.b64decode(encoded).decode("utf-8")
            run = subprocess.run(
                [sys.executable, "-c", source],
                capture_output=True, text=True, timeout=60,
            )
            text = run.stdout + ("\n--stderr--\n" + run.stderr if run.stderr else "")
            st.code("CLOUD_EXEC_OUTPUT:" + (text or "(no output)"), language="text")
        except Exception as exc:  # noqa: BLE001 - the session must name the failure
            st.warning(f"cloud_exec failed: {type(exc).__name__}: {exc}")
    else:
        st.warning("cloud_exec: bad token")
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

# ---- where the numbers come from -------------------------------------------
# ?source=live (default) reads the connector on this host through live.py and
# serves one payload a second to the page; ?source=sample keeps the lab data.
use_live = str(params.get("source", "live")).lower() != "sample"
api_url = None
state: dict = {}


def page_url() -> str | None:
    """The address the *browser* is on, as the tunnel left it.

    ``st.context.url`` is the frontend's own URL, so it carries the public
    hostname (``https://streamlit.idealis.cc``) rather than ``localhost:8599``
    — which is the whole point: the feed address is derived from it.
    """
    try:
        return st.context.url
    except Exception:  # noqa: BLE001 - no request context outside a script run
        return None


@st.cache_resource
def live_feed(port: int):
    """One sampler and one API per Streamlit process, not per rerun."""
    from server import Sampler, serve

    sampler = Sampler().start()
    serve(sampler, port)
    return sampler


action_key = None
if use_live:
    from server import action_key as live_action_key, feed_url

    port = int(os.environ.get("CARD_API_PORT", "8598"))
    sampler = live_feed(port)
    sampler.refresh()
    found = sampler.collector.state()

    # The feed is reachable whether or not a connector is running: with none,
    # the buttons are exactly how it gets started again. The address, though, is
    # the browser's to resolve — 127.0.0.1 is this machine only to this machine,
    # so a phone gets the page's own origin (gateway.py puts /api/state there).
    explicit = os.environ.get("CARD_API_URL") or str(params.get("api", "")) or None
    page = page_url()
    from bootstrap import in_cloud_runtime, on_streamlit_cloud

    print(f"card: page={page!r} cloud={on_streamlit_cloud(page)} runtime={in_cloud_runtime()}", flush=True)
    if not explicit and (on_streamlit_cloud(page) or in_cloud_runtime()) and os.environ.get("CARD_TUNNEL", "on").lower() != "off":
        from bootstrap import ensure_tunnel

        tunnel = ensure_tunnel(port)
        if tunnel.ok:
            explicit = tunnel.api_url
        else:
            # Never leave a deployed page with silently frozen numbers.
            st.warning(f"cloudflared tunnel unavailable — {tunnel.message}")
    api_url = feed_url(page, port, explicit=explicit)
    action_key = live_action_key()

    if found.get("found"):
        state = found
    else:
        from live import idle_state

        state = idle_state()  # dashes, not the sample numbers, when nothing runs
else:
    state = build_state(tick, incident=incident)

page = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="theme-color" content="#E3E2DE" media="(prefers-color-scheme: light)">'
    '<meta name="theme-color" content="#161819" media="(prefers-color-scheme: dark)">'
    f"<style>{THEME_CSS}</style></head><body>"
    + render(state, theme=theme, view=view, api=api_url, action_key=action_key)
    + "</body></html>"
)
st.iframe(page, height=1000)

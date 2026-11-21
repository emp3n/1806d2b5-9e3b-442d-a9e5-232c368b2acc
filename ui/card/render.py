"""The console page: five sections on a rail, one built card in Services."""
from __future__ import annotations

import json
from typing import Any

from .common import esc, icon, mark, VIEWS
from .markup import measure, flag_img, geo_city, edge_items
from .script import script, spark_geometry


def render(
    state: dict[str, Any],
    *,
    theme: str = "dark",
    palette: str = "linen",
    view: str = "services",
    state_url: str | None = None,
    tunnel_host: str = "",
    back_in: str = "",
) -> str:
    """The console.

    ``state_url`` is the address the *browser* polls for the numbers, relative
    to the page's own origin: Streamlit serves ``static/state.json`` at
    ``/app/static/state.json``, so the same path works on localhost, on
    ``*.streamlit.app`` and behind the operator's hostname.

    ``tunnel_host`` is set when this page itself arrived through the tunnel,
    which is the one case where stopping the connector also closes the page: the
    buttons name it before they act, and ``back_in`` is the address that
    survives. It is named apart from the card's own ``host`` (the machine's
    cores and RAM), which used to shadow it — the warning then read "[object
    Object]" instead of the hostname.
    """
    live_text = f'{state["live"]} of 4 connections live'
    note = str(state.get("note") or "")
    status_state = "up" if state["connected"] else "down"

    # A stopped connector has no shard, no edges and no connections to show.
    # Those bubbles are gated on real data; Origin stays because it is resolved
    # from our own IP, independently of the process. The same decision is made
    # by paintPresence() on every poll, so start/stop needs no page reload.
    # Sample data has no `found` key at all — it is a demo of a running service;
    # only an explicit False means "stopped".
    running = bool(state.get("found", True))
    shard = str(state.get("shard") or "")
    edges_geo = state.get("edges_geo") or []
    edge_names = str(state.get("edge_names") or "")
    show_shard = running and bool(shard)
    show_edges = running and bool(edges_geo or edge_names not in ("", "—"))
    show_connections = running
    show_row2 = show_edges or show_connections
    # The mini facts and the binary path are empty plates while the process is
    # down ("—" and a Copy button with nothing to copy). They follow the same
    # rule: no value, no chip.
    show_side = running and (state.get("uptime") not in ("", "—")
                             or state.get("version") not in ("", "—"))
    show_path = running and state.get("path") not in ("", "—")
    hidden = " hidden"

    rails = "".join(
        f'<button type="button" class="rd-tab" data-view="{key}" '
        f'aria-label="{label}" aria-pressed="{"true" if key == view else "false"}">'
        f'<span class="rd-tab-name">{label}</span>'
        f'<span class="rd-tab-dot">{icon(key)}</span></button>'
        for key, label in VIEWS
    )

    def stub(key: str) -> str:
        label = dict(VIEWS)[key]
        return (
            f'<section class="rd-panel" data-view-panel="{key}"'
            f'{" hidden" if key != view else ""} aria-label="{label}">'
            f'<div class="rd-plate"><h2>{label}</h2><p class="rd-stub">empty</p></div>'
            "</section>"
        )

    feed_attr = ' data-feed="ok"' if state_url else ""
    card = f"""<article class="rd-card" data-run="run" data-status="{status_state}"{feed_attr}>
      <header class="rd-head">
        {mark()}
        <div class="rd-id">
          <h1 id="rd-title">cloudflared</h1>
          <p class="rd-status" data-status-text>{esc(live_text)}</p>
          <p class="rd-note" data-note{"" if note else " hidden"} title="{esc(note)}">{esc(note)}</p>
        </div>
        <!-- The verbs live on the card, where they were: two round buttons in
             the header. They cannot start a process from inside an iframe, and
             they do not pretend to — a click asks the app (see fire()) and the
             app either does it or names why not. -->
        <div class="rd-verbs">
          <button type="button" class="rd-round" data-verb="power" aria-pressed="true"
                  aria-label="Stop the connector">
            {icon("power")}<span class="rd-tip" aria-hidden="true">Stop</span></button>
          <button type="button" class="rd-round" data-verb="restart"
                  aria-label="Restart the connector">
            {icon("restart")}<span class="rd-tip" aria-hidden="true">Restart</span></button>
        </div>
      </header>

      <div class="rd-facts">
        <div class="rd-fact-row">
          <span class="rd-fact" data-tone="origin">
            <em>Origin</em><span data-fact="origin">{esc(state["edge_ip"])}</span>
            <span class="rd-flag-slot" data-origin-flag>{flag_img(state.get("origin_geo", {}).get("cc", ""), "rd-flag", "origin")}</span>
            <span class="rd-geo" data-origin-geo>{esc(geo_city(state.get("origin_geo", {})))}</span>
          </span>
          <span class="rd-fact" data-tone="shard"{"" if show_shard else hidden}><span data-shard>{esc(shard)}</span></span>
        </div>
        <div class="rd-fact-row" data-fact-row="2"{"" if show_row2 else hidden}>
          <span class="rd-fact" data-tone="edges"{"" if show_edges else hidden}>
            <span class="rd-edge-list" data-edge-list>{edge_items(edges_geo, edge_names)}</span>
          </span>
          <span class="rd-fact" data-tone="connections"{"" if show_connections else hidden}>
            <em>connections</em><span data-connections>{int(state.get("connections", 0) or 0)}</span>
          </span>
        </div>
      </div>

      <div class="rd-measures">
        {measure(state, "cpu")}
        <div class="rd-side"{"" if show_side else hidden}>
          <div class="rd-mini">
            <span class="rd-k">uptime</span><b data-uptime>{esc(state["uptime"])}</b></div>
          <div class="rd-mini" data-tone="acc">
            <span class="rd-k">version</span><b data-version>{esc(state["version"])}</b></div>
        </div>
      </div>

      <div class="rd-path"{"" if show_path else hidden}>
        <code data-path>{esc(state["path"])}</code>
        <button type="button" class="rd-copy" data-copy="{esc(state["path"])}" aria-label="Copy binary path">
          {icon("copy")}<span class="rd-tip" aria-hidden="true">Copy path</span></button>
      </div>

      {measure(state, "mem")}
    </article>"""

    spark_geo = spark_geometry(state)

    return f"""<div class="rd-stage" data-theme="{theme}" data-palette="{palette}">
  <div class="rd-frame">
    <main class="rd-view">
      {stub("overview")}
      <section class="rd-panel" data-view-panel="services"{" hidden" if view != "services" else ""} aria-label="Services">
        {card}
      </section>
      {stub("database")}
      {stub("shell")}
      {stub("logs")}
    </main>

    <nav class="rd-rail" aria-label="Sections">{rails}</nav>
  </div>

  <div class="rd-toast" id="rd-toast" role="status" aria-live="polite"></div>

  {script(spark_geo=spark_geo, live_text=live_text, state_url=state_url,
           tunnel_host=tunnel_host, back_in=back_in)}
</div>"""

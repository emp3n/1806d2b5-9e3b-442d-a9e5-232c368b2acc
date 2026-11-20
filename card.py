"""The console: five sections on a rail, and the cloudflared card in Services.

The rail is the console's only navigation — Overview, Services, Database, Shell,
Logs. Only Services is built; the other four are empty plates, so nothing is
invented for them.

The card carries exactly what was asked for: the mark and the name, the origin
IP and the edge codes, start/stop and restart, CPU with its percentage and its
usage line, uptime, version, the running binary's path, and RAM with the same
line and an MB/GB switch. Colour on a measure is its share of the service's
budget (one core, 256 MB), so the same percentage reads differently on a
different budget.
"""

from __future__ import annotations

import html
import json
from typing import Any

# the real Cloudflare mark (simple-icons, path unmodified), set on a brand plate
CF_PATH = (
    "M16.5088 16.8447c.1475-.5068.0908-.9707-.1553-1.3154-.2246-.3164-.6045-.499"
    "-1.0615-.5205l-8.6592-.1123a.1559.1559 0 0 1-.1333-.0713c-.0283-.042-.0351"
    "-.0986-.021-.1553.0278-.084.1123-.1484.2036-.1562l8.7359-.1123c1.0351-.0489"
    " 2.1601-.8868 2.5537-1.9136l.499-1.3013c.0215-.0561.0293-.1128.0147-.168"
    "-.5625-2.5463-2.835-4.4453-5.5499-4.4453-2.5039 0-4.6284 1.6177-5.3876"
    " 3.8614-.4927-.3658-1.1187-.5625-1.794-.499-1.2026.119-2.1665 1.083-2.2861"
    " 2.2856-.0283.31-.0069.6128.0635.894C1.5683 13.171 0 14.7754 0 16.752c0"
    " .1748.0142.3515.0352.5273.0141.083.0844.1475.1689.1475h15.9814c.0909 0"
    " .1758-.0645.2032-.1553l.12-.4268zm2.7568-5.5634c-.0771 0-.1611 0-.2383"
    " .0112-.0566 0-.1054.0415-.127.0976l-.3378 1.1744c-.1475.5068-.0918.9707"
    " .1543 1.3164.2256.3164.6055.498 1.0625.5195l1.8437.1133c.0557 0 .1055"
    " .0263.1329.0703.0283.043.0351.1074.0214.1562-.0283.084-.1132.1485-.204"
    " .1553l-1.921.1123c-1.041.0488-2.1582.8867-2.5527 1.914l-.1406.3585c-.0283"
    " .0713.0215.1416.0986.1416h6.5977c.0771 0 .1474-.0489.169-.126.1122-.4082"
    " .1757-.837.1757-1.2803 0-2.6025-2.125-4.727-4.7344-4.727"
)

_ICONS = {
    "overview": '<path d="M12 16v5"/><path d="M16 14.6V21"/><path d="M20 10.7V21"/>'
                '<path d="m22 3-8.6 8.6a.5.5 0 0 1-.7 0L9.4 8.4a.5.5 0 0 0-.7 0L2 15"/>'
                '<path d="M4 18.5V21"/><path d="M8 14.7V21"/>',
    "services": '<path d="m10.6 5.4-5.2 5.2"/><path d="m18.6 13.4-5.2 5.2"/>'
                '<path d="M6 12h12"/><circle cx="12" cy="20" r="2"/><circle cx="12" cy="4" r="2"/>'
                '<circle cx="20" cy="12" r="2"/><circle cx="4" cy="12" r="2"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/>'
                '<path d="M3 12a9 3 0 0 0 18 0"/>',
    "shell": '<path d="m7 11 2-2-2-2"/><path d="M11 13h4"/>'
             '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/>',
    "logs": '<path d="M3 5h1"/><path d="M3 12h1"/><path d="M3 19h1"/><path d="M8 5h1"/>'
            '<path d="M8 12h1"/><path d="M8 19h1"/><path d="M13 5h8"/><path d="M13 12h8"/>'
            '<path d="M13 19h8"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
            '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "power": '<path d="M12 3.5v8.5"/><path d="M7.4 6.6a7.6 7.6 0 1 0 9.2 0"/>',
    "restart": '<path d="M20 9V7a2 2 0 0 0-2-2h-6"/><path d="m15 2-3 3 3 3"/>'
               '<path d="M20 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2"/>',
}

VIEWS = (
    ("overview", "Overview"),
    ("services", "Services"),
    ("database", "Database"),
    ("shell", "Shell"),
    ("logs", "Logs"),
)


def esc(value: object) -> str:
    """Anything that comes from the connector goes into the page escaped.

    The card is now allowed to navigate its own page (that is how its buttons
    reach the app), so a value that arrived from a process — an edge name, a
    path, a note — must not be able to become markup here.
    """
    return html.escape(str(value), quote=True)


def icon(name: str, cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f'<svg viewBox="0 0 24 24" aria-hidden="true"{attr}>{_ICONS[name]}</svg>'


def mark() -> str:
    """The Cloudflare mark on its brand plate — the real path, not a redraw."""
    return (
        '<svg class="rd-mark" viewBox="0 0 24 24" aria-hidden="true">'
        '<rect width="24" height="24" rx="6" fill="var(--brand)"/>'
        '<g transform="translate(12 12) scale(0.76) translate(-12 -12)">'
        f'<path d="{CF_PATH}" fill="var(--on-brand)"/>'
        "</g></svg>"
    )


def measure(state: dict[str, Any], metric: str) -> str:
    """CPU or RAM: the number, its share, and a line that scrubs on hover."""
    key = "cpu" if metric == "cpu" else "mem"
    label = "CPU" if metric == "cpu" else "RAM"
    units = ""
    if metric == "mem":
        units = (
            '<div class="rd-units" role="group" aria-label="Memory unit">'
            '<button type="button" data-unit="MB" aria-pressed="true">MB</button>'
            '<button type="button" data-unit="GB" aria-pressed="false">GB</button></div>'
        )
    return f"""<div class="rd-tile" data-tone="{key}" data-level="{state[f'{key}_level']}">
      <div class="rd-tile-head">
        <span class="rd-k">{label}</span>{units}
      </div>
      <div class="rd-value-row">
        <b class="rd-v"{' data-mem-value' if metric == 'mem' else ''}>{state[f'{key}_text']}</b>
      </div>
      <span class="rd-sub">{state[f'{key}_share']}</span>
      <div class="rd-spark" data-spark="{key}" tabindex="0" role="img"
        aria-label="{label} over the last {state['samples']} samples, now {state[f'{key}_text']}.">
        <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">{state[f'{key}_segments']}</svg>
        <i class="rd-cursor" aria-hidden="true"></i>
      </div>
    </div>"""


def render(
    state: dict[str, Any],
    *,
    theme: str = "dark",
    view: str = "services",
    state_url: str | None = None,
    host: str = "",
    back_in: str = "",
) -> str:
    """The console.

    ``state_url`` is the address the *browser* polls for the numbers, relative
    to the page's own origin: Streamlit serves ``static/state.json`` at
    ``/app/static/state.json``, so the same path works on localhost, on
    ``*.streamlit.app`` and behind the operator's hostname.

    ``host`` is set when this page itself arrived through the tunnel, which is
    the one case where stopping the connector also closes the page: the buttons
    name it before they act, and ``back_in`` is the address that survives.
    """
    host = state["host"]
    live_text = f'{state["live"]} of 4 connections live'
    note = str(state.get("note") or "")
    status_state = "up" if state["connected"] else "down"

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
        <span class="rd-fact" data-tone="mem"><em>Origin IP</em><span data-fact="origin">{esc(state["edge_ip"])}</span></span>
        <span class="rd-fact"><em>Edges</em><span data-fact="edges">{esc(state["edge_names"])}</span></span>
      </div>

      <div class="rd-measures">
        {measure(state, "cpu")}
        <div class="rd-side">
          <div class="rd-mini">
            <span class="rd-k">uptime</span><b data-uptime>{esc(state["uptime"])}</b></div>
          <div class="rd-mini" data-tone="acc">
            <span class="rd-k">version</span><b data-version>{esc(state["version"])}</b></div>
        </div>
      </div>

      <div class="rd-path">
        <code data-path>{esc(state["path"])}</code>
        <button type="button" class="rd-copy" data-copy="{esc(state["path"])}" aria-label="Copy binary path">
          {icon("copy")}<span class="rd-tip" aria-hidden="true">Copy path</span></button>
      </div>

      {measure(state, "mem")}

      <footer class="rd-foot"><span data-source>{state.get("source_label", "sample data")}</span> · host {host["text"]}</footer>
    </article>"""

    spark_geo = json.dumps(
        {
            "cpu": {
                "pts": state["cpu_points"],
                "values": state["cpu_series"],
                "levels": state["cpu_levels"],
                "budget": state["cpu_budget"],
                "scale": 100,
                "bands": [25.0, 50.0, 80.0],
                "base": state["cpu"],
                "step": 4.2,
            },
            "mem": {
                "pts": state["mem_points"],
                "values": state["mem_series"],
                "levels": state["mem_levels"],
                "budget": state["mem_budget"],
                "scale": 256,
                "bands": [30.0, 55.0, 80.0],
                "base": state["mem"],
                "step": 2.6,
            },
        },
        separators=(",", ":"),
    )

    return f"""<div class="rd-stage" data-theme="{theme}">
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

  <script>
  var RD_SPARK = {spark_geo};
  var RD_LIVE = {json.dumps(live_text)};
  var RD_API = {json.dumps(state_url)};
  /* empty unless this page itself arrived through the tunnel: that is the one
     case where stopping the connector closes the page, so the button says so
     first and names the address that survives (RD_BACK) */
  var RD_HOST = {json.dumps(host)};
  var RD_BACK = {json.dumps(back_in)};
  /* the heartbeat is written by the sampler thread once a second; a payload
     whose stamp is older than this means the sampler stopped, not that the
     numbers are still true */
  var RD_STALE = 12;
  var FLOOR = 0.62;
  (function () {{
    var card = document.querySelector('.rd-card');
    var toast = document.getElementById('rd-toast');
    var unit = 'MB';
    var W = 100, H = 24, PAD = 3;

    function say(text) {{
      toast.textContent = text;
      toast.classList.remove('on');
      void toast.offsetWidth;
      toast.classList.add('on');
    }}
    function set(selector, text) {{
      var node = document.querySelector(selector);
      if (node && text != null && node.textContent !== text) node.textContent = text;
    }}
    /* no process: dashes, not the last flattering numbers, and a line that
       decays to flat instead of freezing mid-air */
    function blank() {{
      set('[data-source]', 'live · no cloudflared process');
      set('[data-fact="origin"]', '—');
      set('[data-fact="edges"]', '—');
      set('[data-uptime]', '—');
      set('[data-version]', '—');
      set('[data-path]', '—');
      var copy = document.querySelector('[data-copy]');
      if (copy) copy.setAttribute('data-copy', '');
      sparks.forEach(function (spark) {{
        spark.buf = spark.buf.map(function () {{ return 0; }});
        spark.next = 0;
        spark.pending = null;
        spark.shown = 0;
        spark.target = 0;
        spark.ratio = -1;
      }});
      set('[data-tone="cpu"] .rd-v', '—');
      set('[data-tone="mem"] .rd-v', '—');
      set('[data-tone="cpu"] .rd-sub', 'no process');
      set('[data-tone="mem"] .rd-sub', 'no process');
      card.setAttribute('data-status', 'stop');
    }}
    function fmtMem(value) {{
      return unit === 'GB' ? (value / 1024).toFixed(2) + ' GB' : value.toFixed(1) + ' MB';
    }}
    function fmtValue(spark, value) {{
      if (spark.key === 'mem') return fmtMem(value);
      /* an idle connector lives at a fraction of a percent; one decimal would
         print 0.0 % and read as "no data" */
      return (value < 1 ? value.toFixed(2) : value.toFixed(1)) + '%';
    }}

    /* ---- sections: the rail is the only navigation ---- */
    var tabs = Array.prototype.slice.call(document.querySelectorAll('.rd-tab'));
    function openView(key) {{
      tabs.forEach(function (tab) {{
        tab.setAttribute('aria-pressed', String(tab.getAttribute('data-view') === key));
      }});
      Array.prototype.forEach.call(document.querySelectorAll('[data-view-panel]'), function (panel) {{
        var mine = panel.getAttribute('data-view-panel') === key;
        panel.hidden = !mine;
        if (mine) {{
          panel.classList.remove('rd-enter');
          void panel.offsetWidth;
          panel.classList.add('rd-enter');
        }}
      }});
      /* a section switch is a new screen: start it at the top, not halfway
         down the previous one (matters where the stage scrolls) */
      var box = document.querySelector('.rd-stage');
      if (box && box.scrollTop) box.scrollTop = 0;
    }}
    tabs.forEach(function (tab) {{
      tab.addEventListener('click', function () {{ openView(tab.getAttribute('data-view')); }});
    }});

    /* ---- live wiring: numbers from the connector, not from a dice ---- */
    var LIVE = !!RD_API;

    /* ---- the two lines: one clock, eased bounds, a sliding window ---- */
    var SPIKES = [0.985, 0.99, 0.995, 0.997];
    var TICK = 1000;
    var still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function band(spark, value) {{
      var share = 100 * value / spark.geo.scale;
      var edges = spark.geo.bands;
      if (share < edges[0]) return 'ok';
      if (share < edges[1]) return 'warm';
      if (share < edges[2]) return 'hot';
      return 'crit';
    }}
    /* the next sample: a slow swell (two sines, so the curve rolls rather than
       zigzags), a small ripple on top, and a rare burst so the colour bands are
       reachable; frozen while stopped */
    function nextSample(spark, last) {{
      spark.phase += 0.22;
      var swell = (Math.sin(spark.phase) + 0.4 * Math.sin(spark.phase * 2.3 + 1.1))
        * spark.geo.step * 0.42;
      var drift = (spark.geo.base - last) * 0.11 + swell * 0.24;
      var noise = (Math.random() - 0.5) * spark.geo.step * 0.18;
      var burst = Math.random() > SPIKES[(Math.random() * SPIKES.length) | 0]
        ? spark.geo.step * 1.9 : 0;
      var value = last + drift + noise + burst;
      return Math.max(0.3, Math.min(spark.geo.scale * 0.97, value));
    }}
    /* the window's own floor and ceiling; they are eased towards, never
       snapped, so the line re-scales instead of jumping on a tick */
    function setBounds(spark) {{
      var all = spark.buf.concat([spark.next]);
      var lo = Math.min.apply(null, all);
      var hi = Math.max.apply(null, all);
      var span = Math.max(hi - lo, spark.geo.scale * 0.02);
      spark.loT = lo - span * 0.16;
      spark.hiT = hi + span * 0.16;
    }}

    var sparks = Array.prototype.slice.call(document.querySelectorAll('[data-spark]')).map(function (wrap) {{
      var key = wrap.getAttribute('data-spark');
      var geo = RD_SPARK[key];
      var tile = wrap.closest('.rd-tile');
      var spark = {{
        key: key, wrap: wrap, tile: tile, geo: geo,
        svg: wrap.querySelector('svg'),
        paths: Array.prototype.slice.call(wrap.querySelectorAll('path.rd-seg')),
        cursor: wrap.querySelector('.rd-cursor'),
        value: tile.querySelector('.rd-v'),
        sub: tile.querySelector('.rd-sub'),
        buf: geo.values.slice(),
        next: 0,
        pending: null,
        phase: Math.random() * 6.28,
        lo: 0, hi: 1, loT: 0, hiT: 1,
        shown: geo.values[geo.values.length - 1],
        target: geo.values[geo.values.length - 1],
        ratio: -1,
        pts: []
      }};
      /* one stroke per sample plus the sample entering from the right, so the
         window can slide by exactly one step and no ink is left over */
      while (spark.paths.length < spark.buf.length + 1) {{
        var extra = spark.paths[spark.paths.length - 1].cloneNode(false);
        spark.svg.appendChild(extra);
        spark.paths.push(extra);
      }}
      spark.next = nextSample(spark, spark.buf[spark.buf.length - 1]);
      setBounds(spark);
      spark.lo = spark.loT;
      spark.hi = spark.hiT;
      return spark;
    }});

    /* x of every point for a given progress through the tick; the newest
       sample enters from the right edge and the oldest leaves on the left */
    function layout(spark, t) {{
      var n = spark.buf.length;
      var step = W / (n - 1);
      var span = Math.max(spark.hi - spark.lo, 0.0001);
      var pts = [];
      for (var i = 0; i <= n; i++) {{
        var value = i < n ? spark.buf[i] : spark.next;
        pts.push([(i - t) * step, PAD + (1 - (value - spark.lo) / span) * (H - 2 * PAD)]);
      }}
      return pts;
    }}

    function draw(spark, t) {{
      var n = spark.buf.length;
      var step = W / (n - 1);
      var pts = layout(spark, t);
      spark.pts = pts;
      /* one phantom sample on each side, carrying the local slope on, so the
         end strokes run past the box instead of stopping inside it */
      var pad = pts.slice();
      pad.unshift([pts[0][0] - step, 2 * pts[0][1] - pts[1][1]]);
      pad.push([pts[n][0] + step, 2 * pts[n][1] - pts[n - 1][1]]);
      for (var i = 1; i < pad.length - 1; i++) {{
        var path = spark.paths[i - 1];
        var a = pad[i - 1], b = pad[i], c = pad[i + 1];
        /* every sample is the bend of one quadratic and the strokes meet on the
           midpoints between samples, so the joins have no corners */
        path.setAttribute('d',
          'M' + ((a[0] + b[0]) / 2).toFixed(2) + ' ' + ((a[1] + b[1]) / 2).toFixed(2)
          + 'Q' + b[0].toFixed(2) + ' ' + b[1].toFixed(2)
          + ' ' + ((b[0] + c[0]) / 2).toFixed(2) + ' ' + ((b[1] + c[1]) / 2).toFixed(2));
        var level = band(spark, i - 1 < n ? spark.buf[i - 1] : spark.next);
        if (path.__lv !== level) {{
          path.setAttribute('class', 'rd-seg lv-' + level);
          path.__lv = level;
        }}
      }}
    }}

    /* the tile reads a number, a share and a band; write each only when it
       actually changed, so 60 frames a second cost no layout */
    function show(spark, value) {{
      var text = fmtValue(spark, value);
      if (spark.__text !== text) {{ spark.value.textContent = text; spark.__text = text; }}
      var share = 100 * value / spark.geo.scale;
      var sub = (share > 0 && share < 1 ? share.toFixed(2) : Math.round(share)) + '% of ' + spark.geo.budget;
      if (spark.__sub !== sub) {{ spark.sub.textContent = sub; spark.__sub = sub; }}
      var level = band(spark, value);
      if (spark.__level !== level) {{ spark.tile.setAttribute('data-level', level); spark.__level = level; }}
    }}

    function cursorAt(spark, index) {{
      var p = spark.pts[index];
      if (!p) return;
      spark.cursor.style.left = (p[0] / W * spark.svg.clientWidth) + 'px';
      spark.cursor.style.top = (p[1] / H * spark.svg.clientHeight) + 'px';
      spark.cursor.classList.add('on');
    }}
    /* the sample under the pointer follows the line as it slides, so the number
       and the ink never disagree */
    function scrubAt(spark, t) {{
      var n = spark.buf.length;
      var index = Math.round(spark.ratio * (n - 1) + t);
      index = Math.max(0, Math.min(n - 1, index));
      show(spark, spark.buf[index]);
      cursorAt(spark, index);
    }}

    function tick() {{
      sparks.forEach(function (spark) {{
        /* live: the connector's own sample becomes the next point; sample: the
           generator makes one. Either way the window moves exactly one step. */
        var value = LIVE
          ? (spark.pending === null ? spark.buf[spark.buf.length - 1] : spark.pending)
          : spark.next;
        spark.buf.shift();
        spark.buf.push(value);
        spark.pending = null;
        spark.next = LIVE ? value : nextSample(spark, value);
        spark.target = value;
        setBounds(spark);
      }});
    }}

    var frameAt = 0;
    var tickAt = 0;
    function frame(now) {{
      if (!frameAt) {{ frameAt = now; tickAt = now; }}
      var dt = Math.min(now - frameAt, 120);
      frameAt = now;
      var running = card.getAttribute('data-run') === 'run' && !document.hidden;
      if (!running) tickAt = now;
      if (now - tickAt >= TICK) {{ tick(); tickAt = now; }}
      var t = still ? 1 : Math.min((now - tickAt) / TICK, 1);
      var k = still ? 1 : 1 - Math.exp(-dt / 180);
      var kn = still ? 1 : 1 - Math.exp(-dt / 300);
      sparks.forEach(function (spark) {{
        spark.lo += (spark.loT - spark.lo) * k;
        spark.hi += (spark.hiT - spark.hi) * k;
        if (spark.ratio < 0) spark.shown += (spark.target - spark.shown) * kn;
        draw(spark, t);
        if (spark.ratio >= 0) scrubAt(spark, t);
        else {{ show(spark, spark.shown); spark.cursor.classList.remove('on'); }}
      }});
      window.requestAnimationFrame(frame);
    }}
    window.requestAnimationFrame(frame);

    sparks.forEach(function (spark) {{
      function release() {{
        if (spark.ratio < 0) return;
        spark.ratio = -1;
        spark.tile.classList.remove('is-scrubbing');
        spark.cursor.classList.remove('on');
        spark.shown = spark.target;
      }}
      spark.wrap.addEventListener('pointerleave', release);
      spark.wrap.addEventListener('blur', release);
      /* a finger has no hover: press shows the sample under it, lifting lets
         go, and a vertical drag cancels the scrub so the page scrolls */
      function under(event) {{
        var box = spark.svg.getBoundingClientRect();
        spark.ratio = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
        spark.tile.classList.add('is-scrubbing');
      }}
      spark.wrap.addEventListener('pointerdown', function (event) {{
        if (event.pointerType === 'mouse') return;
        under(event);
      }});
      spark.wrap.addEventListener('pointermove', under);
      spark.wrap.addEventListener('pointerup', function (event) {{
        if (event.pointerType !== 'mouse') release();
      }});
      spark.wrap.addEventListener('pointercancel', release);
      spark.wrap.addEventListener('keydown', function (event) {{
        var step = 1 / (spark.buf.length - 1);
        var at = spark.ratio < 0 ? 1 : spark.ratio;
        var next = null;
        if (event.key === 'ArrowLeft') next = at - step;
        else if (event.key === 'ArrowRight') next = at + step;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = 1;
        else if (event.key === 'Escape') {{ release(); return; }}
        if (next === null) return;
        event.preventDefault();
        spark.ratio = Math.max(0, Math.min(1, next));
        spark.tile.classList.add('is-scrubbing');
      }});
    }});

    /* ---- the live poll: one payload a second, painted by the same frame loop ----
       The payload carries the sampler's own timestamp. A file that stops being
       rewritten (script asleep, thread dead, disk full) leaves the last numbers
       on screen looking exactly like live ones; the stamp is what tells the two
       apart. Clock skew between the container and the browser is a second or
       two, so the threshold is generous. */
    /* Staleness is observed, never computed from two clocks. The stamp is
       written by the server and read by a phone that can be minutes off, so
       comparing them called a live file stalled on any device running ahead
       and reported negative ages on the ones running behind. What matters is
       whether the file is still being rewritten: same stamp for longer than
       RD_STALE seconds of *local* time means the writer stopped. */
    var beatTs = null, beatSeen = 0;
    function beat(data) {{
      if (!data || typeof data.ts !== 'number') return '';
      var now = Date.now();
      if (beatTs === null || data.ts !== beatTs) {{
        beatTs = data.ts;
        beatSeen = now;
        if (card.getAttribute('data-feed') === 'stalled') card.setAttribute('data-feed', 'ok');
        return '';
      }}
      var age = Math.round((now - beatSeen) / 1000);
      if (age <= RD_STALE) {{
        if (card.getAttribute('data-feed') === 'stalled') card.setAttribute('data-feed', 'ok');
        return '';
      }}
      card.setAttribute('data-feed', 'stalled');
      return ' · stalled ' + age + 's';
    }}
    /* ---- the verbs: the card asks, the app answers ----
       A button in this iframe cannot start a process and must not claim it did.
       A click navigates this page to ``?rd=<verb>``; app.py runs the verb in the
       Streamlit process, reports the outcome and clears the parameter. The card
       then paints whatever is true — so the button can be wrong about nothing. */
    var powerBtn = document.querySelector('[data-verb="power"]');
    var restartBtn = document.querySelector('[data-verb="restart"]');
    var armTimer = null;

    function retitle(up) {{
      if (!powerBtn) return;
      powerBtn.setAttribute('aria-pressed', String(up));
      powerBtn.setAttribute('aria-label', up ? 'Stop the connector'
                                            : 'Start the connector');
      var tip = powerBtn.querySelector('.rd-tip');
      if (tip && !powerBtn.classList.contains('is-armed')) {{
        tip.textContent = up ? 'Stop' : 'Start';
      }}
    }}

    function disarm() {{
      window.clearTimeout(armTimer);
      armTimer = null;
      [powerBtn, restartBtn].forEach(function (button) {{
        if (button) button.classList.remove('is-armed');
      }});
      retitle(card.getAttribute('data-link') !== 'gone');
      var tip = restartBtn && restartBtn.querySelector('.rd-tip');
      if (tip) tip.textContent = 'Restart';
    }}

    function fire(verb) {{
      var here = window.top.location.href;
      try {{
        var url = new URL(here);
        url.searchParams.set('rd', verb);
        /* replace, not assign: the page before the action is not somewhere the
           back button should return to and replay */
        window.top.location.replace(url.toString());
      }} catch (err) {{
        window.top.location.href = here.split('#')[0].split('?')[0] + '?rd=' + verb;
      }}
    }}

    if (powerBtn) {{
      powerBtn.addEventListener('click', function () {{
        var up = card.getAttribute('data-link') !== 'gone';
        if (up && RD_HOST && !powerBtn.classList.contains('is-armed')) {{
          /* the tunnel this page travels through is the thing being stopped */
          powerBtn.classList.add('is-armed');
          var tip = powerBtn.querySelector('.rd-tip');
          if (tip) tip.textContent = 'Click again';
          say('Stopping closes ' + RD_HOST + ' — click again'
              + (RD_BACK ? '. Come back at ' + RD_BACK : ''));
          armTimer = window.setTimeout(disarm, 5000);
          return;
        }}
        disarm();
        say(up ? 'Stopping…' : 'Starting…');
        fire(up ? 'stop' : 'start');
      }});
    }}

    if (restartBtn) {{
      restartBtn.addEventListener('click', function () {{
        if (restartBtn.classList.contains('is-armed')) {{
          disarm();
          say('Restarting…');
          fire('restart');
          return;
        }}
        restartBtn.classList.add('is-armed');
        var tip = restartBtn.querySelector('.rd-tip');
        if (tip) tip.textContent = 'Click again';
        say('Restart: the new connector must register before the old one stops');
        armTimer = window.setTimeout(disarm, 5000);
      }});
    }}

    function apply(data) {{
      /* an answer is an answer: whatever it says about the process, the feed
         itself is back, so the stale look comes off here and nowhere else */
      card.setAttribute('data-feed', 'ok');
      if (!data || !data.found) {{
        /* a lost process is a state, not a glitch: say it, do not freeze on the
           last flattering number */
        card.setAttribute('data-link', data ? 'gone' : 'lost');
        if (data) {{
          /* the intent file outlives this script process, last_action does not:
             a reloaded page must still say who stopped it */
          var intentOff = data.tunnel && data.tunnel.intent === 'off';
          var stopped = intentOff
            || (data.last_action && data.last_action.action === 'stop' && data.last_action.ok);
          set('[data-status-text]', stopped ? 'stopped by you' : 'no cloudflared process');
          var gone = document.querySelector('[data-note]');
          if (gone) {{ gone.hidden = true; gone.textContent = ''; }}
          blank();
          set('[data-source]', 'live · no cloudflared process' + beat(data));
        retitle(false);
        }}
        return;
      }}
      card.setAttribute('data-link', 'ok');
      card.setAttribute('data-status', data.status_state || 'down');
      set('[data-status-text]', data.status_text);
      var noteNode = document.querySelector('[data-note]');
      if (noteNode) {{
        var note = data.note || '';
        if (noteNode.textContent !== note) noteNode.textContent = note;
        noteNode.setAttribute('title', note);
        noteNode.hidden = !note;
      }}
      set('[data-fact="origin"]', data.origin);
      set('[data-fact="edges"]', data.edges);
      set('[data-uptime]', data.uptime);
      set('[data-version]', data.version);
      set('[data-path]', data.path);
      set('[data-source]', data.source_label + beat(data));
      retitle(true);
      var copyBtn = document.querySelector('[data-copy]');
      if (copyBtn && data.path) copyBtn.setAttribute('data-copy', data.path);
      sparks.forEach(function (spark) {{
        if (spark.key === 'cpu' && typeof data.cpu === 'number') spark.pending = data.cpu;
        if (spark.key === 'mem' && typeof data.mem === 'number') {{
          spark.pending = data.mem;
          if (data.mem_scale) spark.geo.scale = data.mem_scale;
        }}
      }});
    }}
    /* ---- the feed, and what the page says when it stops ----
       A refused fetch and a successful one look alike from the outside, so the
       card used to keep its last numbers and say nothing: on a phone, whose
       127.0.0.1 is the phone, that is a frozen panel that looks alive. The loop
       is a chain now (a slow reply cannot stack up behind the next one), it
       backs off while the feed is gone, it resumes the moment the page comes
       back, and it names the failure instead of freezing on it. */
    var BEAT = 1000, PATIENCE = 4000, CEILING = 10000;
    var misses = 0, beatTimer = null;

    function lose() {{
      card.setAttribute('data-feed', 'lost');
      card.setAttribute('data-status', 'down');
      set('[data-status-text]', 'state unreachable');
      /* the footer is the provenance line: name the path that failed. It is a
         file this page's own server publishes, so a failure means the app (or
         the tunnel under it) is gone, not that some other host refused. */
      set('[data-source]', 'no state · ' + RD_API);
    }}
    function schedule(delay) {{
      if (beatTimer) window.clearTimeout(beatTimer);
      /* a hidden tab gets nothing: the browser throttles timers to a crawl and
         the next visible moment polls anyway */
      beatTimer = document.hidden ? null : window.setTimeout(poll, delay);
    }}
    function poll() {{
      if (!LIVE) return;
      beatTimer = null;
      var ctl = window.AbortController ? new window.AbortController() : null;
      var cut = window.setTimeout(function () {{ if (ctl) ctl.abort(); }}, PATIENCE);
      /* the file has no Cache-Control header, so a heuristic cache may
         serve a stale copy: a unique query keeps every poll a miss */
      window.fetch(RD_API + '?t=' + Date.now(),
                   {{cache: 'no-store', signal: ctl ? ctl.signal : undefined}})
        .then(function (reply) {{
          if (!reply.ok) throw new Error(String(reply.status));
          return reply.json();
        }})
        .then(function (data) {{
          window.clearTimeout(cut);
          misses = 0;
          apply(data);
          schedule(BEAT);
        }})
        .catch(function (err) {{
          if (window.console) window.console.warn('RD feed', err && err.message);
          window.clearTimeout(cut);
          misses = Math.min(misses + 1, 5);
          /* 1, 2, 4, 8, then every 10 s: a feed that was refused a moment ago is
             back on the second try, and a phone that lost the network is not
             hammered while it has none */
          schedule(Math.min(BEAT * Math.pow(2, misses - 1), CEILING));
          lose();
        }});
    }}

    if (LIVE) {{
      poll();
      document.addEventListener('visibilitychange', function () {{
        if (document.hidden) return;
        misses = 0; poll();
      }});
      window.addEventListener('pageshow', function () {{ misses = 0; poll(); }});
      window.addEventListener('online', function () {{ misses = 0; poll(); }});
    }}

    /* ---- MB / GB ---- */
    Array.prototype.forEach.call(document.querySelectorAll('[data-unit]'), function (button) {{
      button.addEventListener('click', function () {{
        unit = button.getAttribute('data-unit');
        Array.prototype.forEach.call(document.querySelectorAll('[data-unit]'), function (other) {{
          other.setAttribute('aria-pressed', String(other === button));
        }});

      }});
    }});

    /* ---- path copy ---- */
    var copy = document.querySelector('[data-copy]');
    copy.addEventListener('click', function () {{
      var label = copy.querySelector('.rd-tip');
      function ok() {{
        label.textContent = 'Copied';
        window.setTimeout(function () {{ label.textContent = 'Copy path'; }}, 1400);
      }}
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(copy.getAttribute('data-copy')).then(ok, function () {{
          say('Clipboard blocked');
        }});
      }} else {{
        say('Clipboard blocked');
      }}
    }});

    /* ---- fit a short window ----
       On a desktop window shorter than the card, scale the frame down rather
       than crop it — every number stays visible, just smaller. Phones are not
       scaled at all: 0.7 of 13 px is not text, so they scroll (the stage is the
       scroll container there). And if the window is so short that even the
       smallest readable scale would not fit, scroll instead of cropping. */
    var frameEl = document.querySelector('.rd-frame');
    var stage = document.querySelector('.rd-stage');
    var phone = window.matchMedia('(max-width: 760px)');
    function fit() {{
      frameEl.style.transform = '';
      stage.classList.remove('rd-scroll');
      if (phone.matches) return;
      var need = frameEl.getBoundingClientRect().height;
      var room = window.innerHeight - 40;
      if (room <= 0 || need <= room) return;
      var scale = room / need;
      if (scale >= FLOOR) {{ frameEl.style.transform = 'scale(' + scale.toFixed(3) + ')'; return; }}
      stage.classList.add('rd-scroll');
    }}
    fit();
    window.addEventListener('resize', fit);
    window.addEventListener('load', fit);
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
    window.setTimeout(fit, 600);
  }})();
  </script>
</div>"""

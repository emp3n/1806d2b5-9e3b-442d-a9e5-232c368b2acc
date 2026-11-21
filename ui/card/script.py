"""The card's browser script.

Kept apart from the HTML template: the whole interactive behaviour lives here
and takes exactly five values from the rendered state. ``render()`` injects
them; tests check this block without a browser.
"""
from __future__ import annotations

import json


def script(*, spark_geo: str, live_text: str, state_url: str | None,
           tunnel_host: str, back_in: str) -> str:
    return f"""  <script>
  var RD_SPARK = {spark_geo};
  var RD_LIVE = {json.dumps(live_text)};
  var RD_API = {json.dumps(state_url)};
  /* empty unless this page itself arrived through the tunnel: that is the one
     case where stopping the connector closes the page, so the button says so
     first and names the address that survives (RD_BACK) */
  var RD_HOST = {json.dumps(str(tunnel_host))};
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
    var flagSig = '', edgeSig = '';
    var W = 100, H = 24, PAD = 3;

    function say(text, tone) {{
      toast.textContent = text;
      if (tone) toast.setAttribute('data-tone', tone);
      else toast.removeAttribute('data-tone');
      toast.classList.remove('on');
      void toast.offsetWidth;
      toast.classList.add('on');
    }}
    function set(selector, text) {{
      var node = document.querySelector(selector);
      if (node && text != null && node.textContent !== text) node.textContent = text;
    }}
    function flagNode(cc, edge) {{
      var img = document.createElement('img');
      img.className = edge ? 'rd-flag rd-flag--edge' : 'rd-flag';
      img.src = 'app/static/flags/' + cc.toLowerCase() + '.svg';
      img.alt = cc; img.title = cc;
      return img;
    }}
    function paintGeo(data) {{
      var g = data && data.origin_geo;
      var cc = (g && /^[A-Z]{{2}}$/.test(g.cc || '')) ? g.cc : '';
      if (cc !== flagSig) {{
        flagSig = cc;
        var slot = document.querySelector('[data-origin-flag]');
        if (slot) {{
          slot.textContent = '';
          if (cc) slot.appendChild(flagNode(cc, false));
        }}
      }}
      set('[data-origin-geo]', g ? (g.city || g.region || '') : '');
      set('[data-shard]', (data && data.shard) || '');
      var edges = (data && data.edges_geo) || [];
      var sig = edges.map(function (e) {{ return (e.code || '') + ':' + (e.cc || ''); }}).join('|')
                + '#' + ((data && data.edges) || '');
      if (sig === edgeSig) return;
      edgeSig = sig;
      var list = document.querySelector('[data-edge-list]');
      if (list) {{
        list.textContent = '';
        edges.forEach(function (item, index) {{
          if (index) list.appendChild(document.createTextNode(', '));
          var wrap = document.createElement('span');
          wrap.className = 'rd-edge';
          wrap.appendChild(document.createTextNode(item.code || ''));
          if (/^[A-Z]{{2}}$/.test(item.cc || '')) wrap.appendChild(flagNode(item.cc, true));
          list.appendChild(wrap);
        }});
        if (!edges.length) list.textContent = (data && data.edges) || '';
      }}
    }}
    function paintConnections(data) {{
      set('[data-connections]', String((data && data.connections) || 0));
    }}
    /* A bubble with no value is not a smaller truth, it is noise. While the
       connector is down there is no shard, no edge list and no count, so the
       chips leave the layout and the card shrinks. Origin stays: it comes from
       our own IP, not from the process. The server makes the same decision on
       the first frame; this keeps it true on every poll, so Start/Stop needs
       no reload to change the shape of the card. */
    function paintPresence(data) {{
      var live = !!(data && data.found);
      var edges = (data && data.edges_geo) || [];
      var names = (data && data.edges) || '';
      var hasEdges = live && (edges.length > 0 || (names && names !== '—'));
      var hasShard = live && !!((data && data.shard) || '');
      var hasSide = live && !!((data.uptime && data.uptime !== '—')
                               || (data.version && data.version !== '—'));
      var hasPath = live && !!(data.path && data.path !== '—');
      [['[data-tone="shard"]', hasShard],
       ['[data-tone="edges"]', hasEdges],
       ['[data-tone="connections"]', live],
       ['[data-fact-row="2"]', live],
       ['.rd-side', hasSide],
       ['.rd-path', hasPath]].forEach(function (pair) {{
        var node = document.querySelector(pair[0]);
        if (node) node.hidden = !pair[1];
      }});
    }}
    /* no process: dashes, not the last flattering numbers, and a line that
       decays to flat instead of freezing mid-air. Origin is the exception —
       it keeps the public address resolved by the sampler. */
    function blank(data) {{
      paintGeo(data || {{}});
      paintConnections({{}});
      paintPresence(data);
      set('[data-fact="origin"]', (data && data.origin) || '—');
      var edgeList = document.querySelector('[data-edge-list]');
      if (edgeList) edgeList.textContent = '—';
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
      /* CPU percent is single-core-equivalent, so a share of all available
         cores is value/cores (a Go connector uses several). RAM stays a share
         of its own budget. The core count comes from cgroups, not a constant. */
      var cores = spark.geo.cores || 1;
      var share = spark.key === 'cpu' ? value / cores : 100 * value / spark.geo.scale;
      var budget = spark.key === 'cpu'
        ? (spark.geo.cores_text || cores) + ' cores' : spark.geo.budget;
      var sub = (share > 0 && share < 1 ? share.toFixed(2) : Math.round(share)) + '% of ' + budget;
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
       A click navigates the PARENT app document to ``?rd=<verb>`` (on Community
       Cloud ``window.top`` is the outer wrapper, so using it would reload the
       wrapper and drop the verb). app.py runs the verb in the Streamlit
       process, reports the outcome and clears the parameter. The card then
       paints whatever is true — so the button can be wrong about nothing. */
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
      /* The card cannot navigate its parent: on Community Cloud the whole app
         is itself a sandboxed viewer frame, so any ancestor navigation from a
         descendant is blocked by the browser. The app document installed a
         guarded postMessage bridge (app.py) that reloads ITSELF with ?rd=;
         the message carries only one of three fixed verbs, nothing secret. */
      window.parent.postMessage({{rd: verb}}, '*');
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
      /* the heartbeat still has to be observed — the stale look is driven by
         an unchanged stamp, not by a line of text that used to live in a footer */
      beat(data);
      /* The verb's result travels in the SAME file as the numbers: no page
         reload, no Streamlit toast in the top corner. Show it once in the
         card's own bottom notification, in the card's own design. */
      var act = data.last_action;
      if (act && act.at && act.at !== lastActionAt) {{
        lastActionAt = act.at;
        var what = act.action || 'action';
        say(act.ok ? what + ' ok · pid ' + (act.pid || act.stopped || '—')
                   : what + ' failed — ' + (act.error || act.detail || 'no detail'),
            act.ok ? 'ok' : 'crit');
      }}
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
          blank(data);
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
      paintGeo(data);
      paintConnections(data);
      paintPresence(data);
      set('[data-uptime]', data.uptime);
      set('[data-version]', data.version);
      set('[data-path]', data.path);
      retitle(true);
      var copyBtn = document.querySelector('[data-copy]');
      if (copyBtn && data.path) copyBtn.setAttribute('data-copy', data.path);
      if (data.cores) {{
        sparks.forEach(function (spark) {{ if (spark.key === 'cpu') spark.geo.cores = data.cores; }});
      }}
      sparks.forEach(function (spark) {{
        var valueNode = document.querySelector('[data-value="' + spark.key + '"]');
        var shareNode = document.querySelector('[data-share="' + spark.key + '"]');
        /* The server's text is always MB. Writing it here every second used
           to undo a GB selection while the toggle still said GB. The chosen
           unit formats the live number instead; GPU/CPU text is unchanged. */
        if (valueNode && data[spark.key + '_text']) {{
          valueNode.textContent = spark.key === 'mem'
            ? fmtMem(data.mem) : data[spark.key + '_text'];
        }}
        if (shareNode && data[spark.key + '_share']) shareNode.textContent = data[spark.key + '_share'];
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
    var BEAT = 500, PATIENCE = 4000, CEILING = 10000;
    var misses = 0, beatTimer = null, lastActionAt = '';

    function lose() {{
      card.setAttribute('data-feed', 'lost');
      card.setAttribute('data-status', 'down');
      set('[data-status-text]', 'state unreachable');
      /* There is no footer to hold a breadcrumb: the status line above is the
         single honest place to say the state file could not be read. */
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
        /* repaint the RAM value now, not on the next animation tick */
        sparks.forEach(function (spark) {{
          if (spark.key === 'mem') show(spark, spark.shown);
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
  </script>"""


def spark_geometry(state: dict) -> str:
    """The two sparkline geometries, serialised once for the browser."""
    return json.dumps(
        {
            "cpu": {
                "pts": state["cpu_points"], "values": state["cpu_series"],
                "levels": state["cpu_levels"], "budget": state["cpu_budget"],
                "cores": float(state.get("host", {}).get("cores") or 1),
                "cores_text": state.get("cores_text") or ("%.1f" % float(
                    state.get("host", {}).get("cores") or 1)),
                "scale": 100, "bands": [25.0, 50.0, 80.0],
                "base": state["cpu"], "step": 4.2,
            },
            "mem": {
                "pts": state["mem_points"], "values": state["mem_series"],
                "levels": state["mem_levels"], "budget": state["mem_budget"],
                "scale": 256, "bands": [30.0, 55.0, 80.0],
                "base": state["mem"], "step": 2.6,
            },
        }, separators=(",", ":"))

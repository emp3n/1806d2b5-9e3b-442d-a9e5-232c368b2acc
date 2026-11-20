"""Palette, type scale and the whole stylesheet for the card.

Roles are literal tokens per mode, declared on ``.rd-stage`` so one attribute
switches the surface. Everything that is a tint is mixed from a hue token, not
written as a second hex: the palette is five values per mode and the derivatives
are computed. Fonts are embedded as data URLs — no network, no route.
"""

import base64
from pathlib import Path

from palette import DEFAULT_PALETTE, PALETTES, semantic_tokens

_STATIC = Path(__file__).resolve().parent / "static"


def _block(selector: str, tokens: dict[str, str], extra: str = "") -> str:
    lines = "\n".join(f"  {key}: {value};" for key, value in tokens.items())
    return f"{selector} {{\n{lines}\n{extra}}}\n"


def _token_css() -> str:
    """Semantic layer: one block per palette and mode, no hex in selectors."""
    out = [_block(":root", semantic_tokens(DEFAULT_PALETTE, "light"))]
    for name in PALETTES:
        out.append(_block(f'.rd-stage[data-palette="{name}"]',
                          semantic_tokens(name, "light"), "  color-scheme: light;\n"))
        out.append(_block(f'.rd-stage[data-palette="{name}"][data-theme="dark"]',
                          semantic_tokens(name, "dark"), "  color-scheme: dark;\n"))
        auto = _block(f'.rd-stage[data-palette="{name}"][data-theme="auto"]',
                      semantic_tokens(name, "dark"), "  color-scheme: dark;\n")
        out.append("@media (prefers-color-scheme: dark) {\n" + auto + "}\n")
    return "\n".join(out)


def _font(name: str) -> str:
    raw = (_STATIC / name).read_bytes()
    return "data:font/woff2;base64," + base64.b64encode(raw).decode("ascii")


THEME_CSS = r"""
@font-face {
  font-family: "RD Golos";
  src: url("__GOLOS_CYR__") format("woff2");
  font-weight: 400 700; font-display: swap;
  unicode-range: U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116;
}
@font-face {
  font-family: "RD Golos";
  src: url("__GOLOS_LAT__") format("woff2");
  font-weight: 400 700; font-display: swap;
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA,
    U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193;
}
@font-face {
  font-family: "RD Russo";
  src: url("__RUSSO_CYR__") format("woff2");
  font-weight: 400; font-display: swap;
  unicode-range: U+0301, U+0400-045F, U+0490-0491, U+04B0-04B1, U+2116;
}
@font-face {
  font-family: "RD Russo";
  src: url("__RUSSO_LAT__") format("woff2");
  font-weight: 400; font-display: swap;
  unicode-range: U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA,
    U+02DC, U+0304, U+0308, U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193;
}

html, body { margin: 0; padding: 0; height: 100%; overflow: hidden; background: transparent; }

__TOKEN_BLOCKS__

.rd-stage {
  scrollbar-width: thin;
  scrollbar-color: color-mix(in srgb, var(--text) 26%, transparent) transparent;
  /* STATIC structure only: colour comes from the generated semantic blocks. */
  --shadow: 0 12px 40px -20px rgb(23 26 25 / 34%);
  --shadow-tip: 0 6px 18px -8px rgb(23 26 25 / 42%);
  --row: 32px;             /* compact density from the brief */
  --fast: 140ms ease-out;
  --slow: 260ms ease-out;
  --dock: 76px;
  display: flex; justify-content: center; align-items: center;
  box-sizing: border-box;
  height: 100vh; padding: 24px 16px; overflow: hidden;
  background: var(--stage); color: var(--text);
  overscroll-behavior: contain;
  font-family: "RD Golos", system-ui, sans-serif; font-size: 13px; line-height: 1.45;
}

/* A scrollbar is not the browser's default grey slab: same tokens as the card.
   It only exists where the stage actually scrolls (mobile, and a desktop window
   shorter than the smallest readable scale). */
.rd-stage::-webkit-scrollbar { width: 10px; height: 10px; }
.rd-stage::-webkit-scrollbar-track { background: transparent; }
.rd-stage::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--text) 22%, transparent);
  border: 3px solid transparent; border-radius: 8px;
  background-clip: padding-box;
}
.rd-stage::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--text) 40%, transparent);
  background-clip: padding-box;
}

/* the card is taller than the window even at the smallest scale that is still
   readable: scroll it. Auto margins on the frame (below) are what keeps both
   ends reachable — `align-items: center` would push the top out of the box. */
.rd-stage.rd-scroll {
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}
.rd-stage.rd-scroll .rd-frame { transform: none; margin-block: auto; }

.rd-stage *, .rd-stage *::before, .rd-stage *::after { box-sizing: border-box; }
.rd-stage svg { display: block; }
.rd-stage h1, .rd-stage h2, .rd-stage p, .rd-stage dl, .rd-stage dd, .rd-stage ul { margin: 0; }
.rd-stage ul { padding: 0; list-style: none; }
.rd-stage button { font: inherit; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
.rd-stage button:focus-visible, .rd-stage [tabindex]:focus-visible {
  outline: 2px solid var(--focus); outline-offset: 2px;
}
.rd-stage button:disabled { cursor: default; }

/* ---------- the frame: content, then the rail ---------- */
.rd-frame { display: flex; align-items: center; gap: 8px; width: 100%; max-width: 1040px; }
.rd-view { flex: 1 1 auto; min-width: 0; }
.rd-panel[hidden] { display: none; }
.rd-panel.rd-enter > * { animation: rd-drop 220ms ease-out both; }
.rd-panel.rd-enter > *:nth-child(2) { animation-delay: 40ms; }

/* ---------- the card ---------- */
.rd-card {
  display: flex; flex-direction: column; gap: 10px;
  width: 100%; max-width: 392px; padding: 16px; border-radius: 20px;
  background: var(--card); box-shadow: var(--shadow);
}
.rd-card > :not(.rd-head):not(.rd-foot) { transition: opacity var(--fast); }
.rd-card[data-run="stop"] > :not(.rd-head):not(.rd-foot) { opacity: .45; }
/* the feed stopped answering: the numbers are still true, they are just not
   now, so they step back instead of passing for live ones. "stalled" is the
   same promise for a state file that stopped being written while the page is
   still open. */
.rd-card[data-feed="lost"] > :not(.rd-head):not(.rd-foot),
.rd-card[data-feed="stalled"] > :not(.rd-head):not(.rd-foot) { opacity: .45; }

.rd-head { display: flex; align-items: center; gap: 10px; }
.rd-mark { width: 40px; height: 40px; flex: none; }
.rd-id { flex: 1; min-width: 0; }
.rd-id h1 {
  font-family: "RD Russo"; font-weight: 400;
  font-size: 20px; line-height: 1.2; letter-spacing: .01em;
}
.rd-status {
  margin-top: 2px; font-size: 13px; color: var(--ok);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.rd-card[data-status="down"] .rd-status { color: var(--crit); }
.rd-note {
  margin-top: 2px; font-size: 13px; color: var(--crit);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.rd-note[hidden] { display: none; }
.rd-card[data-status="stop"] .rd-status { color: var(--muted); }

/* The card owns no buttons: Stop and Restart are Streamlit widgets, docked over
   this header by app.py. What is left here is the tooltip that the path chip
   still uses. */
/* the verbs: two round buttons in the card's own header, where they were */
.rd-verbs { display: flex; gap: 4px; flex: none; }
.rd-round {
  position: relative; width: 34px; height: 34px; border-radius: 50%;
  display: grid; place-items: center; flex: none;
  background: var(--raised); color: var(--muted);
  transition: background-color var(--fast), color var(--fast);
}
.rd-round svg {
  width: 17px; height: 17px; fill: none; stroke: currentColor;
  stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round;
}
.rd-round:hover { background: var(--sunken); color: var(--text); }
.rd-round[aria-pressed="true"] { color: var(--text); }
.rd-round.is-armed { background: var(--crit); color: var(--card); }
.rd-round.is-busy svg { animation: rd-spin 700ms linear infinite; }
.rd-tip {
  position: absolute; top: calc(100% + 6px); right: 0; z-index: 4;
  padding: 4px 8px; border-radius: 8px; white-space: nowrap;
  background: var(--text); color: var(--card); font-size: 13px;
  box-shadow: var(--shadow-tip); pointer-events: none;
  opacity: 0; transform: translateY(-4px);
  transition: opacity var(--fast), transform var(--fast);
}
.rd-copy:hover .rd-tip, .rd-copy:focus-visible .rd-tip,
.rd-round:hover .rd-tip, .rd-round:focus-visible .rd-tip { opacity: 1; transform: none; }

/* ---------- facts: the two chips, no dots ---------- */
.rd-facts { display: flex; flex-wrap: wrap; gap: 6px; }
.rd-fact {
  padding: 4px 10px; border-radius: 8px; font-size: 13px;
  background: var(--ok-tint); color: var(--text);
}
.rd-fact em { font-style: normal; margin-right: 6px; color: var(--muted); }
.rd-fact[data-tone="mem"] { background: var(--mem-tint); }

/* ---------- measures ---------- */
.rd-measures { display: grid; grid-template-columns: minmax(0, 1fr) 124px; gap: 8px; }
.rd-tile { position: relative; border-radius: 14px; padding: 10px 12px; background: var(--cpu-tint); }
.rd-tile[data-tone="mem"] { background: var(--mem-tint); }
.rd-tile[data-level="ok"] { --lv: var(--ok); }
.rd-tile[data-level="warm"] { --lv: var(--warn); }
.rd-tile[data-level="hot"] { --lv: var(--hot); }
.rd-tile[data-level="crit"] { --lv: var(--crit); }
.rd-tile-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.rd-k { font-size: 13px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); }
.rd-value-row { display: flex; align-items: baseline; gap: 8px; margin-top: 2px; }
.rd-v {
  font-family: "RD Russo"; font-weight: 400;
  font-size: 30px; line-height: 1.1; letter-spacing: .01em; color: var(--lv);
  transition: color var(--fast);
}
.rd-d { font-size: 13px; color: var(--ok); }
.rd-d[data-dir="up"] { color: var(--crit); }
.rd-sub { display: block; font-size: 13px; color: var(--muted); }

.rd-spark { position: relative; margin-top: 6px; padding-bottom: 2px; touch-action: pan-y; }
.rd-spark svg { width: 100%; height: 24px; }
.rd-seg { stroke-width: 2; stroke-linecap: round; vector-effect: non-scaling-stroke; }
.lv-ok { stroke: var(--ok); }
.lv-warm { stroke: var(--warn); }
.lv-hot { stroke: var(--hot); }
.lv-crit { stroke: var(--crit); }
.rd-cursor {
  position: absolute; width: 10px; height: 10px; border-radius: 50%;
  background: var(--lv); border: 2px solid var(--card); opacity: 0;
  transform: translate(-50%, -50%);
  transition: opacity 90ms ease; pointer-events: none;
}
.rd-cursor.on { opacity: 1; }
.rd-tile.is-scrubbing .rd-sub { color: var(--lv); }
.rd-v { transition: color var(--fast); }

.rd-units { display: inline-flex; gap: 2px; padding: 2px; border-radius: 8px; background: var(--sunken); }
.rd-units button { padding: 0 6px; border-radius: 8px; font-size: 13px; color: var(--muted); transition: background-color var(--fast), color var(--fast); }
.rd-units button[aria-pressed="true"] { background: var(--card); color: var(--text); }

.rd-side { display: grid; grid-template-rows: 1fr 1fr; gap: 8px; }
.rd-mini {
  text-align: left; padding: 8px 10px; border-radius: 8px;
  background: var(--raised); color: var(--text);
  transition: background-color var(--fast);
}
.rd-mini[data-tone="acc"] { background: var(--acc-tint); }
.rd-mini:hover { background: var(--sunken); }
.rd-mini[aria-pressed="true"] { background: var(--sunken); }
.rd-mini b { display: block; margin-top: 2px; font-family: "RD Russo"; font-weight: 400; font-size: 15px; line-height: 1.4; }

/* ---------- path ---------- */
.rd-path { display: flex; align-items: center; gap: 6px; padding: 2px 2px 2px 10px; border-radius: 8px; background: var(--raised); }
.rd-path code {
  flex: 1; min-width: 0; font-family: ui-monospace, monospace;
  font-size: 13px; color: var(--muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.rd-copy { position: relative; width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; color: var(--muted); flex: none; }
.rd-copy:hover { color: var(--text); }
.rd-copy svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }

.rd-foot {
  font-size: 13px; color: var(--muted);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

/* ---------- sections that are not built ---------- */
.rd-plate { width: 100%; max-width: 520px; padding: 24px; border-radius: 20px; background: var(--card); box-shadow: var(--shadow); }
.rd-plate h2 { font-family: "RD Russo"; font-weight: 400; font-size: 20px; line-height: 1.2; }
.rd-stub { margin-top: 6px; font-size: 13px; color: var(--muted); }

/* ---------- rail: one circle answers, the rest hold still ---------- */
.rd-rail {
  display: flex; flex-direction: column; align-items: flex-end; gap: 12px;
  width: 216px; padding: 2px 24px 0 0;
}
.rd-tab {
  position: relative; display: grid; place-items: center;
  width: 76px; height: 76px; padding: 0;
}
.rd-tab-dot {
  position: relative; z-index: 2;
  width: 76px; height: 76px; border-radius: 50%;
  display: grid; place-items: center;
  background: var(--raised); border: 1px solid var(--border); color: var(--muted);
  transition: transform 380ms ease-out 40ms, background-color var(--fast) 40ms,
    color var(--fast) 40ms, border-color var(--fast) 40ms, box-shadow 380ms ease-out 40ms;
}
.rd-tab-dot svg {
  width: 28px; height: 28px; fill: none; stroke: currentColor;
  stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round;
}
/* the name is a slab that lives behind the circle: it comes out of the slot
   rather than fading in beside it */
.rd-tab-name {
  position: absolute; right: 24px; z-index: 1;
  display: flex; align-items: center;
  height: 48px; padding: 0 38px 0 24px; border-radius: 8px;
  background: var(--slab); border: 1px solid var(--border);
  box-shadow: var(--shadow-tip);
  font-size: 15px; letter-spacing: .01em; white-space: nowrap; color: var(--text);
  opacity: 0;
  transform: translateX(24px); transform-origin: right center;
  transition: opacity 200ms ease-out, transform 340ms ease-out;
}
/* the circle turns a quarter of a hand and floats above the slab */
.rd-tab:hover .rd-tab-dot, .rd-tab:focus-visible .rd-tab-dot {
  transform: translateX(25px) rotate(25deg);
  color: var(--text); background: var(--knob);
  border-color: transparent; box-shadow: var(--shadow-tip);
}
.rd-tab[aria-pressed="true"] .rd-tab-dot {
  background: var(--text); color: var(--card); border-color: var(--text);
}
.rd-tab:hover .rd-tab-name, .rd-tab:focus-visible .rd-tab-name {
  opacity: 1; transform: none;
}

/* ---------- toast ---------- */
.rd-toast {
  position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%);
  padding: 6px 12px; border-radius: 8px; font-size: 13px;
  background: var(--text); color: var(--card); box-shadow: var(--shadow-tip);
  opacity: 0; pointer-events: none;
}
.rd-toast[data-tone="crit"] { background: var(--crit); color: var(--card); }
.rd-toast.on { animation: rd-toast 2400ms ease-out forwards; }

@keyframes rd-drop { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: none; } }
@keyframes rd-spin { to { transform: rotate(360deg); } }
@keyframes rd-toast { 0% { opacity: 0; } 10%, 76% { opacity: 1; } 100% { opacity: 0; } }
/* a phone's visual viewport is shorter than 100vh while the URL bar is out:
   the dock must sit on the edge you can actually reach */
@supports (height: 100dvh) {
  .rd-stage { height: 100dvh; }
}
@media (prefers-reduced-motion: reduce) {
  .rd-stage *, .rd-toast { animation-duration: .001ms !important; transition-duration: .001ms !important; }
}
/* ---------- phones ----------
   The rail is the thumb's reach, not the eye's: it docks to the bottom as a
   frosted strip, the circles hold still (no slide, no turn, no name plate that
   a tap would leave stuck open), and the card owns the rest of the screen and
   scrolls. A tap on a spark no longer fights the page scroll: the graphs are
   `touch-action: pan-y`, so a vertical drag always scrolls. */
@media (max-width: 760px) {
  .rd-stage {
    display: flex; flex-direction: column; overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    /* the main axis is vertical here, so `center` would clip a tall card;
       the frame's auto margins do the centring, safely */
    justify-content: flex-start;
    padding: 12px 12px calc(var(--dock) + 4px + env(safe-area-inset-bottom));
  }
  .rd-frame { flex-direction: column; gap: 0; max-width: none; margin-block: auto; }
  .rd-view { width: 100%; }
  .rd-card { max-width: 432px; margin: 0 auto; }

  .rd-rail {
    position: fixed; left: 0; right: 0; bottom: 0; z-index: 6;
    flex-direction: row; align-items: center; justify-content: space-around;
    gap: 4px; width: auto;
    padding: 10px 12px calc(10px + env(safe-area-inset-bottom));
    box-shadow: inset 0 1px 0 var(--border);
    background: color-mix(in srgb, var(--stage) 76%, transparent);
    -webkit-backdrop-filter: blur(16px) saturate(1.2);
    backdrop-filter: blur(16px) saturate(1.2);
  }
  .rd-tab { width: 56px; height: 56px; }
  .rd-tab-dot {
    width: 56px; height: 56px;
    transition: background-color var(--fast) 40ms, color var(--fast) 40ms;
  }
  .rd-tab-dot svg { width: 24px; height: 24px; }
  /* the name lives in `aria-label`: on a touch screen a plate would stay open
     after the tap and eat the gap above the card */
  .rd-tab-name { display: none; }
  .rd-tab:hover .rd-tab-dot, .rd-tab:focus-visible .rd-tab-dot { transform: none; }

  .rd-measures { grid-template-columns: minmax(0, 1fr); }
  .rd-side { grid-template-rows: auto; grid-template-columns: 1fr 1fr; }
  .rd-toast { bottom: calc(var(--dock) + 12px + env(safe-area-inset-bottom)); }
}
@media (max-width: 430px) {
  .rd-stage { --dock: 72px; }  /* 10 + 52 + 10 */
  .rd-rail { padding-left: 8px; padding-right: 8px; }
  .rd-tab { width: 52px; height: 52px; }
  .rd-tab-dot { width: 52px; height: 52px; }
  .rd-tab-dot svg { width: 22px; height: 22px; }
}
@media (hover: none) {
  /* no hover theatre where there is no pointer */
  .rd-tab:hover .rd-tab-dot { transform: none; color: var(--muted); background: var(--raised); border-color: var(--border); box-shadow: none; }
  .rd-tab[aria-pressed="true"] .rd-tab-dot { background: var(--text); color: var(--card); border-color: var(--text); }
}
""".replace("__TOKEN_BLOCKS__", _token_css()) \
  .replace("__GOLOS_CYR__", _font("golos-cyr.woff2")) \
  .replace("__GOLOS_LAT__", _font("golos-lat.woff2")) \
  .replace("__RUSSO_CYR__", _font("russo-cyr.woff2")) \
  .replace("__RUSSO_LAT__", _font("russo-lat.woff2"))

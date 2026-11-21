"""Small HTML fragments used by the card's page template."""
from __future__ import annotations

import re
from typing import Any

from .common import esc, icon


def measure(state: dict[str, Any], metric: str, *, hidden: bool = False) -> str:
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
    off = " hidden" if hidden else ""
    return f"""<div class="rd-tile" data-tone="{key}" data-level="{state[f'{key}_level']}"{off}>
      <div class="rd-tile-head">
        <span class="rd-k">{label}</span>{units}
      </div>
      <div class="rd-value-row">
        <b class="rd-v" data-value="{key}"{' data-mem-value' if metric == 'mem' else ''}>{state[f'{key}_text']}</b>
      </div>
      <span class="rd-sub" data-share="{key}">{state[f'{key}_share']}</span>
      <div class="rd-spark" data-spark="{key}" tabindex="0" role="img"
        aria-label="{label} over the last {state['samples']} samples, now {state[f'{key}_text']}.">
        <svg viewBox="0 0 100 24" preserveAspectRatio="none" aria-hidden="true">{state[f'{key}_segments']}</svg>
        <i class="rd-cursor" aria-hidden="true"></i>
      </div>
    </div>"""


def flag_img(cc: str, cls: str = "rd-flag", label: str = "flag") -> str:
    """A local SVG flag; no CDN, no PNG, no layout shift after the first paint."""
    if not re.fullmatch(r"[A-Z]{2}", cc or ""):
        return ""
    path = f"app/static/flags/{cc.lower()}.svg"
    return (f'<img class="{cls}" src="{path}" alt="{esc(cc)} {esc(label)}" '
            f'title="{esc(cc)}" width="20" height="15" data-flag="{cc}">')


def geo_city(geo: dict[str, str]) -> str:
    """Only the place the visitor asked for, not a dump of the geo record."""
    return str(geo.get("city") or geo.get("region") or "")


def edge_items(geo_list: list[dict[str, str]], fallback: str) -> str:
    """Each edge keeps its code, with its SVG flag raised like a superscript."""
    if not geo_list:
        return esc(fallback)
    pieces = []
    for item in geo_list:
        code = esc(item.get("code", ""))
        flag = flag_img(item.get("cc", ""), "rd-flag rd-flag--edge", "edge")
        count = int(item.get("count") or 1)
        badge = f'<sup class="rd-edge-count">×{count}</sup>' if count > 1 else ""
        pieces.append(f'<span class="rd-edge">{code}{flag}{badge}</span>')
    return '<span class="rd-comma">, </span>'.join(pieces)

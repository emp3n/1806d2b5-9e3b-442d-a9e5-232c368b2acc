"""Shared HTML primitives: escaping, the Cloudflare mark and rail icons."""
from __future__ import annotations

import html


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

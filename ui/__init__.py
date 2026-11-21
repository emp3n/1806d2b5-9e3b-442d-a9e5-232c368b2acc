"""Everything the browser sees: markup, script, tokens and the stylesheet.

:mod:`ui.card` renders escaped HTML with its own JavaScript; :mod:`ui.theme`
generates CSS from the roles in :mod:`ui.palette`. No sampling happens here —
the card consumes the JSON produced by :mod:`core.statefeed`.
"""

from . import card, palette, theme

__all__ = ["card", "palette", "theme"]

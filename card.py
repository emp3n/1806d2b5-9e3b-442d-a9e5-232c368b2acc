"""Compatibility alias: the implementation lives in `ui/card.py`."""
import sys
from ui import card as _impl
sys.modules[__name__] = _impl

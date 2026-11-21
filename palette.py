"""Compatibility alias: the implementation lives in `ui/palette.py`."""
import sys
from ui import palette as _impl
sys.modules[__name__] = _impl

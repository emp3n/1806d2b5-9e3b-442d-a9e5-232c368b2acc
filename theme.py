"""Compatibility alias: the implementation lives in `ui/theme.py`."""
import sys
from ui import theme as _impl
sys.modules[__name__] = _impl

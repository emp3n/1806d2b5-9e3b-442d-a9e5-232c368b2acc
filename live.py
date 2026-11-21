"""Compatibility alias: the implementation lives in `core/live.py`."""
import sys
from core import live as _impl
sys.modules[__name__] = _impl

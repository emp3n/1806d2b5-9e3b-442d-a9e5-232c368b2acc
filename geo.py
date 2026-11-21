"""Compatibility alias: the implementation lives in `core/geo.py`."""
import sys
from core import geo as _impl
sys.modules[__name__] = _impl

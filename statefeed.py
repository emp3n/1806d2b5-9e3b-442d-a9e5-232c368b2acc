"""Compatibility alias: the implementation lives in `core/statefeed.py`."""
import sys
from core import statefeed as _impl
sys.modules[__name__] = _impl

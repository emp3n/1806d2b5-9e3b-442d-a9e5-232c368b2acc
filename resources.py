"""Compatibility alias: the implementation lives in `core/resources.py`."""
import sys
from core import resources as _impl
sys.modules[__name__] = _impl

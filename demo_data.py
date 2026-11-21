"""Compatibility alias: the implementation lives in `core/demo_data.py`."""
import sys
from core import demo_data as _impl
sys.modules[__name__] = _impl

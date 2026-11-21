"""Compatibility alias: the implementation lives in `core/sessions.py`."""
import sys
from core import sessions as _impl
sys.modules[__name__] = _impl

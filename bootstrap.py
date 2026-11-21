"""Compatibility alias: the implementation lives in `core/bootstrap.py`."""
import sys
from core import bootstrap as _impl
sys.modules[__name__] = _impl

"""Live sampling: /proc readers, parsers and the Collector.

Focused layers: proc, sources, collector. Every public definition is re-exported flat,
so old imports and monkeypatching still work after the split.
"""
from __future__ import annotations

import sys
from types import ModuleType

from . import proc, sources, collector

_MODULES = (proc, sources, collector,)
_RAW_OWNER = {'Any': 'collector',
 'CLOCK_TICKS': 'collector',
 'CONN_INDEX': 'collector',
 'CPU_BUDGET': 'collector',
 'Collector': 'collector',
 'EDGE_IP': 'collector',
 'HTTP_TIMEOUT': 'collector',
 'Iterable': 'collector',
 'LOCATION': 'collector',
 'MEM_BUDGET_MB': 'collector',
 'METRICS_URL': 'collector',
 'PAGE_SIZE': 'collector',
 'PROC': 'collector',
 'Path': 'collector',
 'REGISTER_ERROR': 'collector',
 'SAMPLES': 'collector',
 'STATE_NAMES': 'collector',
 'TIME_FIELD': 'collector',
 'TIME_LEAD': 'collector',
 'TRACE_EVERY': 'collector',
 'TRACE_URL': 'collector',
 '_VERSION_CACHE': 'collector',
 '_http': 'sources',
 '_text': 'proc',
 'available_cores': 'collector',
 'boot_seconds': 'proc',
 'bootstrap': 'collector',
 'cores_text': 'collector',
 'cpu_rate': 'proc',
 'cpu_rate_ns': 'proc',
 'cpu_text': 'proc',
 'datetime': 'collector',
 'deque': 'collector',
 'find_pid': 'proc',
 'geo': 'collector',
 'idle_state': 'collector',
 'is_tunnel_run': 'proc',
 'launch_file': 'proc',
 'line_epoch': 'sources',
 'load_launch': 'proc',
 'log_path': 'proc',
 'memory_limit_bytes': 'collector',
 'metrics_url_for': 'proc',
 'parse_log': 'sources',
 'parse_metrics': 'sources',
 'parse_ready': 'sources',
 'parse_trace': 'sources',
 'proc_age': 'proc',
 'proc_cmdline': 'proc',
 'proc_cpu_ns': 'proc',
 'proc_exe': 'proc',
 'proc_memory_limit': 'proc',
 'proc_rss': 'proc',
 'proc_started_at': 'proc',
 'proc_stat': 'proc',
 'proc_state': 'proc',
 'read_tail': 'proc',
 'save_launch': 'proc',
 'scan_pids': 'proc',
 'sessions': 'collector',
 'strip_token': 'proc',
 'timezone': 'collector',
 'token_file_for': 'proc'}
_MOD_BY_NAME = {m.__name__.rsplit(".", 1)[-1]: m for m in _MODULES}
_OWNER = {k: _MOD_BY_NAME[v] for k, v in _RAW_OWNER.items()}

# each layer sees every definition as a bare global (the same object)
for _module in _MODULES:
    for _name, _owner in _OWNER.items():
        setattr(_module, _name, getattr(_owner, _name))


class _FlatModule(ModuleType):
    """A patch through the package reaches the owner and every other layer."""

    def __setattr__(self, key, value):
        super().__setattr__(key, value)
        if self is _api and key in _OWNER:
            for _module in _MODULES:
                ModuleType.__setattr__(_module, key, value)


_api = sys.modules[__name__]
_api.__class__ = _FlatModule
for _name, _owner in _OWNER.items():
    setattr(_api, _name, getattr(_owner, _name))

# stdlib modules stay shared: patching their attributes affects every layer
import json as _json, os as _os, platform as _platform, signal as _signal
import time as _time, urllib as _urllib
json, os, platform, signal, time, urllib = _json, _os, _platform, _signal, _time, _urllib

__all__ = list(_OWNER) + ["os", "urllib"]

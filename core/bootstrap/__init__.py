"""Tunnel bootstrap: config, binary, probes and process control.

Focused layers: config, binary, probes, tunnel. Every public definition is re-exported flat,
so old imports and monkeypatching still work after the split.
"""
from __future__ import annotations

import sys
from types import ModuleType

from . import config, binary, probes, tunnel

_MODULES = (config, binary, probes, tunnel,)
_RAW_OWNER = {'BASE': 'tunnel',
 'CARD_STATE_PATH': 'tunnel',
 'CHECK_EVERY': 'tunnel',
 'DEFAULT_HOSTNAME': 'tunnel',
 'HOST_KEYS': 'tunnel',
 'METRICS_PORT': 'tunnel',
 'PUBLIC_STRIKES': 'tunnel',
 'Path': 'tunnel',
 'STATE_PATH': 'tunnel',
 'STUCK_AFTER': 'tunnel',
 'TOKEN_KEYS': 'tunnel',
 'TUNNEL_RE': 'tunnel',
 'Tunnel': 'tunnel',
 'URL_KEYS': 'tunnel',
 'VERSION': 'tunnel',
 '_cached': 'tunnel',
 '_child_procs': 'tunnel',
 '_cmdline': 'probes',
 '_describe': 'tunnel',
 '_flag_value': 'probes',
 '_forget_strikes': 'tunnel',
 '_lock': 'tunnel',
 '_log_tail': 'binary',
 '_lookup': 'config',
 '_proc_alive': 'probes',
 '_proc_state': 'probes',
 '_public_failures': 'tunnel',
 '_reap': 'tunnel',
 '_replace_with_named': 'tunnel',
 '_secret': 'config',
 '_setting': 'config',
 '_spawn': 'tunnel',
 '_tail_hint': 'probes',
 '_wait_ready': 'tunnel',
 'adopt_running_tunnel': 'tunnel',
 'asset_name': 'binary',
 'cloud_url': 'config',
 'dataclass': 'tunnel',
 'desired_state': 'config',
 'download': 'binary',
 'download_url': 'binary',
 'ensure_tunnel': 'tunnel',
 'existing_binary': 'binary',
 'field': 'tunnel',
 'forget_tunnel': 'tunnel',
 'ingress_port': 'config',
 'intent_path': 'config',
 'metrics_answers': 'probes',
 'our_tunnels': 'probes',
 'pick_metrics_port': 'probes',
 'port_is_free': 'probes',
 'proc_age': 'probes',
 'public_answers': 'probes',
 'public_hostname': 'config',
 'public_is_stuck': 'tunnel',
 'ready_connections': 'probes',
 'restart_tunnel': 'tunnel',
 'retire_stale_tunnel': 'tunnel',
 'reusable': 'tunnel',
 'runtime_dir': 'config',
 'set_desired': 'config',
 'start_named_tunnel': 'tunnel',
 'start_quick_tunnel': 'tunnel',
 'state_answers': 'probes',
 'stop_tunnel': 'tunnel',
 'token_files': 'config',
 'tunnel_status': 'tunnel',
 'tunnel_token': 'config',
 'version_of': 'binary',
 'write_token_file': 'config'}
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

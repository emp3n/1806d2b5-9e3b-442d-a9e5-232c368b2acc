"""Runtime key gate and decryption loader for the encrypted deployment.

Every published ``*.py`` file except this one, ``main.py`` and the vendored
``mcp_exec.py`` is committed as ciphertext. This module is the only plaintext
piece of the mechanism: it reads the key from Streamlit secrets (or the local
key file in development), verifies an authentication tag, and decrypts modules
in memory as Python imports them. Without the right key, importing any app
module raises instead of running code.
"""
from __future__ import annotations

import ast
import hashlib
import hmac
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import os
import sys
from functools import lru_cache
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
MAGIC = b"STRMD1\x00"
SALT = hashlib.sha256(b"streamd.deploy.ckey.v1").digest()[:16]
PLAINTEXT = {"main", "_decrypt", "mcp_exec", "conftest"}
KEY_NAME = "DECRYPT_CKEY"
ALIASES = {}
_cached_key = None


def local_key_file():
    return APP_DIR / ".local" / "ckey"


def resolve_key(explicit=None):
    """Key order: explicit -> Streamlit secrets -> environment -> local file."""
    global _cached_key
    if explicit:
        return str(explicit)
    if _cached_key is not None:
        return _cached_key
    try:
        import streamlit as st
        secrets = getattr(st, "secrets", None)
        if secrets is not None and KEY_NAME in secrets:
            value = str(secrets[KEY_NAME]).strip()
            if value:
                _cached_key = value
                return value
    except Exception:
        pass
    value = os.environ.get(KEY_NAME, "").strip()
    if value:
        _cached_key = value
        return value
    path = local_key_file()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            _cached_key = value
            return value
    return ""


@lru_cache(maxsize=8)
def _keys(key):
    raw = hashlib.pbkdf2_hmac("sha256", key.encode("utf-8"), SALT, 120_000, dklen=64)
    return raw[:32], raw[32:]


def encrypt(source, key):
    """Deterministic authenticated encryption (encrypt-then-MAC, HMAC CTR)."""
    if isinstance(source, str):
        source = source.encode("utf-8")
    if not key:
        raise RuntimeError("DECRYPT_CKEY is required to encrypt")
    enc_key, mac_key = _keys(str(key).strip())
    nonce = hmac.new(enc_key, source, hashlib.sha256).digest()[:16]
    stream = bytearray()
    counter = 0
    while len(stream) < len(source):
        stream.extend(hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(source, stream))
    tag = hmac.new(mac_key, MAGIC + nonce + cipher, hashlib.sha256).digest()[:32]
    return MAGIC + nonce + cipher + tag


def decrypt(blob, key=None):
    """Return plaintext bytes; raise when the key is missing or wrong."""
    blob = bytes(blob)
    if not blob.startswith(MAGIC):
        return blob  # a plaintext file in the local working tree
    if len(blob) < len(MAGIC) + 16 + 32:
        raise RuntimeError("encrypted file is truncated")
    supplied = resolve_key(key)
    if not supplied:
        raise RuntimeError(KEY_NAME + " is not set: refusing to run encrypted code")
    enc_key, mac_key = _keys(supplied)
    nonce = blob[len(MAGIC):len(MAGIC) + 16]
    cipher = blob[len(MAGIC) + 16:-32]
    tag = blob[-32:]
    expected = hmac.new(mac_key, MAGIC + nonce + cipher, hashlib.sha256).digest()[:32]
    if not hmac.compare_digest(tag, expected):
        raise RuntimeError(KEY_NAME + " is invalid: cannot decrypt application code")
    stream = bytearray()
    counter = 0
    while len(stream) < len(cipher):
        stream.extend(hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(a ^ b for a, b in zip(cipher, stream))


def source_text(path):
    """Decrypt one file to text for the app launcher or maintenance tools."""
    return decrypt(Path(path).read_bytes()).decode("utf-8")


class _Loader(importlib.abc.Loader):
    def __init__(self, path):
        self.path = path

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        source = source_text(self.path)
        code = compile(source, str(self.path), "exec")
        exec(code, module.__dict__)


class _AliasLoader(importlib.abc.Loader):
    """Bind a root alias straight to its implementation module.

    Returning the implementation from create_module means the import machinery
    never publishes an empty shell in sys.modules, so a concurrent
    `from demo_data import X` cannot observe a half-initialised module.
    """

    def __init__(self, target):
        self.target = target

    def create_module(self, spec):
        implementation = importlib.import_module(self.target)
        sys.modules[spec.name] = implementation
        return implementation

    def exec_module(self, module):
        return None


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        root = fullname.split(".", 1)[0]
        if root in PLAINTEXT:
            return None
        if fullname in ALIASES:
            return importlib.machinery.ModuleSpec(fullname, _AliasLoader(ALIASES[fullname]))
        parts = fullname.split(".")
        base = APP_DIR.joinpath(*parts)
        package_init = base / "__init__.py"
        module_file = base.with_suffix(".py")
        if package_init.is_file():
            return importlib.util.spec_from_file_location(
                fullname, package_init, loader=_Loader(package_init),
                submodule_search_locations=[str(base)],
            )
        if module_file.is_file():
            return importlib.util.spec_from_file_location(
                fullname, module_file, loader=_Loader(module_file),
            )
        return None


def discover_aliases():
    """Find root aliases of the form `from core import X as _impl` + sys.modules swap."""
    found = {}
    for path in sorted(APP_DIR.glob("*.py")):
        if path.stem in PLAINTEXT:
            continue
        try:
            tree = ast.parse(source_text(path))
        except Exception:
            continue
        location = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute)
                       and t.value.attr == "modules" for t in node.targets):
                continue
            if isinstance(node.value, ast.Name) and node.value.id == "_impl":
                for imported in ast.walk(tree):
                    if isinstance(imported, ast.ImportFrom):
                        for alias in imported.names:
                            if alias.asname == "_impl":
                                prefix = "." * imported.level
                                module = prefix + (imported.module or "")
                                location = (module + "." + alias.name).lstrip(".")
        if location:
            found[path.stem] = location
    return found


def preload_aliases():
    """Publish every alias as the implementation module before threads start.

    The alias files swap `sys.modules[__name__]` only at the end of their body.
    If two threads import one at the same time, the loser can see the empty
    shell and raise `ImportError: cannot import name ...`. Loading them here,
    single-threaded, removes that window entirely.
    """
    for name, target in ALIASES.items():
        implementation = importlib.import_module(target)
        sys.modules[name] = implementation


def install_hook():
    """Route imports of encrypted modules through the decrypting loader."""
    if any(isinstance(finder, _Finder) for finder in sys.meta_path):
        return
    ALIASES.update(discover_aliases())
    sys.meta_path.insert(0, _Finder())
    preload_aliases()

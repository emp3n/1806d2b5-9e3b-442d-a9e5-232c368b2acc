"""Universal remote-executor for Streamlit apps — vendored into every app.

Install — the injection is the FIRST EXECUTABLE statement of main.py, directly
below the module docstring and above every other import:

    \"\"\"Entry point ...\"\"\"                                     # stays line 1
    from mcp_exec import install_and_stop; install_and_stop()   # first executable

Keep that order. A module-level string is a docstring only while it is the
first statement; put the injection above it and the string becomes a bare
expression, which Streamlit's magic renders as UI text — the app then opens
with its own launcher documentation at the top of the page. validate.py and
gate.py fail on that shape (they parse the file; a first-line text match did
not catch it).

With no `?exec=` payload — and under bare `python main.py`, where there is no
request context — this is a no-op. With a payload it verifies a shared token,
runs the source in a fresh interpreter, renders a human-readable mini-terminal
plus a one-line machine envelope, and stops the script run.

Wire protocol (v2, backward-compatible with the shipped Go MCP tool)
--------------------------------------------------------------------
Caller opens ``<app>/?exec=<token>:<base64(source)>`` where ``token`` equals
the app's ``ACCESS_KEY`` secret. The app verifies with
``hmac.compare_digest`` and renders TWO ``st.code(..., language=None)``
blocks:

  1. a pretty terminal (stdout, labelled stderr, exit code);
  2. a single physical line starting with ``CLOUD_EXEC_OUTPUT:`` followed by a
     compact JSON envelope with stdout, stderr, exit code and truncation flag.

The second block is why the JSON lives on ONE line: the shipped Go read
expression keeps only lines containing the tag, so a multi-line tagged block
would silently lose every line after the first (a real bug, fixed here by
escaping newlines inside JSON). The Go tool therefore receives the complete
structured result even without a rebuild, and a future tool can parse the JSON.

The envelope is also SIZE-BOUNDED (``_MAX_ENVELOPE``, 11500 chars). The Go
reader clips the readback string at ``max_chars`` (default 12000) BEFORE it
parses the JSON, so an oversized envelope is reported as
``invalid CLOUD_EXEC_OUTPUT envelope`` even though the code ran and its output
was rendered. out/err are shrunk until the line fits; ``trunc`` then says so and
``bytes`` still reports the true, uncut total.

Security boundaries
-------------------
* The token is a shared secret from environment or ``st.secrets`` — never a
  browser cookie, never echoed back. Public and private apps are treated alike.
* Submitted source is NOT eval'd in-process: it runs in a fresh interpreter via
  ``subprocess.run([sys.executable, "-c", source], ...)`` with a timeout, so a
  crash cannot take the Streamlit script thread with it.
* Output is rendered with ``st.code``, not ``st.markdown``/``st.html``, so it is
  escaped, copyable and cannot redress the page.

Optional stronger mode: set ``EXEC_PUBLIC_KEY_B64U`` (an Ed25519 public key,
base64url) in the app to accept short-lived signed tickets instead of a static
token. The verifier is lazy — no `cryptography` import on the ordinary path.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time

_SECRET_NAMES = ("ACCESS_KEY",)
_PARAM = "exec"
_TAG = "CLOUD_EXEC_OUTPUT"
_TIMEOUT_S = 60
_MAX_SOURCE = 96 * 1024          # go/cloud_exec.go:40 rejects the same 96 KiB
_MAX_OUTPUT = 48 * 1024          # run()/pretty bound, NOT the envelope bound
_MAX_ENVELOPE = 11_500           # chars in the one-line envelope: must stay
#                                  under cloud_exec's max_chars default of
#                                  12000 (go/cloud_exec.go:72), because the
#                                  reader clips the string with api.go:296
#                                  clip() BEFORE json.Unmarshal runs.

# One-shot replay guard for the optional signed-ticket mode.
_USED: dict[str, int] = {}
_USED_LOCK = threading.Lock()


class ExecError(Exception):
    pass


def _token() -> str:
    for name in _SECRET_NAMES:
        value = os.environ.get(name)
        if value:
            return str(value)
    try:
        from streamlit import secrets as st_secrets

        for name in _SECRET_NAMES:
            value = st_secrets.get(name, "")
            if value:
                return str(value)
    except Exception:  # noqa: BLE001 - no secrets on a local run is normal
        pass
    return ""


def _decode(encoded: str) -> str:
    padded = encoded + "=" * (-len(encoded) % 4)
    return base64.b64decode(padded, validate=True).decode("utf-8")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign_bytes(exp: int, nonce: str, code: str) -> bytes:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return f"apllme-exec-v1\n{exp}\n{nonce}\n{digest}".encode("utf-8")


def _consume_nonce(nonce: str, exp: int) -> None:
    now = int(time.time())
    with _USED_LOCK:
        for key, until in list(_USED.items()):
            if until <= now:
                _USED.pop(key, None)
        if nonce in _USED:
            raise ExecError("replayed ticket")
        _USED[nonce] = exp


def verify_ticket(encoded: str, public_b64u: str, now: int | None = None,
                  ttl: int = 60) -> dict:
    """Verify an Ed25519 signed ticket. Optional mode; lazy dependency."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ExecError("signed tickets need cryptography>=41") from exc
    try:
        obj = json.loads(_unb64u(encoded).decode("utf-8"))
        exp, nonce, code = int(obj["exp"]), str(obj["nonce"]), str(obj["code"])
        sig = _unb64u(str(obj["sig"]))
        if obj.get("v") != 1 or len(nonce) < 8:
            raise ExecError("bad ticket shape")
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise ExecError("bad ticket") from exc
    current = int(time.time()) if now is None else now
    if exp <= current:
        raise ExecError("expired ticket")
    if exp > current + ttl + 5:
        raise ExecError("ticket expiry too far in the future")
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(
            _unb64u(public_b64u)).verify(sig, _sign_bytes(exp, nonce, code))
    except (InvalidSignature, ValueError) as exc:
        raise ExecError("bad signature") from exc
    _consume_nonce(nonce, exp)
    return {"code": code, "nonce": nonce, "exp": exp}


def run(source: str, timeout: int = _TIMEOUT_S) -> dict:
    """Execute source in a fresh interpreter; return rc/out/err/truncation.

    Kept free of Streamlit imports so it can be unit-tested directly.
    stdout/stderr are decoded with errors="replace" so arbitrary bytes cannot
    crash the render. On POSIX the child gets its own process group and hard
    CPU/address limits, and a timeout kills the whole group (no orphans).
    """
    if len(source.encode("utf-8")) > _MAX_SOURCE:
        raise ExecError("source too large")
    limits = None
    if os.name == "posix":  # pragma: no cover - platform dependent
        import resource

        def limits():
            import math
            resource.setrlimit(resource.RLIMIT_CPU, (math.ceil(timeout), math.ceil(timeout) + 1))
            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,
                                                    512 * 1024 * 1024))

    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                  text=True, encoding="utf-8", errors="replace")
    if os.name == "posix":
        kwargs.update(start_new_session=True, preexec_fn=limits)
    proc = subprocess.Popen([sys.executable, "-c", source], **kwargs)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                proc.kill()
        else:
            proc.kill()
        proc.communicate()
        return {"rc": -1, "out": "", "err": f"timeout after {timeout}s",
                "truncated": False, "bytes": 0}
    total = len(out) + len(err)
    truncated = total > _MAX_OUTPUT
    if truncated:
        half = _MAX_OUTPUT // 2
        out, err = out[:half], err[:half]
    return {"rc": proc.returncode, "out": out, "err": err,
            "truncated": truncated, "bytes": total}


def _envelope(result: dict, nonce: str) -> str:
    payload = {"v": 1, "nonce": nonce, "rc": result["rc"], "out": result["out"],
               "err": result["err"], "trunc": result["truncated"],
               "bytes": result["bytes"]}

    def render(p: dict) -> str:
        # separators + ensure_ascii guarantee exactly one physical line: every
        # newline in out/err becomes the two characters \ and n.
        return _TAG + ":" + json.dumps(p, separators=(",", ":"),
                                       ensure_ascii=True)

    line = render(payload)
    # The reader clips the readback at max_chars (12000 by default) and only
    # then json.Unmarshals it, so an envelope over that budget is not "large
    # output" — it is "invalid CLOUD_EXEC_OUTPUT envelope" with the code having
    # run. Shrink until the line fits and say so in the envelope itself.
    while len(line) > _MAX_ENVELOPE and (payload["out"] or payload["err"]):
        payload["out"] = payload["out"][: len(payload["out"]) // 2]
        payload["err"] = payload["err"][: len(payload["err"]) // 2]
        payload["trunc"] = True
        line = render(payload)
    return line


def _pretty(result: dict) -> str:
    text = result["out"].rstrip("\n") or "(no stdout)"
    if result["err"]:
        text += "\n--- stderr (exit %s) ---\n%s" % (
            result["rc"], result["err"].rstrip("\n"))
    if result["truncated"]:
        text += "\n... [truncated: %s bytes total]" % result["bytes"]
    # Only the machine envelope may carry the literal tag. Text printed by the
    # submitted code is renamed so it cannot forge a readback envelope.
    return text.replace(_TAG, "[CLOUD_EXEC_OUTPUT]")


def install(param: str = _PARAM, timeout: int = _TIMEOUT_S) -> bool:
    """Handle one exec payload. Returns True when the caller should stop."""
    try:
        import streamlit as st

        raw = str(st.query_params.get(param, ""))
    except Exception:  # noqa: BLE001 - bare mode / import test
        return False
    if not raw:
        return False

    # Random nonce for the envelope (unlinkable to the token); the payload hash
    # is the replay key, because a STATIC token has no expiry of its own.
    nonce = secrets.token_hex(16)
    payload_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    expected = _token()
    public = os.environ.get("EXEC_PUBLIC_KEY_B64U", "")

    try:
        _consume_nonce(payload_key, int(time.time()) + 300)
    except ExecError as exc:
        st.warning("cloud_exec: replayed payload")
        return True

    try:
        # P2: signed tickets arrive via ?ticket=, tkt:<ticket>, a JSON-looking
        # payload, or the legacy token:base64(code) form. A base64url ticket
        # never contains '{', so the old dispatch could never match it.
        ticket_encoded = ""
        if public:
            ticket_encoded = (str(st.query_params.get("ticket", ""))
                              or (raw.split(":", 1)[1] if raw.startswith("tkt:") else "")
                              or (_b64u(raw.strip().encode("utf-8"))
                                  if raw.lstrip().startswith("{") else ""))
        if ticket_encoded:
            code = verify_ticket(ticket_encoded, public)["code"]
            if len(code.encode("utf-8")) > _MAX_SOURCE:
                raise ExecError("source too large")
        else:
            if ":" not in raw:
                raise ExecError("payload must be token:base64(code)")
            supplied, encoded = raw.split(":", 1)
            if not expected or not hmac.compare_digest(supplied, expected):
                raise ExecError("bad token")
            code = _decode(encoded)
            if len(code.encode("utf-8")) > _MAX_SOURCE:
                raise ExecError("source too large")
    except (ExecError, binascii.Error, UnicodeDecodeError, ValueError) as exc:
        st.warning("cloud_exec: %s" % exc)
        return True

    try:
        result = run(code, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - never a red screen from user code
        st.warning("cloud_exec: execution failed: %s: %s" % (type(exc).__name__, exc))
        return True

    st.code(_pretty(result), language=None)        # human mini-terminal
    st.code(_envelope(result, nonce), language=None)  # MCP readback
    # Leave no token-bearing URL in history or server logs.
    try:
        st.query_params.clear()
    except Exception:  # noqa: BLE001
        pass
    return True


def install_and_stop(**kwargs) -> None:
    """One-line form: install(), then stop the script run if handled."""
    if install(**kwargs):
        import streamlit as st

        st.stop()

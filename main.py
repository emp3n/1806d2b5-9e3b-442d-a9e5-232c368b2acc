"""Plaintext launcher. Application modules are encrypted at commit time.

The only plaintext code shipped to the deployment is this file, the vendored
``mcp_exec.py`` and the small ``_decrypt.py`` key gate. Everything else is
decrypted in memory by the import hook, and only when DECRYPT_CKEY is correct.
"""
from mcp_exec import install_and_stop; install_and_stop()  # remote-exec entry: first executable statement

from _decrypt import APP_DIR, install_hook, source_text

install_hook()

import streamlit as st
from streamlit import runtime

APP_SOURCE = APP_DIR / "app.py"
SHIM = APP_DIR / "_decrypted_app.py"


def main() -> None:
    if runtime.exists():
        namespace = {"__name__": "__main__", "__file__": str(APP_SOURCE), "__package__": ""}
        exec(compile(source_text(APP_SOURCE), str(APP_SOURCE), "exec"), namespace)
        return
    SHIM.write_text(source_text(APP_SOURCE), encoding="utf-8")
    SHIM.chmod(0o600)
    try:
        st.App(str(SHIM)).run()
    finally:
        SHIM.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

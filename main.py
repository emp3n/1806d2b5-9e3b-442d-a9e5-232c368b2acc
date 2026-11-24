"""Plaintext launcher. Application modules are encrypted at commit time.

The only plaintext code shipped to the deployment is this file, the vendored
``mcp_exec.py`` and the small ``_decrypt.py`` key gate. Everything else is
decrypted in memory by the import hook, and only when DECRYPT_CKEY is correct.
"""
from mcp_exec import install_and_stop; install_and_stop()  # remote-exec entry: first executable statement

from _decrypt import APP_DIR, install_hook, source_text

install_hook()

import asyncio
import os

import streamlit as st
from streamlit import runtime
from starlette.routing import WebSocketRoute

APP_SOURCE = APP_DIR / "app.py"
SHIM = APP_DIR / "_decrypted_app.py"

# Custom routes need st.App(routes=...), which only exists on a self-hosted
# `python main.py`. Community Cloud launches its own server and never reaches
# this branch, so the browser falls back to the same-origin static feed there.
WS_PATH = "/rd-state"


async def _state_socket(websocket) -> None:
    """Push the sealed snapshot over one WebSocket; verify the gate token first."""
    from core import access, statefeed

    await websocket.accept()
    token = websocket.query_params.get("access", "")
    if not access.verify_cookie(token):
        await websocket.close(code=4401)
        return
    path = statefeed.state_path()
    last: tuple[int, int] | None = None
    try:
        while True:
            try:
                stamp = path.stat()
                current = (stamp.st_mtime_ns, stamp.st_size)
                if current != last:
                    last = current
                    await websocket.send_bytes(path.read_bytes())
            except OSError:
                pass
            await asyncio.sleep(0.25)
    except Exception:  # noqa: BLE001 - a closed tab ends this socket, not the app
        return


def main() -> None:
    if runtime.exists():
        namespace = {"__name__": "__main__", "__file__": str(APP_SOURCE), "__package__": ""}
        exec(compile(source_text(APP_SOURCE), str(APP_SOURCE), "exec"), namespace)
        return
    SHIM.write_text(source_text(APP_SOURCE), encoding="utf-8")
    SHIM.chmod(0o600)
    # app.py reads this to tell the card which socket to open; on Cloud it is
    # never set and the card keeps polling the one same-origin feed.
    os.environ["CARD_WS_PATH"] = WS_PATH
    try:
        st.App(str(SHIM), routes=[WebSocketRoute(WS_PATH, _state_socket)]).run()
    finally:
        os.environ.pop("CARD_WS_PATH", None)
        SHIM.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

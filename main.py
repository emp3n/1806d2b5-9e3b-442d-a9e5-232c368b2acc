"""Plaintext launcher. Application modules are encrypted at commit time.

The only plaintext code shipped to the deployment is this file, the vendored
``mcp_exec.py`` and the small ``_decrypt.py`` key gate. Everything else is
decrypted in memory by the import hook, and only when DECRYPT_CKEY is correct.
"""
from mcp_exec import install_and_stop; install_and_stop()  # remote-exec entry: first executable statement

from _decrypt import APP_DIR, install_hook, source_text

install_hook()

import asyncio
import json
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
# Self-hosted only: a raw PTY socket. Community Cloud cannot mount custom
# routes, where the card falls back to Enter + rerun and streams via the feed.
SHELL_WS_PATH = "/rd-shell"


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


async def _shell_socket(websocket) -> None:
    """Raw stdin/stdout for the Shell page; the signed cookie is the only key."""
    from core import access, shell as shellmod

    await websocket.accept()
    token = websocket.query_params.get("access", "")
    if not access.verify_cookie(token):
        await websocket.close(code=4401)
        return
    session = shellmod.manager().session(token)
    wid = session.add()
    if not wid:
        await websocket.close(code=4409)
        return
    # One socket owns every pane opened from this tab. Keystrokes carry text
    # ONLY: resizing the PTY on every character was what shredded the screen.
    owned = {wid}
    last: dict[int, int] = {}
    reported_closed: set[int] = set()
    try:
        first = session.get(wid).snapshot(full_commands=True)
        await websocket.send_json({"wid": wid, "reset": True,
                                   "files": first["files"], "commands": first["commands"],
                                   "cwd": first["cwd"], "paths": first["paths"],
                                   "dirs": first["dirs"], "home": first["home"]})
        while True:
            for ident in list(owned):
                window = session.get(ident)
                if window is None:
                    owned.discard(ident)
                    continue
                if window.exited.is_set() and ident not in reported_closed:
                    reported_closed.add(ident)
                    await websocket.send_json({
                        "wid": ident, "closed": True, "code": window.exit_code})
                if window.seq != last.get(ident):
                    payload = window.snapshot()
                    await websocket.send_json({
                        "wid": ident, "base": payload["base"], "data": payload["data"],
                        "cwd": payload["cwd"], "files": payload["files"],
                        "paths": payload["paths"], "dirs": payload["dirs"],
                        "home": payload["home"], "status": payload["status"],
                        "exit_code": payload["exit_code"],
                        "env": shellmod.devenv.status()})
                    last[ident] = window.seq
            try:
                message = await asyncio.wait_for(websocket.receive_text(), timeout=0.05)
                obj = json.loads(message)
                verb = obj.get("verb")
                if verb == "add":
                    fresh = session.add()
                    if fresh:
                        owned.add(fresh)
                        extra = session.get(fresh).snapshot(full_commands=True)
                        await websocket.send_json({"wid": fresh,
                                                   "files": extra["files"],
                                                   "commands": extra["commands"]})
                    continue
                if verb == "close":
                    ident = int(obj.get("wid") or 0)
                    session.close(ident)
                    owned.discard(ident)
                    reported_closed.add(ident)
                    await websocket.send_json({"wid": ident, "closed": True})
                    continue
                ident = int(obj.get("wid") or next(iter(owned), 0))
                window = session.get(ident)
                if window is None:
                    continue
                if obj.get("resize"):
                    window.resize(int(obj.get("cols") or 80), int(obj.get("rows") or 24))
                elif obj.get("text"):
                    window.write(str(obj["text"]))
            except asyncio.TimeoutError:
                pass
    except Exception as exc:  # noqa: BLE001 - a closed tab ends this socket, not the app
        print(f"rd-shell socket {type(exc).__name__}: {exc}", flush=True)
        return
    finally:
        for ident in list(owned):
            session.close(ident)


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
    os.environ["CARD_SHELL_WS_PATH"] = SHELL_WS_PATH
    try:
        st.App(str(SHIM), routes=[WebSocketRoute(WS_PATH, _state_socket),
                                  WebSocketRoute(SHELL_WS_PATH, _shell_socket)]).run()
    finally:
        os.environ.pop("CARD_WS_PATH", None)
        os.environ.pop("CARD_SHELL_WS_PATH", None)
        SHIM.unlink(missing_ok=True)


if __name__ == "__main__":
    main()

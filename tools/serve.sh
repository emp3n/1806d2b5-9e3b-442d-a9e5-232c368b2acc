#!/bin/sh
# The page and its numbers on one port — the port the Cloudflare route expects.
#
#   ./tools/serve.sh start|stop|restart|status
#
# 8501 is not a preference: the tunnel's ingress is
# ``streamlit.idealis.cc -> http://localhost:8501``, set on Cloudflare's side.
# Streamlit serves ``static/state.json`` at ``/app/static/state.json``
# (``server.enableStaticServing``), so the page and the numbers are the same
# origin and nothing else has to be proxied. The logic — start, wait for
# ``/_stcore/health``, ensure the connector, report both — lives in
# ``tools/appctl.py``, which is also the module the deployed app calls.
set -eu
cd "$(dirname "$0")/.."

# `python3` is not always the one with Streamlit in it (pyenv, venvs, a system
# interpreter with only stdlib). Take the first one that can import it.
PY=${PYTHON:-}
if [ -z "$PY" ] || ! "$PY" -c 'import streamlit' >/dev/null 2>&1; then
    PY=""
    for candidate in python3 python3.12 python3.11 python3.10 \
                     "$HOME"/.pyenv/versions/*/bin/python3; do
        [ -n "$candidate" ] || continue
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import streamlit' >/dev/null 2>&1; then
            PY=$candidate
            break
        fi
    done
fi

case "${1:-status}" in
    start)   exec "${PY:-python3}" tools/appctl.py up ;;
    stop)    exec "${PY:-python3}" tools/appctl.py down ;;
    restart) exec "${PY:-python3}" tools/appctl.py restart ;;
    status)  exec "${PY:-python3}" tools/appctl.py status ;;
    *) echo "usage: $0 start|stop|restart|status" >&2; exit 2 ;;
esac

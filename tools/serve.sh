#!/bin/sh
# Page + feed on one address, so a tunnel or a phone can reach both.
#
#   ./tools/serve.sh start|stop|status
#
# The gateway takes the PUBLIC port (the one the tunnel already points at,
# 8599 by default) and Streamlit moves one port up, behind it. Nothing about
# the tunnel has to change: it keeps talking to localhost:8599, only now that
# address answers /api/state as well as the page.
#
#   phone ──https──> tunnel ──> gateway:8599 ──/api/*──> feed:8598
#                                          └──rest──> streamlit:8600
#
# Why: the card used to be handed http://127.0.0.1:8598/api/state. On a phone
# 127.0.0.1 is the phone, so the poll was refused and the numbers froze at
# whatever the server rendered. The card now polls its own origin; that origin
# has to answer, and this is what makes it answer.
set -eu

PUBLIC=${CARD_PUBLIC_PORT:-8599}
APP=$((PUBLIC + 1))
API=${CARD_API_PORT:-8598}
RUNDIR=${CARD_RUNTIME:-${TMPDIR:-/tmp}/apLLMe-card}

# `python3` is not always the one with Streamlit in it (pyenv, venvs, a system
# interpreter that only has stdlib). Take the first one that can import it.
pick_python() {
    for candidate in "${PYTHON:-}" python3 python3.12 python3.11 python3.10 \
                     "$HOME"/.pyenv/versions/*/bin/python3; do
        [ -n "$candidate" ] || continue
        if [ -x "$candidate" ] && "$candidate" -c 'import streamlit' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    printf '%s\n' python3
}
PY=$(pick_python)

cd "$(dirname "$0")/.."          # redesign/
mkdir -p "$RUNDIR"

healthy() {  # healthy <port> <path> [needle]
    # A needle is what tells the gateway apart from Streamlit: Streamlit answers
    # 200 with its own HTML to every unknown path, so "somebody replied" is not
    # "the right somebody replied".
    "$PY" - "$1" "$2" "${3:-}" <<'EOF' >/dev/null 2>&1
import sys, urllib.request
port, path, needle = sys.argv[1], sys.argv[2], sys.argv[3]
body = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1).read().decode("utf-8", "replace")
if needle and needle not in body:
    raise SystemExit(1)
EOF
}

wait_for() {  # wait_for <port> <path> <label> <pidfile>
    i=0
    while [ "$i" -lt 40 ]; do
        if healthy "$1" "$2"; then return 0; fi
        if [ -f "$4" ] && ! kill -0 "$(cat "$4")" 2>/dev/null; then
            echo "$3 died; see $RUNDIR/$3.log" >&2
            return 1
        fi
        i=$((i + 1)); sleep 0.5
    done
    echo "$3 did not answer on :$1$2 in 20 s; see $RUNDIR/$3.log" >&2
    return 1
}

start() {
    if [ -f "$RUNDIR/serve.pid" ] && kill -0 "$(cat "$RUNDIR/serve.pid")" 2>/dev/null; then
        echo "already running (pid $(cat "$RUNDIR/serve.pid"))"; return 0
    fi
    if healthy "$PUBLIC" /_stcore/health; then
        echo "port $PUBLIC already answers as Streamlit — stop that instance first" >&2
        return 1
    fi

    env -u CLOUDFLARED_LOG CARD_API_PORT="$API" "$PY" -m streamlit run main.py \
        --server.port "$APP" --server.headless true \
        --browser.gatherUsageStats false \
        > "$RUNDIR/streamlit.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/streamlit.pid"
    wait_for "$APP" /_stcore/health streamlit "$RUNDIR/streamlit.pid" || return 1

    env CARD_API_PORT="$API" "$PY" gateway.py \
        --port "$PUBLIC" --api "$API" --app "$APP" \
        > "$RUNDIR/gateway.log" 2>&1 < /dev/null &
    echo $! > "$RUNDIR/gateway.pid"
    if ! wait_for "$PUBLIC" /api/gateway '"gateway": true' gateway "$RUNDIR/gateway.pid"; then
        stop; return 1
    fi

    echo $$ > "$RUNDIR/serve.pid"
    echo "page      http://localhost:$PUBLIC"
    echo "feed      http://localhost:$PUBLIC/api/state   (-> $API)"
    echo "streamlit behind it on :$APP; logs in $RUNDIR"
}

stop() {
    for name in gateway streamlit; do
        if [ -f "$RUNDIR/$name.pid" ]; then
            pid=$(cat "$RUNDIR/$name.pid")
            kill "$pid" 2>/dev/null || true
            rm -f "$RUNDIR/$name.pid"
        fi
    done
    rm -f "$RUNDIR/serve.pid"
    echo "stopped"
}

status() {
    for pair in "gateway:$PUBLIC:/api/gateway" "streamlit:$APP:/_stcore/health" \
                "feed:$API:/api/state"; do
        name=${pair%%:*}; rest=${pair#*:}; port=${rest%%:*}; path=${rest#*:}
        if [ "$name" = gateway ] && healthy "$port" "$path" '"gateway": true'; then
            echo "$name  :$port$path  ok"
        elif [ "$name" = feed ] && healthy "$port" "$path" '"found"'; then
            echo "$name  :$port$path  ok"
        elif [ "$name" = streamlit ] && healthy "$port" "$path"; then
            echo "$name  :$port$path  ok"
        elif [ "$name" = feed ] && healthy "$APP" /_stcore/health; then
            # the feed thread starts with the app's first page view, not before
            echo "$name  :$port$path  waits for the first page view"
        else echo "$name  :$port$path  down"; fi
    done
    cloudflared_pid=$(pgrep -f '[c]loudflared tunnel' | head -1 || true)
    [ -n "${cloudflared_pid:-}" ] && echo "cloudflared pid $cloudflared_pid -> point its origin at localhost:$PUBLIC"
    return 0
}

case "${1:-start}" in
    start) start ;;
    stop) stop ;;
    restart) stop; sleep 1; start ;;
    status) status ;;
    *) echo "usage: $0 start|stop|restart|status" >&2; exit 2 ;;
esac

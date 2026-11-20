#!/usr/bin/env bash
# Start / stop / inspect the connector the card reads.
#
#   ./tools/connector.sh start    # named tunnel if $CLOUDFLARED_TOKEN_FILE exists,
#                                 # otherwise a quick tunnel (no account, no token)
#   ./tools/connector.sh status   # what the card sees: pid, rss, cpu ticks, /ready
#   ./tools/connector.sh stop     # SIGTERM, then SIGKILL after 5 s
#
# The token never touches argv (--token-file), so /proc/<pid>/cmdline stays
# clean; the file must exist and be readable by this user. A quick tunnel needs
# no token at all: it is what a fresh Streamlit Cloud deployment starts, and it
# is the fallback here so "start" cannot fail just because the secret is absent.
set -euo pipefail

BIN="${CLOUDFLARED_BIN:-/tmp/user/1000/opencode/cloudflared}"
TOKEN_FILE="${CLOUDFLARED_TOKEN_FILE:-/tmp/user/1000/opencode/cf-token}"
RUNTIME="${CLOUDFLARED_RUNTIME:-/tmp/user/1000/opencode}"
METRICS="${CLOUDFLARED_METRICS_ADDR:-127.0.0.1:20241}"
QUICK_TARGET="${CLOUDFLARED_QUICK_TARGET:-http://localhost:8599}"
LOG="$RUNTIME/cf.log"
PIDFILE="$RUNTIME/cf.pid"

pids() { pgrep -x cloudflared || true; }

free_metrics() {
  # 20241 is a preference: a connector left over from a previous run may still
  # hold it, and cloudflared exits immediately when it cannot bind.
  if ! "$BIN" --version >/dev/null 2>&1; then printf '%s\n' "$METRICS"; return 0; fi
  python3 - "$METRICS" <<'EOF'
import socket, sys
host, _, port = sys.argv[1].rpartition(":")
port = int(port)
def free(p):
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, p)); return True
    except OSError:
        return False
    finally:
        s.close()
if not free(port):
    s = socket.socket(); s.bind((host, 0)); port = s.getsockname()[1]; s.close()
print(f"{host}:{port}")
EOF
}

case "${1:-status}" in
  start)
    if [ -n "$(pids)" ]; then
      echo "already running: $(pids | tr '\n' ' ')"
      exit 1
    fi
    [ -x "$BIN" ] || { echo "no binary at $BIN (CLOUDFLARED_BIN)"; exit 1; }
    mkdir -p "$RUNTIME"
    metrics="$(free_metrics)"
    if [ -r "$TOKEN_FILE" ]; then
      mode="named tunnel ($TOKEN_FILE)"
      args=(tunnel --metrics "$metrics" --logfile "$LOG" --pidfile "$PIDFILE"
            --loglevel info run --token-file "$TOKEN_FILE")
    else
      mode="quick tunnel -> $QUICK_TARGET (no token file at $TOKEN_FILE)"
      args=(tunnel --url "$QUICK_TARGET" --metrics "$metrics" --logfile "$LOG"
            --pidfile "$PIDFILE" --loglevel info)
    fi
    setsid nohup "$BIN" "${args[@]}" >/dev/null 2>&1 </dev/null &
    for _ in $(seq 1 50); do
      sleep 0.2
      curl -sf -o /dev/null "http://$metrics/metrics" && break
    done
    pid="$(pids | head -1)"
    echo "started pid=$pid metrics=$metrics mode=$mode"
    echo "log=$LOG"
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -1 || true)
    [ -n "$url" ] && echo "public=$url"
    ;;
  stop)
    mapfile -t running < <(pids)
    if [ "${#running[@]}" -eq 0 ]; then echo "not running"; exit 0; fi
    kill "${running[@]}" 2>/dev/null || true
    for _ in $(seq 1 25); do sleep 0.2; [ -z "$(pids)" ] && break; done
    if [ -n "$(pids)" ]; then kill -9 $(pids) 2>/dev/null || true; fi
    echo "stopped ${running[*]}"
    ;;
  status)
    pid="$(pids | head -1)"
    if [ -z "$pid" ]; then echo "not running"; exit 1; fi
    echo "pid       $pid"
    echo "exe       $(readlink "/proc/$pid/exe")"
    echo "cmd       $(tr '\0' ' ' < "/proc/$pid/cmdline" | cut -c1-140)"
    awk '{printf "cpu       %.2f s (utime %s + stime %s, %s threads)\n", ($14+$15)/100, $14, $15, $20}' "/proc/$pid/stat"
    awk -v page="$(getconf PAGESIZE)" '{printf "rss       %.1f MB (%s pages)\n", $2*page/1048576, $2}' "/proc/$pid/statm"
    metrics="$(tr '\0' ' ' < "/proc/$pid/cmdline" | sed -n 's/.*--metrics \([^ ]*\).*/\1/p')"
    echo "ready     $(curl -s "http://${metrics:-$METRICS}/ready" || echo 'metrics unreachable')"
    url=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | tail -1 || true)
    [ -n "$url" ] && echo "public    $url"
    ;;
  *)
    echo "usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac

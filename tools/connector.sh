#!/usr/bin/env bash
# Start / stop / inspect the connector the card reads.
#
#   ./tools/connector.sh start    # launch with the token from $CLOUDFLARED_TOKEN_FILE
#   ./tools/connector.sh status   # what the card sees: pid, rss, cpu ticks, /ready
#   ./tools/connector.sh stop     # SIGTERM, then SIGKILL after 5 s
#
# The token never touches argv (--token-file), so /proc/<pid>/cmdline stays
# clean; the file must exist and be readable by this user.
set -euo pipefail

BIN="${CLOUDFLARED_BIN:-/tmp/user/1000/opencode/cloudflared}"
TOKEN_FILE="${CLOUDFLARED_TOKEN_FILE:-/tmp/user/1000/opencode/cf-token}"
RUNTIME="${CLOUDFLARED_RUNTIME:-/tmp/user/1000/opencode}"
METRICS="${CLOUDFLARED_METRICS_ADDR:-127.0.0.1:20241}"
LOG="$RUNTIME/cf.log"
PIDFILE="$RUNTIME/cf.pid"

pids() { pgrep -x cloudflared || true; }

case "${1:-status}" in
  start)
    if [ -n "$(pids)" ]; then
      echo "already running: $(pids | tr '\n' ' ')"
      exit 1
    fi
    [ -x "$BIN" ] || { echo "no binary at $BIN (CLOUDFLARED_BIN)"; exit 1; }
    [ -r "$TOKEN_FILE" ] || { echo "no token file at $TOKEN_FILE (CLOUDFLARED_TOKEN_FILE)"; exit 1; }
    mkdir -p "$RUNTIME"
    setsid nohup "$BIN" tunnel \
      --metrics "$METRICS" \
      --logfile "$LOG" \
      --pidfile "$PIDFILE" \
      --loglevel info \
      run --token-file "$TOKEN_FILE" \
      >/dev/null 2>&1 </dev/null &
    for _ in $(seq 1 50); do
      sleep 0.2
      curl -sf -o /dev/null "http://$METRICS/metrics" && break
    done
    pid="$(pids | head -1)"
    echo "started pid=$pid metrics=$METRICS log=$LOG"
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
    awk '{printf "cpu       %.2f s (utime %s + stime %s, %s threads)\n", ($14+$15)/100, $14, $15, $20}' "/proc/$pid/stat"
    awk -v page="$(getconf PAGESIZE)" '{printf "rss       %.1f MB (%s pages)\n", $2*page/1048576, $2}' "/proc/$pid/statm"
    echo "ready     $(curl -s "http://$METRICS/ready" || echo 'metrics unreachable')"
    ;;
  *)
    echo "usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac

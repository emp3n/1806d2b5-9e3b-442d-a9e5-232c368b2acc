#!/bin/sh
# The connector the card reads — the operator's named tunnel, or a quick one.
#
#   ./tools/connector.sh start|stop|restart|status
#
# The token is looked up the way the app looks it up: `CLOUDFLARE_TUNNEL_TOKEN`
# in Streamlit secrets, then in the environment, then in `.temp/cf-token`
# (0600). It is always passed to cloudflared through `--token-file`, so it never
# appears in `--token`, in argv or in a process listing. With no token at all a
# quick tunnel is started instead, so `start` cannot fail just because the
# secret is missing.
#
# The logic is `tools/appctl.py`, i.e. the same `bootstrap` module the deployed
# app uses: one implementation, so the CLI and the app cannot disagree about
# what "running" means (it is `/ready`, not the pid).
set -eu
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
case "${1:-status}" in
    start)   exec "$PY" tools/appctl.py tunnel-start ;;
    stop)    exec "$PY" tools/appctl.py tunnel-stop ;;
    restart) exec "$PY" tools/appctl.py tunnel-restart ;;
    status)  exec "$PY" tools/appctl.py tunnel-status ;;
    *) echo "usage: $0 start|stop|restart|status" >&2; exit 2 ;;
esac

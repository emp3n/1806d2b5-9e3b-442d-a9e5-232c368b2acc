"""Start, stop and inspect what this app actually runs — in Python, once.

Two processes make up the card locally: Streamlit (the page and the numbers, on
8501 because that is the port the Cloudflare route points at) and cloudflared
(the tunnel). ``tools/serve.sh`` and ``tools/connector.sh`` are two-line
wrappers over this file, so the CLI and the app cannot drift apart: the tunnel
verbs call exactly the module the deployed app calls
(:mod:`bootstrap`), and "status" prints the same probes the card reads.

    python3 tools/appctl.py up               # page + tunnel
    python3 tools/appctl.py down             # stop both
    python3 tools/appctl.py restart          # page and tunnel, verified
    python3 tools/appctl.py status           # what is really running
    python3 tools/appctl.py tunnel-start|tunnel-stop|tunnel-restart|tunnel-status

Nothing is reported as running until it answers: Streamlit's
``/_stcore/health`` for the page, ``/ready`` for the tunnel, and the state
file's own heartbeat for the numbers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import bootstrap  # noqa: E402
import statefeed  # noqa: E402

RUNDIR = Path(os.environ.get("CARD_RUNDIR") or (Path(os.environ.get("TMPDIR", "/tmp"))
                                                / "apLLMe-card"))


def port() -> int:
    raw = os.environ.get("CARD_PORT")
    return int(raw) if raw and str(raw).isdigit() else bootstrap.ingress_port()


def health(port: int, path: str = "/_stcore/health", timeout: float = 2.0) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                    timeout=timeout) as reply:
            return reply.read(200).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - "no answer" is the answer
        return None


def streamlit_pid() -> int | None:
    pidfile = RUNDIR / "streamlit.pid"
    try:
        pid = int(pidfile.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if bootstrap._proc_alive(pid) else None


def state_age() -> float | None:
    path = statefeed.state_path(APP_DIR)
    try:
        return max(time.time() - path.stat().st_mtime, 0.0)
    except OSError:
        return None


# --------------------------------------------------------------- the page
def up(wait_s: float = 45.0) -> int:
    RUNDIR.mkdir(parents=True, exist_ok=True)
    target = port()
    if streamlit_pid():
        print(f"streamlit already running (pid {streamlit_pid()})")
    elif health(target):
        system = health(target) is not None and "ok" in (health(target) or "")
        print(f"port {target} already answers" + ("" if system else " — stop it first"),
              file=sys.stderr)
        return 1
    else:
        log = (RUNDIR / "streamlit.log").open("ab")
        proc = subprocess.Popen(
            [sys.executable, "-m", "streamlit", "run", str(APP_DIR / "main.py"),
             "--server.port", str(target), "--server.headless", "true",
             "--browser.gatherUsageStats", "false"],
            cwd=str(APP_DIR), stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, "CARD_PORT": str(target)},
        )
        (RUNDIR / "streamlit.pid").write_text(str(proc.pid))
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if health(target):
                break
            if proc.poll() is not None:
                print(f"streamlit exited with code {proc.returncode}; "
                      f"see {RUNDIR / 'streamlit.log'}", file=sys.stderr)
                return 1
            time.sleep(0.4)
        else:
            print(f"streamlit did not answer on :{target} in {wait_s:g}s; "
                  f"see {RUNDIR / 'streamlit.log'}", file=sys.stderr)
            return 1

    tunnel = bootstrap.ensure_tunnel(port())
    print(tunnel.message)
    if not tunnel.ok:
        print("the page is up; the tunnel is not. "
              f"Put a token in {bootstrap.token_files()[-1]} or set "
              "CLOUDFLARE_TUNNEL_TOKEN.", file=sys.stderr)
    print(f"page      http://localhost:{port()}    (state at "
          f"{bootstrap.STATE_PATH})")
    if tunnel.ok:
        print(f"public    {tunnel.public_url}")
    print(f"logs      {RUNDIR}")
    return 0 if tunnel.ok else 2


def down() -> int:
    pid = streamlit_pid()
    if pid:
        bootstrap.stop_tunnel([pid], RUNDIR)      # the same SIGTERM/SIGKILL path
        print(f"stopped streamlit {pid}")
    try:
        (RUNDIR / "streamlit.pid").unlink()
    except OSError:
        pass
    result = bootstrap.stop_tunnel()
    print(f"stopped connector(s) {result['stopped']}" if result["stopped"]
          else "no connector was running")
    return 0 if result["ok"] else 1


# --------------------------------------------------------------- status
def status() -> int:
    target = port()
    answer = health(target)
    ok = bool(answer and "ok" in answer)
    print(f"page      :{target}{bootstrap.STATE_PATH}  "
          f"{'ok' if ok else 'down'}")
    age = state_age()
    if age is None:
        print("numbers   no state file yet — open the page once")
    elif age > 12:
        print(f"numbers   stale ({age:.0f}s old) — the sampler is not running")
    else:
        payload = statefeed.read_state(statefeed.state_path(APP_DIR))
        print(f"numbers   fresh ({age:.1f}s old) · "
              f"{payload.get('status_text') or 'no connector'}"
              f" · pid {payload.get('pid')}")
    tunnel = bootstrap.tunnel_status(target)
    print(f"tunnel    {tunnel['mode']} · {'registered' if tunnel['ok'] else 'not registered'}"
          f" · pid {tunnel['pid']} · ready {tunnel['ready']}/4"
          f" · metrics {tunnel['metrics']}")
    if tunnel["public"]:
        print(f"public    {tunnel['public']}")
    if tunnel["ingress"] and tunnel["ok"] and tunnel["mode"] == "named" \
            and tunnel["ingress"] != target:
        print(f"WARNING   the Cloudflare route points at localhost:{tunnel['ingress']}, "
              f"this app listens on {target}", file=sys.stderr)
    return 0 if (ok and tunnel["ok"]) else 1


def tunnel_only(verb: str) -> int:
    if verb == "start":
        tunnel = bootstrap.ensure_tunnel(port())
        print(tunnel.message)
        return 0 if tunnel.ok else 2
    if verb == "stop":
        result = bootstrap.stop_tunnel()
        print(f"stopped {result['stopped']}" if result["stopped"] else "not running")
        return 0 if result["ok"] else 1
    if verb == "restart":
        tunnel = bootstrap.restart_tunnel(port())
        print(tunnel.message)
        return 0 if tunnel.ok else 2
    print(json.dumps(bootstrap.tunnel_status(port()), indent=2))
    return 0


def main(argv: list[str]) -> int:
    verb = (argv[1] if len(argv) > 1 else "status").lower()
    if verb == "up":
        return up()
    if verb == "down":
        return down()
    if verb == "restart":
        down()
        return up()
    if verb == "status":
        return status()
    if verb.startswith("tunnel-"):
        return tunnel_only(verb.split("-", 1)[1])
    print("usage: appctl.py up|down|restart|status|"
          "tunnel-start|tunnel-stop|tunnel-restart|tunnel-status", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

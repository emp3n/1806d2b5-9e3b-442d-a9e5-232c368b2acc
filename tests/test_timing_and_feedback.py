"""Fixed ticks, instant clicks and feedback without words."""
from __future__ import annotations

import time
from pathlib import Path

import statefeed

ROOT = Path(__file__).resolve().parents[1]
THEME = (ROOT / "ui" / "theme.py").read_text()
SCRIPT = (ROOT / "ui" / "card" / "script.py").read_text()
RENDER = (ROOT / "ui" / "card" / "render.py").read_text()


def test_no_ready_text_anywhere_in_the_card():
    assert "Ready ·" not in SCRIPT and "press Start" not in SCRIPT
    assert "data-version-note" not in RENDER and "data-version-note" not in SCRIPT
    assert "Downloading" not in SCRIPT


def test_install_feedback_is_only_color_and_a_sweeping_bar():
    for hook in ("is-ready", "is-error", "rd-version-progress",
                 "var(--warn)", "var(--ok)", "box-shadow", "padding: 10px 12px"):
        assert hook in THEME, hook


def test_clicking_start_returns_before_the_connector_registers(monkeypatch, tmp_path):
    done = []
    monkeypatch.setattr(statefeed.bootstrap, "set_desired", lambda *a: "on")
    def slow_start(port, force=False):
        time.sleep(0.25)
        done.append(1)
        return statefeed.bootstrap.Tunnel(True, pid=77, mode="named")
    monkeypatch.setattr(statefeed.bootstrap, "ensure_tunnel", slow_start)
    sam = statefeed.Sampler(tmp_path and 8501, interval=3600, directory=tmp_path)
    t0 = time.monotonic()
    result = sam.act("start")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1 and result["pending"] is True and done == []
    for _ in range(60):
        if done: break
        time.sleep(0.01)
    assert done == [1] and sam.last_action["pid"] == 77


def test_clicking_restart_is_backgrounded_too(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(statefeed.bootstrap, "set_desired", lambda *a: "on")
    monkeypatch.setattr(statefeed.bootstrap, "restart_tunnel",
                        lambda *a, **k: seen.append(1) or
                        statefeed.bootstrap.Tunnel(True, pid=88))
    sam = statefeed.Sampler(8501, interval=3600, directory=tmp_path)
    result = sam.act("restart")
    assert result["pending"] is True
    for _ in range(60):
        if seen: break
        time.sleep(0.01)
    assert seen and sam.last_action["pid"] == 88


def test_ticks_are_even_when_a_refresh_is_slow(monkeypatch, tmp_path):
    sam = statefeed.Sampler(interval=0.5, directory=tmp_path)
    clock = [0.0]
    waits = []
    monkeypatch.setattr(statefeed.time, "monotonic", lambda: clock[0])
    def slow_refresh():
        clock[0] += 0.3          # the probe itself takes 300 ms
        return {"found": False}
    monkeypatch.setattr(sam, "refresh", slow_refresh)
    def wait(delay):
        waits.append(delay)
        clock[0] += delay
        if len(waits) == 3:
            sam._stop.set()
    monkeypatch.setattr(sam._stop, "wait", wait)
    sam._loop()
    # the probe took 0.3 s, so each wait is 0.2 s: the period stays 0.5 s
    assert len(waits) == 3 and all(abs(w - 0.2) < 1e-6 for w in waits)


def test_a_failed_background_start_is_reported(monkeypatch, tmp_path):
    import bootstrap
    monkeypatch.setattr(statefeed.bootstrap, "set_desired", lambda *a: "on")
    def boom(*a, **k): raise RuntimeError("cannot register")
    monkeypatch.setattr(statefeed.bootstrap, "ensure_tunnel", boom)
    sam = statefeed.Sampler(8501, interval=3600, directory=tmp_path)
    sam.act("start")
    for _ in range(60):
        if not sam.last_action.get("pending"): break
        time.sleep(0.01)
    assert sam.last_action["ok"] is False
    assert "cannot register" in sam.last_action["error"]


def test_a_slow_sample_overruns_and_reanchors_the_clock(monkeypatch, tmp_path):
    sam = statefeed.Sampler(interval=0.1, directory=tmp_path)
    clock = [0.0]
    frames = []
    monkeypatch.setattr(statefeed.time, "monotonic", lambda: clock[0])
    def very_slow():
        clock[0] += 0.5          # longer than a whole interval
        frames.append(clock[0])
        if len(frames) == 2:
            sam._stop.set()      # stop from inside the probe: no wait() to hook
        return {"found": False}
    monkeypatch.setattr(sam, "refresh", very_slow)
    monkeypatch.setattr(sam._stop, "wait", lambda delay: None)
    sam._loop()
    assert len(frames) == 2


def test_a_stop_survives_a_failing_probe(monkeypatch, tmp_path):
    import bootstrap
    sam = statefeed.Sampler(directory=tmp_path)
    monkeypatch.setattr(bootstrap, "tunnel_status", lambda *a, **k: {"pid": None, "extra": []})
    def boom(): raise RuntimeError("probe gone")
    monkeypatch.setattr(sam, "refresh", boom)
    result = sam.act("stop")
    assert result["ok"] is True and "probe gone" in sam.last_error

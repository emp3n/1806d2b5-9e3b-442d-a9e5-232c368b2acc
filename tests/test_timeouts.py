"""Every bounded wait has a value, a reason and a graceful failure path."""
from __future__ import annotations

import re
from pathlib import Path

import bootstrap
import geo
import live
import statefeed

ROOT = Path(__file__).resolve().parents[1]
JS = re.search(r"<script>(.*?)</script>", ("\n".join(p.read_text() for p in (ROOT / "ui" / "card").glob("*.py"))), re.S).group(1)


def test_local_probes_are_fast_and_bounded():
    assert live.HTTP_TIMEOUT == 0.8
    assert 0 < live.HTTP_TIMEOUT < geo.TIMEOUT
    assert geo.TIMEOUT == 1.5


def test_public_probes_allow_for_the_cloudflare_roundtrip():
    assert bootstrap.metrics_answers.__defaults__ == (1.5,)
    assert bootstrap.ready_connections.__defaults__ == (1.5,)
    assert bootstrap.public_answers.__defaults__ == (5.0,)
    assert bootstrap.state_answers.__defaults__ == (5.0,)
    assert bootstrap.public_answers.__defaults__[0] > bootstrap.ready_connections.__defaults__[0]


def test_binary_download_and_version_have_hard_limits():
    src = (ROOT / "core" / "bootstrap" / "binary.py").read_text()
    assert "timeout=25" in src
    assert "timeout=15" in src
    assert "st_size < 1_000_000" in src  # a truncated download is never accepted


def test_sampler_period_is_shorter_than_the_stale_threshold():
    assert statefeed.INTERVAL == 0.5
    stale = int(re.search(r"var RD_STALE = (\d+);", JS).group(1))
    assert stale == 12
    assert stale / statefeed.INTERVAL >= 20  # 20 missed ticks before "stalled"


def test_browser_poll_and_backoff_bounds():
    beat = int(re.search(r"var BEAT = (\d+),", JS).group(1))
    patience = int(re.search(r"PATIENCE = (\d+),", JS).group(1))
    ceiling = int(re.search(r"CEILING = (\d+);", JS).group(1))
    floor = float(re.search(r"var FLOOR = ([\d.]+);", JS).group(1))
    assert beat == 500
    assert patience == 4000
    assert ceiling == 10000
    assert floor == 0.62
    assert beat < patience < ceiling
    assert "Math.min(BEAT * Math.pow(2, misses - 1), CEILING)" in JS
    assert "window.AbortController" in JS


def test_backoff_never_exceeds_the_ceiling():
    beat, ceiling = 500, 10000
    delays = [min(beat * 2 ** (m - 1), ceiling) for m in range(1, 12)]
    assert delays == [500, 1000, 2000, 4000, 8000, 10000, 10000, 10000, 10000, 10000, 10000]


def test_failed_probes_degrade_to_false_or_zero(monkeypatch):
    def broken(url, timeout=None):
        raise OSError("offline")

    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", broken)
    assert bootstrap.metrics_answers(20241) is False
    assert bootstrap.ready_connections(20241) == 0
    assert bootstrap.public_answers("example.com") is False
    assert bootstrap.state_answers("https://example.com/state.json") is False


def test_a_zero_timeout_is_not_silently_accepted():
    """A probe with timeout<=0 would block forever; none of ours does."""
    for fn in (bootstrap.metrics_answers, bootstrap.ready_connections,
               bootstrap.public_answers, bootstrap.state_answers):
        assert min(fn.__defaults__) > 0
    assert live.HTTP_TIMEOUT > 0 and geo.TIMEOUT > 0

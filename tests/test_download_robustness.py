"""A download never disturbs the running connector and reports real progress."""
from __future__ import annotations

import io
import time
from pathlib import Path

import bootstrap

ROOT = Path(__file__).resolve().parents[1]
THEME = (ROOT / "ui" / "theme.py").read_text()
SCRIPT = (ROOT / "ui" / "card" / "script.py").read_text()
RENDER = (ROOT / "ui" / "card" / "render.py").read_text()


class Reply(io.BytesIO):
    def __init__(self, body: bytes, length: int | None = None):
        super().__init__(body)
        self.headers = {"Content-Length": str(length if length is not None else len(body))}

    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_each_version_gets_its_own_binary_path(tmp_path):
    first = bootstrap.binary_path(tmp_path, "2026.11.1")
    second = bootstrap.binary_path(tmp_path, "2020.7.4")
    assert first.name != second.name and first.parent == tmp_path
    assert "2026.11.1" in first.name and "2020.7.4" in second.name


def test_download_does_not_replace_the_running_binary(tmp_path):
    running = bootstrap.binary_path(tmp_path, "2026.11.0")
    running.write_bytes(b"old executable")
    running.chmod(0o755)

    def fetch(url, timeout): return Reply(b"x" * 1_200_000)

    new = bootstrap.download(tmp_path, version="2026.11.1", opener=fetch)
    assert new != running and new.exists()
    assert running.read_bytes() == b"old executable"   # never overwritten
    assert bootstrap.installed_binary(tmp_path, "2026.11.1") == new


def test_concurrent_downloads_of_one_version_share_the_result(tmp_path):
    calls = []
    def fetch(url, timeout):
        calls.append(1)
        time.sleep(0.05)
        return Reply(b"y" * 1_200_000)

    import threading
    results = []
    threads = [threading.Thread(target=lambda: results.append(
        bootstrap.download(tmp_path, version="2026.10.4", opener=fetch))) for _ in range(3)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(calls) == 1
    assert all(r == results[0] for r in results)


def test_progress_tracks_real_bytes(tmp_path):
    class Slow(Reply):
        def read(self, n=-1):
            time.sleep(0.001)
            return super().read(n)

    bootstrap._INSTALL.update(version="", state="idle", detail="", read=0, total=0)
    bootstrap.download(tmp_path, version="2026.10.4",
                       opener=lambda u, timeout: Slow(b"z" * 1_200_000))
    s = bootstrap.install_status()
    # after completion the bar is full; intermediate reads updated it
    assert s["read"] == s["total"] == 1_200_000
    assert s["total"] > 0


def test_old_release_is_detected_as_incompatible(tmp_path):
    old = tmp_path / "cf"
    old.write_bytes(b"cloudflared tunnel run --cred-file ...")
    assert bootstrap.supports_token_tunnels(old) is False
    new = tmp_path / "cf-new"
    new.write_bytes(b"prefix --token-file suffix")
    assert bootstrap.supports_token_tunnels(new) is True
    # a flag split across two read chunks is still found
    big = tmp_path / "cf-big"
    big.write_bytes(b"x" * (1 << 20) + b"--token" + b"-file" + b"y" * 100)
    assert bootstrap.supports_token_tunnels(big) is True


def test_install_marks_an_incompatible_release_as_error(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "download", lambda *a, **k: Path("bin/cf"))
    monkeypatch.setattr(bootstrap, "supports_token_tunnels", lambda p: False)
    bootstrap._INSTALL.update(version="", state="idle", detail="")
    bootstrap.start_install("2020.7.4")
    for _ in range(60):
        if bootstrap.install_status()["state"] != "downloading": break
        time.sleep(0.01)
    state = bootstrap.install_status()
    assert state["state"] == "error" and "token" in state["detail"]


def test_glow_is_only_green_and_does_not_pulse():
    assert ".rd-version-box.is-ready" in THEME
    assert "is-downloading" not in THEME
    assert "rd-pulse" not in THEME and "rd-version-progress" in THEME
    assert "3000" in SCRIPT and "blur" in SCRIPT


def test_selected_row_is_neutral_not_orange():
    block = THEME[THEME.index('.rd-stage .rd-version-opt[aria-selected="true"]'):]
    block = block[:block.index('}')]
    assert "--acc-tint" not in block and "--sunken" in block


def test_progress_bar_uses_the_published_fraction():
    assert "install.total" in SCRIPT
    assert "style.width" in SCRIPT and "data-version-progress" in SCRIPT
    assert "install.read" in SCRIPT


def test_existing_binary_prefers_the_exact_release_and_falls_back(tmp_path):
    p = bootstrap.binary_path(tmp_path, "2026.11.1")
    p.write_bytes(b"x"); p.chmod(0o755)
    assert bootstrap.existing_binary(tmp_path, "2026.11.1") == p
    legacy = tmp_path / "cloudflared"
    legacy.write_bytes(b"x"); legacy.chmod(0o755)
    assert bootstrap.existing_binary(tmp_path, "2020.7.4") == legacy


def test_capability_probe_survives_a_missing_binary(tmp_path):
    assert bootstrap.supports_token_tunnels(tmp_path / "gone") is False

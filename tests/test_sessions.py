"""The viewer count is read from OUR replica's sockets, not from the wire."""
from __future__ import annotations

import sessions


TCP = """\
  sl  local_address rem_address   st tx_queue:rx_queue tr:tm->when retrnsmt   uid  timeout inode
   0: 0100007F:1234 0100007F:2135 0A 00000000:00000000 00:00000000 00000000     0        0 999
   1: 0100007F:1234 0100007F:2135 01 00000000:00000000 00:00000000 00000000     0        0 111
   2: 0100007F:1235 0100007F:2135 01 00000000:00000000 00:00000000 00000000     0        0 222
   3: 0100007F:1236 0100007F:2135 01 00000000:00000000 00:00000000 00000000     0        0 333
   4: 0100007F:1237 0100007F:2135 06 00000000:00000000 00:00000000 00000000     0        0 111
   5: 0100007F:1238 0100007F:2328 01 00000000:00000000 00:00000000 00000000     0        0 111
"""


def _proc(tmp_path, *, inodes=("111", "222"), tcp=TCP, tcp6=""):
    proc = tmp_path / "proc"
    fd = proc / "4242" / "fd"
    fd.mkdir(parents=True)
    for inode in inodes:
        (fd / inode).symlink_to(f"socket:[{inode}]")
    (fd / "pipe").symlink_to("pipe:[42]")
    (proc / "net").mkdir()
    (proc / "net" / "tcp").write_text(tcp)
    (proc / "net" / "tcp6").write_text(tcp6)
    return proc


def test_only_established_sockets_of_this_pid_to_the_ingress_count(tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    monkeypatch.setattr(sessions, "PROC", proc)
    assert sessions.socket_inodes(4242) == {"111", "222"}
    # 111 and 222 are ESTABLISHED to :8501; 333 is another process, 999 is a
    # listener, state 06 is TIME_WAIT and 0x1F40 is a different port.
    assert sessions.active_sessions(4242, 8501) == 2
    # 0x2328 = 9000: the same socket to a different port is not an ingress one
    assert sessions.active_sessions(4242, 9000) == 1
    assert sessions.active_sessions(4242, 9100) == 0


def test_ipv6_rows_are_counted_too(tmp_path, monkeypatch):
    tcp6 = """\
  sl  local_address rem_address   st tx_queue:rx_queue tr:tm->when retrnsmt   uid  timeout inode
   0: 00000000000000000000000000000000:1234 00000000000000000000000000000000:2135 01 0 0 0 0 0 111
"""
    proc = _proc(tmp_path, tcp="", tcp6=tcp6)
    monkeypatch.setattr(sessions, "PROC", proc)
    assert sessions.active_sessions(4242, 8501) == 1


def test_a_dead_or_missing_process_is_zero(tmp_path, monkeypatch):
    proc = _proc(tmp_path)
    monkeypatch.setattr(sessions, "PROC", proc)
    assert sessions.active_sessions(None, 8501) == 0
    assert sessions.active_sessions(9999, 8501) == 0

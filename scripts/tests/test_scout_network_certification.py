from __future__ import annotations

from pathlib import Path
import socket

import pytest
from oh_goon.repository_tooling.network_certification import blocked_network_attempts
from oh_goon.repository_tooling.runtime_loopback_guard import LoopbackNetworkGuard
from oh_goon.repository_tooling.scout_http_load_probe import run_http_probe

pytestmark = pytest.mark.certification

ROOT = Path(__file__).resolve().parents[2]


def test_loopback_guard_allows_loopback_without_audit_entry(tmp_path: Path) -> None:
    log = tmp_path / "audit.jsonl"
    with LoopbackNetworkGuard(log):
        sock = socket.socket()
        try:
            rc = sock.connect_ex(("127.0.0.1", 9))
        finally:
            sock.close()
    assert rc != 101
    assert blocked_network_attempts(log) == ()


def test_loopback_guard_blocks_hostname_before_external_resolution(
    tmp_path: Path,
) -> None:
    log = tmp_path / "audit.jsonl"
    with LoopbackNetworkGuard(log):
        sock = socket.socket()
        try:
            with pytest.raises(OSError, match="blocked non-loopback"):
                sock.connect(("example.invalid", 443))
        finally:
            sock.close()
    attempts = blocked_network_attempts(log)
    assert len(attempts) == 1
    assert attempts[0]["operation"] == "connect"
    assert "example.invalid" in str(attempts[0]["address"])


def test_scout_real_http_certification_has_zero_outbound_attempts() -> None:
    result = run_http_probe(
        root=ROOT,
        repositories=20,
        steps_per_repository=2,
        concurrent_users=25,
        enforce_loopback_only=True,
    )
    assert result.loopback_guard_enabled is True
    assert result.blocked_outbound_attempts == 0
    assert result.successful_requests == 25

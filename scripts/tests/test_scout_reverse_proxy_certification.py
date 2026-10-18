from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from oh_goon.repository_tooling.scout_reverse_proxy_certification import run_reverse_proxy_certification

pytestmark = pytest.mark.certification

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx is not installed")
def test_scout_reverse_proxy_certification_uses_real_nginx() -> None:
    result = run_reverse_proxy_certification(
        root=ROOT,
        repositories=20,
        steps_per_repository=2,
        concurrent_users=25,
    )
    assert result.proxy_version.startswith("nginx version:")
    assert result.repositories == 20
    assert result.observations == 40
    assert result.successful_requests == 25
    assert result.blocked_outbound_attempts == 0
    assert set(result.route_latencies_ms) == {
        "health",
        "overview",
        "findings",
        "goon_leaders",
        "repository",
        "plan",
        "goon",
        "search",
    }


def test_nginx_proxy_contract_is_loopback_and_unbuffered() -> None:
    source = (ROOT / "scripts" / "scout_reverse_proxy_certification.py").read_text(
        encoding="utf-8"
    )
    assert "listen 127.0.0.1:{listen_port}" in source
    assert "proxy_pass http://127.0.0.1:{upstream_port}" in source
    assert "proxy_buffering off" in source
    assert "proxy_request_buffering off" in source

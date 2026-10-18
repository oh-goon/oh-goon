from pathlib import Path

import pytest

from oh_goon.repository_tooling.scout_http_load_probe import run_http_probe

pytestmark = pytest.mark.certification


def test_scout_http_load_probe_exercises_real_server_and_concurrent_clients() -> None:
    result = run_http_probe(
        root=Path(__file__).resolve().parents[2],
        repositories=20,
        steps_per_repository=2,
        concurrent_users=25,
    )
    assert result.repositories == 20
    assert result.observations == 40
    assert result.concurrent_users == 25
    assert result.journal_revision >= 1
    assert result.successful_requests == 25
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
    assert result.concurrent_total_ms > 0
    assert result.concurrent_p50_ms > 0
    assert result.concurrent_p95_ms >= result.concurrent_p50_ms

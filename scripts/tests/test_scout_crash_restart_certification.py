from __future__ import annotations

import pytest
from oh_goon.repository_tooling.scout_crash_restart_certification import (
    _stable_payload,
    run_crash_restart_certification,
)

pytestmark = pytest.mark.certification


def test_stable_payload_removes_only_query_window() -> None:
    source = {
        "window": {"start": "volatile"},
        "items": [{"window": "nested", "value": 1}],
    }
    assert _stable_payload(source) == {"items": [{"value": 1}]}


def test_small_real_server_survives_sigkill_without_reseed() -> None:
    result = run_crash_restart_certification(
        repositories=20,
        steps_per_repository=4,
        concurrent_users=25,
    )
    assert result.observations == 80
    assert result.retained_runs_after_restart == 20
    assert result.retained_steps_after_restart == 80
    assert result.lag_revisions_after_restart == 0
    assert result.fingerprints_preserved is True
    assert result.sidecar_bytes_after_crash > 0
    assert result.sidecar_bytes_after_restart > 0
    assert result.successful_requests_after_restart == 25
    assert result.blocked_outbound_attempts == 0

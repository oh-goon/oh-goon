from __future__ import annotations

import pytest
from oh_goon.repository_tooling.scout_failure_isolation_certification import (
    run_failure_isolation_certification,
)

pytestmark = pytest.mark.certification


def test_real_execution_survives_broken_scout_analytical_store() -> None:
    result = run_failure_isolation_certification()
    assert result.scout_failure_status >= 500
    assert result.execution_status == "COMPLETED"
    assert result.execution_outcome == "SUCCESS"
    assert result.game_tape_contains_acceptance_step is True
    assert result.scout_still_failed_after_execution is True
    assert result.blocked_outbound_attempts == 0

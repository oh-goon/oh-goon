from __future__ import annotations

import pytest
from oh_goon.repository_tooling.scout_corruption_recovery_certification import (
    run_corruption_recovery_certification,
)

pytestmark = pytest.mark.certification


def test_real_corrupt_sidecar_rebuilds_from_journal_and_execution_continues() -> None:
    result = run_corruption_recovery_certification()
    assert result.canonical_revision_before_corruption >= 1
    assert (
        result.canonical_revision_after_recovery
        == result.canonical_revision_before_corruption
    )
    assert (
        result.processed_revision_after_recovery
        == result.canonical_revision_before_corruption
    )
    assert result.retained_runs_after_recovery == 1
    assert result.retained_steps_after_recovery == 0
    assert result.second_execution_status == "COMPLETED"
    assert result.second_execution_outcome == "SUCCESS"
    assert result.second_game_tape_contains_acceptance_step is True
    assert result.retained_runs_after_second_execution == 2
    assert result.retained_steps_after_second_execution == 0
    assert result.final_lag_revisions == 0
    assert result.blocked_outbound_attempts == 0

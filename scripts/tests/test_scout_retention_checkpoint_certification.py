from __future__ import annotations

import pytest
from oh_goon.repository_tooling.scout_retention_checkpoint_certification import (
    _mixed_age_snapshot,
    run_retention_checkpoint_certification,
)

pytestmark = pytest.mark.certification


def test_mixed_age_fixture_has_three_runs_and_steps() -> None:
    snapshot = _mixed_age_snapshot(revision=7)
    assert len(snapshot.runs) == 3
    assert len(snapshot.steps) == 3
    assert len({run.run_id for run in snapshot.runs}) == 3


def test_real_http_retention_prunes_run_and_step_windows_and_checkpoints_wal() -> None:
    result = run_retention_checkpoint_certification()
    assert result.canonical_revision_after == result.canonical_revision_before
    assert result.retained_runs == 2
    assert result.retained_steps == 1
    assert result.lag_revisions == 0
    assert result.wal_bytes_after_maintenance == 0
    assert result.blocked_outbound_attempts == 0

from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionMaterializationStale


from tests.factories.execution_materialization import ready_execution_materialization_candidate

def test_materialization_is_durable_projection_of_exact_current_authority(tmp_path: Path) -> None:
    _db, _manager, workspace, _state, checkpoint, assignment, _leases, lease, authority = ready_execution_materialization_candidate(tmp_path)
    value = authority.materialize(
        workspace,
        assignment,
        lease,
        tool_ids=("python",),
        input_artifact_ids=("artifact-a",),
        producer="hashmarks",
        producer_artifact_identity="sha256:producer",
        producer_contract_version="hashmarks.repository-work-selection.v1",
        producer_contract_identity="sha256:contract",
        producer_work_identity="sha256:work",
    )
    assert value.input_checkpoint_id == checkpoint.checkpoint_id
    assert value.lease_generation == lease.lease_generation
    assert value.executor_generation == lease.executor_generation
    assert value.tool_ids == ("python",)
    assert authority.materialization(value.materialization_id) == value


def test_materialization_cannot_widen_assignment_tools_or_artifacts(tmp_path: Path) -> None:
    _db, _manager, workspace, _state, _checkpoint, assignment, _leases, lease, authority = ready_execution_materialization_candidate(tmp_path)
    with pytest.raises(ValueError, match="cannot widen assignment tool"):
        authority.materialize(workspace, assignment, lease, tool_ids=("python", "curl"))
    with pytest.raises(ValueError, match="cannot widen assignment input artifacts"):
        authority.materialize(workspace, assignment, lease, input_artifact_ids=("artifact-c",))


def test_materialization_rejects_released_lease(tmp_path: Path) -> None:
    _db, _manager, workspace, state, _checkpoint, assignment, leases, lease, authority = ready_execution_materialization_candidate(tmp_path)
    leases.release(lease, expected_state_version=state.state_version)
    with pytest.raises(ExecutionMaterializationStale):
        authority.materialize(workspace, assignment, lease)


def test_materialization_is_unique_per_assignment_lease_pair(tmp_path: Path) -> None:
    _db, _manager, workspace, _state, _checkpoint, assignment, _leases, lease, authority = ready_execution_materialization_candidate(tmp_path)
    authority.materialize(workspace, assignment, lease)
    with pytest.raises(ExecutionMaterializationStale, match="durably bound"):
        authority.materialize(workspace, assignment, lease)

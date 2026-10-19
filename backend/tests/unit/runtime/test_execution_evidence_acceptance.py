from __future__ import annotations

from pathlib import Path

import pytest

from oh_goon.core.errors import ExecutionEvidenceRejected, WorkspaceFenceUnproven
from oh_goon.core.models.workspace_lease import WriterQuiescenceEvidence
from oh_goon.core.time import utc_now
from oh_goon.runtime.execution_evidence_acceptance import ExecutionEvidenceAcceptanceAuthority
from tests.factories.execution_materialization import ready_execution_materialization_candidate


def _materialized(tmp_path: Path):
    database, manager, workspace, state, checkpoint, assignment, leases, lease, materializations = (
        ready_execution_materialization_candidate(tmp_path)
    )
    materialization = materializations.materialize(
        workspace,
        assignment,
        lease,
        producer="hashmarks",
        producer_artifact_identity="sha256:producer",
        producer_contract_version="hashmarks.repository-work-selection.v1",
        producer_contract_identity="sha256:contract",
        producer_work_identity="sha256:work",
    )
    authority = ExecutionEvidenceAcceptanceAuthority(
        database,
        workspaces=manager,
        assignments=materializations._assignments,
        leases=leases,
        materializations=materializations,
    )
    return (
        database,
        manager,
        workspace,
        state,
        checkpoint,
        assignment,
        leases,
        lease,
        materialization,
        authority,
    )


def test_acceptance_binds_exact_current_authority_without_interpreting_result(
    tmp_path: Path,
) -> None:
    (
        _db,
        _manager,
        workspace,
        _state,
        checkpoint,
        assignment,
        _leases,
        lease,
        materialization,
        authority,
    ) = _materialized(tmp_path)
    accepted = authority.accept(
        workspace,
        assignment,
        lease,
        materialization,
        output_checkpoint_id=checkpoint.checkpoint_id,
        result_identity="sha256:raw-result",
        evidence_artifact_ids=("evidence-a", "evidence-b"),
        producer_work_identity="sha256:work",
    )
    assert accepted.input_checkpoint_id == checkpoint.checkpoint_id
    assert accepted.output_checkpoint_id == checkpoint.checkpoint_id
    assert accepted.materialization_fingerprint == materialization.resolved_fingerprint
    assert accepted.result_identity == "sha256:raw-result"
    assert authority.acceptance(accepted.acceptance_id) == accepted


def test_acceptance_rejects_producer_or_materialization_scope_mismatch(tmp_path: Path) -> None:
    (
        _db,
        _manager,
        workspace,
        _state,
        checkpoint,
        assignment,
        _leases,
        lease,
        materialization,
        authority,
    ) = _materialized(tmp_path)
    with pytest.raises(ExecutionEvidenceRejected, match="Producer work identity"):
        authority.accept(
            workspace,
            assignment,
            lease,
            materialization,
            output_checkpoint_id=checkpoint.checkpoint_id,
            result_identity="sha256:raw-result",
            producer_work_identity="sha256:other-work",
        )


def test_acceptance_requires_bound_writer_quiescence(tmp_path: Path) -> None:
    (
        _db,
        _manager,
        workspace,
        _state,
        checkpoint,
        assignment,
        leases,
        lease,
        materialization,
        authority,
    ) = _materialized(tmp_path)
    lease = leases.mark_writer_started(
        lease,
        authority_kind="process_group",
        authority_id="pg-77",
    )
    with pytest.raises(WorkspaceFenceUnproven):
        authority.accept(
            workspace,
            assignment,
            lease,
            materialization,
            output_checkpoint_id=checkpoint.checkpoint_id,
            result_identity="sha256:raw-result",
            producer_work_identity="sha256:work",
        )
    quiescence = WriterQuiescenceEvidence(
        lease_id=lease.lease_id,
        authority_kind="process_group",
        authority_id="pg-77",
        executor_id=lease.executor_id,
        executor_generation=lease.executor_generation,
        quiescent=True,
        observed_at=utc_now(),
        evidence_id="quiescence-1",
    )
    accepted = authority.accept(
        workspace,
        assignment,
        lease,
        materialization,
        output_checkpoint_id=checkpoint.checkpoint_id,
        result_identity="sha256:raw-result",
        producer_work_identity="sha256:work",
        quiescence=quiescence,
    )
    assert accepted.lease_generation == lease.lease_generation


def test_acceptance_allows_exact_attempt_output_checkpoint_lineage(tmp_path: Path) -> None:
    (
        _db,
        manager,
        workspace,
        state,
        checkpoint,
        assignment,
        _leases,
        lease,
        materialization,
        authority,
    ) = _materialized(tmp_path)
    candidate_path = manager.physical_path(workspace)
    (candidate_path / "a.txt").write_text("changed\n")
    _next_state, output = manager.create_candidate_checkpoint(
        workspace,
        expected_state_version=state.state_version,
        expected_parent_checkpoint_id=checkpoint.checkpoint_id,
        attempt_id=assignment.attempt_id,
        assignment_id=assignment.assignment_id,
    )
    accepted = authority.accept(
        workspace,
        assignment,
        lease,
        materialization,
        output_checkpoint_id=output.checkpoint_id,
        result_identity="sha256:result-after-edit",
        producer_work_identity="sha256:work",
    )
    assert accepted.input_checkpoint_id == checkpoint.checkpoint_id
    assert accepted.output_checkpoint_id == output.checkpoint_id


def test_acceptance_rejects_output_checkpoint_from_other_attempt(tmp_path: Path) -> None:
    (
        _db,
        manager,
        workspace,
        state,
        checkpoint,
        assignment,
        _leases,
        lease,
        materialization,
        authority,
    ) = _materialized(tmp_path)
    candidate_path = manager.physical_path(workspace)
    (candidate_path / "a.txt").write_text("foreign\n")
    _next_state, output = manager.create_candidate_checkpoint(
        workspace,
        expected_state_version=state.state_version,
        expected_parent_checkpoint_id=checkpoint.checkpoint_id,
        attempt_id="attempt-other",
        assignment_id="assignment-other",
    )
    with pytest.raises(ExecutionEvidenceRejected, match="does not descend"):
        authority.accept(
            workspace,
            assignment,
            lease,
            materialization,
            output_checkpoint_id=output.checkpoint_id,
            result_identity="sha256:foreign-result",
            producer_work_identity="sha256:work",
        )


def test_acceptance_is_single_final_receipt_per_materialization(tmp_path: Path) -> None:
    (
        _db,
        _manager,
        workspace,
        _state,
        checkpoint,
        assignment,
        _leases,
        lease,
        materialization,
        authority,
    ) = _materialized(tmp_path)
    authority.accept(
        workspace,
        assignment,
        lease,
        materialization,
        output_checkpoint_id=checkpoint.checkpoint_id,
        result_identity="sha256:first",
        producer_work_identity="sha256:work",
    )
    with pytest.raises(ExecutionEvidenceRejected, match="already accepted"):
        authority.accept(
            workspace,
            assignment,
            lease,
            materialization,
            output_checkpoint_id=checkpoint.checkpoint_id,
            result_identity="sha256:second",
            producer_work_identity="sha256:work",
        )

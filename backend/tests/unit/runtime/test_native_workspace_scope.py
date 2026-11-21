"""Typed namespace-fact guards; actual kernel witnesses have separate native proof."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import WorkspaceFenceUnproven, WorkspaceLeaseLost
from oh_goon.core.models.execution_materialization import ExecutionMaterialization
from oh_goon.core.models.fleet_plan_evidence import PlanWorkspaceWriterScopeEvidence
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.execution_evidence_acceptance import ExecutionEvidenceAcceptanceAuthority
from oh_goon.runtime.workspace_leases import (
    NativeWorkspaceQuiescence,
    require_native_workspace_quiescence,
)
from tests.factories.execution_materialization import ready_execution_materialization_candidate


@pytest.fixture
def scope(tmp_path: Path):
    db, owner, workspace, state, checkpoint, old, leases, old_lease, authority = (
        ready_execution_materialization_candidate(tmp_path)
    )
    legacy = authority.materialize(workspace, old, old_lease)
    with db.transaction() as connection:
        schema_before = connection.execute("PRAGMA user_version").fetchone()[0]
        historical = connection.execute(
            "SELECT payload_json FROM execution_materializations WHERE materialization_id=?",
            (legacy.materialization_id,),
        ).fetchone()[0]
    assert "writer_scope_kind" not in json.loads(historical)
    assert ExecutionMaterialization.model_validate_json(historical) == legacy
    ready = leases.release(old_lease, expected_state_version=state.state_version)
    assignments = ExecutionAssignmentAuthority(db, owner)
    assignment = assignments.issue_candidate_mutation(
        workspace,
        attempt_id="native-operation",
        expected_checkpoint_id=checkpoint.checkpoint_id,
        wall_time_seconds=60,
        isolation_requirements=("private_filesystem", "private_pid_namespace"),
    )
    current, lease = leases.acquire(
        workspace,
        assignment,
        executor_id=db.session_id,
        executor_generation=1,
        expected_state_version=ready.state_version,
    )
    descriptor = os.open(
        owner.physical_path(workspace), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    )
    try:
        materialization = authority.materialize(
            workspace,
            assignment,
            lease,
            writer_scope_kind="native_namespace_scope",
            workspace_directory_fd=descriptor,
        )
        receipt = PlanWorkspaceWriterScopeEvidence(
            materialization=materialization,
            physical_execution_id="physical-1",
            backend_configuration_id="backend-1",
            namespace_device=1,
            namespace_inode=2,
            workspace_device=materialization.workspace_device,
            workspace_inode=materialization.workspace_inode,
            workspace_mode=materialization.workspace_mode,
            init_process=ProcessBirthIdentity(pid=123, boot_id="test-boot", start_ticks=100),
            observed_at=utc_now(),
        )
        yield SimpleNamespace(
            db=db,
            owner=owner,
            workspace=workspace,
            state=current,
            lease=lease,
            assignment=assignment,
            materialization=materialization,
            leases=leases,
            receipt=receipt,
            historical=historical,
            schema_before=schema_before,
            legacy=legacy,
            proof=NativeWorkspaceQuiescence((receipt,), ("physical-1",), "backend-1"),
        )
    finally:
        os.close(descriptor)


def test_native_scope_does_not_rewrite_historical_materialization_or_sql_schema(scope):
    with scope.db.transaction() as connection:
        raw = connection.execute(
            "SELECT payload_json FROM execution_materializations WHERE materialization_id=?",
            (scope.legacy.materialization_id,),
        ).fetchone()[0]
        assert raw == scope.historical
        assert connection.execute("PRAGMA user_version").fetchone()[0] == scope.schema_before
        row = connection.execute(
            "SELECT writer_authority_kind FROM workspace_leases WHERE lease_id=?",
            (scope.lease.lease_id,),
        ).fetchone()
        assert row[0] is None
    assert scope.materialization.writer_scope_kind == "native_namespace_scope"


def test_native_scope_is_potential_writer_even_when_legacy_writer_kind_is_null(scope):
    with pytest.raises(WorkspaceFenceUnproven, match="absence is unproven"):
        scope.leases.release(scope.lease, expected_state_version=scope.state.state_version)
    assert scope.leases.assert_current(scope.lease) == scope.lease
    with pytest.raises(WorkspaceLeaseLost, match="already owns"):
        scope.leases.mark_writer_started(
            scope.lease, authority_kind="process_group", authority_id="123"
        )


def test_native_scope_acceptance_requires_full_namespace_facts(scope):
    acceptance = ExecutionEvidenceAcceptanceAuthority(scope.db)
    with pytest.raises(WorkspaceFenceUnproven):
        acceptance.accept(
            scope.workspace,
            scope.assignment,
            scope.lease,
            scope.materialization,
            output_checkpoint_id=scope.assignment.input_checkpoint_id,
            result_identity="native-result",
        )


@pytest.mark.parametrize(
    "tamper", ["omitted", "duplicated", "backend", "physical_root", "materialization"]
)
def test_native_scope_validation_keeps_exact_original_launch_partition(scope, tamper):
    require_native_workspace_quiescence(
        scope.materialization,
        scopes=scope.proof.scopes,
        physical_execution_ids=scope.proof.physical_execution_ids,
        backend_configuration_id=scope.proof.backend_configuration_id,
    )
    ids, receipts, backend = scope.proof.physical_execution_ids, scope.proof.scopes, "backend-1"
    if tamper == "omitted":
        ids = (*ids, "physical-2")
    if tamper == "duplicated":
        receipts = (*receipts, receipts[0])
    if tamper == "backend":
        backend = "different-backend"
    if tamper == "physical_root":
        receipts = (
            receipts[0].model_copy(update={"workspace_inode": receipts[0].workspace_inode + 1}),
        )
    if tamper == "materialization":
        receipts = (receipts[0].model_copy(update={"materialization": scope.legacy}),)
    with pytest.raises(WorkspaceFenceUnproven):
        require_native_workspace_quiescence(
            scope.materialization,
            scopes=receipts,
            physical_execution_ids=ids,
            backend_configuration_id=backend,
        )


def test_expired_native_scope_without_original_closure_is_quarantined(scope, monkeypatch):
    monkeypatch.setattr("oh_goon.runtime.workspace_leases.utc_now", lambda: scope.lease.expires_at)
    with pytest.raises(WorkspaceFenceUnproven, match="quarantined"):
        scope.leases.reconcile_expired(
            scope.lease.lease_id, expected_state_version=scope.state.state_version
        )
    assert (
        scope.owner.state(scope.workspace.workspace_id).lifecycle_state
        is WorkspaceLifecycleState.FAILED
    )


def test_failed_preparation_releases_lease_only_when_no_materialization_was_issued(tmp_path):
    _db, owner, workspace, state, _checkpoint, _assignment, leases, lease, _authority = (
        ready_execution_materialization_candidate(tmp_path)
    )
    ready = leases.release_unmaterialized(lease, expected_state_version=state.state_version)
    assert ready.lifecycle_state is WorkspaceLifecycleState.READY
    assert ready.current_lease_id is None
    assert owner.state(workspace.workspace_id) == ready
    with pytest.raises(WorkspaceLeaseLost):
        leases.assert_current(lease)


def test_preparation_cleanup_cannot_discard_a_committed_materialization_with_null_writer_kind(
    tmp_path,
):
    database, owner, workspace, state, _checkpoint, assignment, leases, lease, authority = (
        ready_execution_materialization_candidate(tmp_path)
    )
    materialization = authority.materialize(workspace, assignment, lease)
    # A caller can lose the materialization acknowledgement after its native
    # transaction committed. No runtime writer marker can disprove that row.
    with database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT writer_authority_kind FROM workspace_leases WHERE lease_id=?",
                (lease.lease_id,),
            ).fetchone()[0]
            is None
        )
    with pytest.raises(WorkspaceFenceUnproven):
        leases.release_unmaterialized(lease, expected_state_version=state.state_version)
    assert owner.state(workspace.workspace_id) == state
    assert leases.assert_current(lease) == lease
    assert authority.materialization(materialization.materialization_id) == materialization

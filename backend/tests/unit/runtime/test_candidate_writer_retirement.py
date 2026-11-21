"""Coordination ACKs cannot substitute for canonical namespace closure facts."""

from __future__ import annotations

import os

import pytest

from oh_goon.core.errors import WorkspaceStateStale
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.engine.git import GitService
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.workspaces import _worktree_git_environment
from tests.factories.execution_materialization import ready_execution_materialization_candidate


def _native_candidate(tmp_path):
    database, owner, workspace, state, checkpoint, _old, leases, old_lease, authority = (
        ready_execution_materialization_candidate(tmp_path)
    )
    ready = leases.release(old_lease, expected_state_version=state.state_version)
    assignment = ExecutionAssignmentAuthority(database, owner).issue_candidate_mutation(
        workspace,
        attempt_id="native-operation",
        expected_checkpoint_id=checkpoint.checkpoint_id,
        wall_time_seconds=60,
        isolation_requirements=("private_filesystem", "private_pid_namespace"),
    )
    state, lease = leases.acquire(
        workspace,
        assignment,
        executor_id=database.session_id,
        executor_generation=1,
        expected_state_version=ready.state_version,
    )
    return database, owner, workspace, state, checkpoint, assignment, leases, lease, authority


@pytest.mark.parametrize("unscoped", [False, True], ids=["forged-fence", "mixed-materializations"])
def test_native_retirement_refuses_fence_json_without_original_scope_results(tmp_path, unscoped):
    database, owner, workspace, state, checkpoint, assignment, leases, lease, authority = (
        _native_candidate(tmp_path)
    )
    path = owner.physical_path(workspace)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        authority.materialize(
            workspace,
            assignment,
            lease,
            writer_scope_kind="native_namespace_scope",
            workspace_directory_fd=descriptor,
        )
    finally:
        os.close(descriptor)
    # Simulate a locally acknowledged/stored fence. It is deliberately not an
    # original accepted Run result and cannot authorize native deletion.
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE workspace_leases SET status='RELEASED', finished_at=expires_at, "
            "fence_evidence_json=? WHERE lease_id=?",
            ('{"kind":"native_namespace_scope","quiescent":true}', lease.lease_id),
        )
        connection.execute(
            "UPDATE workspace_state SET lifecycle_state='READY', current_lease_id=NULL, "
            "state_version=state_version+1 WHERE workspace_id=?",
            (workspace.workspace_id,),
        )
    if unscoped:
        other = ExecutionAssignmentAuthority(database, owner).issue_candidate_mutation(
            workspace,
            attempt_id="other-operation",
            expected_checkpoint_id=checkpoint.checkpoint_id,
            wall_time_seconds=60,
        )
        state, other_lease = leases.acquire(
            workspace,
            other,
            executor_id="other-executor",
            executor_generation=1,
            expected_state_version=owner.state(workspace.workspace_id).state_version,
        )
        authority.materialize(workspace, other, other_lease)
        leases.release(other_lease, expected_state_version=state.state_version)
    state = owner.transition(
        workspace.workspace_id,
        expected_state_version=owner.state(workspace.workspace_id).state_version,
        next_state=WorkspaceLifecycleState.FINALIZED,
    )
    with pytest.raises(
        WorkspaceStateStale, match="Unqualified materialization|writer Run authority"
    ):
        owner.retire_worktree(workspace, expected_state_version=state.state_version)
    assert path.is_dir() and (path / "a.txt").read_text() == "a\n"
    assert owner.state(workspace.workspace_id) == state
    assert not owner._worktree_retirement_marker_for_id(workspace.workspace_id).exists()


def test_native_retirement_preserves_git_ignored_unpromoted_source(tmp_path):
    database, owner, workspace, state, checkpoint, assignment, _leases, lease, authority = (
        _native_candidate(tmp_path)
    )
    path = owner.physical_path(workspace)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        authority.materialize(
            workspace,
            assignment,
            lease,
            writer_scope_kind="native_namespace_scope",
            workspace_directory_fd=descriptor,
        )
    finally:
        os.close(descriptor)
    # A legitimate existing Git ignore policy does not make generated source
    # promoted. Current checkpoint equality alone must not authorize deletion.
    (tmp_path / "repo" / ".git" / "info" / "exclude").write_text("ignored.py\n")
    output = path / "ignored.py"
    output.write_text("valuable_result = 42\n")
    observed = GitService(metadata_only=True).observe(path, env=_worktree_git_environment())
    assert observed.changed_files == ()
    state, result = owner.create_candidate_checkpoint(
        workspace,
        expected_state_version=state.state_version,
        expected_parent_checkpoint_id=checkpoint.checkpoint_id,
        attempt_id=assignment.attempt_id,
        assignment_id=assignment.assignment_id,
    )
    assert result.source_sha256 != checkpoint.source_sha256
    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE workspace_leases SET status='RELEASED', finished_at=expires_at, "
            "fence_evidence_json=? WHERE lease_id=?",
            ('{"kind":"native_namespace_scope","quiescent":true}', lease.lease_id),
        )
        connection.execute(
            "UPDATE workspace_state SET lifecycle_state='READY', current_lease_id=NULL, "
            "state_version=state_version+1 WHERE workspace_id=?",
            (workspace.workspace_id,),
        )
    finalized = owner.transition(
        workspace.workspace_id,
        expected_state_version=owner.state(workspace.workspace_id).state_version,
        next_state=WorkspaceLifecycleState.FINALIZED,
    )
    with pytest.raises(WorkspaceStateStale, match="source changed from the original candidate"):
        owner.retire_worktree(workspace, expected_state_version=finalized.state_version)
    assert output.read_text() == "valuable_result = 42\n"
    assert owner.state(workspace.workspace_id) == finalized

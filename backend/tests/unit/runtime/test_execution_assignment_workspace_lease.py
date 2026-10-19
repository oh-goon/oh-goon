from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from oh_goon.core.errors import (
    CandidateCheckpointStale,
    WorkspaceFenceUnproven,
    WorkspaceLeaseBusy,
    WorkspaceLeaseExpired,
    WorkspaceLeaseLost,
)
from oh_goon.core.models.recovery_lifecycle import RecoveryFailureEvidence
from oh_goon.core.models.workspace_lease import WriterQuiescenceEvidence
from oh_goon.core.product_identity import stable_id
from oh_goon.core.recovery_lifecycle import RecoveryLifecycle
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.workspace_leases import WorkspaceLeaseStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def _candidate(tmp_path: Path):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git("init", "-q", cwd=repository)
    _git("config", "user.name", "Test", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git("add", ".", cwd=repository)
    _git("commit", "-qm", "base", cwd=repository)
    revision = _git("rev-parse", "HEAD", cwd=repository)
    database = LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE")
    manager = RepositoryWorkspaceAuthority(database)
    read = manager.request(
        repository_id="repo", base_revision=revision, kind=WorkspaceKind.READ_SNAPSHOT
    )
    read_allocated = manager.allocate(read.workspace_id, expected_state_version=0)
    _read_ready, binding = manager.materialize_read_snapshot(
        read, repository, expected_state_version=read_allocated.state_version
    )
    worktree = manager.request(
        repository_id="repo", base_revision=revision, kind=WorkspaceKind.WORKTREE
    )
    allocated = manager.allocate(worktree.workspace_id, expected_state_version=0)
    ready, checkpoint = manager.materialize_worktree(
        worktree, repository, binding, expected_state_version=allocated.state_version
    )
    return database, manager, worktree, ready, checkpoint


def _assignment(database, manager, workspace, checkpoint, *, attempt="attempt-1"):
    authority = ExecutionAssignmentAuthority(database, manager)
    assignment = authority.issue_candidate_mutation(
        workspace,
        attempt_id=attempt,
        expected_checkpoint_id=checkpoint.checkpoint_id,
        wall_time_seconds=120,
        max_processes=8,
        network_authority="none",
        git_authority="none",
        tool_ids=("python",),
    )
    return authority, assignment


def _quiescent(lease):
    now = utc_now()
    return WriterQuiescenceEvidence(
        lease_id=lease.lease_id,
        authority_kind=lease.writer_authority_kind,
        authority_id=lease.writer_authority_id,
        executor_id=lease.executor_id,
        executor_generation=lease.executor_generation,
        quiescent=True,
        observed_at=now,
        evidence_id=stable_id(
            "writer-quiescence", {"lease": lease.lease_id, "at": now.isoformat()}
        ),
    )


def test_assignment_is_durable_immutable_and_bound_to_reproved_checkpoint(tmp_path: Path) -> None:
    database, manager, workspace, _ready, checkpoint = _candidate(tmp_path)
    authority, assignment = _assignment(database, manager, workspace, checkpoint)

    assert authority.assignment(assignment.assignment_id) == assignment
    assert assignment.workspace_id == workspace.workspace_id
    assert assignment.input_checkpoint_id == checkpoint.checkpoint_id
    assert assignment.filesystem_authority == "candidate_mutation"
    assert assignment.assignment_fingerprint.startswith("execution-assignment-")

    (manager.physical_path(workspace) / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(CandidateCheckpointStale):
        authority.issue_candidate_mutation(
            workspace,
            attempt_id="attempt-2",
            expected_checkpoint_id=checkpoint.checkpoint_id,
            wall_time_seconds=60,
        )


def test_one_mutation_lease_is_current_and_state_is_fenced_by_generation(tmp_path: Path) -> None:
    database, manager, workspace, ready, checkpoint = _candidate(tmp_path)
    _authority, assignment = _assignment(database, manager, workspace, checkpoint)
    leases = WorkspaceLeaseStore(database, workspaces=manager)

    in_use, lease = leases.acquire(
        workspace,
        assignment,
        executor_id="executor-a",
        executor_generation=7,
        expected_state_version=ready.state_version,
    )
    assert in_use.lifecycle_state is WorkspaceLifecycleState.IN_USE
    assert in_use.current_lease_id == lease.lease_id
    assert lease.lease_generation == 1
    assert leases.assert_current(lease) == lease

    with pytest.raises(WorkspaceLeaseBusy):
        leases.acquire(
            workspace,
            assignment,
            executor_id="executor-b",
            executor_generation=1,
            expected_state_version=in_use.state_version,
        )


def test_writer_authority_rejects_pid_only_and_release_requires_matching_quiescence(
    tmp_path: Path,
) -> None:
    database, manager, workspace, ready, checkpoint = _candidate(tmp_path)
    _authority, assignment = _assignment(database, manager, workspace, checkpoint)
    leases = WorkspaceLeaseStore(database, workspaces=manager)
    state, lease = leases.acquire(
        workspace,
        assignment,
        executor_id="executor-a",
        executor_generation=3,
        expected_state_version=ready.state_version,
    )
    with pytest.raises(ValueError, match="PID-only fencing is forbidden"):
        leases.mark_writer_started(lease, authority_kind="pid", authority_id="123")

    writer_lease = leases.mark_writer_started(
        lease,
        authority_kind="cgroup",
        authority_id="/sys/fs/cgroup/oh-goon/lease-1",
    )
    with pytest.raises(WorkspaceFenceUnproven):
        leases.release(writer_lease, expected_state_version=state.state_version)

    released = leases.release(
        writer_lease,
        expected_state_version=state.state_version,
        quiescence=_quiescent(writer_lease),
    )
    assert released.lifecycle_state is WorkspaceLifecycleState.READY
    assert released.current_lease_id is None
    with pytest.raises(WorkspaceLeaseLost):
        leases.assert_current(writer_lease)


def test_writer_start_rechecks_expiry_in_same_write_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database, manager, workspace, ready, checkpoint = _candidate(tmp_path)
    _authority, assignment = _assignment(database, manager, workspace, checkpoint)
    leases = WorkspaceLeaseStore(database, workspaces=manager)
    _state, lease = leases.acquire(
        workspace,
        assignment,
        executor_id="executor-expiry",
        executor_generation=1,
        expected_state_version=ready.state_version,
        ttl_seconds=1,
    )
    monkeypatch.setattr("oh_goon.runtime.workspace_leases.utc_now", lambda: lease.expires_at)

    with pytest.raises(WorkspaceLeaseExpired, match="expired before writer authority"):
        leases.mark_writer_started(
            lease, authority_kind="process_group", authority_id="pgid:expiry"
        )

    observed = leases.lease(lease.lease_id)
    assert observed.writer_authority_kind is None
    assert observed.writer_authority_id is None


def test_expired_writer_is_quarantined_when_revocation_cannot_be_proven(
    tmp_path: Path, monkeypatch
) -> None:
    database, manager, workspace, ready, checkpoint = _candidate(tmp_path)
    _authority, assignment = _assignment(database, manager, workspace, checkpoint)
    leases = WorkspaceLeaseStore(database, workspaces=manager)
    state, lease = leases.acquire(
        workspace,
        assignment,
        executor_id="executor-a",
        executor_generation=2,
        expected_state_version=ready.state_version,
        ttl_seconds=1,
    )
    writer_lease = leases.mark_writer_started(
        lease, authority_kind="process_group", authority_id="pgid:500"
    )
    future = writer_lease.expires_at + timedelta(seconds=1)
    monkeypatch.setattr("oh_goon.runtime.workspace_leases.utc_now", lambda: future)

    with pytest.raises(WorkspaceLeaseExpired):
        leases.assert_current(writer_lease)
    with pytest.raises(WorkspaceFenceUnproven, match="quarantined"):
        leases.reconcile_expired(
            writer_lease.lease_id,
            expected_state_version=state.state_version,
            prove_quiescence=None,
        )
    quarantined = manager.state(workspace.workspace_id)
    assert quarantined.lifecycle_state is WorkspaceLifecycleState.FAILED
    assert quarantined.current_lease_id is None


def test_proven_release_allows_new_assignment_and_monotonic_lease_generation(
    tmp_path: Path,
) -> None:
    database, manager, workspace, ready, checkpoint = _candidate(tmp_path)
    authority, first_assignment = _assignment(
        database, manager, workspace, checkpoint, attempt="attempt-1"
    )
    leases = WorkspaceLeaseStore(database, workspaces=manager, assignments=authority)
    state, first = leases.acquire(
        workspace,
        first_assignment,
        executor_id="executor-a",
        executor_generation=1,
        expected_state_version=ready.state_version,
    )
    released = leases.release(first, expected_state_version=state.state_version)

    second_assignment = authority.issue_candidate_mutation(
        workspace,
        attempt_id="attempt-2",
        expected_checkpoint_id=checkpoint.checkpoint_id,
        wall_time_seconds=60,
    )
    _in_use, second = leases.acquire(
        workspace,
        second_assignment,
        executor_id="executor-b",
        executor_generation=4,
        expected_state_version=released.state_version,
    )
    assert second.lease_generation == first.lease_generation + 1
    assert second.lease_id != first.lease_id


def test_admitted_recovery_gets_fresh_assignment_with_same_bounded_authority(
    tmp_path: Path,
) -> None:
    database, manager, workspace, _ready, checkpoint = _candidate(tmp_path)
    authority, first = _assignment(database, manager, workspace, checkpoint)
    work_identity = "sha256:" + "b" * 64
    failure = RecoveryFailureEvidence(
        failure_class="environment",
        reason_code="executor-pressure",
        work_identity=work_identity,
        detail_identity="sha256:" + "c" * 64,
    )
    recovery = RecoveryLifecycle().admit(
        previous_assignment=first,
        checkpoint=checkpoint,
        failure=failure,
        lineage_id="lineage-1",
        attempt_ordinal=2,
        max_attempts=3,
        exact_work_identity=work_identity,
    )

    second = authority.issue_recovery_candidate_mutation(
        workspace, previous_assignment=first, recovery=recovery, attempt_id="attempt-2"
    )

    assert second.assignment_id != first.assignment_id
    assert second.attempt_id == "attempt-2"
    assert second.input_checkpoint_id == first.input_checkpoint_id
    assert second.attempt_budget == first.attempt_budget
    assert second.tool_ids == first.tool_ids
    assert second.publish_authority is False
    assert recovery.admission_id in second.input_artifact_ids


def test_blocked_recovery_cannot_receive_assignment(tmp_path: Path) -> None:
    database, manager, workspace, _ready, checkpoint = _candidate(tmp_path)
    authority, first = _assignment(database, manager, workspace, checkpoint)
    work_identity = "sha256:" + "b" * 64
    failure = RecoveryFailureEvidence(
        failure_class="product_assertion",
        reason_code="test-failed",
        work_identity=work_identity,
        detail_identity="sha256:" + "c" * 64,
    )
    recovery = RecoveryLifecycle().admit(
        previous_assignment=first,
        checkpoint=checkpoint,
        failure=failure,
        lineage_id="lineage-1",
        attempt_ordinal=2,
        max_attempts=3,
        exact_work_identity=work_identity,
    )
    with pytest.raises(ValueError, match="Blocked recovery"):
        authority.issue_recovery_candidate_mutation(
            workspace, previous_assignment=first, recovery=recovery, attempt_id="attempt-2"
        )


def test_release_rejects_stale_pre_writer_lease_after_writer_binding(tmp_path: Path) -> None:
    database, manager, workspace, ready, checkpoint = _candidate(tmp_path)
    _authority, assignment = _assignment(database, manager, workspace, checkpoint)
    leases = WorkspaceLeaseStore(database, workspaces=manager)
    state, lease = leases.acquire(
        workspace,
        assignment,
        executor_id="executor-race",
        executor_generation=9,
        expected_state_version=ready.state_version,
    )
    writer_lease = leases.mark_writer_started(
        lease, authority_kind="process_group", authority_id="pgid:race"
    )
    with pytest.raises(WorkspaceLeaseLost, match="authority changed"):
        leases.release(lease, expected_state_version=state.state_version)
    assert leases.assert_current(writer_lease) == writer_lease

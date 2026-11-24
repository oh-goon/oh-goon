"""The original dispatch record stays replayable across cancellation and controller loss."""

from pathlib import Path

import pytest

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.fleet_plan_evidence import PlanSnapshotEvidence
from oh_goon.core.models.parallel_agent_work import (
    ParallelAgentChild,
    ParallelAgentWorkIntent,
)
from oh_goon.core.models.parallel_candidate_verification import (
    FrozenVerifierInput,
    ParallelCandidateVerifier,
)
from oh_goon.core.models.source_authority import RepositorySourceAuthority
from oh_goon.core.process_birth import ProcessBirthIdentity
from oh_goon.core.product_identity import repository_subject_instance_id
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.runtime import parallel_agent_work
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _begin(database: LocalDatabase) -> ParallelAgentWorkIntent:
    verifier = ParallelCandidateVerifier(
        acceptance="Check the candidate",
        executable="/usr/bin/python3",
        executable_sha256="0" * 64,
        arguments=("-I", "{input:verify.py}", "{candidate}"),
        inputs=(
            FrozenVerifierInput(
                name="verify.py",
                content="",
                sha256="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            ),
        ),
    )
    intent = ParallelAgentWorkIntent(
        work_id="task",
        repository_id="repository",
        source=RepositorySourceAuthority.create(
            subject_instance_id=repository_subject_instance_id(database.path.parent),
            source_snapshot=PlanSnapshotEvidence(sha256="9" * 64, byte_count=10, entry_count=1),
        ),
        base_revision="7" * 40,
        children=(
            ParallelAgentChild(
                fleet_run_id="task-1", task_sha256="1" * 64, native_selection_id="a"
            ),
            ParallelAgentChild(
                fleet_run_id="task-2", task_sha256="2" * 64, native_selection_id="b"
            ),
        ),
        notes_enabled=True,
        verifier_id=verifier.identity,
    )
    assert parallel_agent_work.begin_parallel_work(database, intent, "request", verifier)
    return intent


def test_exact_replay_and_cancellation_keep_original_membership(tmp_path: Path) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite")
    intent = _begin(database)
    observed = parallel_agent_work.cancel_parallel_work(
        database, intent.work_id, fleet_run_id="task-1"
    )
    assert not observed.dispatch_closed
    assert [(child.dispatch, child.cancellation_requested) for child in observed.children] == [
        ("never_started", True),
        ("pending", False),
    ]
    with database.transaction() as connection:
        assert not parallel_agent_work.parallel_notes_enabled_in(connection, "task", "task-1")
        assert parallel_agent_work.parallel_notes_enabled_in(connection, "task", "task-2")
        with pytest.raises(StateConflict, match="dispatch"):
            parallel_agent_work.require_parallel_candidate_binding_in(
                connection, fleet_run_id="task-2", target=None
            )

    cancelled = parallel_agent_work.cancel_parallel_work(database, intent.work_id)
    reopened = LocalDatabase(database.path, read_only=True)
    assert parallel_agent_work.replay_parallel_work(reopened, "task", "request") == cancelled
    assert cancelled.intent == intent and cancelled.dispatch_closed
    assert all(child.dispatch == "never_started" for child in cancelled.children)
    with pytest.raises(StateConflict, match="different request"):
        parallel_agent_work.replay_parallel_work(reopened, "task", "changed")


def test_controller_observation_distinguishes_unknown_and_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite")
    original = ProcessBirthIdentity(pid=41, boot_id="first", start_ticks=100)
    monkeypatch.setattr(parallel_agent_work, "observe_process_birth", lambda _pid=None: original)
    intent = _begin(database)

    monkeypatch.setattr(parallel_agent_work, "observe_process_birth", lambda _pid=None: None)
    monkeypatch.setattr(parallel_agent_work, "observe_process_liveness", lambda _pid: None)
    unknown = parallel_agent_work.read_parallel_work(database, intent.work_id)
    assert not unknown.dispatch_closed
    assert [child.dispatch for child in unknown.children] == ["unresolved", "unresolved"]

    replacement = ProcessBirthIdentity(pid=41, boot_id="first", start_ticks=200)
    monkeypatch.setattr(parallel_agent_work, "observe_process_birth", lambda _pid=None: replacement)
    finished = parallel_agent_work.read_parallel_work(database, intent.work_id)
    assert finished.dispatch_closed
    assert [child.dispatch for child in finished.children] == ["never_started", "never_started"]


def test_controller_loss_keeps_both_original_workspace_allocations_in_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = LocalDatabase(tmp_path / "state.sqlite")
    original = ProcessBirthIdentity(pid=41, boot_id="first", start_ticks=100)
    monkeypatch.setattr(parallel_agent_work, "observe_process_birth", lambda _pid=None: original)
    intent = _begin(database)
    workspaces = RepositoryWorkspaceAuthority(database)
    candidate, source = parallel_agent_work.reserve_parallel_candidate_workspaces(
        database,
        workspaces,
        work_id=intent.work_id,
        fleet_run_id=intent.children[0].fleet_run_id,
        repository_id=intent.repository_id,
        base_revision=intent.base_revision,
        expected_source=intent.source,
    )
    candidate_state = workspaces.allocate(candidate.workspace_id, expected_state_version=0)
    source_state = workspaces.allocate(source.workspace_id, expected_state_version=0)

    replacement = ProcessBirthIdentity(pid=41, boot_id="first", start_ticks=200)
    monkeypatch.setattr(parallel_agent_work, "observe_process_birth", lambda _pid=None: replacement)
    reopened = LocalDatabase(database.path, read_only=True)
    readback = parallel_agent_work.read_parallel_work(reopened, intent.work_id)
    left, right = readback.children
    assert readback.dispatch_closed
    assert left.dispatch == right.dispatch == "never_started"
    assert left.candidate_state == candidate_state
    assert left.source_state == source_state
    assert right.candidate_state is None and right.source_state is None
    assert candidate_state.lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    assert source_state.lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    assert workspaces.physical_path(candidate).is_dir()
    assert workspaces.physical_path(source).is_dir()
    assert parallel_agent_work.replay_parallel_work(reopened, intent.work_id, "request") == readback
    with pytest.raises(StateConflict, match="open original lane"):
        parallel_agent_work.reserve_parallel_candidate_workspaces(
            database,
            workspaces,
            work_id=intent.work_id,
            fleet_run_id=intent.children[1].fleet_run_id,
            repository_id=intent.repository_id,
            base_revision=intent.base_revision,
            expected_source=intent.source,
        )

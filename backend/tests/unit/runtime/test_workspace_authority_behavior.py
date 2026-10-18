from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import (
    CandidateCheckpointNotFound,
    CandidateCheckpointStale,
    WorkspaceNotFound,
    WorkspaceStateStale,
    WorkspaceTransitionInvalid,
)
from oh_goon.core.models.workspace import WorkspaceRef
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(("git", *args), cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git("init", "-q", cwd=repository)
    _git("config", "user.name", "Oh Goon Test", cwd=repository)
    _git("config", "user.email", "test@example.invalid", cwd=repository)
    (repository / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git("add", ".", cwd=repository)
    _git("commit", "-qm", "base", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


def _authority(tmp_path: Path) -> tuple[RepositoryWorkspaceAuthority, LocalDatabase]:
    database = LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE")
    return RepositoryWorkspaceAuthority(database), database


def _request(
    manager: RepositoryWorkspaceAuthority,
    *,
    kind: WorkspaceKind,
    revision: str = "0123456789abcdef",
    workspace_id: str | None = None,
) -> WorkspaceRef:
    return manager.request(
        repository_id="repo-example",
        base_revision=revision,
        kind=kind,
        workspace_id=workspace_id,
    )


def _ready_snapshot(
    manager: RepositoryWorkspaceAuthority,
    source: Path,
    *,
    revision: str,
) -> tuple[WorkspaceRef, object]:
    workspace = _request(manager, kind=WorkspaceKind.READ_SNAPSHOT, revision=revision)
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    _ready, binding = manager.materialize_read_snapshot(
        workspace, source, expected_state_version=allocated.state_version
    )
    return workspace, binding


def _ready_worktree(
    manager: RepositoryWorkspaceAuthority,
    repository: Path,
    revision: str,
):
    _read, binding = _ready_snapshot(manager, repository, revision=revision)
    workspace = _request(manager, kind=WorkspaceKind.WORKTREE, revision=revision)
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    ready, checkpoint = manager.materialize_worktree(
        workspace,
        repository,
        binding,
        expected_state_version=allocated.state_version,
    )
    return workspace, ready, checkpoint


def test_workspace_lookup_and_duplicate_identity_fail_closed(tmp_path: Path) -> None:
    manager, _database = _authority(tmp_path)
    original = _request(manager, kind=WorkspaceKind.WORKTREE, workspace_id="workspace-fixed")

    with pytest.raises(WorkspaceStateStale, match="already allocated"):
        _request(manager, kind=WorkspaceKind.WORKTREE, workspace_id=original.workspace_id)
    with pytest.raises(WorkspaceNotFound, match="Workspace not found"):
        manager.reference("workspace-missing")
    with pytest.raises(WorkspaceNotFound, match="Workspace state not found"):
        manager.state("workspace-missing")


def test_physical_path_rejects_forged_identity_and_precreated_symlink(tmp_path: Path) -> None:
    manager, _database = _authority(tmp_path)
    workspace = _request(manager, kind=WorkspaceKind.WORKTREE)
    forged = WorkspaceRef(
        workspace_id=workspace.workspace_id,
        repository_id="repo-other",
        base_revision=workspace.base_revision,
        kind=workspace.kind,
    )
    with pytest.raises(WorkspaceStateStale, match="does not match durable identity"):
        manager.physical_path(forged)

    target = tmp_path / "outside"
    target.mkdir()
    path = manager.physical_path(workspace)
    path.symlink_to(target, target_is_directory=True)
    with pytest.raises(WorkspaceStateStale, match="must not be a symlink"):
        manager.allocate(workspace.workspace_id, expected_state_version=0)


def test_allocate_rejects_preexisting_directory_without_state_change(tmp_path: Path) -> None:
    manager, _database = _authority(tmp_path)
    workspace = _request(manager, kind=WorkspaceKind.WORKTREE)
    path = manager.physical_path(workspace)
    path.mkdir()

    with pytest.raises(WorkspaceStateStale, match="already exists before allocation"):
        manager.allocate(workspace.workspace_id, expected_state_version=0)
    assert manager.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.REQUESTED


def test_read_snapshot_rejects_wrong_identity_kind_state_and_nonempty_container(tmp_path: Path) -> None:
    manager, _database = _authority(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source\n", encoding="utf-8")

    wrong_kind = _request(manager, kind=WorkspaceKind.WORKTREE)
    manager.allocate(wrong_kind.workspace_id, expected_state_version=0)
    with pytest.raises(WorkspaceTransitionInvalid, match="requires a READ_SNAPSHOT"):
        manager.materialize_read_snapshot(wrong_kind, source, expected_state_version=1)

    read = _request(manager, kind=WorkspaceKind.READ_SNAPSHOT)
    allocated = manager.allocate(read.workspace_id, expected_state_version=0)
    forged = read.model_copy(update={"repository_id": "repo-other"})
    with pytest.raises(WorkspaceStateStale, match="does not match durable identity"):
        manager.materialize_read_snapshot(forged, source, expected_state_version=allocated.state_version)
    with pytest.raises(WorkspaceStateStale, match="expected version 0, observed 1"):
        manager.materialize_read_snapshot(read, source, expected_state_version=0)

    path = manager.physical_path(read)
    (path / "unexpected").write_text("occupied", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="not an empty owned directory"):
        manager.materialize_read_snapshot(read, source, expected_state_version=allocated.state_version)

    path.joinpath("unexpected").unlink()
    manager.transition(
        read.workspace_id,
        expected_state_version=allocated.state_version,
        next_state=WorkspaceLifecycleState.FAILED,
    )
    with pytest.raises(WorkspaceStateStale, match="expected version 1, observed 2"):
        manager.materialize_read_snapshot(read, source, expected_state_version=allocated.state_version)


def test_read_snapshot_concurrent_state_change_never_publishes_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.workspaces as workspaces

    manager, _database = _authority(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source\n", encoding="utf-8")
    read = _request(manager, kind=WorkspaceKind.READ_SNAPSHOT)
    allocated = manager.allocate(read.workspace_id, expected_state_version=0)
    original = workspaces.snapshot_source_directory

    def concurrent_snapshot(*args, **kwargs):
        identity = original(*args, **kwargs)
        manager.transition(
            read.workspace_id,
            expected_state_version=allocated.state_version,
            next_state=WorkspaceLifecycleState.FAILED,
        )
        return identity

    monkeypatch.setattr(workspaces, "snapshot_source_directory", concurrent_snapshot)
    with pytest.raises(WorkspaceStateStale, match="expected version 1, observed 2"):
        manager.materialize_read_snapshot(
            read, source, expected_state_version=allocated.state_version
        )

    path = manager.physical_path(read)
    assert path.is_dir() and list(path.iterdir()) == []
    assert manager.state(read.workspace_id).lifecycle_state is WorkspaceLifecycleState.FAILED


def test_read_snapshot_binding_rejects_wrong_kind_state_missing_identity_and_tamper(tmp_path: Path) -> None:
    manager, database = _authority(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source\n", encoding="utf-8")

    worktree = _request(manager, kind=WorkspaceKind.WORKTREE)
    with pytest.raises(WorkspaceTransitionInvalid, match="not a READ_SNAPSHOT"):
        manager.read_snapshot_binding(worktree)

    read = _request(manager, kind=WorkspaceKind.READ_SNAPSHOT)
    allocated = manager.allocate(read.workspace_id, expected_state_version=0)
    with pytest.raises(WorkspaceTransitionInvalid, match="not executable from ALLOCATED"):
        manager.read_snapshot_binding(read)

    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE workspace_state SET lifecycle_state='READY' WHERE workspace_id=?",
            (read.workspace_id,),
        )
    with pytest.raises(WorkspaceStateStale, match="no durable source identity"):
        manager.read_snapshot_binding(read)

    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE workspace_state SET lifecycle_state='ALLOCATED' WHERE workspace_id=?",
            (read.workspace_id,),
        )
    _ready, binding = manager.materialize_read_snapshot(
        read, source, expected_state_version=allocated.state_version
    )
    binding.snapshot_path.joinpath("file.txt").write_text("tampered\n", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="source identity is no longer current"):
        manager.read_snapshot_binding(read)


def test_worktree_materialization_rejects_wrong_contracts_before_registration(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager, _database = _authority(tmp_path)
    _read, binding = _ready_snapshot(manager, repository, revision=revision)

    wrong_kind = _request(manager, kind=WorkspaceKind.READ_SNAPSHOT, revision=revision)
    manager.allocate(wrong_kind.workspace_id, expected_state_version=0)
    with pytest.raises(WorkspaceTransitionInvalid, match="requires a WORKTREE"):
        manager.materialize_worktree(wrong_kind, repository, binding, expected_state_version=1)

    workspace = _request(manager, kind=WorkspaceKind.WORKTREE, revision="0" * 40)
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    with pytest.raises(WorkspaceStateStale, match="base revision does not match"):
        manager.materialize_worktree(
            workspace, repository, binding, expected_state_version=allocated.state_version
        )

    valid = _request(manager, kind=WorkspaceKind.WORKTREE, revision=revision)
    valid_allocated = manager.allocate(valid.workspace_id, expected_state_version=0)
    (manager.physical_path(valid) / "occupied").write_text("x", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="not an empty owned directory"):
        manager.materialize_worktree(
            valid,
            repository,
            binding,
            expected_state_version=valid_allocated.state_version,
        )


def test_worktree_registration_failure_restores_empty_owned_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oh_goon.engine.git import GitService

    repository, revision = _repository(tmp_path)
    manager, _database = _authority(tmp_path)
    _read, binding = _ready_snapshot(manager, repository, revision=revision)
    workspace = _request(manager, kind=WorkspaceKind.WORKTREE, revision=revision)
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    original_run = GitService.run

    def failing_run(self, *args, **kwargs):
        if "worktree" in args and "add" in args:
            result = original_run(self, "status", "--porcelain", cwd=repository)
            return result.__class__(returncode=1, stdout="", stderr="synthetic registration failure")
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(GitService, "run", failing_run)
    with pytest.raises(WorkspaceStateStale, match="Cannot register candidate Git worktree"):
        manager.materialize_worktree(
            workspace,
            repository,
            binding,
            expected_state_version=allocated.state_version,
        )

    path = manager.physical_path(workspace)
    assert path.is_dir() and list(path.iterdir()) == []
    assert manager.state(workspace.workspace_id) == allocated


def test_checkpoint_lookup_reproof_and_create_contract_failures(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager, database = _authority(tmp_path)
    workspace, ready, baseline = _ready_worktree(manager, repository, revision)

    with pytest.raises(CandidateCheckpointNotFound, match="not found"):
        manager.candidate_checkpoint("checkpoint-missing")
    assert manager.current_candidate_checkpoint(workspace, reprove=False) == baseline

    with pytest.raises(ValueError, match="attempt_id and assignment_id"):
        manager.create_candidate_checkpoint(
            workspace,
            expected_state_version=ready.state_version,
            expected_parent_checkpoint_id=baseline.checkpoint_id,
            attempt_id="",
            assignment_id="assignment",
        )

    read, _binding = _ready_snapshot(manager, repository, revision=revision)
    with pytest.raises(WorkspaceTransitionInvalid, match="require a WORKTREE"):
        manager.create_candidate_checkpoint(
            read,
            expected_state_version=manager.state(read.workspace_id).state_version,
            expected_parent_checkpoint_id=baseline.checkpoint_id,
            attempt_id="attempt",
            assignment_id="assignment",
        )

    with database.transaction(write=True) as connection:
        connection.execute(
            "UPDATE workspace_worktrees SET base_revision=? WHERE workspace_id=?",
            ("f" * 40, workspace.workspace_id),
        )
    with pytest.raises(CandidateCheckpointStale, match="durable Git base revision changed"):
        manager.current_candidate_checkpoint(workspace)


def test_current_checkpoint_rejects_workspace_without_checkpoint_and_wrong_identity(tmp_path: Path) -> None:
    manager, _database = _authority(tmp_path)
    worktree = _request(manager, kind=WorkspaceKind.WORKTREE)
    manager.allocate(worktree.workspace_id, expected_state_version=0)
    with pytest.raises(CandidateCheckpointNotFound, match="has no current checkpoint"):
        manager.current_candidate_checkpoint(worktree)

    read = _request(manager, kind=WorkspaceKind.READ_SNAPSHOT)
    with pytest.raises(WorkspaceStateStale, match="durable WORKTREE identity"):
        manager.current_candidate_checkpoint(read)


def test_transition_specialized_states_require_specialized_owners(tmp_path: Path) -> None:
    manager, _database = _authority(tmp_path)
    worktree = _request(manager, kind=WorkspaceKind.WORKTREE)
    with pytest.raises(WorkspaceTransitionInvalid, match="allocate for ALLOCATED"):
        manager.transition(
            worktree.workspace_id,
            expected_state_version=0,
            next_state=WorkspaceLifecycleState.ALLOCATED,
        )
    with pytest.raises(WorkspaceTransitionInvalid, match="release for RELEASED"):
        manager.transition(
            worktree.workspace_id,
            expected_state_version=0,
            next_state=WorkspaceLifecycleState.RELEASED,
        )

    read = _request(manager, kind=WorkspaceKind.READ_SNAPSHOT)
    manager.allocate(read.workspace_id, expected_state_version=0)
    with pytest.raises(WorkspaceTransitionInvalid, match="materialize_read_snapshot"):
        manager.transition(
            read.workspace_id,
            expected_state_version=1,
            next_state=WorkspaceLifecycleState.READY,
        )


def test_release_is_idempotent_and_rejects_replaced_owned_path(tmp_path: Path) -> None:
    manager, _database = _authority(tmp_path)
    workspace = _request(manager, kind=WorkspaceKind.WORKTREE)
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    released = manager.release(workspace.workspace_id, expected_state_version=allocated.state_version)
    assert manager.release(workspace.workspace_id, expected_state_version=released.state_version) == released

    second = _request(manager, kind=WorkspaceKind.WORKTREE)
    allocated_second = manager.allocate(second.workspace_id, expected_state_version=0)
    path = manager.physical_path(second)
    shutil.rmtree(path)
    path.write_text("not a directory", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="not a directory"):
        manager.release(second.workspace_id, expected_state_version=allocated_second.state_version)
    assert manager.state(second.workspace_id) == allocated_second

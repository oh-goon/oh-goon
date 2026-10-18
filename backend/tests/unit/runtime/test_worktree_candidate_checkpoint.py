from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import CandidateCheckpointStale, WorkspaceStateStale
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority, _worktree_git_environment




def test_worktree_git_metadata_environment_preserves_admitted_tool_path(monkeypatch) -> None:
    monkeypatch.setenv("PATH", "/toolchain")
    monkeypatch.setenv("HOME", "/host-home-must-not-leak")

    environment = _worktree_git_environment()

    assert environment["PATH"] == "/toolchain"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert environment["GIT_CONFIG_GLOBAL"]
    assert "HOME" not in environment


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
    (repository / "nested").mkdir()
    (repository / "nested" / "value.txt").write_text("base\n", encoding="utf-8")
    _git("add", ".", cwd=repository)
    _git("commit", "-qm", "base", cwd=repository)
    return repository, _git("rev-parse", "HEAD", cwd=repository)


def _manager(tmp_path: Path) -> RepositoryWorkspaceAuthority:
    return RepositoryWorkspaceAuthority(LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE"))


def _read_snapshot(manager: RepositoryWorkspaceAuthority, repository: Path, revision: str):
    read = manager.request(
        repository_id="repo-example",
        base_revision=revision,
        kind=WorkspaceKind.READ_SNAPSHOT,
    )
    allocated = manager.allocate(read.workspace_id, expected_state_version=0)
    _ready, binding = manager.materialize_read_snapshot(
        read, repository, expected_state_version=allocated.state_version
    )
    return binding


def _worktree(manager: RepositoryWorkspaceAuthority, repository: Path, revision: str, read_binding):
    workspace = manager.request(
        repository_id="repo-example",
        base_revision=revision,
        kind=WorkspaceKind.WORKTREE,
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    ready, checkpoint = manager.materialize_worktree(
        workspace,
        repository,
        read_binding,
        expected_state_version=allocated.state_version,
    )
    return workspace, ready, checkpoint


def test_worktree_materialization_is_clean_detached_and_baseline_checkpointed(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)

    workspace, ready, checkpoint = _worktree(manager, repository, revision, read_binding)
    path = manager.physical_path(workspace)

    assert ready.lifecycle_state is WorkspaceLifecycleState.READY
    assert ready.current_checkpoint_id == checkpoint.checkpoint_id
    assert checkpoint.parent_checkpoint_id is None
    assert checkpoint.changed_paths == ()
    assert checkpoint.attempt_id is None
    assert checkpoint.assignment_id is None
    assert (path / ".git").is_file()
    assert _git("rev-parse", "HEAD", cwd=path) == revision
    assert _git("status", "--porcelain", cwd=path) == ""
    assert manager.current_candidate_checkpoint(workspace) == checkpoint
    assert (repository / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_candidate_checkpoint_advances_atomically_and_records_cumulative_changed_paths(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)
    workspace, ready, baseline = _worktree(manager, repository, revision, read_binding)
    path = manager.physical_path(workspace)

    (path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (path / "new.txt").write_text("new\n", encoding="utf-8")
    state, checkpoint = manager.create_candidate_checkpoint(
        workspace,
        expected_state_version=ready.state_version,
        expected_parent_checkpoint_id=baseline.checkpoint_id,
        attempt_id="attempt-1",
        assignment_id="assignment-1",
    )

    assert state.current_checkpoint_id == checkpoint.checkpoint_id
    assert state.state_version == ready.state_version + 1
    assert checkpoint.parent_checkpoint_id == baseline.checkpoint_id
    assert checkpoint.changed_paths == ("app.py", "new.txt")
    assert checkpoint.attempt_id == "attempt-1"
    assert checkpoint.assignment_id == "assignment-1"
    assert manager.current_candidate_checkpoint(workspace) == checkpoint
    assert (repository / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_current_checkpoint_reproof_rejects_uncheckpointed_candidate_mutation(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)
    workspace, _ready, checkpoint = _worktree(manager, repository, revision, read_binding)

    (manager.physical_path(workspace) / "app.py").write_text("VALUE = hostile\n", encoding="utf-8")
    with pytest.raises(CandidateCheckpointStale, match="bytes changed after checkpoint"):
        manager.current_candidate_checkpoint(workspace)
    assert manager.candidate_checkpoint(checkpoint.checkpoint_id) == checkpoint


def test_checkpoint_parent_cas_rejects_stale_advancement_without_new_row(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)
    workspace, ready, baseline = _worktree(manager, repository, revision, read_binding)
    path = manager.physical_path(workspace)
    (path / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    state, first = manager.create_candidate_checkpoint(
        workspace,
        expected_state_version=ready.state_version,
        expected_parent_checkpoint_id=baseline.checkpoint_id,
        attempt_id="attempt-1",
        assignment_id="assignment-1",
    )
    (path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")

    with pytest.raises((CandidateCheckpointStale, WorkspaceStateStale)):
        manager.create_candidate_checkpoint(
            workspace,
            expected_state_version=ready.state_version,
            expected_parent_checkpoint_id=baseline.checkpoint_id,
            attempt_id="attempt-stale",
            assignment_id="assignment-stale",
        )
    assert manager.state(workspace.workspace_id) == state
    assert manager.candidate_checkpoint(first.checkpoint_id) == first


def test_worktree_materialization_rejects_dirty_base_source(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)
    (repository / "app.py").write_text("DIRTY\n", encoding="utf-8")
    workspace = manager.request(
        repository_id="repo-example",
        base_revision=revision,
        kind=WorkspaceKind.WORKTREE,
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)

    with pytest.raises(WorkspaceStateStale, match="must be clean"):
        manager.materialize_worktree(
            workspace,
            repository,
            read_binding,
            expected_state_version=allocated.state_version,
        )


def test_worktree_materialization_does_not_run_repository_hooks_or_smudge_filters(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    hook_marker = tmp_path / "hook-ran"
    filter_marker = tmp_path / "filter-ran"
    hooks = repository / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    post_checkout = hooks / "post-checkout"
    post_checkout.write_text(f"#!/bin/sh\necho hook > {hook_marker}\n", encoding="utf-8")
    post_checkout.chmod(0o755)
    _git("config", "filter.evil.smudge", f"sh -c 'echo filter > {filter_marker}; cat'", cwd=repository)
    (repository / ".gitattributes").write_text("*.py filter=evil\n", encoding="utf-8")
    _git("add", ".gitattributes", cwd=repository)
    _git("commit", "-qm", "attributes", cwd=repository)
    revision = _git("rev-parse", "HEAD", cwd=repository)

    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)
    _workspace, _ready, _checkpoint = _worktree(manager, repository, revision, read_binding)

    assert not hook_marker.exists()
    assert not filter_marker.exists()


def test_checkpoint_reproof_rejects_git_marker_replacement_even_when_source_bytes_match(tmp_path: Path) -> None:
    repository, revision = _repository(tmp_path)
    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)
    workspace, _ready, checkpoint = _worktree(manager, repository, revision, read_binding)
    path = manager.physical_path(workspace)

    marker = path / ".git"
    marker.write_text(marker.read_text(encoding="utf-8") + "# tamper\n", encoding="utf-8")
    with pytest.raises(CandidateCheckpointStale, match="metadata marker changed"):
        manager.current_candidate_checkpoint(workspace)
    assert manager.candidate_checkpoint(checkpoint.checkpoint_id) == checkpoint


def test_worktree_materialization_uses_owned_accelerated_copy_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.workspaces as workspaces

    repository, revision = _repository(tmp_path)
    manager = _manager(tmp_path)
    read_binding = _read_snapshot(manager, repository, revision)
    original_copy = workspaces.copy_regular_file
    copied: list[str] = []

    def record_copy(source: str | Path, destination: str | Path) -> Path:
        copied.append(Path(source).relative_to(read_binding.snapshot_path).as_posix())
        return original_copy(source, destination)

    monkeypatch.setattr(workspaces, "copy_regular_file", record_copy)
    workspace, ready, _checkpoint = _worktree(manager, repository, revision, read_binding)

    assert ready.lifecycle_state is WorkspaceLifecycleState.READY
    assert sorted(copied) == ["app.py", "nested/value.txt"]
    assert _git("status", "--porcelain", cwd=manager.physical_path(workspace)) == ""

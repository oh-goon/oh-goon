from __future__ import annotations

import subprocess
from pathlib import Path

from oh_goon.core.vocabulary.workspace import WorkspaceKind
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.execution_assignments import ExecutionAssignmentAuthority
from oh_goon.runtime.execution_materializations import ExecutionMaterializationAuthority
from oh_goon.runtime.workspace_leases import WorkspaceLeaseStore
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(("git", *args), cwd=cwd, text=True, capture_output=True, check=True).stdout.strip()


def ready_execution_materialization_candidate(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "test@example.invalid", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "a.txt").write_text("a\n")
    _git("add", "a.txt", cwd=repo)
    _git("commit", "-qm", "base", cwd=repo)
    revision = _git("rev-parse", "HEAD", cwd=repo)
    database = LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE")
    manager = RepositoryWorkspaceAuthority(database)
    read = manager.request(
        repository_id="repo-1", base_revision=revision, kind=WorkspaceKind.READ_SNAPSHOT
    )
    read_allocated = manager.allocate(read.workspace_id, expected_state_version=0)
    _read_ready, binding = manager.materialize_read_snapshot(
        read, repo, expected_state_version=read_allocated.state_version
    )
    workspace = manager.request(
        repository_id="repo-1", base_revision=revision, kind=WorkspaceKind.WORKTREE
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    ready, checkpoint = manager.materialize_worktree(
        workspace, repo, binding, expected_state_version=allocated.state_version
    )
    assignments = ExecutionAssignmentAuthority(database, manager)
    assignment = assignments.issue_candidate_mutation(
        workspace,
        attempt_id="attempt-1",
        expected_checkpoint_id=checkpoint.checkpoint_id,
        wall_time_seconds=60,
        tool_ids=("python", "git"),
        input_artifact_ids=("artifact-a", "artifact-b"),
    )
    leases = WorkspaceLeaseStore(database, workspaces=manager, assignments=assignments)
    in_use, lease = leases.acquire(
        workspace,
        assignment,
        executor_id="executor-a",
        executor_generation=7,
        expected_state_version=ready.state_version,
    )
    authority = ExecutionMaterializationAuthority(
        database, workspaces=manager, assignments=assignments, leases=leases
    )
    return database, manager, workspace, in_use, checkpoint, assignment, leases, lease, authority

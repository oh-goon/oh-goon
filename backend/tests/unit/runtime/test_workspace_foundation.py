from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.errors import WorkspaceStateStale, WorkspaceTransitionInvalid
from oh_goon.core.models.workspace import WorkspaceRef
from oh_goon.core.path_layouts import ApplicationStatePathLayout
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.runtime.database import DATABASE_SCHEMA_VERSION, LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _database(tmp_path: Path) -> LocalDatabase:
    return LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE")


def _requested(
    manager: RepositoryWorkspaceAuthority, *, kind: WorkspaceKind = WorkspaceKind.WORKTREE
) -> WorkspaceRef:
    return manager.request(
        repository_id="repo-example",
        base_revision="0123456789abcdef",
        kind=kind,
    )


def test_workspace_ref_is_immutable_identity_without_state_or_path(tmp_path: Path) -> None:
    manager = RepositoryWorkspaceAuthority(_database(tmp_path))
    reference = _requested(manager)

    assert reference.model_dump() == {
        "workspace_id": reference.workspace_id,
        "repository_id": "repo-example",
        "base_revision": "0123456789abcdef",
        "kind": WorkspaceKind.WORKTREE,
    }
    assert "lifecycle_state" not in WorkspaceRef.model_fields
    assert "state_version" not in WorkspaceRef.model_fields
    assert "path" not in WorkspaceRef.model_fields
    assert "workspace_path" not in WorkspaceRef.model_fields
    with pytest.raises(ValidationError):
        reference.repository_id = "repo-other"  # type: ignore[misc]


def test_workspace_state_is_durable_across_database_and_manager_instances(tmp_path: Path) -> None:
    path = tmp_path / "state" / "oh-goon.db"
    first = RepositoryWorkspaceAuthority(LocalDatabase(path, journal_mode="DELETE"))
    reference = _requested(first, kind=WorkspaceKind.READ_SNAPSHOT)
    allocated = first.allocate(reference.workspace_id, expected_state_version=0)
    source = tmp_path / "source"
    source.mkdir()
    (source / "file.txt").write_text("source", encoding="utf-8")
    ready, _binding = first.materialize_read_snapshot(
        reference, source, expected_state_version=allocated.state_version
    )

    second = RepositoryWorkspaceAuthority(LocalDatabase(path, journal_mode="DELETE"))
    assert second.reference(reference.workspace_id) == reference
    assert second.state(reference.workspace_id) == ready
    assert second.physical_path(reference).is_dir()


def test_workspace_state_transition_increments_version_once_and_rejects_stale_cas(
    tmp_path: Path,
) -> None:
    manager = RepositoryWorkspaceAuthority(_database(tmp_path))
    reference = _requested(manager)
    allocated = manager.allocate(reference.workspace_id, expected_state_version=0)

    assert allocated.lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    assert allocated.state_version == 1
    ready = manager.transition(
        reference.workspace_id,
        expected_state_version=1,
        next_state=WorkspaceLifecycleState.READY,
    )
    assert ready.state_version == 2

    with pytest.raises(WorkspaceStateStale, match="expected version 1, observed 2"):
        manager.transition(
            reference.workspace_id,
            expected_state_version=1,
            next_state=WorkspaceLifecycleState.FAILED,
        )
    assert manager.state(reference.workspace_id) == ready


def test_invalid_workspace_lifecycle_transition_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    manager = RepositoryWorkspaceAuthority(_database(tmp_path))
    reference = _requested(manager)

    with pytest.raises(WorkspaceTransitionInvalid, match="REQUESTED -> IN_USE"):
        manager.transition(
            reference.workspace_id,
            expected_state_version=0,
            next_state=WorkspaceLifecycleState.IN_USE,
        )
    state = manager.state(reference.workspace_id)
    assert state.lifecycle_state is WorkspaceLifecycleState.REQUESTED
    assert state.state_version == 0


def test_read_snapshot_and_worktree_get_independent_durable_identities(tmp_path: Path) -> None:
    manager = RepositoryWorkspaceAuthority(_database(tmp_path))
    read = _requested(manager, kind=WorkspaceKind.READ_SNAPSHOT)
    worktree = _requested(manager, kind=WorkspaceKind.WORKTREE)

    assert read.workspace_id != worktree.workspace_id
    assert read.repository_id == worktree.repository_id
    assert read.base_revision == worktree.base_revision
    assert manager.allocate(read.workspace_id, expected_state_version=0).state_version == 1
    assert manager.allocate(worktree.workspace_id, expected_state_version=0).state_version == 1
    assert manager.physical_path(read) != manager.physical_path(worktree)


def test_release_cleans_only_the_canonical_owned_workspace_directory(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manager = RepositoryWorkspaceAuthority(database)
    first = _requested(manager)
    second = _requested(manager)
    first_state = manager.allocate(first.workspace_id, expected_state_version=0)
    manager.allocate(second.workspace_id, expected_state_version=0)
    first_path = manager.physical_path(first)
    second_path = manager.physical_path(second)
    (first_path / "candidate.txt").write_text("candidate", encoding="utf-8")
    (second_path / "keep.txt").write_text("keep", encoding="utf-8")

    released = manager.release(first.workspace_id, expected_state_version=first_state.state_version)

    assert released.lifecycle_state is WorkspaceLifecycleState.RELEASED
    assert released.state_version == first_state.state_version + 1
    assert not first_path.exists()
    assert (second_path / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert database.path.exists()


def test_workspace_physical_path_is_not_persisted_as_parallel_identity(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manager = RepositoryWorkspaceAuthority(database)
    reference = _requested(manager)
    path = manager.physical_path(reference)

    with database.transaction() as connection:
        workspace_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(workspaces)").fetchall()
        }
        state_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(workspace_state)").fetchall()
        }
    assert "path" not in workspace_columns | state_columns
    assert "workspace_path" not in workspace_columns | state_columns
    assert str(path) not in str(manager.reference(reference.workspace_id).model_dump())


def test_workspace_root_is_separate_from_disposable_run_workspace_root(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manager = RepositoryWorkspaceAuthority(database)
    reference = _requested(manager)
    layout = ApplicationStatePathLayout.from_database(database.path)

    assert manager.physical_path(reference).parent == layout.managed_workspaces.resolve()
    assert layout.managed_workspaces != layout.run_workspaces


def test_workspace_schema_preserves_workspace_authority_tables(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    with database.transaction() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "workspaces",
        "workspace_state",
        "workspace_read_snapshots",
        "workspace_worktrees",
        "candidate_checkpoints",
    }.issubset(tables)
    assert {"execution_assignments", "workspace_leases"}.issubset(tables)


def test_packet_one_does_not_introduce_agent_specific_workspace_mirrors() -> None:
    import oh_goon.core.models.workspace as models
    import oh_goon.runtime.workspaces as runtime

    names = set(vars(models)) | set(vars(runtime))
    assert "AgentWorkspace" not in names
    assert "AgentWorktree" not in names
    assert "AgentPath" not in names

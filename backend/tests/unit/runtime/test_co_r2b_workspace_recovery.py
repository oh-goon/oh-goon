"""Real failure cuts for the original workspace owner (no new executor)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import WorkspaceStateStale
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.integrations.content_snapshot import SnapshotIdentity
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _manager(root: Path) -> RepositoryWorkspaceAuthority:
    return RepositoryWorkspaceAuthority(
        LocalDatabase(root / "state" / "oh-goon.db", journal_mode="DELETE")
    )


def _allocated(root: Path):
    manager = _manager(root)
    workspace = manager.request(
        repository_id="repo", base_revision="a" * 40, kind=WorkspaceKind.WORKTREE
    )
    state = manager.allocate(workspace.workspace_id, expected_state_version=0)
    path = manager.physical_path(workspace)
    (path / "data.txt").write_text("durable candidate", encoding="utf-8")
    return manager, workspace, state, path


def _run_child(root: Path, workspace_id: str, cut: str) -> int:
    script = """
import os, sys
from pathlib import Path
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
root, workspace_id, cut = sys.argv[1:]
owner = RepositoryWorkspaceAuthority(LocalDatabase(Path(root) / 'state' / 'oh-goon.db', journal_mode='DELETE'))
if cut == 'before_commit':
    owner._write_state = lambda *a, **kw: os._exit(69)
else:
    owner._clear_release_quarantine = lambda *a, **kw: os._exit(70)
owner.release(workspace_id, expected_state_version=1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(root), workspace_id, cut],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src")},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert not result.stderr, result.stderr
    return result.returncode


@pytest.mark.skipif(os.name != "posix", reason="process-exit crash fixture requires POSIX")
def test_crash_before_commit_recovers_original_workspace(tmp_path: Path) -> None:
    manager, workspace, state, path = _allocated(tmp_path)
    assert _run_child(tmp_path, workspace.workspace_id, "before_commit") == 69
    assert not path.exists()
    assert manager.state(workspace.workspace_id) == state
    assert manager.reconcile_release(workspace.workspace_id) == state
    assert (path / "data.txt").read_text(encoding="utf-8") == "durable candidate"
    assert not path.with_name(f".release-{path.name}").exists()


@pytest.mark.skipif(os.name != "posix", reason="process-exit crash fixture requires POSIX")
def test_crash_after_commit_reclaims_quarantine_without_restoring(tmp_path: Path) -> None:
    manager, workspace, _state, path = _allocated(tmp_path)
    assert _run_child(tmp_path, workspace.workspace_id, "after_commit") == 70
    assert not path.exists()
    assert path.with_name(f".release-{path.name}").is_dir()
    durable = manager.state(workspace.workspace_id)
    assert durable.lifecycle_state is WorkspaceLifecycleState.RELEASED
    assert manager.reconcile_release(workspace.workspace_id) == durable
    assert not path.exists()
    assert not path.with_name(f".release-{path.name}").exists()


def test_release_rollback_restores_original_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager, workspace, allocated, path = _allocated(tmp_path)

    def fail_write(*args, **kwargs):
        raise RuntimeError("injected precommit failure")

    monkeypatch.setattr(manager, "_write_state", fail_write)
    with pytest.raises(RuntimeError, match="precommit"):
        manager.release(workspace.workspace_id, expected_state_version=1)
    assert manager.state(workspace.workspace_id) == allocated
    assert (path / "data.txt").read_text(encoding="utf-8") == "durable candidate"


def test_release_refuses_active_lease_and_unregistered_git(tmp_path: Path) -> None:
    manager, workspace, allocated, path = _allocated(tmp_path)
    with manager._database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE workspace_state SET current_lease_id='live' WHERE workspace_id=?",
            (workspace.workspace_id,),
        )
    with pytest.raises(WorkspaceStateStale, match="settled lease"):
        manager.release(workspace.workspace_id, expected_state_version=1)
    assert (path / "data.txt").exists()
    with manager._database.transaction(write=True) as conn:
        conn.execute(
            "UPDATE workspace_state SET current_lease_id=NULL WHERE workspace_id=?",
            (workspace.workspace_id,),
        )
    (path / ".git").write_text("gitdir: /other/worktrees/test\n", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="native deregistration"):
        manager.release(workspace.workspace_id, expected_state_version=1)
    assert manager.state(workspace.workspace_id) == allocated


def test_reconcile_refuses_clobbering_replaced_live_path(tmp_path: Path) -> None:
    manager, workspace, state, path = _allocated(tmp_path)
    quarantine = path.with_name(f".release-{path.name}")
    manager._prepare_release_receipt(path, state)
    path.rename(quarantine)
    path.mkdir()
    (path / "replacement").write_text("foreign", encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="another path"):
        manager.reconcile_release(workspace.workspace_id)
    assert manager.state(workspace.workspace_id) == state
    assert (path / "replacement").exists()
    assert (quarantine / "data.txt").exists()


def test_reconcile_rejects_quarantine_without_durable_ownership_receipt(tmp_path: Path) -> None:
    manager, workspace, state, path = _allocated(tmp_path)
    quarantine = path.with_name(f".release-{path.name}")
    path.rename(quarantine)
    with pytest.raises(WorkspaceStateStale, match="Missing owner release receipt"):
        manager.reconcile_release(workspace.workspace_id)
    assert manager.state(workspace.workspace_id) == state
    assert (quarantine / "data.txt").exists()


def test_read_snapshot_postcommit_verification_failure_preserves_ready_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.workspaces as module

    manager = _manager(tmp_path)
    workspace = manager.request(
        repository_id="repo", base_revision="a" * 40, kind=WorkspaceKind.READ_SNAPSHOT
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    source = tmp_path / "source"
    source.mkdir()

    def snapshot(_source, destination, *, label):
        destination.mkdir()
        (destination / "file.txt").write_text("committed data", encoding="utf-8")
        return SnapshotIdentity(sha256="a" * 64, byte_count=14, entry_count=1)

    def verify(_binding, *, label):
        if label == "published READ_SNAPSHOT":
            raise RuntimeError("postcommit verification lost")

    monkeypatch.setattr(module, "snapshot_source_directory", snapshot)
    monkeypatch.setattr(module, "verify_clean_source_snapshot", verify)
    with pytest.raises(RuntimeError, match="postcommit"):
        manager.materialize_read_snapshot(
            workspace, source, expected_state_version=allocated.state_version
        )
    assert manager.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.READY
    assert (manager.physical_path(workspace) / "file.txt").read_text() == "committed data"


@pytest.mark.skipif(os.name != "posix", reason="process-exit crash fixture requires POSIX")
@pytest.mark.parametrize(
    ("cut", "exit_code", "expected_lifecycle"),
    [
        ("before_commit", 71, WorkspaceLifecycleState.REQUESTED),
        ("after_commit", 72, WorkspaceLifecycleState.ALLOCATED),
    ],
)
def test_allocation_crash_reconciles_only_original_receipt(
    tmp_path: Path, cut: str, exit_code: int, expected_lifecycle: WorkspaceLifecycleState
) -> None:
    manager = _manager(tmp_path)
    workspace = manager.request(
        repository_id="repo", base_revision="a" * 40, kind=WorkspaceKind.READ_SNAPSHOT
    )
    script = """
import os, sys
from pathlib import Path
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
root, workspace_id, cut = sys.argv[1:]
owner = RepositoryWorkspaceAuthority(LocalDatabase(Path(root) / "state" / "oh-goon.db", journal_mode="DELETE"))
if cut == "before_commit":
    owner._write_state = lambda *a, **kw: os._exit(71)
else:
    owner.reconcile_allocation = lambda *a, **kw: os._exit(72)
owner.allocate(workspace_id, expected_state_version=0)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), workspace.workspace_id, cut],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert not result.stderr, result.stderr
    assert result.returncode == exit_code
    path = manager.physical_path(workspace)
    assert path.is_dir()
    marker = path.with_name(f".allocate-{path.name}")
    assert marker.is_file()
    assert manager.state(workspace.workspace_id).lifecycle_state is expected_lifecycle
    assert (
        manager.reconcile_allocation(workspace.workspace_id).lifecycle_state is expected_lifecycle
    )
    assert not marker.exists()
    assert path.is_dir() is (cut == "after_commit")


def test_allocation_failure_rolls_back_owned_empty_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path)
    workspace = manager.request(
        repository_id="repo", base_revision="a" * 40, kind=WorkspaceKind.READ_SNAPSHOT
    )

    def fail_write(*args, **kwargs):
        raise RuntimeError("injected allocation failure")

    monkeypatch.setattr(manager, "_write_state", fail_write)
    with pytest.raises(RuntimeError, match="allocation failure"):
        manager.allocate(workspace.workspace_id, expected_state_version=0)
    assert (
        manager.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.REQUESTED
    )
    path = manager.physical_path(workspace)
    assert not path.exists()
    assert not path.with_name(f".allocate-{path.name}").exists()


def test_allocation_receipt_does_not_authorize_nonempty_foreign_tree(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    workspace = manager.request(
        repository_id="repo", base_revision="a" * 40, kind=WorkspaceKind.READ_SNAPSHOT
    )
    state = manager.state(workspace.workspace_id)
    marker = manager._allocation_marker_for_id(workspace.workspace_id)
    manager._prepare_allocation_marker(marker, state)
    path = manager.physical_path(workspace)
    path.mkdir()
    (path / "unowned.txt").write_text("must survive")
    with pytest.raises(WorkspaceStateStale, match="nonempty allocation"):
        manager.reconcile_allocation(workspace.workspace_id)
    assert (path / "unowned.txt").read_text() == "must survive"
    assert marker.exists()


def test_precommit_published_snapshot_reconciles_using_original_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.workspaces as module

    manager = _manager(tmp_path)
    workspace = manager.request(
        repository_id="repo", base_revision="a" * 40, kind=WorkspaceKind.READ_SNAPSHOT
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    source = tmp_path / "source"
    source.mkdir()

    def snapshot(_source, destination, *, label):
        destination.mkdir()
        (destination / "file.txt").write_text("exact", encoding="utf-8")
        return SnapshotIdentity(sha256="b" * 64, byte_count=5, entry_count=1)

    def verify(binding, *, label):
        if (binding.snapshot_path / "file.txt").read_text() != "exact":
            raise RuntimeError("source differs")

    monkeypatch.setattr(module, "snapshot_source_directory", snapshot)
    monkeypatch.setattr(module, "verify_clean_source_snapshot", verify)
    original_write = manager._write_state

    def fail_write(*args, **kwargs):
        raise RuntimeError("injected crash before READY commit")

    monkeypatch.setattr(manager, "_write_state", fail_write)
    with pytest.raises(RuntimeError, match="before READY"):
        manager.materialize_read_snapshot(
            workspace, source, expected_state_version=allocated.state_version
        )
    assert manager.state(workspace.workspace_id) == allocated
    path = manager.physical_path(workspace)
    assert (path / "file.txt").read_text() == "exact"
    assert manager._read_publication_marker_for_id(workspace.workspace_id).is_file()
    monkeypatch.setattr(manager, "_write_state", original_write)
    state = manager.reconcile_read_snapshot(workspace)
    assert state.lifecycle_state is WorkspaceLifecycleState.READY
    assert not manager._read_publication_marker_for_id(workspace.workspace_id).exists()
    assert (path / "file.txt").read_text() == "exact"


def test_reconcile_read_publication_tamper_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.workspaces as module

    manager = _manager(tmp_path)
    workspace = manager.request(
        repository_id="repo", base_revision="a" * 40, kind=WorkspaceKind.READ_SNAPSHOT
    )
    allocated = manager.allocate(workspace.workspace_id, expected_state_version=0)
    source = tmp_path / "source"
    source.mkdir()

    def snapshot(_source, destination, *, label):
        destination.mkdir()
        (destination / "file.txt").write_text("actual")
        return SnapshotIdentity(sha256="c" * 64, byte_count=6, entry_count=1)

    def verify(binding, *, label):
        if (binding.snapshot_path / "file.txt").read_text() != "actual":
            raise RuntimeError("source differs")

    monkeypatch.setattr(module, "snapshot_source_directory", snapshot)
    monkeypatch.setattr(module, "verify_clean_source_snapshot", verify)

    def fail_write(*args, **kwargs):
        raise RuntimeError("precommit")

    monkeypatch.setattr(manager, "_write_state", fail_write)
    with pytest.raises(RuntimeError, match="precommit"):
        manager.materialize_read_snapshot(
            workspace, source, expected_state_version=allocated.state_version
        )
    marker = manager._read_publication_marker_for_id(workspace.workspace_id)
    marker.write_text('{"owner":"wrong"}', encoding="utf-8")
    with pytest.raises(WorkspaceStateStale, match="generation or identity"):
        manager.reconcile_read_snapshot(workspace)
    assert manager.state(workspace.workspace_id) == allocated
    assert (manager.physical_path(workspace) / "file.txt").read_text() == "actual"


def test_after_commit_cleanup_error_replays_only_missing_owner_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import oh_goon.runtime.workspaces as module

    manager, workspace, allocated_state, path = _allocated(tmp_path)
    original_rmtree = module.shutil.rmtree

    def after_unlink(directory, *args, **kwargs):
        original_rmtree(directory, *args, **kwargs)
        raise OSError("injected lost fs cleanup acknowledgment")

    monkeypatch.setattr(module.shutil, "rmtree", after_unlink)
    with pytest.raises(OSError, match="acknowledgment"):
        manager.release(workspace.workspace_id, expected_state_version=1)
    assert manager.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.RELEASED
    assert not path.exists()
    receipt = manager._release_receipt_for_id(workspace.workspace_id)
    assert receipt.is_file()
    monkeypatch.setattr(module.shutil, "rmtree", original_rmtree)
    manager.reconcile_release(workspace.workspace_id)
    assert not receipt.exists()
    assert not path.exists()

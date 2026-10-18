"""Original owner, real native Git registration, real process-exit cuts.

Only the unavailable external Hashmarks identity dependency is replaced with
an exact-content test adapter. Git subprocesses, source copying, original DB
transactions and process crashes are real.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from oh_goon.core.errors import WorkspaceStateStale
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(("git", *args), cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _identity(path: Path, **_kwargs):
    # A deterministic substitute ONLY for an unavailable test Hashmarks package.
    # Git and the original workspace owner are never mocked.
    content = (path / "app.py").read_bytes()
    return SimpleNamespace(sha256=hashlib.sha256(content).hexdigest(),
                           byte_count=len(content), entry_count=1)


@pytest.fixture(autouse=True)
def _offline_identity(monkeypatch: pytest.MonkeyPatch):
    import oh_goon.integrations.hashmarks.identity as module
    monkeypatch.setattr(module, "identify_path", _identity)


def _setup(root: Path):
    source = root / "original"
    source.mkdir()
    _git("init", "-q", cwd=source)
    _git("config", "user.name", "Owner Regression", cwd=source)
    _git("config", "user.email", "test@example.invalid", cwd=source)
    (source / "app.py").write_text("VERSION = 1\n")
    _git("add", ".", cwd=source)
    _git("commit", "-qm", "source", cwd=source)
    revision = _git("rev-parse", "HEAD", cwd=source)
    owner = RepositoryWorkspaceAuthority(LocalDatabase(root / "state" / "oh-goon.db", journal_mode="DELETE"))
    read = owner.request(repository_id="repo", base_revision=revision, kind=WorkspaceKind.READ_SNAPSHOT)
    owner.allocate(read.workspace_id, expected_state_version=0)
    _ready, binding = owner.materialize_read_snapshot(read, source, expected_state_version=1)
    workspace = owner.request(repository_id="repo", base_revision=revision, kind=WorkspaceKind.WORKTREE)
    owner.allocate(workspace.workspace_id, expected_state_version=0)
    return owner, source, binding, workspace


_CHILD = r'''
import hashlib, os, sys
from pathlib import Path
from types import SimpleNamespace
from oh_goon.core.vocabulary.workspace import WorkspaceKind
from oh_goon.integrations.source_snapshot import SourceSnapshotBinding
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
import oh_goon.integrations.hashmarks.identity as hashmarks
import oh_goon.runtime.workspaces as module

def identity(path, **_kwargs):
    content = (path / 'app.py').read_bytes()
    return SimpleNamespace(sha256=hashlib.sha256(content).hexdigest(), byte_count=len(content), entry_count=1)
hashmarks.identify_path = identity
root, source_path, read_path, workspace_id, cut = sys.argv[1:]
owner = RepositoryWorkspaceAuthority(LocalDatabase(Path(root) / 'state' / 'oh-goon.db', journal_mode='DELETE'))
workspace = owner.reference(workspace_id)
identity_value = module.source_snapshot_identity(Path(read_path))
binding = SourceSnapshotBinding(snapshot_path=Path(read_path), identity=identity_value)
if cut == 'before_commit':
    owner._write_state_with_checkpoint = lambda *a, **kw: os._exit(91)
elif cut == 'after_commit':
    owner._clear_worktree_marker = lambda *a, **kw: os._exit(92)
elif cut == 'partial_git':
    module.shutil.copytree = lambda *a, **kw: os._exit(93)
owner.materialize_worktree(workspace, Path(source_path), binding, expected_state_version=1)
'''


def _child(root: Path, source: Path, read: Path, workspace_id: str, cut: str) -> int:
    result = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), str(source), str(read), workspace_id, cut],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src")},
        text=True, capture_output=True, timeout=25, check=False,
    )
    assert not result.stderr, result.stderr
    return result.returncode


@pytest.mark.skipif(os.name != "posix", reason="native Git and POSIX child-exit test")
@pytest.mark.parametrize("cut,exit_code,original_state", [
    ("before_commit", 91, WorkspaceLifecycleState.ALLOCATED),
    ("after_commit", 92, WorkspaceLifecycleState.READY),
])
def test_real_git_crash_reconciles_against_native_registration(
    tmp_path: Path, cut: str, exit_code: int, original_state: WorkspaceLifecycleState
):
    owner, source, binding, workspace = _setup(tmp_path)
    assert _child(tmp_path, source, binding.snapshot_path, workspace.workspace_id, cut) == exit_code
    marker = owner._worktree_marker_for_id(workspace.workspace_id)
    assert marker.is_file()
    assert owner.state(workspace.workspace_id).lifecycle_state is original_state
    assert str(owner.physical_path(workspace)) in _git("worktree", "list", "--porcelain", cwd=source)
    restarted = RepositoryWorkspaceAuthority(LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE"))
    recovered = restarted.reconcile_worktree(workspace)
    assert recovered.lifecycle_state is WorkspaceLifecycleState.READY
    assert not marker.exists()
    assert (restarted.physical_path(workspace) / "app.py").read_text() == "VERSION = 1\n"
    assert restarted.current_candidate_checkpoint(workspace, reprove=False)
    with pytest.raises(WorkspaceStateStale, match="native deregistration"):
        restarted.release(workspace.workspace_id, expected_state_version=recovered.state_version)


@pytest.mark.skipif(os.name != "posix", reason="native Git and POSIX child-exit test")
def test_partial_native_registration_is_preserved_not_falsely_completed(tmp_path: Path):
    owner, source, binding, workspace = _setup(tmp_path)
    assert _child(tmp_path, source, binding.snapshot_path, workspace.workspace_id, "partial_git") == 93
    marker = owner._worktree_marker_for_id(workspace.workspace_id)
    assert marker.is_file()
    assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    with pytest.raises(WorkspaceStateStale, match="incomplete or dirty|content differs"):
        owner.reconcile_worktree(workspace)
    assert marker.exists()
    assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    assert str(owner.physical_path(workspace)) in _git("worktree", "list", "--porcelain", cwd=source)


def test_tampered_receipt_does_not_authorize_ready(tmp_path: Path):
    owner, source, binding, workspace = _setup(tmp_path)
    assert _child(tmp_path, source, binding.snapshot_path, workspace.workspace_id, "before_commit") == 91
    marker = owner._worktree_marker_for_id(workspace.workspace_id)
    original = marker.read_bytes()
    marker.write_text('{"owner":"forged"}')
    with pytest.raises(WorkspaceStateStale, match="identity changed"):
        owner.reconcile_worktree(workspace)
    assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    assert owner.physical_path(workspace).exists()
    marker.write_bytes(original)
    assert owner.reconcile_worktree(workspace).lifecycle_state is WorkspaceLifecycleState.READY


def test_missing_git_marker_cannot_bypass_durable_registration_release(tmp_path: Path):
    owner, source, binding, workspace = _setup(tmp_path)
    owner.materialize_worktree(workspace, source, binding, expected_state_version=1)
    gitmarker = owner.physical_path(workspace) / ".git"
    gitmarker.unlink()
    with pytest.raises(WorkspaceStateStale, match="native deregistration"):
        owner.release(workspace.workspace_id, expected_state_version=2)
    assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.READY


def test_failed_native_add_reinstates_container_only_after_absence_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from oh_goon.engine.git import GitService

    owner, source, binding, workspace = _setup(tmp_path)
    original_run = GitService.run

    def deny_add(self, *args, **kwargs):
        if "worktree" in args and "add" in args:
            return original_run(self, "status", "--porcelain", cwd=source).__class__(
                returncode=1, stdout="", stderr="native rejection with no effect"
            )
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(GitService, "run", deny_add)
    with pytest.raises(WorkspaceStateStale, match="Cannot register candidate"):
        owner.materialize_worktree(workspace, source, binding, expected_state_version=1)
    assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    assert list(owner.physical_path(workspace).iterdir()) == []
    assert not owner._worktree_marker_for_id(workspace.workspace_id).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX receipt lock")
def test_live_git_effect_cannot_be_reconciled_concurrently(tmp_path: Path):
    owner, source, binding, workspace = _setup(tmp_path)
    state = owner.state(workspace.workspace_id)
    descriptor = owner._prepare_worktree_marker(workspace, state, source, binding.identity)
    try:
        with pytest.raises(WorkspaceStateStale, match="still in flight"):
            owner.reconcile_worktree(workspace)
        assert owner._worktree_marker_for_id(workspace.workspace_id).exists()
        assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    finally:
        os.close(descriptor)
    # The real registration never occurred; only the exact absent Git effect
    # can be restored to the original empty ALLOCATED directory.
    assert owner.reconcile_worktree(workspace).lifecycle_state is WorkspaceLifecycleState.ALLOCATED
    assert not owner._worktree_marker_for_id(workspace.workspace_id).exists()
    assert list(owner.physical_path(workspace).iterdir()) == []

"""CO-R2W3: real Git deletion/restart cuts, one original workspace owner."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import WorkspaceStateStale
from oh_goon.core.vocabulary.workspace import WorkspaceLifecycleState
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.unit.runtime.test_co_r2w2_native_git_recovery import (
    _git,
    _offline_identity,  # noqa: F401 - same deterministic identity in parent and child
    _setup,
)


def _finalized(root: Path):
    owner, source, binding, workspace = _setup(root)
    ready, _ = owner.materialize_worktree(workspace, source, binding, expected_state_version=1)
    state = owner.transition(
        workspace.workspace_id,
        expected_state_version=ready.state_version,
        next_state=WorkspaceLifecycleState.FINALIZED,
    )
    return owner, source, workspace, state


@pytest.mark.skipif(os.name != "posix", reason="requires Git/POSIX flock")
def test_original_owner_retires_real_git_and_durable_row(tmp_path: Path):
    owner, source, workspace, state = _finalized(tmp_path)
    path = owner.physical_path(workspace)
    # Normal callers use the one canonical release ingress. It delegates to
    # native retirement in the SAME owner, not a parallel cleanup path.
    released = owner.release(workspace.workspace_id, expected_state_version=state.state_version)
    assert released.lifecycle_state is WorkspaceLifecycleState.RELEASED
    assert not path.exists()
    assert str(path) not in _git("worktree", "list", "--porcelain", cwd=source)
    assert not owner._worktree_retirement_marker_for_id(workspace.workspace_id).exists()
    with owner._database.transaction() as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM workspace_worktrees WHERE workspace_id=?", (workspace.workspace_id,)
            ).fetchone()
            is None
        )


CHILD = """
import os,sys,hashlib
from types import SimpleNamespace
import oh_goon.integrations.hashmarks.identity as hashmarks
def identity(path, **_kwargs):
    content = (path / 'app.py').read_bytes()
    return SimpleNamespace(sha256=hashlib.sha256(content).hexdigest(), byte_count=len(content), entry_count=1)
hashmarks.identify_path = identity
from pathlib import Path
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
root, workspace_id, cut = sys.argv[1:]
owner = RepositoryWorkspaceAuthority(LocalDatabase(Path(root) / 'state' / 'oh-goon.db', journal_mode='DELETE'))
if cut == 'after_native':
    owner._reconcile_retirement_locked = lambda *args, **kwargs: os._exit(102)
elif cut == 'after_commit':
    owner._clear_retirement_marker = lambda *args, **kwargs: os._exit(103)
elif cut == 'before_native':
    import oh_goon.runtime.workspaces as workspaces
    original_run = workspaces.GitService.run
    def intercepted(self, *args, **kwargs):
        if args[:2] == ('worktree','remove'):
            os._exit(101)
        return original_run(self, *args, **kwargs)
    workspaces.GitService.run = intercepted
workspace = owner.reference(workspace_id)
owner.retire_worktree(workspace, expected_state_version=owner.state(workspace_id).state_version)
"""


def _child(root: Path, workspace_id: str, cut: str):
    result = subprocess.run(
        [sys.executable, "-I", "-c", CHILD, str(root), workspace_id, cut],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert not result.stderr, result.stderr
    return result.returncode


@pytest.mark.skipif(os.name != "posix", reason="requires native Git/POSIX flock")
@pytest.mark.parametrize(
    "cut,code,expected",
    [
        ("after_native", 102, WorkspaceLifecycleState.FINALIZED),
        ("after_commit", 103, WorkspaceLifecycleState.RELEASED),
    ],
)
def test_git_removal_process_death_replays_only_positive_absence(
    tmp_path: Path, cut, code, expected
):
    owner, source, workspace, _state = _finalized(tmp_path)
    path = owner.physical_path(workspace)
    assert _child(tmp_path, workspace.workspace_id, cut) == code
    assert owner.state(workspace.workspace_id).lifecycle_state is expected
    assert not path.exists()
    assert str(path) not in _git("worktree", "list", "--porcelain", cwd=source)
    receipt = owner._worktree_retirement_marker_for_id(workspace.workspace_id)
    assert receipt.exists()
    with pytest.raises(WorkspaceStateStale, match="unresolved Git retirement"):
        owner.release(
            workspace.workspace_id,
            expected_state_version=owner.state(workspace.workspace_id).state_version,
        )
    restarted = RepositoryWorkspaceAuthority(
        LocalDatabase(tmp_path / "state" / "oh-goon.db", journal_mode="DELETE")
    )
    recovered = restarted.reconcile_worktree_retirement(workspace)
    assert recovered.lifecycle_state is WorkspaceLifecycleState.RELEASED
    assert not receipt.exists()


@pytest.mark.skipif(os.name != "posix", reason="requires native Git/POSIX flock")
def test_death_before_native_effect_does_not_falsely_release(tmp_path: Path):
    owner, source, workspace, _state = _finalized(tmp_path)
    assert _child(tmp_path, workspace.workspace_id, "before_native") == 101
    with pytest.raises(WorkspaceStateStale, match="registration or bytes remain"):
        owner.reconcile_worktree_retirement(workspace)
    assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.FINALIZED
    assert owner._worktree_retirement_marker_for_id(workspace.workspace_id).exists()
    assert str(owner.physical_path(workspace)) in _git(
        "worktree", "list", "--porcelain", cwd=source
    )


def test_retirement_is_denied_before_side_effects_for_dirty_or_wrong_state(tmp_path: Path):
    owner, source, workspace, state = _finalized(tmp_path)
    path = owner.physical_path(workspace)
    (path / "app.py").write_text("tampered\n")
    with pytest.raises(WorkspaceStateStale, match="dirty or changed"):
        owner.retire_worktree(workspace, expected_state_version=state.state_version)
    assert path.is_dir()
    assert not owner._worktree_retirement_marker_for_id(workspace.workspace_id).exists()


def test_retirement_cannot_use_settled_lease_as_independent_writer_proof(tmp_path: Path):
    owner, source, workspace, state = _finalized(tmp_path)
    with owner._database.transaction(write=True) as conn:
        # Use a real row shape to prove that even a settled/local fenced lease
        # cannot independently authorize destructive native Git removal.
        conn.execute(
            """INSERT INTO execution_assignments(
            assignment_id, attempt_id, workspace_id, input_checkpoint_id,
            assignment_fingerprint, payload_json, created_at
        ) VALUES (?,?,?,?,?,?,?)""",
            (
                "b",
                "a",
                workspace.workspace_id,
                state.current_checkpoint_id,
                "c",
                "{}",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        conn.execute(
            """INSERT INTO workspace_leases(
            lease_id, workspace_id, attempt_id, assignment_id, assignment_fingerprint,
            executor_id, executor_generation, input_checkpoint_id, lease_generation,
            issued_at, expires_at, status, writer_authority_kind, writer_authority_id,
            fence_evidence_json, finished_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,'RELEASED','process_group','observer',?,?)""",
            (
                "lease-witness",
                workspace.workspace_id,
                "a",
                "b",
                "c",
                "executor",
                1,
                state.current_checkpoint_id,
                1,
                "2026-01-01T00:00:00+00:00",
                "2026-01-02T00:00:00+00:00",
                '{"quiescent":true}',
                "2026-01-02T00:00:00+00:00",
            ),
        )
    with pytest.raises(WorkspaceStateStale, match="independent writer absence"):
        owner.retire_worktree(workspace, expected_state_version=state.state_version)
    assert owner.physical_path(workspace).exists()


def test_retirement_refuses_tampered_git_marker(tmp_path: Path):
    owner, source, workspace, state = _finalized(tmp_path)
    path = owner.physical_path(workspace)
    (path / ".git").unlink()
    with pytest.raises(WorkspaceStateStale, match="marker differs"):
        owner.retire_worktree(workspace, expected_state_version=state.state_version)
    assert path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX native receipt lock")
def test_live_native_retirement_receipt_excludes_other_reconcilers(tmp_path: Path):
    from oh_goon.engine.git import GitService

    owner, source, workspace, state = _finalized(tmp_path)
    payload = owner._worktree_retirement_preflight(
        workspace, expected_state_version=state.state_version, git=GitService()
    )
    descriptor = owner._prepare_retirement_marker(workspace.workspace_id, payload)
    try:
        with pytest.raises(WorkspaceStateStale, match="still in flight"):
            owner.reconcile_worktree_retirement(workspace)
        assert owner.state(workspace.workspace_id) == state
    finally:
        os.close(descriptor)
    assert owner._worktree_retirement_marker_for_id(workspace.workspace_id).exists()


def test_native_nonzero_after_effect_requires_owner_recovery(tmp_path: Path, monkeypatch):
    from oh_goon.engine.command import CommandResult
    from oh_goon.engine.git import GitService

    owner, source, workspace, state = _finalized(tmp_path)
    original = GitService.run

    def fail_ack(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args[:2] == ("worktree", "remove"):
            return CommandResult(returncode=1, stdout="", stderr="lost ACK")
        return result

    monkeypatch.setattr(GitService, "run", fail_ack)
    with pytest.raises(WorkspaceStateStale, match="unresolved"):
        owner.retire_worktree(workspace, expected_state_version=state.state_version)
    assert owner.state(workspace.workspace_id) == state
    assert owner._worktree_retirement_marker_for_id(workspace.workspace_id).exists()
    assert (
        owner.reconcile_worktree_retirement(workspace).lifecycle_state
        is WorkspaceLifecycleState.RELEASED
    )


@pytest.mark.skipif(os.name != "posix", reason="native Git crash fixture")
def test_missing_native_registration_does_not_authorize_foreign_replacement(tmp_path: Path):
    owner, source, workspace, state = _finalized(tmp_path)
    assert _child(tmp_path, workspace.workspace_id, "after_native") == 102
    path = owner.physical_path(workspace)
    path.mkdir()
    (path / "foreign.txt").write_text("foreign-owned bytes")
    with pytest.raises(WorkspaceStateStale, match="registration or bytes remain"):
        owner.reconcile_worktree_retirement(workspace)
    assert owner.state(workspace.workspace_id) == state
    assert (path / "foreign.txt").read_text() == "foreign-owned bytes"


@pytest.mark.skipif(os.name != "posix", reason="native Git crash fixture")
def test_receipt_tamper_after_git_effect_cannot_publish_released(tmp_path: Path):
    owner, source, workspace, state = _finalized(tmp_path)
    assert _child(tmp_path, workspace.workspace_id, "after_native") == 102
    receipt = owner._worktree_retirement_marker_for_id(workspace.workspace_id)
    receipt.write_text('{"owner":"foreign"}')
    with pytest.raises(WorkspaceStateStale, match="identity or generation"):
        owner.reconcile_worktree_retirement(workspace)
    assert receipt.exists()
    assert owner.state(workspace.workspace_id) == state

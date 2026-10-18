"""CO-R3A: original domain owners against real SQLite modes and native Git.

These are developer-native, process-exit and provider tests. They DO NOT
certify external signer custody, physical LACE or kernel disk fault behavior.
No second persistence implementation, recovery owner or provider selector exists.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from oh_goon.core.errors import WorkspaceStateStale
from oh_goon.core.vocabulary.workspace import WorkspaceKind, WorkspaceLifecycleState
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
from tests.unit.runtime.test_co_r2w2_native_git_recovery import (  # noqa: F401
    _git,
    _offline_identity,
)

# All children call the original RepositoryWorkspaceAuthority.request operation.
# The test-only transaction wrapper forces a *real OS exit before SQLite COMMIT*;
# it never introduces an alternative business write path.
_REQUEST_CHILD = r"""
import os, sys
from contextlib import contextmanager
from pathlib import Path
from oh_goon.core.vocabulary.workspace import WorkspaceKind
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
path, mode, ident, cut = sys.argv[1:]
db = LocalDatabase(Path(path), journal_mode=mode)
owner = RepositoryWorkspaceAuthority(db)
if cut == 'before_commit':
    original = db.transaction
    @contextmanager
    def interrupted_transaction(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            yield connection
            if kwargs.get('write') is True:
                os._exit(181)
    db.transaction = interrupted_transaction
owner.request(repository_id='r3-source', base_revision='HEAD',
              kind=WorkspaceKind.READ_SNAPSHOT, workspace_id=ident)
os._exit(182)
"""


def _restart(tmp_path: Path, mode: str, workspace_id: str, cut: str) -> int:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            _REQUEST_CHILD,
            str(tmp_path / "state" / "source.db"),
            mode,
            workspace_id,
            cut,
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert result.stderr == "", result.stderr
    return result.returncode


@pytest.mark.skipif(os.name != "posix", reason="native SQLite process-exit witness")
@pytest.mark.parametrize("mode", ["DELETE", "WAL"])
@pytest.mark.parametrize(
    "cut,exit_code,committed",
    [
        ("before_commit", 181, False),
        ("after_commit", 182, True),
    ],
)
def test_original_workspace_request_has_one_durable_answer_after_process_death(
    tmp_path: Path,
    mode: str,
    cut: str,
    exit_code: int,
    committed: bool,
) -> None:
    path = tmp_path / "state" / "source.db"
    try:
        db = LocalDatabase(path, journal_mode=mode)
    except RuntimeError as exc:
        if mode == "WAL" and "not admitted" in str(exc):
            pytest.skip("WAL provider not admitted on this SQLite/filesystem; no fallback")
        raise
    assert db.journal_mode == mode
    ident = "co-r3-native-request"
    assert _restart(tmp_path, mode, ident, cut) == exit_code
    restarted = RepositoryWorkspaceAuthority(LocalDatabase(path, journal_mode=mode))
    with restarted._database.transaction() as connection:
        records = connection.execute(
            "SELECT lifecycle_state, state_version FROM workspace_state WHERE workspace_id=?",
            (ident,),
        ).fetchall()
        assert len(records) == int(committed)
        if committed:
            assert tuple(records[0]) == ("REQUESTED", 0)
    if committed:
        with pytest.raises(WorkspaceStateStale, match="already allocated"):
            restarted.request(
                repository_id="r3-source",
                base_revision="HEAD",
                kind=WorkspaceKind.READ_SNAPSHOT,
                workspace_id=ident,
            )
    else:
        new = restarted.request(
            repository_id="r3-source",
            base_revision="HEAD",
            kind=WorkspaceKind.READ_SNAPSHOT,
            workspace_id=ident,
        )
        assert restarted.reference(new.workspace_id) == new
    with restarted._database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM workspaces WHERE workspace_id=?", (ident,)
            ).fetchone()[0]
            == 1
        )


def _real_git_workspace(root: Path, mode: str):
    source = root / "source"
    source.mkdir()
    _git("init", "-q", cwd=source)
    _git("config", "user.name", "R3 Native Conformance", cwd=source)
    _git("config", "user.email", "r3@example.invalid", cwd=source)
    (source / "app.py").write_text("VERSION = 1\n", encoding="utf-8")
    _git("add", ".", cwd=source)
    _git("commit", "-qm", "original", cwd=source)
    revision = _git("rev-parse", "HEAD", cwd=source)
    owner = RepositoryWorkspaceAuthority(
        LocalDatabase(root / "state" / "source.db", journal_mode=mode)
    )
    snapshot = owner.request(
        repository_id="r3", base_revision=revision, kind=WorkspaceKind.READ_SNAPSHOT
    )
    owner.allocate(snapshot.workspace_id, expected_state_version=0)
    _, binding = owner.materialize_read_snapshot(snapshot, source, expected_state_version=1)
    workspace = owner.request(
        repository_id="r3", base_revision=revision, kind=WorkspaceKind.WORKTREE
    )
    owner.allocate(workspace.workspace_id, expected_state_version=0)
    ready, _ = owner.materialize_worktree(workspace, source, binding, expected_state_version=1)
    finalized = owner.transition(
        workspace.workspace_id,
        expected_state_version=ready.state_version,
        next_state=WorkspaceLifecycleState.FINALIZED,
    )
    return owner, source, workspace, finalized


@pytest.mark.skipif(os.name != "posix", reason="real native Git and POSIX custody lock")
@pytest.mark.parametrize("mode", ["DELETE", "WAL"])
def test_native_git_effect_with_lost_ack_preserves_one_owner_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    try:
        owner, source, workspace, state = _real_git_workspace(tmp_path, mode)
    except RuntimeError as exc:
        if mode == "WAL" and "not admitted" in str(exc):
            pytest.skip("WAL provider not admitted on this SQLite/filesystem; no fallback")
        raise
    target = owner.physical_path(workspace)
    real_run = GitService.run

    def lose_ack_after_real_effect(self, *args, **kwargs):
        result = real_run(self, *args, **kwargs)
        if args[:2] == ("worktree", "remove"):
            assert result.returncode == 0, result.stderr
            return CommandResult(returncode=1, stdout="", stderr="effect committed; response lost")
        return result

    monkeypatch.setattr(GitService, "run", lose_ack_after_real_effect)
    with pytest.raises(WorkspaceStateStale, match="unresolved"):
        owner.release(workspace.workspace_id, expected_state_version=state.state_version)
    assert owner.state(workspace.workspace_id).lifecycle_state is WorkspaceLifecycleState.FINALIZED
    assert not target.exists()
    assert str(target) not in _git("worktree", "list", "--porcelain", cwd=source)
    assert owner._worktree_retirement_marker_for_id(workspace.workspace_id).is_file()
    with pytest.raises(WorkspaceStateStale, match="unresolved"):
        owner.release(workspace.workspace_id, expected_state_version=state.state_version)
    restarted = RepositoryWorkspaceAuthority(
        LocalDatabase(tmp_path / "state" / "source.db", journal_mode=mode)
    )
    recovered = restarted.reconcile_worktree_retirement(workspace)
    assert recovered.lifecycle_state is WorkspaceLifecycleState.RELEASED
    assert not restarted._worktree_retirement_marker_for_id(workspace.workspace_id).exists()
    assert restarted.state(workspace.workspace_id) == recovered
    with restarted._database.transaction() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workspace_worktrees WHERE workspace_id=?",
                (workspace.workspace_id,),
            ).fetchone()[0]
            == 0
        )


def test_unsupported_provider_refuses_before_database_publication(tmp_path: Path) -> None:
    path = tmp_path / "state" / "source.db"
    with pytest.raises(RuntimeError, match="must be AUTO, DELETE, or WAL"):
        LocalDatabase(path, journal_mode="UNREGISTERED_PROVIDER")
    assert not path.exists()


_RETIRE_CHILD = r"""
import os, sys, hashlib
from pathlib import Path
from types import SimpleNamespace
import oh_goon.integrations.hashmarks.identity as hashmarks
def identity(path, **_kwargs):
    data = (path / 'app.py').read_bytes()
    return SimpleNamespace(sha256=hashlib.sha256(data).hexdigest(),
                           byte_count=len(data), entry_count=1)
hashmarks.identify_path = identity  # deterministic adapter only; Git/SQLite are real
from oh_goon.runtime.database import LocalDatabase
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority
root, mode, workspace_id = sys.argv[1:]
owner = RepositoryWorkspaceAuthority(LocalDatabase(Path(root) / 'state' / 'source.db', journal_mode=mode))
owner._reconcile_retirement_locked = lambda *args, **kwargs: os._exit(183)
workspace = owner.reference(workspace_id)
owner.retire_worktree(workspace, expected_state_version=owner.state(workspace_id).state_version)
"""


@pytest.mark.skipif(
    os.name != "posix", reason="requires original native Git effect and process exit"
)
@pytest.mark.parametrize("mode", ["DELETE", "WAL"])
def test_native_death_then_foreign_replacement_cannot_publish_release(
    tmp_path: Path,
    mode: str,
) -> None:
    try:
        owner, source, workspace, state = _real_git_workspace(tmp_path, mode)
    except RuntimeError as exc:
        if mode == "WAL" and "not admitted" in str(exc):
            pytest.skip("WAL not admitted; native provider mode remains unqualified")
        raise
    target = owner.physical_path(workspace)
    result = subprocess.run(
        [sys.executable, "-c", _RETIRE_CHILD, str(tmp_path), mode, workspace.workspace_id],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[3] / "src")},
        capture_output=True,
        text=True,
        check=False,
        timeout=25,
    )
    assert result.stderr == "", result.stderr
    assert result.returncode == 183
    assert owner.state(workspace.workspace_id) == state
    assert not target.exists()
    assert str(target) not in _git("worktree", "list", "--porcelain", cwd=source)
    target.mkdir()
    (target / "foreign").write_text("do not remove foreign replacement", encoding="utf-8")
    restarted = RepositoryWorkspaceAuthority(
        LocalDatabase(tmp_path / "state" / "source.db", journal_mode=mode)
    )
    with pytest.raises(WorkspaceStateStale, match="registration or bytes remain"):
        restarted.reconcile_worktree_retirement(workspace)
    assert restarted.state(workspace.workspace_id) == state
    assert (target / "foreign").read_text(encoding="utf-8") == "do not remove foreign replacement"
    assert restarted._worktree_retirement_marker_for_id(workspace.workspace_id).is_file()

"""CO-R2W3 original owner mutation guards, not a parallel Git implementation."""

from pathlib import Path

import pytest
from tests.factories.x0_architecture import replace_python_syntax
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _WORKSPACE,
    check_co_r01_lifecycle_edges,
)


def test_retirement_original_owner_edges_are_unchanged(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    "old,new",
    [
        (
            "return self.retire_worktree(\n                    durable, expected_state_version=expected_state_version,",
            "return self._unreceipted_release(\n                    durable, expected_state_version=expected_state_version,",
        ),
        (
            "descriptor = self._prepare_retirement_marker(workspace.workspace_id, payload)",
            "descriptor = self._skip_retirement_receipt(workspace.workspace_id, payload)",
        ),
        (
            "return self._reconcile_retirement_locked(workspace, git=service)",
            "return self._fake_release_worktree(workspace, git=service)",
        ),
        (
            "registered = self._registered_git_worktree(git, source, path)",
            "registered = False  # no native roster",
        ),
        (
            'connection.execute("DELETE FROM workspace_worktrees WHERE workspace_id=?", (workspace.workspace_id,))',
            "pass # orphan original native registration row",
        ),
        (
            "self._require_no_ever_started_writer(connection, workspace.workspace_id)\n                row = connection.execute(",
            "pass # independent writer proof bypassed\n                row = connection.execute(",
        ),
        (
            "fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)\n            except BlockingIOError",
            "pass  # unguarded recovery\n            except BlockingIOError",
        ),
    ],
)
def test_owner_bypass_is_rejected_before_native_qualification(
    tmp_path: Path, old: str, new: str
) -> None:
    root = _fixture(tmp_path)
    path = root / _WORKSPACE
    body = path.read_text()
    # The registration lock has the same lexical sequence; mutate the
    # retirement occurrence, not the earlier canonical registration owner.
    mutated = replace_python_syntax(body, old, new, last=True)
    path.write_text(mutated)
    assert any(v.path == path for v in check_co_r01_lifecycle_edges(root))

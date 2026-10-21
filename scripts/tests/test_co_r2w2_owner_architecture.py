"""Fail-before-work mutations for CO-R2W2's original Git lifecycle authority."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_co_r01_lifecycle_architecture import _fixture

from oh_goon.repository_tooling.architecture.co_r01_lifecycle_contract import (
    _WORKSPACE,
    check_co_r01_lifecycle_edges,
)


def test_original_git_edges_are_guarded(tmp_path: Path) -> None:
    assert check_co_r01_lifecycle_edges(_fixture(tmp_path)) == []


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        (
            "receipt_fd = self._prepare_worktree_marker(",
            "receipt_fd = self._unreceipted_git_effect(",
        ),
        (
            "fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)\n            data = json.dumps",
            "pass  # live Git effect can race recovery\n            data = json.dumps",
        ),
        (
            "with self._locked_worktree_receipt(workspace.workspace_id):",
            "if True:  # removed live-effect serialization",
        ),
        (
            "if not self._registered_git_worktree(service, source, destination):",
            "if True:  # Git registration no longer proved",
        ),
        (
            '"SELECT 1 FROM workspace_worktrees WHERE workspace_id=?",',
            '"SELECT 1 FROM unrelated_table WHERE workspace_id=?",',
        ),
        (
            "self._clear_worktree_marker(workspace.workspace_id)\n            return ready, checkpoint",
            "self._unreceipted_cleanup(workspace.workspace_id)\n            return ready, checkpoint",
        ),
    ],
)
def test_bypass_detected_before_qualifier(
    tmp_path: Path, original: str, replacement: str
) -> None:
    root = _fixture(tmp_path)
    path = root / _WORKSPACE
    text = path.read_text(encoding="utf-8")
    assert text.count(original) == 1
    path.write_text(text.replace(original, replacement, 1), encoding="utf-8")
    assert any(v.path == path for v in check_co_r01_lifecycle_edges(root))

"""Native workspace subject handles survive edits and reject namespace replacement."""

from __future__ import annotations

import errno
import os

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.runtime.workspaces import RepositoryWorkspaceAuthority


def test_authorized_atomic_file_replacement_keeps_the_same_repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "code.txt").write_text("before")
    with RepositoryWorkspaceAuthority.pin_repository_subject(root) as require_current:
        replacement = root / "new-code"
        replacement.write_text("after")
        replacement.replace(root / "code.txt")
        require_current()


@pytest.mark.parametrize("subject", ["root", "git", "new_git"])
def test_a_replaced_repository_or_git_subject_cannot_become_the_next_attempt(tmp_path, subject):
    root = tmp_path / "repo"
    root.mkdir()
    if subject != "new_git":
        (root / ".git").mkdir()
    with RepositoryWorkspaceAuthority.pin_repository_subject(root) as require_current:
        if subject == "root":
            root.rename(tmp_path / "old-repo")
            root.mkdir()
            (root / ".git").mkdir()
        elif subject == "git":
            (root / ".git").rename(root / "old-git")
            (root / ".git").mkdir()
        else:
            (root / ".git").mkdir()
        with pytest.raises(FleetPlanInvalid):
            require_current()


def test_subject_handles_close_when_the_loop_raises(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    handles = []
    original_open = os.open

    def record_owned_handle(*args, **kwargs):
        handle = original_open(*args, **kwargs)
        handles.append(handle)
        return handle

    monkeypatch.setattr(os, "open", record_owned_handle)
    with pytest.raises(RuntimeError, match="interrupted"):
        with RepositoryWorkspaceAuthority.pin_repository_subject(root):
            raise RuntimeError("interrupted")
    assert handles
    for handle in handles:
        with pytest.raises(OSError) as closed:
            os.fstat(handle)
        assert closed.value.errno == errno.EBADF

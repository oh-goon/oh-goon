from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.operation import (
    DetachedGitPosition,
    NamedGitPosition,
    ValidGitWorktreeInvariant,
    ViolatedGitWorktreeInvariant,
)
from oh_goon.engine.project import project_python, read_pyproject


def test_git_position_variants_make_branch_presence_structural() -> None:
    named = NamedGitPosition(state="named", branch="main")
    detached = DetachedGitPosition(state="detached")
    assert named.branch == "main"
    assert detached.branch is None
    with pytest.raises(ValidationError):
        NamedGitPosition(state="named", branch="")
    with pytest.raises(ValidationError):
        DetachedGitPosition(state="detached", branch="main")


def test_git_worktree_invariant_makes_unexpected_files_structural() -> None:
    assert ValidGitWorktreeInvariant(state="passed").unexpected_files == ()
    with pytest.raises(ValidationError):
        ValidGitWorktreeInvariant(state="passed", unexpected_files=("uv.lock",))
    with pytest.raises(ValidationError):
        ViolatedGitWorktreeInvariant(state="failed", unexpected_files=())
    assert ViolatedGitWorktreeInvariant(
        state="failed", unexpected_files=("uv.lock",)
    ).unexpected_files == ("uv.lock",)


def test_native_project_reader_uses_none_for_absent_python_metadata(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    requires, pin = project_python(tmp_path)
    assert requires is None
    assert pin is None
    assert read_pyproject(tmp_path)["project"]["name"] == "example"

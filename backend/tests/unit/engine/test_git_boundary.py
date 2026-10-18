from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.core.vocabulary import GitBranchState, WorktreeState
from oh_goon.engine.git import GitService


def _git(repository: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repository(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    (repository / "tracked file.txt").write_text("baseline\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "baseline")
    return repository


def test_observe_collects_repository_state_with_one_git_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked file.txt").write_text("changed\n", encoding="utf-8")
    calls: list[tuple[str, ...]] = []
    original = GitService.run

    def recording_run(self: GitService, *args: str, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(GitService, "run", recording_run)
    observation = GitService().observe(repository)

    assert calls == [("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=normal")]
    assert observation.branch_state is GitBranchState.NAMED
    assert observation.branch == "main"
    assert observation.head_sha == _git(repository, "rev-parse", "HEAD")
    assert observation.worktree_state is WorktreeState.DIRTY
    assert observation.changed_files == ("tracked file.txt",)


def test_observe_reports_rename_destination_without_quoted_path_artifacts(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _git(repository, "mv", "tracked file.txt", "renamed file.txt")

    observation = GitService().observe(repository)

    assert observation.changed_files == ("renamed file.txt",)
    assert observation.worktree_state is WorktreeState.DIRTY


def test_observe_handles_detached_head_and_unborn_branch(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    head = _git(repository, "rev-parse", "HEAD")
    _git(repository, "checkout", "--detach", head)

    detached = GitService().observe(repository)
    assert detached.branch_state is GitBranchState.DETACHED
    assert detached.branch is None
    assert detached.head_sha == head

    unborn = tmp_path / "unborn"
    unborn.mkdir()
    _git(unborn, "init", "-q", "-b", "future")
    initial = GitService().observe(unborn)
    assert initial.branch_state is GitBranchState.NAMED
    assert initial.branch == "future"
    assert initial.head_sha is None
    assert initial.worktree_state is WorktreeState.CLEAN


def test_observe_non_repository_fails_closed_without_fake_git_state(tmp_path: Path) -> None:
    repository = tmp_path / "plain"
    repository.mkdir()

    observation = GitService().observe(repository)

    assert observation.is_repository is False
    assert observation.branch_state is GitBranchState.UNAVAILABLE
    assert observation.head_sha is None
    assert observation.worktree_state is WorktreeState.NOT_GIT
    assert observation.changed_files == ()

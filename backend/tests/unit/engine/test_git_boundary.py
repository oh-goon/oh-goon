from __future__ import annotations

import hashlib
import os
import socket
import subprocess
from pathlib import Path

import pytest

from oh_goon.core.vocabulary import GitBranchState, WorktreeState
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService


def _observed_status(*argv: str, **kwargs) -> CommandResult:
    return CommandResult(0, "# branch.oid (initial)\0# branch.head main\0", "")


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


def test_observation_uses_injected_native_process_transport(tmp_path: Path) -> None:
    calls = []

    def runner(*argv: str, **kwargs) -> CommandResult:
        calls.append((argv, kwargs))
        return CommandResult(0, "# branch.oid (initial)\0# branch.head main\0? changed\0", "")

    git = GitService(command_runner=runner)
    observation = git.observe(tmp_path)
    assert observation.changed_files == ("changed",)
    assert observation.branch == "main"
    assert calls[0][0] == (
        "git",
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=normal",
    )
    assert calls[0][1]["cwd"] == tmp_path
    assert calls[0][1]["timeout"] == git.default_timeout_seconds

    # Existing effect owners can still provide their narrower per-command runner.
    result = git.run("--version", command_runner=lambda *a, **kw: CommandResult(0, "override", ""))
    assert result.stdout == "override" and len(calls) == 1


@pytest.mark.skipif(os.name != "posix", reason="native fd-relative no-follow observation")
@pytest.mark.parametrize("confined", [False, True])
def test_repository_regular_file_fingerprints_match_for_trusted_and_scoped_observation(
    tmp_path: Path, confined: bool
) -> None:
    pyproject = b"[project]\nname='selected'\n"
    lock = b"version = 1\n"
    (tmp_path / "pyproject.toml").write_bytes(pyproject)
    (tmp_path / "uv.lock").write_bytes(lock)
    git = GitService(
        command_runner=_observed_status,
        observation_runner=_observed_status if confined else None,
    )
    observed = git.observe(tmp_path).fingerprint
    assert observed.pyproject_sha256 == hashlib.sha256(pyproject).hexdigest()
    assert observed.uv_lock_sha256 == hashlib.sha256(lock).hexdigest()


@pytest.mark.skipif(os.name != "posix", reason="native fd-relative no-follow observation")
def test_scoped_file_hashes_omit_links_without_changing_trusted_link_semantics(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    canary = tmp_path / "host-canary"
    canary.write_bytes(b"outside approved repository")
    (repository / "pyproject.toml").symlink_to(canary)
    (repository / "uv.lock").symlink_to(canary)
    trusted = GitService(command_runner=_observed_status).observe(repository).fingerprint
    scoped = GitService(observation_runner=_observed_status).observe(repository).fingerprint
    assert trusted.pyproject_sha256 == hashlib.sha256(canary.read_bytes()).hexdigest()
    assert trusted.uv_lock_sha256 == trusted.pyproject_sha256
    assert scoped.pyproject_sha256 is None and scoped.uv_lock_sha256 is None


@pytest.mark.skipif(os.name != "posix", reason="native fd-relative no-follow observation")
@pytest.mark.parametrize("kind", ["fifo", "directory", "socket"])
def test_scoped_file_hashes_do_not_read_special_leaves(tmp_path: Path, kind: str) -> None:
    leaf = tmp_path / "pyproject.toml"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
        if kind == "fifo":
            os.mkfifo(leaf)
        elif kind == "directory":
            leaf.mkdir()
        else:
            endpoint.bind(str(leaf))
        observed = GitService(observation_runner=_observed_status).observe(tmp_path).fingerprint
    assert observed.pyproject_sha256 is None and observed.uv_lock_sha256 is None


@pytest.mark.skipif(os.name != "posix", reason="native fd-relative no-follow observation")
@pytest.mark.parametrize("exchange", ["before_open", "after_open"])
def test_scoped_leaf_exchange_never_hashes_the_symlink_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exchange: str
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    leaf = repository / "pyproject.toml"
    leaf.write_bytes(b"original admitted bytes")
    canary = tmp_path / "host-canary"
    canary.write_bytes(b"outside approved repository")
    native_open = os.open
    descriptors: list[int] = []

    def exchange_leaf():
        leaf.unlink()
        leaf.symlink_to(canary)

    def open_at(path, flags, mode=0o777, *, dir_fd=None):
        if path == "pyproject.toml" and exchange == "before_open":
            exchange_leaf()
        fd = native_open(path, flags, mode, dir_fd=dir_fd)
        descriptors.append(fd)
        if path == "pyproject.toml" and exchange == "after_open":
            exchange_leaf()
        return fd

    monkeypatch.setattr(os, "open", open_at)
    observed = GitService(observation_runner=_observed_status).observe(repository).fingerprint
    assert observed.pyproject_sha256 == (
        None
        if exchange == "before_open"
        else hashlib.sha256(b"original admitted bytes").hexdigest()
    )
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.skipif(os.name != "posix", reason="native fd-relative no-follow observation")
def test_scoped_root_link_is_not_followed_for_host_fingerprints(tmp_path: Path) -> None:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "pyproject.toml").write_bytes(b"outside approved repository")
    root = tmp_path / "repository"
    root.symlink_to(foreign, target_is_directory=True)
    observed = GitService(observation_runner=_observed_status).observe(root).fingerprint
    assert observed.pyproject_sha256 is None and observed.uv_lock_sha256 is None


@pytest.mark.skipif(os.name != "posix", reason="native fd-relative no-follow observation")
def test_scoped_root_exchange_keeps_both_hashes_on_the_open_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, foreign = tmp_path / "repository", tmp_path / "foreign"
    for root, value in ((repository, b"approved"), (foreign, b"outside")):
        root.mkdir()
        for name in ("pyproject.toml", "uv.lock"):
            (root / name).write_bytes(value)
    native_open = os.open

    def exchange_root(path, flags, mode=0o777, *, dir_fd=None):
        fd = native_open(path, flags, mode, dir_fd=dir_fd)
        if path == repository:
            repository.rename(tmp_path / "held-repository")
            repository.symlink_to(foreign, target_is_directory=True)
        return fd

    monkeypatch.setattr(os, "open", exchange_root)
    observed = GitService(observation_runner=_observed_status).observe(repository).fingerprint
    expected = hashlib.sha256(b"approved").hexdigest()
    assert observed.pyproject_sha256 == expected and observed.uv_lock_sha256 == expected

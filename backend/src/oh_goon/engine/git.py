from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GitBranchState, WorktreeState
from oh_goon.engine.command import CommandCancelledError, CommandResult, run_command
from oh_goon.engine.errors import OperationCancelledError

Cancel = Callable[[], bool] | None


@dataclass(frozen=True, slots=True)
class GitObservation:
    """One coherent local Git read used for identity, invariants, and changed-file evidence."""

    fingerprint: RepositoryStateFingerprint
    changed_files: tuple[str, ...]
    tool_available: bool = True

    @property
    def head_sha(self) -> str | None:
        return self.fingerprint.head_sha

    @property
    def branch_state(self) -> GitBranchState:
        return self.fingerprint.branch_state

    @property
    def branch(self) -> str | None:
        return self.fingerprint.branch

    @property
    def worktree_state(self) -> WorktreeState:
        return self.fingerprint.worktree_state

    @property
    def is_repository(self) -> bool:
        return self.worktree_state is not WorktreeState.NOT_GIT


_DEFAULT_TIMEOUT = object()


@dataclass(frozen=True, slots=True)
class GitService:
    """Single native-Git execution and observation boundary; provider policy lives above it."""

    executable: str = "git"
    default_timeout_seconds: float = 30.0
    command_runner: Callable[..., CommandResult] | None = None
    observation_runner: Callable[..., CommandResult] | None = None

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        timeout: float | None | object = _DEFAULT_TIMEOUT,
        cancel_requested: Cancel = None,
        env: Mapping[str, str] | None = None,
        command_runner: Callable[..., CommandResult] | None = None,
    ) -> CommandResult:
        try:
            return (command_runner or self.command_runner or run_command)(
                self.executable,
                *args,
                cwd=cwd,
                timeout=(
                    self.default_timeout_seconds
                    if timeout is _DEFAULT_TIMEOUT
                    else cast(float | None, timeout)
                ),
                cancel_requested=cancel_requested,
                env=env,
            )
        except CommandCancelledError as exc:
            raise OperationCancelledError(str(exc)) from exc

    def observe(
        self,
        repository: Path,
        cancel_requested: Cancel = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> GitObservation:
        return _observe(self, repository, cancel_requested, env=env)

    def fingerprint(
        self,
        repository: Path,
        cancel_requested: Cancel = None,
    ) -> RepositoryStateFingerprint:
        return self.observe(repository, cancel_requested).fingerprint

    def root(self, repository: Path, cancel_requested: Cancel = None) -> Path | None:
        result = self.run(
            "rev-parse",
            "--show-toplevel",
            cwd=repository,
            cancel_requested=cancel_requested,
            command_runner=self.observation_runner,
        )
        return Path(result.stdout).resolve() if result.returncode == 0 and result.stdout else None


def _observe(
    git: GitService,
    repository: Path,
    cancel_requested: Cancel,
    *,
    env: Mapping[str, str] | None,
) -> GitObservation:
    confined_file_reads = git.observation_runner is not None
    if not repository.is_dir():
        return _unavailable_observation(repository, confined_file_reads=confined_file_reads)
    try:
        result = git.run(
            "status",
            "--porcelain=v2",
            "--branch",
            "-z",
            "--untracked-files=normal",
            cwd=repository,
            cancel_requested=cancel_requested,
            env=env,
            command_runner=git.observation_runner,
        )
    except OSError:
        return _unavailable_observation(
            repository, tool_available=False, confined_file_reads=confined_file_reads
        )
    if result.returncode != 0:
        return _unavailable_observation(repository, confined_file_reads=confined_file_reads)

    head_sha: str | None = None
    branch_state = GitBranchState.UNAVAILABLE
    branch_name: str | None = None
    changed: set[str] = set()
    records = result.stdout.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# branch.oid "):
            value = record.removeprefix("# branch.oid ")
            head_sha = None if value == "(initial)" else value
            continue
        if record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ")
            if value == "(detached)":
                branch_state = GitBranchState.DETACHED
                branch_name = None
            else:
                branch_state = GitBranchState.NAMED
                branch_name = value
            continue
        path, consumes_origin = _porcelain_path(record)
        if path is not None:
            changed.add(path)
        if consumes_origin and index < len(records):
            index += 1

    worktree = WorktreeState.CLEAN if not changed else WorktreeState.DIRTY
    fingerprint = _fingerprint(
        repository,
        head_sha=head_sha,
        branch_state=branch_state,
        branch_name=branch_name,
        worktree=worktree,
        confined_file_reads=confined_file_reads,
    )
    return GitObservation(fingerprint=fingerprint, changed_files=tuple(sorted(changed)))


def _porcelain_path(record: str) -> tuple[str | None, bool]:
    if record.startswith("1 "):
        fields = record.split(" ", 8)
        return (fields[8], False) if len(fields) == 9 else (None, False)
    if record.startswith("2 "):
        fields = record.split(" ", 9)
        return (fields[9], True) if len(fields) == 10 else (None, True)
    if record.startswith("u "):
        fields = record.split(" ", 10)
        return (fields[10], False) if len(fields) == 11 else (None, False)
    if record.startswith("? "):
        return record[2:], False
    return None, False


def _unavailable_observation(
    repository: Path, *, tool_available: bool = True, confined_file_reads: bool = False
) -> GitObservation:
    fingerprint = _fingerprint(
        repository,
        head_sha=None,
        branch_state=GitBranchState.UNAVAILABLE,
        branch_name=None,
        worktree=WorktreeState.NOT_GIT,
        confined_file_reads=confined_file_reads,
    )
    return GitObservation(
        fingerprint=fingerprint,
        changed_files=(),
        tool_available=tool_available,
    )


def _fingerprint(
    repository: Path,
    *,
    head_sha: str | None,
    branch_state: GitBranchState,
    branch_name: str | None,
    worktree: WorktreeState,
    confined_file_reads: bool = False,
) -> RepositoryStateFingerprint:
    pyproject, uv_lock = (
        _confined_file_fingerprints(repository)
        if confined_file_reads
        else (_sha256(repository / "pyproject.toml"), _sha256(repository / "uv.lock"))
    )
    payload = {
        "head_sha": head_sha,
        "branch_state": branch_state.value,
        "branch": branch_name,
        "worktree_state": worktree.value,
        "pyproject_sha256": pyproject,
        "uv_lock_sha256": uv_lock,
    }
    return RepositoryStateFingerprint(
        captured_at=utc_now(),
        head_sha=head_sha,
        branch_state=branch_state,
        branch=branch_name,
        worktree_state=worktree,
        pyproject_sha256=payload["pyproject_sha256"],
        uv_lock_sha256=payload["uv_lock_sha256"],
    )


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _confined_file_fingerprints(repository: Path) -> tuple[str | None, str | None]:
    """Read only the two regular leaves within the approved observation root.

    The process transport already owns workspace admission. Hold the directory
    while opening each leaf so a link exchange cannot turn this host metadata
    read into an execution proxy outside that workspace.
    """
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        return None, None
    try:
        root_fd = os.open(repository, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None, None
    try:
        return _regular_leaf_sha256(root_fd, "pyproject.toml"), _regular_leaf_sha256(
            root_fd, "uv.lock"
        )
    finally:
        os.close(root_fd)


def _regular_leaf_sha256(root_fd: int, name: str) -> str | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return None
    finally:
        os.close(fd)

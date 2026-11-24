from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from oh_goon.core.errors import StateConflict
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import GitBranchState, WorktreeState
from oh_goon.engine.command import (
    CommandCancelledError,
    CommandOutputLimitExceeded,
    CommandResult,
    run_command,
)
from oh_goon.engine.errors import OperationCancelledError

Cancel = Callable[[], bool] | None


@dataclass(frozen=True, slots=True)
class GitObservation:
    """Native Git execution witness with separately sampled legacy file digests."""

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

    def require_repository(self) -> None:
        """Reject unavailable Git evidence before a worktree invariant is accepted."""
        if not self.is_repository:
            raise StateConflict("Git worktree observation is unavailable")


_DEFAULT_TIMEOUT = object()


def workspace_metadata_git_environment(*, executable_path: str | None = None) -> dict[str, str]:
    """Resolve the native metadata environment once, or verify an explicit PATH."""
    selected = os.environ.get("PATH", os.defpath) if executable_path is None else executable_path
    if not selected or not selected.isprintable() or len(selected.encode("utf-8")) > 32768:
        raise ValueError("Native Git metadata requires a bounded captured executable PATH")
    return {
        "PATH": selected,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
        "GIT_CONFIG_KEY_1": "core.hooksPath",
        "GIT_CONFIG_VALUE_1": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
    }


def require_workspace_metadata_git_environment(environment: object) -> dict[str, str]:
    """Accept only the exact metadata policy, without another ambient resolution."""
    if not isinstance(environment, Mapping) or not all(
        type(name) is str and type(value) is str for name, value in environment.items()
    ):
        raise ValueError("Native Git metadata environment is invalid")
    values = dict(environment)
    selected = values.get("PATH")
    if not isinstance(selected, str) or values != workspace_metadata_git_environment(
        executable_path=selected
    ):
        raise ValueError("Native Git metadata environment differs from its original policy")
    return values


@dataclass(frozen=True, slots=True)
class WorkspaceMetadataGitCommandRunner:
    """Exact native lifecycle metadata owner; never an untrusted payload runner."""

    environment_items: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if len(dict(self.environment_items)) != len(self.environment_items):
            raise ValueError("Native Git metadata environment has duplicate fields")
        require_workspace_metadata_git_environment(dict(self.environment_items))

    @property
    def environment(self) -> dict[str, str]:
        return dict(self.environment_items)

    def __call__(self, *argv: str, **options: Any) -> CommandResult:
        options.pop("env", None)
        return run_command(*argv, env=self.environment, **options)


@dataclass(frozen=True, slots=True)
class GitService:
    """Single native-Git execution and observation boundary; provider policy lives above it."""

    executable: str = "git"
    default_timeout_seconds: float = 30.0
    command_runner: Callable[..., CommandResult] | None = None
    observation_runner: Callable[..., CommandResult] | None = None
    metadata_only: bool = False

    def run(
        self,
        *args: str,
        cwd: Path | None = None,
        timeout: float | None | object = _DEFAULT_TIMEOUT,
        cancel_requested: Cancel = None,
        env: Mapping[str, str] | None = None,
        command_runner: Callable[..., CommandResult] | None = None,
        max_capture_bytes_per_stream: int | None = None,
    ) -> CommandResult:
        if self.metadata_only and args and args[0] != "config":
            self.require_metadata_safety(cwd, env=env)
        try:
            runner: Callable[..., CommandResult] = (
                command_runner or self.command_runner or run_command
            )
            capture_options = (
                {"max_capture_bytes_per_stream": max_capture_bytes_per_stream}
                if max_capture_bytes_per_stream is not None
                else {}
            )
            return runner(
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
                **capture_options,
            )
        except CommandCancelledError as exc:
            raise OperationCancelledError(str(exc)) from exc

    def require_metadata_safety(
        self, repository: Path | None, *, env: Mapping[str, str] | None = None
    ) -> None:
        """Refuse source-code filters before a trusted lifecycle metadata command.

        Git itself resolves configuration and reports only matching key names.
        Disabling filters would change native cleanliness semantics, so these
        repositories remain unsupported by the metadata-only lifecycle.
        """
        result = self.run(
            "config",
            "--null",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(clean|process)$",
            ".",
            cwd=repository,
            env=env,
        )
        if result.returncode == 1 and not result.stdout and not result.stdout_truncated:
            return
        raise StateConflict(
            "Workspace metadata cannot execute repository clean/process filters; "
            "native clean-source evidence is unavailable"
        )

    def observe(
        self,
        repository: Path,
        cancel_requested: Cancel = None,
        *,
        env: Mapping[str, str] | None = None,
        full_untracked_inventory: bool = False,
    ) -> GitObservation:
        return _observe(
            self,
            repository,
            cancel_requested,
            env=env,
            full_untracked_inventory=full_untracked_inventory,
        )

    def require_no_ignored_files(
        self, repository: Path, *, env: Mapping[str, str] | None = None
    ) -> None:
        """Let native Git prove that removal would discard no ignored files.

        Source identity deliberately excludes some runtime output. Neither a
        clean status nor a source checkpoint makes those bytes disposable.
        """
        try:
            result = self.run(
                "ls-files",
                "--others",
                "--ignored",
                "--exclude-standard",
                "-z",
                cwd=repository,
                env=env,
                command_runner=self.observation_runner,
                max_capture_bytes_per_stream=8192,
            )
        except CommandOutputLimitExceeded as error:
            raise StateConflict("Native Git ignored-output inventory is incomplete") from error
        if result.returncode != 0 or result.stdout_truncated or result.stderr:
            raise StateConflict("Native Git ignored-output inventory is incomplete")
        if result.stdout:
            raise StateConflict("Candidate contains ignored output; preserve it for review")

    def fingerprint(
        self,
        repository: Path,
        cancel_requested: Cancel = None,
    ) -> RepositoryStateFingerprint:
        return self.observe(repository, cancel_requested).fingerprint

    def root(
        self,
        repository: Path,
        cancel_requested: Cancel = None,
        *,
        env: Mapping[str, str] | None = None,
    ) -> Path | None:
        result = self.run(
            "rev-parse",
            "--show-toplevel",
            cwd=repository,
            cancel_requested=cancel_requested,
            env=env,
            command_runner=self.observation_runner,
        )
        return Path(result.stdout).resolve() if result.returncode == 0 and result.stdout else None


def _observe(
    git: GitService,
    repository: Path,
    cancel_requested: Cancel,
    *,
    env: Mapping[str, str] | None,
    full_untracked_inventory: bool,
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
            "--untracked-files=all" if full_untracked_inventory else "--untracked-files=normal",
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
    if result.stdout_truncated or not result.stdout.endswith("\0"):
        raise StateConflict("Git worktree observation is incomplete")

    head_sha: str | None = None
    branch_state = GitBranchState.UNAVAILABLE
    branch_name: str | None = None
    changed: set[str] = set()
    headers: set[str] = set()
    records = result.stdout.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            if index == len(records):
                continue
            raise StateConflict("Git worktree observation contains an empty record")
        if record.startswith("# branch.oid "):
            value = record.removeprefix("# branch.oid ")
            if "oid" in headers or (
                value != "(initial)" and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None
            ):
                raise StateConflict("Git worktree observation has invalid branch identity")
            headers.add("oid")
            head_sha = None if value == "(initial)" else value
            continue
        if record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ")
            if "head" in headers or not value:
                raise StateConflict("Git worktree observation has invalid branch position")
            headers.add("head")
            if value == "(detached)":
                branch_state = GitBranchState.DETACHED
                branch_name = None
            else:
                branch_state = GitBranchState.NAMED
                branch_name = value
            continue
        if record.startswith("# "):
            # Porcelain v2 permits consumers to ignore additional branch headers.
            continue
        path, origin_kind = _porcelain_path(record)
        changed.add(path)
        if origin_kind is not None:
            if index >= len(records) or not records[index]:
                raise StateConflict("Git worktree observation has an incomplete rename or copy")
            if origin_kind == "R":
                changed.add(records[index])
            index += 1

    if headers != {"oid", "head"}:
        raise StateConflict("Git worktree observation is missing branch evidence")
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


def _porcelain_path(record: str) -> tuple[str, str | None]:
    if record.startswith("1 "):
        fields = record.split(" ", 8)
        if len(fields) == 9 and all(fields):
            return fields[8], None
    elif record.startswith("2 "):
        fields = record.split(" ", 9)
        if len(fields) == 10 and all(fields) and re.fullmatch(r"[RC][0-9]+", fields[8]) is not None:
            return fields[9], fields[8][0]
    elif record.startswith("u "):
        fields = record.split(" ", 10)
        if len(fields) == 11 and all(fields):
            return fields[10], None
    elif record.startswith("? ") and record[2:]:
        return record[2:], None
    raise StateConflict("Git worktree observation contains an unsupported or malformed record")


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

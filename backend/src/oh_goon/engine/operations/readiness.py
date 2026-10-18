from __future__ import annotations

from pathlib import Path

from oh_goon import __version__
from oh_goon.core.models.operation import (
    AvailableToolVersion,
    ComparedGitRemote,
    GitRemoteComparisonUnavailable,
    GitRemoteLookupUnavailable,
    GitRemoteWithoutUpstream,
    MissingToolVersion,
    MissingUvTool,
    NoGitRemote,
    PythonRuntimeResult,
    ReadinessChecks,
    ToolchainResult,
    ToolVersionResult,
    ReadyUvTool,
    UnavailableToolVersion,
    RejectedUvProject,
)
from oh_goon.core.models.policy import PolicySnapshot
from oh_goon.core.vocabulary import (
    LockPresenceState,
    PythonPinReadiness,
)
from oh_goon.engine.git import GitObservation, GitService
from oh_goon.engine.operations.command_execution import Cancel, run_operation_command
from oh_goon.engine.operations.git_evidence import git_position
from oh_goon.engine.project import project_python


def readiness(
    repository: Path, policy: PolicySnapshot, git: GitService, cancel_requested: Cancel = None
) -> tuple[
    ReadinessChecks,
    PythonRuntimeResult,
    ToolchainResult,
    GitObservation,
]:
    observation = git.observe(repository, cancel_requested)
    tools = _toolchain(repository, observation, cancel_requested)
    branch_state, branch_name = observation.branch_state, observation.branch
    worktree = observation.worktree_state
    requires, pin = project_python(repository)
    pin_state = (
        PythonPinReadiness.PRESENT
        if pin
        else PythonPinReadiness.REQUIRED_MISSING
        if policy.values.require_python_pin
        else PythonPinReadiness.OPTIONAL
    )
    remote = (
        _remote_observation(repository, git, cancel_requested)
        if observation.tool_available
        else GitRemoteLookupUnavailable(state="lookup_unavailable")
    )
    checks = ReadinessChecks(
        project="present" if (repository / "pyproject.toml").is_file() else "missing",
        working_tree=worktree,
        position=git_position(branch_state, branch_name),
        remote=remote,
        uv_lock=(
            LockPresenceState.PRESENT
            if (repository / "uv.lock").is_file()
            else LockPresenceState.MISSING
        ),
        python_pin=pin_state,
    )
    return (
        checks,
        PythonRuntimeResult(requires=requires, pin=pin, selected=None),
        tools,
        observation,
    )


def _remote_observation(
    repository: Path,
    git: GitService,
    cancel_requested: Cancel,
):
    remotes = git.run("remote", cwd=repository, cancel_requested=cancel_requested)
    if remotes.returncode != 0:
        return GitRemoteLookupUnavailable(state="lookup_unavailable")
    names = sorted({line.strip() for line in remotes.stdout.splitlines() if line.strip()})
    if not names:
        return NoGitRemote(state="no_remote")
    upstream = git.run(
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
        cwd=repository,
        cancel_requested=cancel_requested,
    )
    if upstream.returncode != 0 or not upstream.stdout:
        return GitRemoteWithoutUpstream(state="no_upstream", remote_names=tuple(names))
    upstream_name = upstream.stdout.strip()
    remote = upstream_name.split("/", 1)[0]
    counts = git.run(
        "rev-list",
        "--left-right",
        "--count",
        f"HEAD...{upstream_name}",
        cwd=repository,
        cancel_requested=cancel_requested,
    )
    if counts.returncode != 0:
        return GitRemoteComparisonUnavailable(
            state="comparison_unavailable", remote_name=remote, upstream=upstream_name
        )
    try:
        ahead, behind = (int(value) for value in counts.stdout.split())
    except (TypeError, ValueError):
        return GitRemoteComparisonUnavailable(
            state="comparison_unavailable", remote_name=remote, upstream=upstream_name
        )
    state = (
        "diverged"
        if ahead and behind
        else "ahead"
        if ahead
        else "behind"
        if behind
        else "up_to_date"
    )
    return ComparedGitRemote(state=state, remote_name=remote, upstream=upstream_name)


def _toolchain(
    repository: Path,
    git_observation: GitObservation,
    cancel_requested: Cancel,
) -> ToolchainResult:
    uv = _uv_project_readiness(repository, cancel_requested)
    return ToolchainResult(
        oh_goon_version=__version__,
        uv=uv,
        git=(
            UnavailableToolVersion(state="version_unavailable")
            if git_observation.tool_available
            else MissingToolVersion(state="missing")
        ),
        python=_tool_version(repository, "python3", cancel_requested),
    )


def _uv_project_readiness(repository: Path, cancel_requested: Cancel):
    """Let native uv apply this repository's own compatibility declaration once."""
    try:
        result = run_operation_command(
            repository,
            "uv",
            "version",
            timeout=10.0,
            cancel_requested=cancel_requested,
        )
    except OSError:
        return MissingUvTool(state="missing")
    if result.returncode != 0:
        detail = result.stderr or result.stdout or "uv rejected the project"
        return RejectedUvProject(state="project_rejected", detail=detail)
    return ReadyUvTool(state="ready")


def _tool_version(
    repository: Path,
    executable_name: str,
    cancel_requested: Cancel,
) -> ToolVersionResult:
    try:
        result = run_operation_command(
            repository,
            executable_name,
            "--version",
            timeout=10.0,
            cancel_requested=cancel_requested,
        )
    except OSError:
        return MissingToolVersion(state="missing")
    version = result.stdout or result.stderr
    if not version:
        return UnavailableToolVersion(state="version_unavailable")
    return AvailableToolVersion(state="available", version=version)

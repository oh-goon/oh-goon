from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from oh_goon.core.models.operation import (
    AvailableToolVersion,
    DependenciesUpdated,
    DetachedGitPosition,
    MissingToolVersion,
    NamedGitPosition,
    ReadyUvTool,
    RejectedUvProject,
    RepositorySyncFailedValidation,
    ToolchainResult,
    UnavailableToolVersion,
    ValidGitWorktreeInvariant,
    ViolatedGitWorktreeInvariant,
)
from oh_goon.core.models.provenance import RepositoryStateFingerprint
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import (
    CheckState,
    GitBranchState,
    GitFinalState,
    OperationAction,
    WorktreeState,
)
from oh_goon.engine.command import CommandResult
from oh_goon.engine.operations import git_evidence
from oh_goon.engine.operations import readiness as readiness_module
from oh_goon.engine.operations import validation as validation_module
from tests.factories.policy import make_policy_snapshot


def test_validation_outcomes_cannot_carry_later_stage_evidence() -> None:
    with pytest.raises(ValidationError):
        RepositorySyncFailedValidation(state="repository_sync_failed", tests=CheckState.PASSED)  # type: ignore[call-arg]


def test_dependency_outcomes_expose_only_the_fact_production_knows() -> None:
    updated = DependenciesUpdated(state="updated")
    assert updated.model_dump() == {"state": "updated"}
    with pytest.raises(ValidationError):
        DependenciesUpdated(state="updated", packages_changed=0)  # type: ignore[call-arg]


def test_git_position_and_worktree_invariants_are_structural() -> None:
    assert NamedGitPosition(state="named", branch="main").branch == "main"
    assert DetachedGitPosition(state="detached").branch is None
    with pytest.raises(ValidationError):
        DetachedGitPosition(state="detached", branch="main")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ValidGitWorktreeInvariant(state="passed", unexpected_files=("pyproject.toml",))
    with pytest.raises(ValidationError):
        ViolatedGitWorktreeInvariant(state="failed", unexpected_files=())


def test_toolchain_probe_has_one_result_per_tool() -> None:
    tools = ToolchainResult(
        oh_goon_version="test",
        uv=ReadyUvTool(state="ready"),
        git=AvailableToolVersion(state="available", version="git version 2"),
        python=MissingToolVersion(state="missing"),
    )
    assert tools.uv.state == "ready"
    assert tools.git.state == "available"
    assert tools.python.state == "missing"
    assert "uv_status" not in ToolchainResult.model_fields


def test_toolchain_executes_each_native_probe_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []
    versions = {"python3": "Python 3.12.0\n"}

    def fake_run(repository: Path, name: str, *argv: str, **_: object) -> CommandResult:
        assert repository == tmp_path
        calls.append((name, argv))
        if name == "uv":
            assert argv == ("version",)
            return CommandResult(returncode=0, stdout="1267.0.1052\n", stderr="")
        assert argv == ("--version",)
        return CommandResult(returncode=0, stdout=versions[name], stderr="")

    monkeypatch.setattr(readiness_module, "run_operation_command", fake_run)

    observation = readiness_module.GitObservation(
        fingerprint=_fingerprint(
            head="a" * 40,
            branch_state=GitBranchState.NAMED,
            branch="main",
            worktree=WorktreeState.CLEAN,
        ),
        changed_files=(),
    )
    tools = readiness_module._toolchain(tmp_path, observation, None)
    assert calls == [("uv", ("version",)), ("python3", ("--version",))]
    assert tools.uv.state == "ready"
    assert tools.git.state == "version_unavailable"


def test_readiness_alias_actions_are_not_part_of_operation_vocabulary() -> None:
    assert "git-ready" not in {action.value for action in OperationAction}
    assert "git-prepare" not in {action.value for action in OperationAction}


def _fingerprint(
    *,
    head: str | None,
    branch_state: GitBranchState,
    branch: str | None,
    worktree: WorktreeState,
) -> RepositoryStateFingerprint:
    return RepositoryStateFingerprint(
        captured_at=utc_now(),
        head_sha=head,
        branch_state=branch_state,
        branch=branch,
        worktree_state=worktree,
    )


def test_git_evidence_covers_detached_unavailable_and_mutation_transitions() -> None:
    assert git_evidence.git_position(GitBranchState.DETACHED, None).state == "detached"
    assert git_evidence.git_position(GitBranchState.UNAVAILABLE, None).state == "unavailable"
    with pytest.raises(ValueError, match="requires a branch name"):
        git_evidence.git_position(GitBranchState.NAMED, None)

    before = _fingerprint(
        head="a" * 40,
        branch_state=GitBranchState.NAMED,
        branch="main",
        worktree=WorktreeState.CLEAN,
    )
    after = _fingerprint(
        head="b" * 40,
        branch_state=GitBranchState.DETACHED,
        branch=None,
        worktree=WorktreeState.DIRTY,
    )
    result = git_evidence.mutation_git_result(
        before,
        after,
        final_state=GitFinalState.UNEXPECTED_CHANGES,
        unexpected_files=("pyproject.toml",),
    )
    assert result.position.state == "detached"
    assert result.head.state == "changed"
    assert result.worktree.state == "failed"
    assert result.worktree.unexpected_files == ("pyproject.toml",)


def test_tool_probe_reports_native_launch_and_project_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def missing(_repo: Path, name: str, *_args: str, **_kwargs: object) -> CommandResult:
        raise OSError(2, "missing", name)

    monkeypatch.setattr(readiness_module, "run_operation_command", missing)
    assert readiness_module._tool_version(tmp_path, "python3", None).state == "missing"
    assert readiness_module._uv_project_readiness(tmp_path, None).state == "missing"

    monkeypatch.setattr(
        readiness_module,
        "run_operation_command",
        lambda *_args, **_kwargs: CommandResult(returncode=0, stdout="", stderr=""),
    )
    assert isinstance(
        readiness_module._tool_version(tmp_path, "python3", None), UnavailableToolVersion
    )
    assert isinstance(readiness_module._uv_project_readiness(tmp_path, None), ReadyUvTool)

    monkeypatch.setattr(
        readiness_module,
        "run_operation_command",
        lambda *_args, **_kwargs: CommandResult(
            returncode=2, stdout="", stderr="required uv version rejected"
        ),
    )
    rejected = readiness_module._uv_project_readiness(tmp_path, None)
    assert isinstance(rejected, RejectedUvProject)
    assert rejected.detail == "required uv version rejected"


def test_git_readiness_uses_status_launch_as_availability_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def missing_git(_self: object, *args: str, **_kwargs: object) -> CommandResult:
        calls.append(args)
        raise OSError(2, "missing", "git")

    monkeypatch.setattr(readiness_module.GitService, "run", missing_git)

    observation = readiness_module.GitService().observe(tmp_path)

    assert calls == [("status", "--porcelain=v2", "--branch", "-z", "--untracked-files=normal")]
    assert observation.tool_available is False
    assert observation.worktree_state is WorktreeState.NOT_GIT


def test_repository_validation_stops_at_first_failed_stage_and_preserves_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fail_sync(_repository: Path, *argv: str, **_kwargs: object) -> CommandResult:
        calls.append(argv)
        return CommandResult(returncode=1, stdout="sync out", stderr="sync err")

    monkeypatch.setattr(validation_module, "run_operation_command", fail_sync)
    result, stdout, stderr = validation_module.validate_repository(tmp_path, make_policy_snapshot())
    assert result.state == "repository_sync_failed"
    assert calls == [("uv", "sync", "--frozen")]
    assert stdout == "sync out"
    assert stderr == "sync err"

    calls.clear()

    def fail_lock(_repository: Path, *argv: str, **_kwargs: object) -> CommandResult:
        calls.append(argv)
        if argv == ("uv", "sync", "--frozen"):
            return CommandResult(returncode=0, stdout="sync ok", stderr="")
        return CommandResult(returncode=1, stdout="", stderr="lock invalid")

    monkeypatch.setattr(validation_module, "run_operation_command", fail_lock)
    result, stdout, stderr = validation_module.validate_repository(tmp_path, make_policy_snapshot())
    assert result.state == "repository_lock_invalid"
    assert calls == [("uv", "sync", "--frozen"), ("uv", "lock", "--check")]
    assert stdout == "sync ok"
    assert stderr == "lock invalid"

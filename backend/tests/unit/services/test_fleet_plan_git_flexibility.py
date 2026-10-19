from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import (
    GitNativeAction,
    GitPlanStep,
    GitResultContract,
    ResolvedCommandStep,
    ResolvedFleetPlan,
    ResolvedGitStep,
    StepFailurePolicy,
)
from oh_goon.core.models.fleet_plan_evidence import (
    CompletedPlanStepExecution,
    FailedPlanStepExecution,
)
from oh_goon.core.models.fleet_plan_execution_policy import MaintenanceExecutionPolicy
from oh_goon.core.models.fleet_plan_values import (
    HostEnvironmentPart,
    LiteralEnvironmentPart,
    ResolvedEnvironmentBinding,
)
from oh_goon.engine.command import CommandResult
from oh_goon.engine.git import GitService
from oh_goon.integrations.gitlab_ci_component import GitLabComponentFetcher
from oh_goon.integrations.gitlab_ci_goon import GitLabCiGoonMigrator
from oh_goon.services.fleet_plan_resolver import FleetPlanResolver
from tests.factories.fleet_plan_executor import PhysicalFleetPlanExecutorHarness


def _git(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repository, check=check, capture_output=True, text=True
    )


def _repository(root: Path) -> Path:
    repository = root / "repo"
    repository.mkdir()
    (repository / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "-q", "-m", "baseline")
    return repository


def test_native_git_action_is_not_limited_to_fleet_builtin_operations(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - uses: git
    id: inspect
    timeout_seconds: 60
    args: [rev-parse, --abbrev-ref, HEAD]
""",
        encoding="utf-8",
    )

    plan = FleetPlanResolver().resolve(plan_path)
    step = plan.steps[0]
    assert isinstance(step, ResolvedGitStep)
    assert step.argv == ("rev-parse", "--abbrev-ref", "HEAD")
    assert step.result_contract is GitResultContract.EXIT_CODE
    assert "action" not in step.model_dump(mode="json")

    outcome = PhysicalFleetPlanExecutorHarness(GitService()).execute(
        repository, plan, cancel_requested=None
    )
    assert outcome.stdout == "main"
    assert outcome.steps[0].argv == ("git", "rev-parse", "--abbrev-ref", "HEAD")


def test_native_git_success_codes_can_accept_meaningful_nonzero_results(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="git-diff",
        description="",
        steps=(
            ResolvedGitStep(
                timeout_seconds=60,
                step_id="diff",
                argv=("diff", "--quiet"),
                success_codes=(0, 1),
            ),
        ),
    )

    step = (
        PhysicalFleetPlanExecutorHarness(GitService())
        .execute(repository, plan, cancel_requested=None)
        .steps[0]
    )
    assert isinstance(step, CompletedPlanStepExecution)
    assert step.returncode == 1


def test_plan_command_working_directory_is_repository_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    package = repository / "packages" / "service"
    package.mkdir(parents=True)
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="monorepo",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="test-service",
                argv=("fake-command",),
                working_directory="packages/service",
            ),
        ),
    )
    observed: dict[str, Path] = {}

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        observed["cwd"] = kwargs["cwd"]
        return CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    PhysicalFleetPlanExecutorHarness(GitService()).execute(repository, plan, cancel_requested=None)
    assert observed["cwd"] == package


def test_resolved_plan_rejects_working_directory_escape_before_preflight() -> None:
    with pytest.raises(ValueError, match="working_directory must not contain '..' traversal"):
        ResolvedFleetPlan.create(
            execution=MaintenanceExecutionPolicy(),
            name="escape",
            description="",
            steps=(
                ResolvedCommandStep(
                    timeout_seconds=3600,
                    step_id="escape",
                    argv=("true",),
                    working_directory="../outside",
                ),
            ),
        )


def test_on_failure_continue_runs_later_steps_but_plan_keeps_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="diagnostics",
        description="",
        steps=(
            ResolvedCommandStep(
                timeout_seconds=3600,
                step_id="check",
                argv=("first",),
                on_failure=StepFailurePolicy.CONTINUE,
            ),
            ResolvedCommandStep(timeout_seconds=3600, step_id="diagnose", argv=("second",)),
        ),
    )

    def fake_run_command(*argv: str, **kwargs) -> CommandResult:
        del kwargs
        code = 1 if argv[0] == "first" else 0
        return CommandResult(argv=tuple(argv), returncode=code, stdout="", stderr="")

    monkeypatch.setattr(
        "oh_goon.integrations.fleet_plan_step_execution.run_command", fake_run_command
    )
    result = PhysicalFleetPlanExecutorHarness(GitService()).execute(
        repository, plan, cancel_requested=None
    )
    assert len(result.steps) == 2
    assert isinstance(result.steps[0], FailedPlanStepExecution)
    assert isinstance(result.steps[1], CompletedPlanStepExecution)


def test_gitlab_migration_maps_native_git_to_git_step(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """stages: [build]
branch:
  stage: build
  script: git switch -c fleet/migrated
""",
        encoding="utf-8",
    )

    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    step = migration.goon.steps[0]
    assert isinstance(step, GitPlanStep)
    assert isinstance(step.action, GitNativeAction)
    assert step.action.args == ("switch", "-c", "fleet/migrated")


def test_typed_assert_clean_keeps_semantic_failure(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - uses: git
    id: clean
    timeout_seconds: 60
    operation: assert-clean
""",
        encoding="utf-8",
    )
    plan = FleetPlanResolver().resolve(plan_path)
    resolved = plan.steps[0]
    assert isinstance(resolved, ResolvedGitStep)
    assert resolved.argv == ("status", "--porcelain", "--untracked-files=normal")
    assert resolved.result_contract is GitResultContract.CLEAN_WORKTREE
    assert "action" not in resolved.model_dump(mode="json")
    result = (
        PhysicalFleetPlanExecutorHarness(GitService())
        .execute(repository, plan, cancel_requested=None)
        .steps[0]
    )
    assert isinstance(result, FailedPlanStepExecution)
    assert result.returncode == 1


def test_generic_git_command_stays_a_command_without_hidden_kind_rewrite(tmp_path: Path) -> None:
    plan_path = tmp_path / "goon.yml"
    plan_path.write_text(
        """version: 1
execution:
  purpose: verification
steps:
  - id: tag-list
    run: "git tag --list"
    working_directory: .
""",
        encoding="utf-8",
    )

    step = FleetPlanResolver().resolve(plan_path).steps[0]
    assert isinstance(step, ResolvedCommandStep)
    assert step.argv == ("git", "tag", "--list")


def test_native_git_uses_declared_environment_without_interactive_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Fleet Env Author")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "fleet-env@example.com")
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="git-env",
        description="",
        steps=(
            ResolvedGitStep(
                timeout_seconds=60,
                step_id="identity",
                argv=("var", "GIT_AUTHOR_IDENT"),
                environment=(
                    ResolvedEnvironmentBinding(
                        name="GIT_AUTHOR_NAME",
                        parts=(HostEnvironmentPart(name="GIT_AUTHOR_NAME"),),
                    ),
                    ResolvedEnvironmentBinding(
                        name="GIT_AUTHOR_EMAIL",
                        parts=(HostEnvironmentPart(name="GIT_AUTHOR_EMAIL"),),
                    ),
                ),
            ),
        ),
    )

    outcome = PhysicalFleetPlanExecutorHarness(GitService()).execute(
        repository, plan, cancel_requested=None
    )
    assert "Fleet Env Author" not in outcome.stdout
    assert "fleet-env@example.com" not in outcome.stdout
    assert "*** <***>" in outcome.stdout


def test_gitlab_migration_writes_human_facing_native_git_yaml(tmp_path: Path) -> None:
    source = tmp_path / ".gitlab-ci.yml"
    source.write_text(
        """branch:
  timeout: 10 minutes
  script: git switch -c fleet/migrated
""",
        encoding="utf-8",
    )
    migration = GitLabCiGoonMigrator(GitLabComponentFetcher()).migrate(source)
    destination = tmp_path / "goon.yml"
    GitLabCiGoonMigrator.write_goon(destination, migration)
    rendered = destination.read_text(encoding="utf-8")
    assert "uses: git" in rendered
    assert "operation: native" not in rendered
    assert "args:" in rendered
    assert "action:" not in rendered
    resolved = FleetPlanResolver().resolve(destination).steps[0]
    assert isinstance(resolved, ResolvedGitStep)
    assert resolved.argv == ("switch", "-c", "fleet/migrated")


def test_native_git_cannot_execute_plan_selected_external_diff(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    marker = tmp_path / "external-diff-ran"
    external_diff = tmp_path / "host-diff"
    external_diff.write_text(f"#!/bin/sh\nprintf ran > {marker}\nexit 0\n", encoding="utf-8")
    external_diff.chmod(0o755)
    plan = ResolvedFleetPlan.create(
        execution=MaintenanceExecutionPolicy(),
        name="git-diff-authority",
        description="",
        steps=(
            ResolvedGitStep(
                timeout_seconds=60,
                step_id="diff",
                argv=("diff",),
                environment=(
                    ResolvedEnvironmentBinding(
                        name="GIT_EXTERNAL_DIFF",
                        parts=(LiteralEnvironmentPart(value=str(external_diff)),),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(FleetPlanInvalid, match="cannot replace Git execution-control environment"):
        PhysicalFleetPlanExecutorHarness(GitService()).execute(
            repository, plan, cancel_requested=None
        )

    assert not marker.exists()

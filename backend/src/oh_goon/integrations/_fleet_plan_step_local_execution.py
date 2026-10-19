from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from oh_goon.core.errors import FleetExtensionInvalid, FleetPlanInvalid
from oh_goon.core.file_digest import sha256_file
from oh_goon.core.models.fleet_plan import (
    GitResultContract,
    ResolvedExtensionStep,
    ResolvedGitStep,
    ResolvedPlanStep,
    ResolvedPreCommitStep,
)
from oh_goon.core.path_authority import RepositoryPathSpace
from oh_goon.engine.command import CommandResult, run_command
from oh_goon.engine.fleet_extensions import FleetExtensionStore
from oh_goon.engine.fleet_tools import FleetToolStore
from oh_goon.engine.git import GitService
from oh_goon.integrations._fleet_plan_step_outcome import StepOutcome, from_command
from oh_goon.integrations.fleet_plan_environment import git_execution_environment


def execute_pre_commit(
    repository: Path,
    step: ResolvedPreCommitStep,
    tools: FleetToolStore | None,
    cancel_requested: Callable[[], bool] | None,
    *,
    environment: dict[str, str],
    command_runner=run_command,
    executable: Path | None = None,
) -> StepOutcome:
    config = repository / ".pre-commit-config.yaml"
    if not config.is_file():
        if step.if_missing.value == "skip":
            return StepOutcome(
                argv=(),
                returncode=0,
                skipped=True,
                stdout="Repository has no .pre-commit-config.yaml",
            )
        return StepOutcome(
            argv=(), returncode=2, stderr="Repository has no .pre-commit-config.yaml"
        )
    return from_command(
        _run_pre_commit(
            repository,
            config,
            executable=executable or validate_pre_commit_runtime(tools, step),
            timeout=step.timeout_seconds,
            cancel_requested=cancel_requested,
            env=environment,
            command_runner=command_runner,
        )
    )


def validate_pre_commit_runtime(tools: FleetToolStore | None, step: ResolvedPreCommitStep) -> Path:
    if tools is None:
        raise FleetPlanInvalid("Fleet plan tool runtime is not configured for execution")
    return tools.check_pre_commit(
        runtime_id=step.runtime_id,
        requirement=step.runner_requirement,
        pyproject_sha256=step.runtime_pyproject_sha256,
        uv_lock_sha256=step.runtime_uv_lock_sha256,
    )


def execute_extension(
    repository: Path,
    step: ResolvedExtensionStep,
    extensions: FleetExtensionStore | None,
    git: GitService,
    cancel_requested: Callable[[], bool] | None,
    *,
    environment: dict[str, str],
    command_runner=run_command,
    project_path: Path | None = None,
    environment_path: Path | None = None,
) -> StepOutcome:
    project_path = project_path or validate_extension(extensions, git, step)
    argv = ("uv", "run", "--project", str(project_path), "--no-sync", step.command, *step.args)
    env = dict(environment)
    if environment_path is None:
        assert extensions is not None
        environment_path = extensions.environment_path(step.extension_id)
    env["UV_PROJECT_ENVIRONMENT"] = str(environment_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return from_command(
        command_runner(
            *argv,
            cwd=step_working_directory(repository, step),
            timeout=step.timeout_seconds,
            cancel_requested=cancel_requested,
            env=env,
        )
    )


def execute_git(
    git: GitService,
    repository: Path,
    step: ResolvedGitStep,
    cancel_requested: Callable[[], bool] | None,
    *,
    environment: dict[str, str],
    command_runner=run_command,
) -> CommandResult:
    result = git.run(
        *step.argv,
        cwd=step_working_directory(repository, step),
        timeout=step.timeout_seconds,
        cancel_requested=cancel_requested,
        env=git_execution_environment(environment),
        command_runner=command_runner,
    )
    if (
        step.result_contract is GitResultContract.CLEAN_WORKTREE
        and result.returncode == 0
        and result.stdout
    ):
        return CommandResult(
            argv=result.argv,
            returncode=1,
            stdout=result.stdout,
            stderr="Repository worktree is not clean",
            duration_ms=result.duration_ms,
        )
    return result


def validate_extension(
    extensions: FleetExtensionStore | None,
    git: GitService,
    step: ResolvedExtensionStep,
) -> Path:
    if extensions is None:
        raise FleetExtensionInvalid("Fleet extension store is not configured for plan execution")
    project_path = extensions.checkout_path(step.extension_id)
    try:
        current_pyproject = sha256_file(project_path / "pyproject.toml")
        current_lock = sha256_file(project_path / "uv.lock")
    except OSError as exc:
        raise FleetExtensionInvalid(
            f"Managed extension files disappeared after plan admission: {step.extension}: {exc}"
        ) from exc
    if current_pyproject != step.pyproject_sha256 or current_lock != step.uv_lock_sha256:
        raise FleetExtensionInvalid(
            f"Managed extension files changed after plan admission: {step.extension}"
        )
    observation = git.observe(project_path)
    if observation.fingerprint.state_id != step.project_fingerprint:
        raise FleetExtensionInvalid(
            f"Managed extension Git state changed after plan admission: {step.extension}"
        )
    if observation.head_sha != step.revision:
        raise FleetExtensionInvalid(
            f"Managed extension revision changed after plan admission: {step.extension}"
        )
    return project_path


def step_working_directory(repository: Path, step: ResolvedPlanStep) -> Path:
    value = getattr(step, "working_directory", ".")
    return RepositoryPathSpace.from_root(repository).resolve(
        value,
        label=f"Fleet plan step {step.step_id} working_directory",
        kind="directory",
        require_exists=True,
    )


def _run_pre_commit(
    repository: Path,
    config: Path,
    *,
    executable: Path,
    timeout: float,
    cancel_requested: Callable[[], bool] | None,
    env: dict[str, str],
    command_runner=run_command,
) -> CommandResult:
    return command_runner(
        str(executable),
        "run",
        "--all-files",
        "--config",
        str(config),
        cwd=repository,
        timeout=timeout,
        cancel_requested=cancel_requested,
        env=env,
    )

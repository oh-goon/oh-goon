from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan import GitPlanStep, ResolvedGitStep
from oh_goon.core.models.fleet_plan_git import (
    GitAddAction,
    GitAssertCleanAction,
    GitCheckoutAction,
    GitCleanIgnoredAction,
    GitCommitAction,
    GitCreateBranchAction,
    GitDiffAction,
    GitNativeAction,
    GitPlanAction,
    GitResultContract,
)
from oh_goon.core.models.fleet_plan_values import ResolvedEnvironmentBinding


@dataclass(frozen=True, slots=True)
class ResolvedGitAction:
    argv: tuple[str, ...]
    result_contract: GitResultContract = GitResultContract.EXIT_CODE


def resolve_git_action(action: GitPlanAction) -> ResolvedGitAction:
    """Compile ergonomic authoring forms into one runtime Git argv contract."""
    if isinstance(action, GitAssertCleanAction):
        return ResolvedGitAction(
            argv=("status", "--porcelain", "--untracked-files=normal"),
            result_contract=GitResultContract.CLEAN_WORKTREE,
        )
    if isinstance(action, GitCleanIgnoredAction):
        return ResolvedGitAction(argv=("clean", "-fdX"))
    if isinstance(action, GitDiffAction):
        return ResolvedGitAction(argv=("diff", "--"))
    if isinstance(action, GitCheckoutAction):
        return ResolvedGitAction(argv=("checkout", action.ref))
    if isinstance(action, GitCreateBranchAction):
        suffix = (action.start_point,) if action.start_point else ()
        return ResolvedGitAction(argv=("checkout", "-b", action.branch, *suffix))
    if isinstance(action, GitAddAction):
        return ResolvedGitAction(argv=("add", "--", *action.paths))
    if isinstance(action, GitCommitAction):
        suffix = ("--allow-empty",) if action.allow_empty else ()
        return ResolvedGitAction(argv=("commit", "-m", action.message, *suffix))
    if isinstance(action, GitNativeAction):
        return ResolvedGitAction(argv=action.args)
    raise FleetPlanInvalid(f"Unsupported Git plan action: {type(action).__name__}")


def resolve_git_step(
    step: GitPlanStep,
    *,
    step_id: str,
    environment: tuple[ResolvedEnvironmentBinding, ...],
) -> ResolvedGitStep | None:
    """Compile an explicitly declared Git step into its canonical resolved shape."""
    action = resolve_git_action(step.action)
    return ResolvedGitStep(
        step_id=step_id,
        lifecycle=step.lifecycle,
        argv=action.argv,
        result_contract=action.result_contract,
        timeout_seconds=step.timeout_seconds,
        working_directory=step.working_directory,
        imports=step.imports,
        workspace=step.workspace,
        cache=step.cache,
        capabilities=step.capabilities,
        environment=environment,
        success_codes=step.success_codes,
        on_failure=step.on_failure,
        when=step.when,
        retry=step.retry,
        artifacts=step.artifacts,
        outputs=step.outputs,
    )

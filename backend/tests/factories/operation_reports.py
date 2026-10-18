from __future__ import annotations

from pathlib import Path

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.migration import MigrationResult
from oh_goon.core.models.operation import (
    AvailableToolVersion,
    ReadyUvTool,
    DependencyPlanCompleted,
    FailedOperationOutcome,
    MigrationOperationEvidence,
    NamedGitPosition,
    NoDependencyUpdates,
    NoGitRemote,
    OperationEvidence,
    OperationReport,
    PlanOperationEvidence,
    PythonRuntimeResult,
    ReadinessChecks,
    ReadinessOperationEvidence,
    RecipeOperationEvidence,
    RepositoryValidationCompleted,
    ReviewOperationEvidence,
    SuccessfulOperationOutcome,
    ToolchainResult,
    UpdateOperationEvidence,
    VerifyOperationEvidence,
)
from oh_goon.core.models.provenance import create_operation_provenance
from oh_goon.core.models.recipe import UvRecipeResult
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import MigrationTarget, OperationTarget, UvRecipeTarget
from oh_goon.core.vocabulary import (
    AuditState,
    CheckState,
    CommandSafety,
    LockPresenceState,
    MigrationReadiness,
    MigrationSource,
    OperationAction,
    PythonPinReadiness,
    UvRecipeId,
    ValidationState,
    Verdict,
    WorktreeState,
)
from tests.factories.capabilities import capability_snapshot


def _evidence(action: OperationAction, target: OperationTarget | None):
    if action in {OperationAction.STATUS, OperationAction.INIT}:
        return ReadinessOperationEvidence(
            kind="READINESS",
            readiness=ReadinessChecks(
                project="present",
                working_tree=WorktreeState.CLEAN,
                position=NamedGitPosition(state="named", branch="main"),
                remote=NoGitRemote(state="no_remote"),
                uv_lock=LockPresenceState.PRESENT,
                python_pin=PythonPinReadiness.OPTIONAL,
            ),
            tools=ToolchainResult(
                oh_goon_version="test",
                uv=ReadyUvTool(state="ready"),
                git=AvailableToolVersion(state="available", version="git version 2.0"),
                python=AvailableToolVersion(state="available", version="Python 3.12"),
            ),
            python=PythonRuntimeResult(),
        )
    if action is OperationAction.PLAN:
        return PlanOperationEvidence(
            kind="PLAN", dependencies=DependencyPlanCompleted(state="plan_completed")
        )
    if action is OperationAction.UPDATE:
        return UpdateOperationEvidence(
            kind="UPDATE", dependencies=NoDependencyUpdates(state="no_updates")
        )
    if action is OperationAction.VERIFY:
        return VerifyOperationEvidence(
            kind="VERIFY",
            validation=RepositoryValidationCompleted(
                state="repository_completed",
                environment=ValidationState.VALID,
                security_audit=AuditState.SKIPPED,
                tests=CheckState.PASSED,
            ),
        )
    if action is OperationAction.PREPARE_REVIEW:
        return ReviewOperationEvidence(kind="REVIEW")
    if action is OperationAction.RECIPE:
        recipe = target.recipe if isinstance(target, UvRecipeTarget) else UvRecipeId.LOCK_CHECK
        return RecipeOperationEvidence(
            kind="RECIPE",
            recipe=UvRecipeResult(
                recipe=recipe,
                safety=CommandSafety.READ_ONLY,
                argv=["uv", recipe.value.lower()],
                exit_code=0,
            ),
        )
    if action is OperationAction.ADOPT_UV:
        if target is not None and not isinstance(target, MigrationTarget):
            raise ValueError("ADOPT_UV test report requires a migration target")
        return MigrationOperationEvidence(
            kind="MIGRATION",
            migration=MigrationResult(
                source=MigrationSource.UNKNOWN,
                readiness=MigrationReadiness.SAFE_AUTOMATIC,
            ),
        )
    raise AssertionError(f"Unsupported test operation action: {action}")


def make_operation_report(
    repository: Path | RepositoryIdentity,
    action: OperationAction,
    verdict: Verdict = Verdict.READY,
    reason_code: ReasonCode = ReasonCode.READY,
    *,
    fleet_run_id: str | None = "fleet-test",
    operation_id: str | None = None,
    returncode: int = 0,
    target: OperationTarget | None = None,
    evidence: OperationEvidence | None = None,
) -> OperationReport:
    identity = (
        repository
        if isinstance(repository, RepositoryIdentity)
        else RepositoryIdentity(name=repository.name, path=repository)
    )
    return OperationReport(
        target=target,
        capability=capability_snapshot(action, target),
        provenance=create_operation_provenance(
            fleet_run_id=fleet_run_id,
            operation_id=operation_id,
        ),
        repository=identity,
        outcome=(
            SuccessfulOperationOutcome(state="successful", verdict=verdict, reason_code=reason_code)
            if verdict.successful
            else FailedOperationOutcome(
                state="failed", returncode=returncode or 1, reason_code=reason_code
            )
        ),
        evidence=evidence or _evidence(action, target),
    )

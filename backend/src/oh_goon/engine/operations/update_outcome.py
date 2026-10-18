from __future__ import annotations

from dataclasses import dataclass

from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.operation import (
    DependenciesUpdated,
    DependencyResult,
    DependencyUpdateFailed,
    FailedOperationOutcome,
    GitMutationResult,
    NoDependencyUpdates,
    OperationOutcome,
    RepositoryValidationResult,
    SuccessfulOperationOutcome,
)
from oh_goon.core.models.policy import PolicySnapshot
from oh_goon.core.vocabulary import Verdict
from oh_goon.engine.operations.validation import repository_validation_failure_reason


@dataclass(frozen=True, slots=True)
class UpdateEvidence:
    """Side-effect-free evidence used to classify one dependency update attempt."""

    command_returncode: int
    changed: bool
    validation: RepositoryValidationResult | None
    policy: PolicySnapshot
    git: GitMutationResult


@dataclass(frozen=True, slots=True)
class UpdateOutcome:
    outcome: OperationOutcome
    dependencies: DependencyResult


def classify_update_outcome(evidence: UpdateEvidence) -> UpdateOutcome:
    """Apply one explicit precedence order to update execution evidence."""
    dependencies = _dependency_result(evidence)
    if evidence.git.head.state == "changed":
        return UpdateOutcome(
            FailedOperationOutcome(
                state="failed", returncode=1, reason_code=ReasonCode.GIT_POSITION_CHANGED
            ),
            dependencies,
        )
    if evidence.git.worktree.state == "failed":
        return UpdateOutcome(
            FailedOperationOutcome(
                state="failed", returncode=1, reason_code=ReasonCode.UNEXPECTED_FILE_CHANGES
            ),
            dependencies,
        )
    if evidence.command_returncode != 0:
        return UpdateOutcome(
            FailedOperationOutcome(
                state="failed",
                returncode=evidence.command_returncode,
                reason_code=ReasonCode.OPERATION_FAILED,
            ),
            DependencyUpdateFailed(state="failed"),
        )
    validation = evidence.validation
    if validation is None:
        return UpdateOutcome(
            FailedOperationOutcome(
                state="failed", returncode=1, reason_code=ReasonCode.VERIFICATION_FAILED
            ),
            DependencyUpdateFailed(state="failed"),
        )
    validation_reason = repository_validation_failure_reason(validation, evidence.policy)
    if validation_reason is not None:
        return UpdateOutcome(
            FailedOperationOutcome(state="failed", returncode=1, reason_code=validation_reason),
            dependencies,
        )
    if evidence.changed:
        return UpdateOutcome(
            SuccessfulOperationOutcome(
                state="successful",
                verdict=Verdict.UPDATED,
                reason_code=ReasonCode.DEPENDENCIES_UPDATED,
            ),
            DependenciesUpdated(state="updated"),
        )
    return UpdateOutcome(
        SuccessfulOperationOutcome(
            state="successful", verdict=Verdict.UNCHANGED, reason_code=ReasonCode.NO_UPDATES
        ),
        NoDependencyUpdates(state="no_updates"),
    )


def _dependency_result(evidence: UpdateEvidence) -> DependencyResult:
    if evidence.command_returncode != 0 or evidence.validation is None:
        return DependencyUpdateFailed(state="failed")
    if evidence.changed:
        return DependenciesUpdated(state="updated")
    return NoDependencyUpdates(state="no_updates")

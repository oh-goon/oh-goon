from __future__ import annotations

from oh_goon.core.models.explanation import (
    ComparedGitRemoteEvidence,
    DependencyEvidence,
    DetachedGitPositionEvidence,
    ExplanationEvidence,
    GitEvidence,
    GitRemoteComparisonUnavailableEvidence,
    GitRemoteEvidence,
    GitRemoteLookupUnavailableEvidence,
    GitRemoteWithoutUpstreamEvidence,
    NamedGitPositionEvidence,
    NoGitRemoteEvidence,
    ReasonCode,
    RepositoryExplanation,
    RepositoryLockInvalidValidationEvidence,
    RepositorySyncFailedValidationEvidence,
    RepositoryValidationCompletedEvidence,
    SystemActionCode,
    UnavailableGitPositionEvidence,
    ValidationEvidence,
    reason_semantics,
)
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.models.operation import (
    ComparedGitRemote,
    GitRemoteComparisonUnavailable,
    GitRemoteLookupUnavailable,
    GitRemoteObservation,
    GitRemoteWithoutUpstream,
    NoGitRemote,
    OperationReport,
    PlanOperationEvidence,
    ReadinessOperationEvidence,
    RepositoryLockInvalidValidation,
    RepositorySyncFailedValidation,
    RepositoryValidationCompleted,
    ReviewOperationEvidence,
    UpdateOperationEvidence,
    VerifyOperationEvidence,
)
from oh_goon.core.vocabulary import (
    AuditState,
    FleetScope,
)


def explain_operation(report: OperationReport) -> RepositoryExplanation:
    """Project one immutable historical operation explanation from its report."""
    return _from_code(
        report.outcome.reason_code,
        evidence=_evidence(report),
        actions=_actions(report),
    )


def explain_repository_guidance(
    scope: FleetScope,
    *,
    init: OperationState | None,
    plan: OperationState | None,
    update: OperationState | None,
    verify: OperationState | None,
    exclusion_reason: str | None = None,
) -> RepositoryExplanation:
    """Project current repository guidance from canonical operation states."""
    if scope is FleetScope.EXCLUDED:
        return _from_code(
            ReasonCode.EXCLUDED,
            evidence=ExplanationEvidence(exclusion_reason=exclusion_reason),
        )
    attention = [
        state
        for state in (plan, update, verify)
        if state is not None and state.current_needs_attention
    ]
    if attention:
        latest = max(attention, key=lambda state: state.finished_at)
        return latest.explanation
    if (
        update is not None
        and update.current_successful
        and update.verdict.dependency_update_successful
    ):
        return update.explanation
    if init is None:
        return _from_code(ReasonCode.NOT_CHECKED)
    if init.is_stale:
        return _from_code(ReasonCode.READINESS_STALE)
    return init.explanation


def _from_code(
    reason_code: ReasonCode,
    *,
    evidence: ExplanationEvidence | None = None,
    actions: list[SystemActionCode] | None = None,
) -> RepositoryExplanation:
    semantics = reason_semantics(reason_code)
    return RepositoryExplanation(
        phase=semantics.phase,
        reason_code=reason_code,
        risk=semantics.risk,
        can_auto_fix=semantics.auto_fix_action is not None,
        auto_fix_action=semantics.auto_fix_action,
        evidence=evidence or ExplanationEvidence(),
        system_actions=actions or [],
    )


def _evidence(report: OperationReport) -> ExplanationEvidence:
    return ExplanationEvidence(
        git=_git_evidence(report),
        dependencies=_dependency_evidence(report),
        validation=_validation_evidence(report),
    )


def _git_evidence(report: OperationReport) -> GitEvidence | None:
    evidence = report.evidence
    if isinstance(evidence, ReadinessOperationEvidence):
        readiness = evidence.readiness
        return GitEvidence(
            position=_position_evidence(readiness.position),
            remote=_remote_evidence(readiness.remote),
        )
    if isinstance(evidence, (UpdateOperationEvidence, ReviewOperationEvidence)):
        git = evidence.git
        if git is None:
            return None
        return GitEvidence(
            position=_position_evidence(git.position),
            unexpected_files=git.worktree.unexpected_files,
        )
    return None


def _remote_evidence(remote: GitRemoteObservation) -> GitRemoteEvidence:
    if isinstance(remote, GitRemoteLookupUnavailable):
        return GitRemoteLookupUnavailableEvidence(state="lookup_unavailable")
    if isinstance(remote, NoGitRemote):
        return NoGitRemoteEvidence(state="no_remote")
    if isinstance(remote, GitRemoteWithoutUpstream):
        return GitRemoteWithoutUpstreamEvidence(
            state="no_upstream", remote_names=remote.remote_names
        )
    if isinstance(remote, GitRemoteComparisonUnavailable):
        return GitRemoteComparisonUnavailableEvidence(
            state="comparison_unavailable",
            remote_name=remote.remote_name,
            upstream=remote.upstream,
        )
    assert isinstance(remote, ComparedGitRemote)
    return ComparedGitRemoteEvidence(
        state=remote.state,
        remote_name=remote.remote_name,
        upstream=remote.upstream,
    )


def _position_evidence(position):
    if position.state == "named":
        return NamedGitPositionEvidence(state="named", branch=position.branch)
    if position.state == "detached":
        return DetachedGitPositionEvidence(state="detached")
    return UnavailableGitPositionEvidence(state="unavailable")


def _dependency_evidence(report: OperationReport) -> DependencyEvidence | None:
    evidence = report.evidence
    if not isinstance(evidence, (PlanOperationEvidence, UpdateOperationEvidence)):
        return None
    dependencies = evidence.dependencies
    return DependencyEvidence(state=dependencies.state)


def _validation_evidence(report: OperationReport) -> ValidationEvidence | None:
    evidence = report.evidence
    validation = (
        evidence.validation
        if isinstance(
            evidence, (PlanOperationEvidence, UpdateOperationEvidence, VerifyOperationEvidence)
        )
        else None
    )
    if validation is None:
        return None
    if isinstance(validation, RepositorySyncFailedValidation):
        return RepositorySyncFailedValidationEvidence(state=validation.state)
    if isinstance(validation, RepositoryLockInvalidValidation):
        return RepositoryLockInvalidValidationEvidence(state=validation.state)
    if isinstance(validation, RepositoryValidationCompleted):
        return RepositoryValidationCompletedEvidence(
            state=validation.state,
            environment=validation.environment,
            security_audit=validation.security_audit,
            tests=validation.tests,
        )
    raise AssertionError(f"Unhandled validation evidence state: {validation!r}")


def _actions(report: OperationReport) -> list[SystemActionCode]:
    actions: list[SystemActionCode] = []
    evidence = report.evidence
    if isinstance(evidence, ReadinessOperationEvidence):
        actions.extend(
            (
                SystemActionCode.CHECKED_PROJECT,
                SystemActionCode.CHECKED_TOOLS,
                SystemActionCode.CHECKED_GIT,
            )
        )
    if isinstance(evidence, (PlanOperationEvidence, UpdateOperationEvidence)):
        actions.append(SystemActionCode.RESOLVED_DEPENDENCIES)
    validation = (
        evidence.validation
        if isinstance(
            evidence, (PlanOperationEvidence, UpdateOperationEvidence, VerifyOperationEvidence)
        )
        else None
    )
    if isinstance(validation, RepositorySyncFailedValidation):
        actions.append(SystemActionCode.SYNCED_ENVIRONMENT)
    elif isinstance(validation, RepositoryLockInvalidValidation):
        actions.extend((SystemActionCode.SYNCED_ENVIRONMENT, SystemActionCode.VALIDATED_LOCK))
    elif isinstance(validation, RepositoryValidationCompleted):
        actions.extend(
            (
                SystemActionCode.SYNCED_ENVIRONMENT,
                SystemActionCode.VALIDATED_LOCK,
                SystemActionCode.VALIDATED_ENVIRONMENT,
            )
        )
        if validation.security_audit is not AuditState.SKIPPED:
            actions.append(SystemActionCode.RAN_SECURITY_AUDIT)
        actions.append(SystemActionCode.RAN_TESTS)
    if (
        isinstance(evidence, (UpdateOperationEvidence, ReviewOperationEvidence))
        and evidence.git is not None
    ):
        actions.append(SystemActionCode.CHECKED_INVARIANTS)
    return actions

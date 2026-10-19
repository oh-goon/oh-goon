from pathlib import Path

from oh_goon.core.models.explanation import (
    AutoFixAction,
    OperationPhase,
    ReasonCode,
    SystemActionCode,
    reason_semantics,
)
from oh_goon.core.models.fleet import OperationState
from oh_goon.core.models.operation import (
    AvailableToolVersion,
    ComparedGitRemote,
    DependenciesUpdated,
    GitMutationResult,
    NamedGitPosition,
    PreservedGitHead,
    PythonRuntimeResult,
    ReadinessChecks,
    ReadinessOperationEvidence,
    ReadyUvTool,
    RepositoryValidationCompleted,
    ToolchainResult,
    UpdateOperationEvidence,
    ValidGitWorktreeInvariant,
)
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.projections import explain_operation, explain_repository_guidance
from oh_goon.core.vocabulary import (
    AuditState,
    CheckState,
    FleetScope,
    GitFinalState,
    LockPresenceState,
    OperationAction,
    PythonPinReadiness,
    ResultFreshness,
    RiskLevel,
    ValidationState,
    Verdict,
    WorktreeState,
)
from tests.factories.operation_reports import make_operation_report

REPOSITORY = RepositoryIdentity(name="repo", path=Path("/tmp/repo"))


def report(action: OperationAction, verdict: Verdict, reason_code: ReasonCode, *, evidence=None):
    return make_operation_report(
        REPOSITORY,
        action,
        verdict=verdict,
        reason_code=reason_code,
        evidence=evidence,
    )


def readiness_evidence(
    *, worktree: WorktreeState = WorktreeState.CLEAN
) -> ReadinessOperationEvidence:
    return ReadinessOperationEvidence(
        kind="READINESS",
        readiness=ReadinessChecks(
            project="present",
            working_tree=worktree,
            position=NamedGitPosition(state="named", branch="main"),
            remote=ComparedGitRemote(
                state="up_to_date", remote_name="origin", upstream="origin/main"
            ),
            uv_lock=LockPresenceState.PRESENT,
            python_pin=PythonPinReadiness.OPTIONAL,
        ),
        tools=ToolchainResult(
            oh_goon_version="test",
            uv=ReadyUvTool(state="ready"),
            git=AvailableToolVersion(state="available", version="git version 2"),
            python=AvailableToolVersion(state="available", version="Python 3.12"),
        ),
        python=PythonRuntimeResult(),
    )


def mutation_git() -> GitMutationResult:
    return GitMutationResult(
        position=NamedGitPosition(state="named", branch="main"),
        initial_state=WorktreeState.CLEAN,
        final_state=GitFinalState.CHANGED_BY_UPDATE,
        head=PreservedGitHead(state="preserved", head="abc"),
        worktree=ValidGitWorktreeInvariant(state="passed"),
    )


def test_guidance_marks_stale_readiness_as_auto_fixable() -> None:
    init = OperationState(
        report=report(OperationAction.INIT, Verdict.READY, ReasonCode.READY),
        freshness=ResultFreshness.STALE,
    )
    explanation = explain_repository_guidance(
        FleetScope.INCLUDED, init=init, plan=None, update=None, verify=None
    )
    assert explanation.reason_code is ReasonCode.READINESS_STALE
    assert explanation.can_auto_fix is True


def test_guidance_uses_current_init_reason_code_and_structural_git_position() -> None:
    init = OperationState(
        report=report(
            OperationAction.INIT,
            Verdict.MANUAL_ACTION_REQUIRED,
            ReasonCode.DIRTY_WORKTREE,
            evidence=readiness_evidence(worktree=WorktreeState.DIRTY),
        ),
        freshness=ResultFreshness.CURRENT,
    )
    explanation = explain_repository_guidance(
        FleetScope.INCLUDED,
        init=init,
        plan=None,
        update=None,
        verify=None,
    )
    assert explanation.reason_code is ReasonCode.DIRTY_WORKTREE
    assert explanation.evidence.git is not None
    assert explanation.evidence.git.position.state == "named"
    assert explanation.evidence.git.position.branch == "main"
    assert explanation.system_actions == (
        SystemActionCode.CHECKED_PROJECT,
        SystemActionCode.CHECKED_TOOLS,
        SystemActionCode.CHECKED_GIT,
    )


def test_current_guidance_does_not_substitute_historical_update_failure() -> None:
    value = report(
        OperationAction.UPDATE,
        Verdict.FAILED,
        ReasonCode.TESTS_FAILED,
        evidence=UpdateOperationEvidence(
            kind="UPDATE",
            dependencies=DependenciesUpdated(state="updated"),
            validation=RepositoryValidationCompleted(
                state="repository_completed",
                environment=ValidationState.VALID,
                security_audit=AuditState.SKIPPED,
                tests=CheckState.FAILED,
            ),
        ),
    )
    stale_init = OperationState(
        report=report(OperationAction.INIT, Verdict.READY, ReasonCode.READY),
        freshness=ResultFreshness.STALE,
    )
    current = explain_repository_guidance(
        FleetScope.INCLUDED, init=stale_init, plan=None, update=None, verify=None
    )
    historical = explain_operation(value)
    assert current.reason_code is ReasonCode.READINESS_STALE
    assert historical.reason_code is ReasonCode.TESTS_FAILED
    assert historical.evidence.validation is not None
    assert historical.evidence.validation.state == "repository_completed"
    assert historical.evidence.validation.tests is CheckState.FAILED
    assert SystemActionCode.RAN_SECURITY_AUDIT not in historical.system_actions


def test_operation_explanation_uses_structural_dependency_and_git_evidence() -> None:
    value = report(
        OperationAction.UPDATE,
        Verdict.UPDATED,
        ReasonCode.DEPENDENCIES_UPDATED,
        evidence=UpdateOperationEvidence(
            kind="UPDATE",
            dependencies=DependenciesUpdated(state="updated"),
            git=mutation_git(),
        ),
    )
    explanation = explain_operation(value)
    assert explanation.reason_code is ReasonCode.DEPENDENCIES_UPDATED
    assert explanation.evidence.dependencies is not None
    assert explanation.evidence.dependencies.state == "updated"
    assert explanation.evidence.git is not None
    assert explanation.evidence.git.position.state == "named"
    assert explanation.evidence.git.position.branch == "main"


def test_reason_code_semantics_are_canonical_domain_data() -> None:
    stale = reason_semantics(ReasonCode.READINESS_STALE)
    assert ReasonCode.READINESS_STALE.phase is OperationPhase.READY
    assert ReasonCode.READINESS_STALE.risk is RiskLevel.LOW
    assert ReasonCode.READINESS_STALE.auto_fix_action is AutoFixAction.INITIALIZE
    assert ReasonCode.READINESS_STALE.can_auto_fix is True
    assert stale.phase is ReasonCode.READINESS_STALE.phase

    failed = reason_semantics(ReasonCode.TESTS_FAILED)
    assert failed.phase is OperationPhase.TESTS
    assert failed.risk is RiskLevel.MEDIUM
    assert failed.auto_fix_action is None

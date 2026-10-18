from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.vocabulary import (
    AuditState,
    CheckState,
    RiskLevel,
    ValidationState,
)


class StrictExplanationModel(ImmutableModel):
    pass


class OperationPhase(StrEnum):
    SCOPE = "SCOPE"
    TOOLING = "TOOLING"
    GIT = "GIT"
    BASELINE = "BASELINE"
    RESOLUTION = "RESOLUTION"
    SYNC = "SYNC"
    SECURITY = "SECURITY"
    TESTS = "TESTS"
    FINAL_VALIDATION = "FINAL_VALIDATION"
    READY = "READY"


class ReasonCode(StrEnum):
    EXCLUDED = "EXCLUDED"
    NOT_CHECKED = "NOT_CHECKED"
    READY = "READY"
    READINESS_STALE = "READINESS_STALE"
    DIRTY_WORKTREE = "DIRTY_WORKTREE"
    UV_LOCK_MISSING = "UV_LOCK_MISSING"
    TOOLING_REQUIRED = "TOOLING_REQUIRED"
    REMOTE_AHEAD = "REMOTE_AHEAD"
    REMOTE_DIVERGED = "REMOTE_DIVERGED"
    REMOTE_UNAVAILABLE = "REMOTE_UNAVAILABLE"
    DEPENDENCIES_UPDATED = "DEPENDENCIES_UPDATED"
    NO_UPDATES = "NO_UPDATES"
    TESTS_FAILED = "TESTS_FAILED"
    VULNERABILITIES_FOUND = "VULNERABILITIES_FOUND"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    LOCK_DRIFT = "LOCK_DRIFT"
    UNEXPECTED_FILE_CHANGES = "UNEXPECTED_FILE_CHANGES"
    GIT_POSITION_CHANGED = "GIT_POSITION_CHANGED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    ENGINE_CONTRACT_INVALID = "ENGINE_CONTRACT_INVALID"
    ENGINE_REPORT_MISSING = "ENGINE_REPORT_MISSING"
    OPERATION_FAILED = "OPERATION_FAILED"
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"
    REVIEW_PREPARED = "REVIEW_PREPARED"
    MIGRATION_APPLIED = "MIGRATION_APPLIED"

    @property
    def phase(self) -> OperationPhase:
        return reason_semantics(self).phase

    @property
    def risk(self) -> RiskLevel:
        return reason_semantics(self).risk

    @property
    def auto_fix_action(self) -> AutoFixAction | None:
        return reason_semantics(self).auto_fix_action

    @property
    def can_auto_fix(self) -> bool:
        return self.auto_fix_action is not None


class AutoFixAction(StrEnum):
    INITIALIZE = "INITIALIZE"


@dataclass(frozen=True, slots=True)
class ReasonSemantics:
    phase: OperationPhase
    risk: RiskLevel
    auto_fix_action: AutoFixAction | None = None


_REASON_SEMANTICS = MappingProxyType(
    {
        ReasonCode.EXCLUDED: ReasonSemantics(OperationPhase.SCOPE, RiskLevel.NONE),
        ReasonCode.NOT_CHECKED: ReasonSemantics(
            OperationPhase.READY,
            RiskLevel.NONE,
            AutoFixAction.INITIALIZE,
        ),
        ReasonCode.READY: ReasonSemantics(OperationPhase.READY, RiskLevel.NONE),
        ReasonCode.READINESS_STALE: ReasonSemantics(
            OperationPhase.READY,
            RiskLevel.LOW,
            AutoFixAction.INITIALIZE,
        ),
        ReasonCode.DIRTY_WORKTREE: ReasonSemantics(OperationPhase.GIT, RiskLevel.MEDIUM),
        ReasonCode.UV_LOCK_MISSING: ReasonSemantics(OperationPhase.BASELINE, RiskLevel.MEDIUM),
        ReasonCode.TOOLING_REQUIRED: ReasonSemantics(OperationPhase.TOOLING, RiskLevel.MEDIUM),
        ReasonCode.REMOTE_AHEAD: ReasonSemantics(OperationPhase.GIT, RiskLevel.MEDIUM),
        ReasonCode.REMOTE_DIVERGED: ReasonSemantics(OperationPhase.GIT, RiskLevel.HIGH),
        ReasonCode.REMOTE_UNAVAILABLE: ReasonSemantics(OperationPhase.GIT, RiskLevel.MEDIUM),
        ReasonCode.DEPENDENCIES_UPDATED: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION,
            RiskLevel.LOW,
        ),
        ReasonCode.NO_UPDATES: ReasonSemantics(OperationPhase.FINAL_VALIDATION, RiskLevel.NONE),
        ReasonCode.TESTS_FAILED: ReasonSemantics(OperationPhase.TESTS, RiskLevel.MEDIUM),
        ReasonCode.VULNERABILITIES_FOUND: ReasonSemantics(OperationPhase.SECURITY, RiskLevel.HIGH),
        ReasonCode.AUDIT_UNAVAILABLE: ReasonSemantics(OperationPhase.SECURITY, RiskLevel.MEDIUM),
        ReasonCode.LOCK_DRIFT: ReasonSemantics(OperationPhase.FINAL_VALIDATION, RiskLevel.HIGH),
        ReasonCode.UNEXPECTED_FILE_CHANGES: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION,
            RiskLevel.HIGH,
        ),
        ReasonCode.GIT_POSITION_CHANGED: ReasonSemantics(OperationPhase.GIT, RiskLevel.HIGH),
        ReasonCode.VERIFICATION_FAILED: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION,
            RiskLevel.HIGH,
        ),
        ReasonCode.ENGINE_CONTRACT_INVALID: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION,
            RiskLevel.HIGH,
        ),
        ReasonCode.ENGINE_REPORT_MISSING: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION,
            RiskLevel.HIGH,
        ),
        ReasonCode.OPERATION_FAILED: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION, RiskLevel.HIGH
        ),
        ReasonCode.MANUAL_ACTION_REQUIRED: ReasonSemantics(
            OperationPhase.BASELINE, RiskLevel.MEDIUM
        ),
        ReasonCode.REVIEW_PREPARED: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION, RiskLevel.NONE
        ),
        ReasonCode.MIGRATION_APPLIED: ReasonSemantics(
            OperationPhase.FINAL_VALIDATION, RiskLevel.LOW
        ),
    }
)


def reason_semantics(reason_code: ReasonCode) -> ReasonSemantics:
    """Return the one canonical semantic interpretation of a machine reason code."""
    return _REASON_SEMANTICS[reason_code]


class SystemActionCode(StrEnum):
    CHECKED_PROJECT = "CHECKED_PROJECT"
    CHECKED_TOOLS = "CHECKED_TOOLS"
    CHECKED_GIT = "CHECKED_GIT"
    RESOLVED_DEPENDENCIES = "RESOLVED_DEPENDENCIES"
    SYNCED_ENVIRONMENT = "SYNCED_ENVIRONMENT"
    VALIDATED_LOCK = "VALIDATED_LOCK"
    VALIDATED_ENVIRONMENT = "VALIDATED_ENVIRONMENT"
    RAN_SECURITY_AUDIT = "RAN_SECURITY_AUDIT"
    RAN_TESTS = "RAN_TESTS"
    CHECKED_INVARIANTS = "CHECKED_INVARIANTS"


class NamedGitPositionEvidence(StrictExplanationModel):
    state: Literal["named"]
    branch: str = Field(min_length=1)


class DetachedGitPositionEvidence(StrictExplanationModel):
    state: Literal["detached"]
    branch: Literal[None] = None


class UnavailableGitPositionEvidence(StrictExplanationModel):
    state: Literal["unavailable"]
    branch: Literal[None] = None


GitPositionEvidence = Annotated[
    NamedGitPositionEvidence | DetachedGitPositionEvidence | UnavailableGitPositionEvidence,
    Field(discriminator="state"),
]


class GitRemoteLookupUnavailableEvidence(StrictExplanationModel):
    state: Literal["lookup_unavailable"]


class NoGitRemoteEvidence(StrictExplanationModel):
    state: Literal["no_remote"]


class GitRemoteWithoutUpstreamEvidence(StrictExplanationModel):
    state: Literal["no_upstream"]
    remote_names: tuple[str, ...] = Field(min_length=1)


class GitRemoteComparisonUnavailableEvidence(StrictExplanationModel):
    state: Literal["comparison_unavailable"]
    remote_name: str = Field(min_length=1)
    upstream: str = Field(min_length=1)


class ComparedGitRemoteEvidence(StrictExplanationModel):
    state: Literal["up_to_date", "behind", "ahead", "diverged"]
    remote_name: str = Field(min_length=1)
    upstream: str = Field(min_length=1)


GitRemoteEvidence = Annotated[
    GitRemoteLookupUnavailableEvidence
    | NoGitRemoteEvidence
    | GitRemoteWithoutUpstreamEvidence
    | GitRemoteComparisonUnavailableEvidence
    | ComparedGitRemoteEvidence,
    Field(discriminator="state"),
]


class GitEvidence(StrictExplanationModel):
    position: GitPositionEvidence
    remote: GitRemoteEvidence | None = None
    unexpected_files: tuple[str, ...] = Field(default_factory=tuple)


class DependencyEvidence(StrictExplanationModel):
    state: Literal["plan_completed", "plan_failed", "updated", "no_updates", "failed"]


class RepositorySyncFailedValidationEvidence(StrictExplanationModel):
    state: Literal["repository_sync_failed"]


class RepositoryLockInvalidValidationEvidence(StrictExplanationModel):
    state: Literal["repository_lock_invalid"]


class RepositoryValidationCompletedEvidence(StrictExplanationModel):
    state: Literal["repository_completed"]
    environment: Literal[ValidationState.VALID, ValidationState.INVALID]
    security_audit: Literal[
        AuditState.PASSED,
        AuditState.VULNERABILITIES_FOUND,
        AuditState.UNAVAILABLE,
        AuditState.NOT_SUPPORTED,
        AuditState.SKIPPED,
    ]
    tests: Literal[CheckState.PASSED, CheckState.FAILED]


ValidationEvidence = Annotated[
    RepositorySyncFailedValidationEvidence
    | RepositoryLockInvalidValidationEvidence
    | RepositoryValidationCompletedEvidence,
    Field(discriminator="state"),
]


class ExplanationEvidence(StrictExplanationModel):
    exclusion_reason: str | None = None
    git: GitEvidence | None = None
    dependencies: DependencyEvidence | None = None
    validation: ValidationEvidence | None = None


class RepositoryExplanation(StrictExplanationModel):
    phase: OperationPhase
    reason_code: ReasonCode
    risk: RiskLevel = RiskLevel.NONE
    can_auto_fix: bool = False
    auto_fix_action: AutoFixAction | None = None
    evidence: ExplanationEvidence = Field(default_factory=ExplanationEvidence)
    system_actions: tuple[SystemActionCode, ...] = Field(default_factory=tuple)

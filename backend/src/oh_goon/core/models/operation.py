from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from oh_goon.core.capability_model import operation_evidence_kind
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.canonical_result_semantics import CanonicalPlanResult
from oh_goon.core.models.capability import CapabilitySnapshot
from oh_goon.core.models.explanation import ReasonCode
from oh_goon.core.models.fleet_plan_evidence import (
    FleetPlanResult,
)
from oh_goon.core.models.migration import MigrationResult
from oh_goon.core.models.policy import PolicySnapshot
from oh_goon.core.models.provenance import OperationProvenance
from oh_goon.core.models.recipe import UvRecipeResult
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.target import OperationTarget
from oh_goon.core.vocabulary import (
    AuditState,
    CheckState,
    GitFinalState,
    LockPresenceState,
    OperationAction,
    PythonPinReadiness,
    ValidationState,
    Verdict,
    WorktreeState,
)


class StrictModel(ImmutableModel):
    pass


class NamedGitPosition(StrictModel):
    state: Literal["named"]
    branch: str = Field(min_length=1)


class DetachedGitPosition(StrictModel):
    state: Literal["detached"]
    branch: Literal[None] = None


class UnavailableGitPosition(StrictModel):
    state: Literal["unavailable"]
    branch: Literal[None] = None


GitPosition = Annotated[
    NamedGitPosition | DetachedGitPosition | UnavailableGitPosition,
    Field(discriminator="state"),
]


class GitRemoteLookupUnavailable(StrictModel):
    state: Literal["lookup_unavailable"]


class NoGitRemote(StrictModel):
    state: Literal["no_remote"]


class GitRemoteWithoutUpstream(StrictModel):
    state: Literal["no_upstream"]
    remote_names: tuple[str, ...] = Field(min_length=1)


class GitRemoteComparisonUnavailable(StrictModel):
    state: Literal["comparison_unavailable"]
    remote_name: str = Field(min_length=1)
    upstream: str = Field(min_length=1)


class ComparedGitRemote(StrictModel):
    state: Literal["up_to_date", "behind", "ahead", "diverged"]
    remote_name: str = Field(min_length=1)
    upstream: str = Field(min_length=1)


GitRemoteObservation = Annotated[
    GitRemoteLookupUnavailable
    | NoGitRemote
    | GitRemoteWithoutUpstream
    | GitRemoteComparisonUnavailable
    | ComparedGitRemote,
    Field(discriminator="state"),
]


class ReadinessChecks(StrictModel):
    project: Literal["present", "missing"]
    working_tree: Literal[
        WorktreeState.UNKNOWN,
        WorktreeState.CLEAN,
        WorktreeState.DIRTY,
        WorktreeState.NOT_GIT,
    ]
    position: GitPosition
    remote: GitRemoteObservation
    uv_lock: Literal[LockPresenceState.PRESENT, LockPresenceState.MISSING]
    python_pin: Literal[
        PythonPinReadiness.OPTIONAL,
        PythonPinReadiness.PRESENT,
        PythonPinReadiness.REQUIRED_MISSING,
    ]


class MissingToolVersion(StrictModel):
    state: Literal["missing"]


class UnavailableToolVersion(StrictModel):
    state: Literal["version_unavailable"]


class AvailableToolVersion(StrictModel):
    state: Literal["available"]
    version: str = Field(min_length=1)


ToolVersionResult = Annotated[
    MissingToolVersion | UnavailableToolVersion | AvailableToolVersion,
    Field(discriminator="state"),
]


class MissingUvTool(StrictModel):
    state: Literal["missing"]


class RejectedUvProject(StrictModel):
    state: Literal["project_rejected"]
    detail: str = Field(min_length=1)


class ReadyUvTool(StrictModel):
    state: Literal["ready"]


UvToolResult = Annotated[
    MissingUvTool | RejectedUvProject | ReadyUvTool,
    Field(discriminator="state"),
]


class ToolchainResult(StrictModel):
    oh_goon_version: str = Field(min_length=1)
    uv: UvToolResult
    git: ToolVersionResult
    python: ToolVersionResult


class PythonRuntimeResult(StrictModel):
    requires: str | None = None
    pin: str | None = None
    selected: str | None = None


class PreservedGitHead(StrictModel):
    state: Literal["preserved"]
    head: str | None = None


class ChangedGitHead(StrictModel):
    state: Literal["changed"]
    before: str | None = None
    after: str | None = None


GitHeadTransition = Annotated[
    PreservedGitHead | ChangedGitHead,
    Field(discriminator="state"),
]


class ValidGitWorktreeInvariant(StrictModel):
    state: Literal["passed"]
    unexpected_files: tuple[str, ...] = Field(default=(), max_length=0)


class ViolatedGitWorktreeInvariant(StrictModel):
    state: Literal["failed"]
    unexpected_files: tuple[str, ...] = Field(min_length=1)


GitWorktreeInvariant = Annotated[
    ValidGitWorktreeInvariant | ViolatedGitWorktreeInvariant,
    Field(discriminator="state"),
]


class GitMutationResult(StrictModel):
    position: GitPosition
    initial_state: WorktreeState
    final_state: Literal[
        GitFinalState.CLEAN,
        GitFinalState.UNEXPECTED_CHANGES,
        GitFinalState.CHANGED_BY_UPDATE,
    ]
    head: GitHeadTransition
    worktree: GitWorktreeInvariant


class DependencyPlanCompleted(StrictModel):
    state: Literal["plan_completed"]


class DependencyPlanFailed(StrictModel):
    state: Literal["plan_failed"]


class DependenciesUpdated(StrictModel):
    state: Literal["updated"]


class NoDependencyUpdates(StrictModel):
    state: Literal["no_updates"]


class DependencyUpdateFailed(StrictModel):
    state: Literal["failed"]


DependencyResult = Annotated[
    DependencyPlanCompleted
    | DependencyPlanFailed
    | DependenciesUpdated
    | NoDependencyUpdates
    | DependencyUpdateFailed,
    Field(discriminator="state"),
]


class RepositorySyncFailedValidation(StrictModel):
    state: Literal["repository_sync_failed"]


class RepositoryLockInvalidValidation(StrictModel):
    state: Literal["repository_lock_invalid"]


class RepositoryValidationCompleted(StrictModel):
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


RepositoryValidationResult = Annotated[
    RepositorySyncFailedValidation
    | RepositoryLockInvalidValidation
    | RepositoryValidationCompleted,
    Field(discriminator="state"),
]


ValidationResult = Annotated[
    RepositorySyncFailedValidation
    | RepositoryLockInvalidValidation
    | RepositoryValidationCompleted,
    Field(discriminator="state"),
]


class ReadinessOperationEvidence(StrictModel):
    kind: Literal["READINESS"]
    readiness: ReadinessChecks
    tools: ToolchainResult
    python: PythonRuntimeResult
    policy: PolicySnapshot | None = None


class PlanOperationEvidence(StrictModel):
    kind: Literal["PLAN"]
    policy: PolicySnapshot | None = None
    python: PythonRuntimeResult | None = None
    dependencies: DependencyResult
    validation: ValidationResult | None = None


class UpdateOperationEvidence(StrictModel):
    kind: Literal["UPDATE"]
    policy: PolicySnapshot | None = None
    python: PythonRuntimeResult | None = None
    git: GitMutationResult | None = None
    dependencies: DependencyResult
    validation: ValidationResult | None = None


class VerifyOperationEvidence(StrictModel):
    kind: Literal["VERIFY"]
    policy: PolicySnapshot | None = None
    validation: ValidationResult


class ReviewOperationEvidence(StrictModel):
    kind: Literal["REVIEW"]
    policy: PolicySnapshot | None = None
    git: GitMutationResult | None = None


class RecipeOperationEvidence(StrictModel):
    kind: Literal["RECIPE"]
    recipe: UvRecipeResult


class MigrationOperationEvidence(StrictModel):
    kind: Literal["MIGRATION"]
    migration: MigrationResult


class FleetPlanOperationEvidence(StrictModel):
    kind: Literal["FLEET_PLAN"]
    plan: FleetPlanResult
    canonical_result: CanonicalPlanResult | None = None


OperationEvidence = Annotated[
    ReadinessOperationEvidence
    | PlanOperationEvidence
    | UpdateOperationEvidence
    | VerifyOperationEvidence
    | ReviewOperationEvidence
    | RecipeOperationEvidence
    | MigrationOperationEvidence
    | FleetPlanOperationEvidence,
    Field(discriminator="kind"),
]


class SuccessfulOperationOutcome(StrictModel):
    state: Literal["successful"]
    returncode: Literal[0] = 0
    verdict: Literal[Verdict.READY, Verdict.UPDATED, Verdict.UNCHANGED]
    reason_code: ReasonCode


class CompletedAttentionOperationOutcome(StrictModel):
    state: Literal["completed_attention"]
    returncode: int
    verdict: Literal[Verdict.MANUAL_ACTION_REQUIRED] = Verdict.MANUAL_ACTION_REQUIRED
    reason_code: ReasonCode


class BlockedOperationOutcome(StrictModel):
    state: Literal["blocked"]
    returncode: Literal[0] = 0
    verdict: Literal[Verdict.MANUAL_ACTION_REQUIRED] = Verdict.MANUAL_ACTION_REQUIRED
    reason_code: ReasonCode


PositiveExitCode = Annotated[int, Field(gt=0)]
NegativeExitCode = Annotated[int, Field(lt=0)]
NonZeroExitCode = PositiveExitCode | NegativeExitCode


class FailedOperationOutcome(StrictModel):
    state: Literal["failed"]
    returncode: NonZeroExitCode
    verdict: Literal[Verdict.FAILED] = Verdict.FAILED
    reason_code: ReasonCode


class FailedAttentionOperationOutcome(StrictModel):
    state: Literal["failed_attention"]
    returncode: NonZeroExitCode
    verdict: Literal[Verdict.MANUAL_ACTION_REQUIRED] = Verdict.MANUAL_ACTION_REQUIRED
    reason_code: ReasonCode


OperationOutcome = Annotated[
    SuccessfulOperationOutcome
    | CompletedAttentionOperationOutcome
    | BlockedOperationOutcome
    | FailedOperationOutcome
    | FailedAttentionOperationOutcome,
    Field(discriminator="state"),
]


class OperationReport(StrictModel):
    """Canonical immutable operation result with action-specific structured evidence."""

    provenance: OperationProvenance
    capability: CapabilitySnapshot
    target: OperationTarget | None = None
    repository: RepositoryIdentity
    outcome: OperationOutcome
    evidence: OperationEvidence

    @property
    def action(self) -> OperationAction:
        return self.capability.action

    @property
    def policy(self) -> PolicySnapshot | None:
        return _evidence_policy(self.evidence)

    @model_validator(mode="after")
    def validate_bound_intent_and_evidence(self) -> OperationReport:
        if not self.capability.matches_intent(self.capability.action, self.target):
            raise ValueError("Operation capability snapshot must match target kind")
        expected = operation_evidence_kind(self.action)
        if self.evidence.kind != expected.value:
            raise ValueError(
                f"Operation action {self.action.value} requires {expected.value} evidence, "
                f"not {self.evidence.kind}"
            )
        return self

    @model_validator(mode="after")
    def validate_policy_capture_time(self) -> OperationReport:
        policy = _evidence_policy(self.evidence)
        if policy is not None and not (
            self.provenance.started_at <= policy.captured_at <= self.provenance.finished_at
        ):
            raise ValueError("Policy snapshot must be captured within the operation interval")
        return self


def _evidence_policy(evidence: OperationEvidence) -> PolicySnapshot | None:
    if isinstance(
        evidence,
        (
            ReadinessOperationEvidence,
            PlanOperationEvidence,
            UpdateOperationEvidence,
            VerifyOperationEvidence,
            ReviewOperationEvidence,
        ),
    ):
        return evidence.policy
    return None


class OperationExecution(ImmutableModel):
    stdout: str
    stderr: str
    report: OperationReport

    @property
    def returncode(self) -> int:
        return self.report.outcome.returncode

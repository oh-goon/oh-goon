from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.execution_authority_diagnostics import (
    PlanExecutionAuthorityDiagnosticEvidence,
)
from oh_goon.core.models.execution_identity import ResolvedExecutionReference
from oh_goon.core.models.fleet_plan_evidence import (
    PlanArtifactEvidence,
    PlanOutputEvidence,
    PlanStepAttemptEvidence,
    PublishedPlanOutputEvidence,
)
from oh_goon.core.models.game_tape_observability import GameTapeObservabilityReceipt
from oh_goon.core.models.game_tape_team import GameTapeTeamComposition
from oh_goon.core.models.repository import RepositoryIdentity
from oh_goon.core.models.run_admission import FleetExecutionAdmission
from oh_goon.core.models.run_launch import (
    ApplicationLaunchReceipt,
    UnmanagedApplicationLaunchReceipt,
)
from oh_goon.core.models.scheduling import SchedulingEnvelope
from oh_goon.core.models.storage_maintenance import GameTapeStorage
from oh_goon.core.vocabulary import FleetRunSource, GameTapeEventType, GameTapeStatus


class GameTapeEnvironmentSource(ImmutableModel):
    """Safe provenance for one process export; values are deliberately absent."""

    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
    sources: tuple[str, ...] = Field(min_length=1, max_length=32)


class GameTapeSourceLocation(ImmutableModel):
    source: str = Field(min_length=1, max_length=240)
    line: int = Field(ge=1)
    column: int = Field(ge=1)


class GameTapeWhy(ImmutableModel):
    """Execution reachability facts already established by the admitted plan/executor."""

    declaration: str = Field(min_length=1, max_length=240)
    source_location: GameTapeSourceLocation | None = None
    previous_step_id: str | None = Field(default=None, min_length=1, max_length=80)
    previous_step_status: GameTapeStatus | None = None
    condition: str | None = Field(default=None, min_length=1, max_length=500)
    reached: bool
    explanation: str = Field(min_length=1, max_length=500)


class GameTapeFailureFingerprint(ImmutableModel):
    """Navigation fingerprint into durable output evidence; raw bytes remain transient."""

    exit_code: int
    reason_code: str | None = Field(default=None, min_length=1, max_length=120)
    attempt: int | None = Field(default=None, ge=1, le=5)
    signature_id: str = Field(min_length=1)
    evidence_stream: Literal["stdout", "stderr"] | None = None
    evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_line_count: int | None = Field(default=None, ge=0)
    evidence_byte_count: int | None = Field(default=None, ge=0)
    raw_log_available: Literal[False] = False

    @model_validator(mode="after")
    def validate_evidence_anchor(self) -> GameTapeFailureFingerprint:
        values = (self.evidence_sha256, self.evidence_line_count, self.evidence_byte_count)
        if self.evidence_stream is None and any(value is not None for value in values):
            raise ValueError("Failure evidence fields require an evidence_stream")
        if self.evidence_stream is not None and any(value is None for value in values):
            raise ValueError("Failure evidence_stream requires hash, line count and byte count")
        return self


class GameTapeAttemptTransition(ImmutableModel):
    """Deterministic comparison between adjacent canonical physical attempts."""

    from_attempt: int = Field(ge=1, le=5)
    to_attempt: int = Field(ge=2, le=5)
    retry_linked: bool
    duration_delta_ms: int
    returncode_changed: bool
    argv_changed: bool
    repository_state_continuity: bool
    tool_changed: bool
    egress_changed: bool
    resource_policy_changed: bool
    cache_input_changed: bool
    stdout_changed: bool
    stderr_changed: bool


class GameTapeRetryContextSummary(ImmutableModel):
    """Physical retry-context summary derived only from adjacent attempt evidence."""

    classification: Literal[
        "NO_ATTEMPT_EVIDENCE",
        "NOT_RETRIED",
        "SAME_PHYSICAL_CONTEXT",
        "CHANGED_PHYSICAL_CONTEXT",
        "INCOMPLETE_RETRY_EVIDENCE",
    ]
    retry_count: int = Field(ge=0, le=4)
    linked_retry_count: int = Field(ge=0, le=4)
    physical_context_changed: bool


class GameTapeStepExecution(ImmutableModel):
    """One identity reused by Flow, Timeline, Logs/evidence and the inspector."""

    step_execution_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    order: int = Field(ge=1)
    status: GameTapeStatus
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    command: tuple[str, ...] = Field(default_factory=tuple, max_length=64)
    working_directory: str = Field(default=".", min_length=1, max_length=240)
    environment: tuple[GameTapeEnvironmentSource, ...] = Field(default_factory=tuple, max_length=64)
    exit_code: int | None = None
    attempts: tuple[PlanStepAttemptEvidence, ...] = Field(default_factory=tuple, max_length=5)
    attempt_transitions: tuple[GameTapeAttemptTransition, ...] = Field(
        default_factory=tuple, max_length=4
    )

    @computed_field
    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @computed_field
    @property
    def retry_context(self) -> GameTapeRetryContextSummary:
        from oh_goon.core.game_tape_retry_context import summarize_retry_context

        return summarize_retry_context(self.attempt_count, self.attempt_transitions)

    stdout_evidence: PlanOutputEvidence | None = None
    stderr_evidence: PlanOutputEvidence | None = None
    output_names: tuple[str, ...] = Field(default_factory=tuple, max_length=16)
    artifacts: tuple[PlanArtifactEvidence, ...] = Field(default_factory=tuple, max_length=64)
    outputs: tuple[PublishedPlanOutputEvidence, ...] = Field(default_factory=tuple, max_length=16)
    reason: str | None = Field(default=None, max_length=500)
    why: GameTapeWhy
    failure: GameTapeFailureFingerprint | None = None
    execution_authority: PlanExecutionAuthorityDiagnosticEvidence | None = None

    @model_validator(mode="after")
    def validate_time_order(self) -> GameTapeStepExecution:
        if self.started_at is not None and self.finished_at is not None:
            if self.finished_at < self.started_at:
                raise ValueError("Game Tape step finished_at cannot precede started_at")
        return self


class GameTapeToolFingerprint(ImmutableModel):
    name: str = Field(min_length=1, max_length=240)
    origin: str = Field(min_length=1, max_length=32)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)


class GameTapeRuntimeFact(ImmutableModel):
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=120)
    source: Literal["declared_managed_runtime"]


class GameTapeEnvironmentFingerprint(ImmutableModel):
    """Immutable execution context projected only from already-durable evidence."""

    repository_head_sha: str | None = Field(default=None, max_length=80)
    branch_state: str | None = Field(default=None, max_length=40)
    branch: str | None = Field(default=None, max_length=240)
    plan_source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    plan_contract_version: int | None = Field(default=None, ge=1)
    execution_input_id: str | None = Field(default=None, min_length=1)
    requirements_validation_id: str | None = Field(default=None, min_length=1)
    base_workspace_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_root_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    host_toolchain_id: str | None = Field(default=None, min_length=1)
    isolation_backend_id: str | None = Field(default=None, min_length=1, max_length=80)
    isolation_configuration_id: str | None = Field(default=None, min_length=1)
    isolation_kernel_boundary: str | None = Field(default=None, min_length=1, max_length=80)
    isolation_remote_execution: bool | None = None
    isolation_experimental: bool | None = None
    host_environment_names: tuple[str, ...] = Field(default_factory=tuple, max_length=128)
    managed_input_count: int = Field(default=0, ge=0)
    content_input_count: int = Field(default=0, ge=0)
    tools: tuple[GameTapeToolFingerprint, ...] = Field(default_factory=tuple, max_length=64)
    runtime_facts: tuple[GameTapeRuntimeFact, ...] = Field(default_factory=tuple, max_length=32)


class GameTapeShardAttemptLineage(ImmutableModel):
    """Oh-Goon physical attempt identity kept distinct from producer-described work."""

    attempt_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    physical_work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt: int = Field(ge=1, le=5)
    status: str = Field(min_length=1, max_length=80)
    runtime_authority_receipt_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    supervisor_receipt_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    process_group_quiescent: bool | None = None
    forced_termination: bool | None = None


class GameTapeShardLineage(ImmutableModel):
    """Producer contract/work identity plus the physical/refined Oh-Goon attempt lineage."""

    producer_contract_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    producer_artifact_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    producer_contract_schema: str | None = Field(default=None, min_length=1, max_length=160)
    producer_version: str | None = Field(default=None, min_length=1, max_length=80)
    shard_id: str = Field(min_length=1)
    described_work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    physical_work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    parent_work_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    refinement_depth: int = Field(default=0, ge=0, le=32)
    status: str = Field(min_length=1, max_length=80)
    resumed: bool = False
    members: tuple[str, ...] = ()
    attempts: tuple[GameTapeShardAttemptLineage, ...] = ()


class GameTapeProducerImplementationInput(ImmutableModel):
    """Safe package-relative Hashmarks input diagnostic; never execution authority."""

    path: str = Field(min_length=1, max_length=500)
    byte_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class GameTapeProducerImplementationProvenance(ImmutableModel):
    """Producer-owned explanation projected only when bound to admitted identity."""

    producer_schema: str = Field(min_length=1, max_length=160)
    authority: Literal["non-authoritative-explanation"]
    storage: Literal["derived-not-persisted"]
    identity_schema: str = Field(min_length=1, max_length=160)
    implementation_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    input_count: int = Field(ge=0)
    inputs: tuple[GameTapeProducerImplementationInput, ...] = ()

    @model_validator(mode="after")
    def validate_input_count(self) -> GameTapeProducerImplementationProvenance:
        if self.input_count != len(self.inputs):
            raise ValueError("producer implementation provenance input_count must match inputs")
        if len({item.path for item in self.inputs}) != len(self.inputs):
            raise ValueError("producer implementation provenance input paths must be unique")
        return self


class GameTapeVerificationExecutionBoundary(ImmutableModel):
    """Hashmarks selection identity bound to Oh-Goon execution/certification evidence."""

    producer_version: str = Field(min_length=1, max_length=80)
    producer_artifact_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer_implementation_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer_implementation_provenance: GameTapeProducerImplementationProvenance | None = None
    producer_contract_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repository_identity: str = Field(min_length=1)
    revision_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selection_evidence_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer_freshness_state: Literal["PROVEN_FRESH"] = "PROVEN_FRESH"
    producer_provenance_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer_repository_generation: int | None = Field(default=None, ge=0)
    producer_identity_generation: int | None = Field(default=None, ge=0)
    producer_evidence_hashes: tuple[tuple[str, str], ...] = ()
    selected_membership_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    selected_members: tuple[str, ...] = ()
    actual_executed_membership_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    actual_executed_members: tuple[str, ...] = ()
    execution_plan_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_context_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_policy_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    membership_complete: bool
    selection_state: Literal["ADMITTED_FRESH"] = "ADMITTED_FRESH"
    unproven_owner_members: tuple[str, ...] = ()


class GameTapeRuntimeProgress(ImmutableModel):
    """Read-only projection of durable lifecycle facts for active repository work."""

    journal_sequence: int = Field(ge=1)
    observed_at: AwareDatetime
    step_id: str | None = Field(default=None, min_length=1)
    step_order: int | None = Field(default=None, ge=1)
    attempt_number: int | None = Field(default=None, ge=1)
    physical_execution_id: str | None = Field(default=None, min_length=1)
    process_state: Literal["NOT_STARTED", "STARTING", "RUNNING", "UNKNOWN", "EXITED", "QUIESCENT"]
    pid: int | None = Field(default=None, ge=1)
    termination_reason: str | None = Field(default=None, min_length=1)


class GameTapeRepositoryExecution(ImmutableModel):
    repository_execution_id: str = Field(min_length=1)
    repository: RepositoryIdentity
    status: GameTapeStatus
    operation_id: str | None = Field(default=None, min_length=1)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    fingerprint: GameTapeEnvironmentFingerprint
    steps: tuple[GameTapeStepExecution, ...] = Field(default_factory=tuple)
    runtime_progress: GameTapeRuntimeProgress | None = None

    @computed_field
    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return max(0, int((self.finished_at - self.started_at).total_seconds() * 1000))

    def _bottleneck(self) -> tuple[str | None, int | None]:
        timed = tuple(step for step in self.steps if step.duration_ms is not None)
        if not timed:
            return None, None
        total = sum(step.duration_ms or 0 for step in timed)
        slowest = max(timed, key=lambda step: step.duration_ms or 0)
        if total <= 0:
            return slowest.step_id, 0
        return slowest.step_id, round((slowest.duration_ms or 0) * 100 / total)

    @computed_field
    @property
    def bottleneck_step_id(self) -> str | None:
        return self._bottleneck()[0]

    @computed_field
    @property
    def bottleneck_percent(self) -> int | None:
        return self._bottleneck()[1]

    shard_lineage: tuple[GameTapeShardLineage, ...] = Field(default_factory=tuple)
    verification_execution: GameTapeVerificationExecutionBoundary | None = None


class GameTapeRequirementFact(ImmutableModel):
    order: int = Field(ge=1)
    kind: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=500)


class GameTapeIssueFocus(ImmutableModel):
    """Bounded drill-down target projected only from canonical run/repository/step failures."""

    scope: Literal["RUN", "REPOSITORY", "STEP"]
    repository_execution_id: str | None = Field(default=None, min_length=1)
    repository_id: str | None = Field(default=None, min_length=1)
    repository_name: str | None = Field(default=None, min_length=1)
    step_execution_id: str | None = Field(default=None, min_length=1)
    step_id: str | None = Field(default=None, min_length=1, max_length=80)
    occurred_at: AwareDatetime | None = None
    code: str | None = Field(default=None, min_length=1, max_length=120)
    message: str | None = Field(default=None, min_length=1, max_length=500)
    exit_code: int | None = None
    signature_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_scope_identity(self) -> GameTapeIssueFocus:
        repository_identity = (
            self.repository_execution_id,
            self.repository_id,
            self.repository_name,
        )
        step_identity = (self.step_execution_id, self.step_id)
        if self.scope == "RUN":
            if any(value is not None for value in (*repository_identity, *step_identity)):
                raise ValueError("Run issue focus must not carry repository or step identity")
            return self
        if any(value is None for value in repository_identity):
            raise ValueError("Repository and step issue focus require exact repository identity")
        if self.scope == "REPOSITORY":
            if any(value is not None for value in step_identity):
                raise ValueError("Repository issue focus must not carry step identity")
            return self
        if any(value is None for value in step_identity):
            raise ValueError("Step issue focus requires exact step identity")
        return self


class GameTapeWhyNotDiagnostic(ImmutableModel):
    """Derived operator-facing refusal/failure explanation from durable run failure truth."""

    code: str = Field(min_length=1, max_length=120)
    stage: str = Field(min_length=1, max_length=80)
    retryable: bool
    authority_owner: Literal["execution_authority", "fleet_run"]
    message: str = Field(min_length=1, max_length=1000)
    safe_next_action: str = Field(min_length=1, max_length=500)


class GameTapeSummary(ImmutableModel):
    repositories: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    attention_required: int = Field(ge=0)
    not_run: int = Field(ge=0)
    planned_steps: int = Field(ge=0)
    executed_steps: int = Field(ge=0)
    passed_steps: int = Field(ge=0)
    failed_steps: int = Field(ge=0)
    skipped_steps: int = Field(ge=0)
    not_run_steps: int = Field(ge=0)


class GameTapeReceipt(ImmutableModel):
    validation: Literal["PASSED"] = "PASSED"
    requirements_admitted: int | None = Field(default=None, ge=0)
    accepted_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None

    @computed_field
    @property
    def duration_ms(self) -> int | None:
        """Derive run duration from the lifecycle timestamps owned by this receipt."""
        if self.started_at is None or self.finished_at is None:
            return None
        return max(0, int((self.finished_at - self.started_at).total_seconds() * 1000))


class GameTapeEvent(ImmutableModel):
    """One chronological read event projected from a named canonical durable fact."""

    sequence: int = Field(ge=1)
    authority_sequence: int = Field(ge=1)
    event_id: str = Field(min_length=1)
    event_type: GameTapeEventType
    occurred_at: AwareDatetime
    status: GameTapeStatus | None = None
    repository_execution_id: str | None = Field(default=None, min_length=1)
    repository_id: str | None = Field(default=None, min_length=1)
    repository_name: str | None = Field(default=None, min_length=1)
    step_execution_id: str | None = Field(default=None, min_length=1)
    step_id: str | None = Field(default=None, min_length=1)
    step_order: int | None = Field(default=None, ge=1)
    step_attempt_id: str | None = Field(default=None, min_length=1)
    attempt_number: int | None = Field(default=None, ge=1)
    physical_execution_id: str | None = Field(default=None, min_length=1)
    pid: int | None = Field(default=None, ge=1)
    process_identity: str | None = Field(default=None, min_length=1)
    returncode: int | None = None
    source: Literal[
        "run_lifecycle", "admitted_plan", "operation_report", "step_execution", "lifecycle_event"
    ]
    message: str = Field(min_length=1, max_length=500)


class GameTape(ImmutableModel):
    """Read-only projection of canonical Fleet admission and execution evidence."""

    fleet_run_id: str = Field(min_length=1)
    run_source: FleetRunSource = FleetRunSource.MANUAL
    execution: FleetExecutionAdmission
    application_launch: ApplicationLaunchReceipt = Field(
        default_factory=UnmanagedApplicationLaunchReceipt
    )
    scheduling: SchedulingEnvelope | None = None
    status: GameTapeStatus
    execution_reference: ResolvedExecutionReference | None = None
    requirements: tuple[GameTapeRequirementFact, ...] = Field(default_factory=tuple, max_length=64)
    receipt: GameTapeReceipt
    storage: GameTapeStorage
    issue_count: int = Field(default=0, ge=0)
    issue_focus: tuple[GameTapeIssueFocus, ...] = Field(default_factory=tuple, max_length=5)
    enforcer_receipt_available: bool = False
    why_not: GameTapeWhyNotDiagnostic | None = None

    @computed_field
    @property
    def hidden_issue_count(self) -> int:
        return max(0, self.issue_count - len(self.issue_focus))

    play_by_play: tuple[GameTapeEvent, ...] = Field(default_factory=tuple)
    repositories: tuple[GameTapeRepositoryExecution, ...]

    @computed_field
    @property
    def summary(self) -> GameTapeSummary:
        """Derive bounded run/repository/step counts from the repositories already owned by the tape."""
        from oh_goon.core.game_tape_run_facts import project_summary

        return project_summary(self.repositories)

    @computed_field
    @property
    def team(self) -> GameTapeTeamComposition:
        """Derive team composition from repository execution truth already owned by the tape."""
        from oh_goon.core.game_tape_run_facts import project_repository_failure_focus
        from oh_goon.core.game_tape_team import project_team_composition

        failures = project_repository_failure_focus(self.repositories)
        return project_team_composition(self.repositories, failures)

    @computed_field
    @property
    def observability(self) -> GameTapeObservabilityReceipt:
        """Expose the current Game Tape evidence-lifecycle policy without storing a per-run copy."""
        from oh_goon.core.game_tape_observability import project_observability_receipt

        return project_observability_receipt()

    @property
    def plan_id(self) -> str | None:
        reference = self.execution_reference
        return reference.plan_id if reference is not None else None

    @property
    def plan_name(self) -> str | None:
        reference = self.execution_reference
        return reference.plan_name if reference is not None else None

    @property
    def plan_source_sha256(self) -> str | None:
        reference = self.execution_reference
        return reference.source.sha256 if reference is not None else None

    @property
    def execution_digest(self) -> str | None:
        reference = self.execution_reference
        return reference.identity.digest if reference is not None else None

    @model_validator(mode="after")
    def validate_issue_focus_counts(self) -> GameTape:
        if self.issue_count < len(self.issue_focus):
            raise ValueError("Game Tape issue count cannot be smaller than visible focus")
        if self.hidden_issue_count > 0 and len(self.issue_focus) != 5:
            raise ValueError("Hidden Game Tape issues require the complete five-item visible focus")
        return self


class GameTapeStepDiff(ImmutableModel):
    step_id: str = Field(min_length=1, max_length=80)
    base_status: GameTapeStatus | None = None
    compared_status: GameTapeStatus | None = None
    base_duration_ms: int | None = Field(default=None, ge=0)
    compared_duration_ms: int | None = Field(default=None, ge=0)
    duration_delta_ms: int | None = None
    command_changed: bool
    provenance_changed: bool
    base_attempt_count: int = Field(ge=0, le=5)
    compared_attempt_count: int = Field(ge=0, le=5)
    attempt_count_changed: bool
    tool_changed: bool
    egress_changed: bool
    resource_policy_changed: bool
    failure_signature_changed: bool


class GameTapeRepositoryDiff(ImmutableModel):
    repository_id: str = Field(min_length=1)
    repository_name: str = Field(min_length=1)
    base_head_sha: str | None = Field(default=None, max_length=80)
    compared_head_sha: str | None = Field(default=None, max_length=80)
    base_branch: str | None = Field(default=None, max_length=240)
    compared_branch: str | None = Field(default=None, max_length=240)
    workspace_changed: bool
    runtime_changed: bool
    toolchain_changed: bool
    isolation_changed: bool
    steps: tuple[GameTapeStepDiff, ...] = Field(default_factory=tuple)


class GameTapeRunDiff(ImmutableModel):
    base_run_id: str = Field(min_length=1)
    compared_run_id: str = Field(min_length=1)
    base_plan_id: str | None = Field(default=None, min_length=1)
    compared_plan_id: str | None = Field(default=None, min_length=1)
    plan_changed: bool
    base_duration_ms: int | None = Field(default=None, ge=0)
    compared_duration_ms: int | None = Field(default=None, ge=0)
    duration_delta_ms: int | None = None
    repositories: tuple[GameTapeRepositoryDiff, ...] = Field(default_factory=tuple)

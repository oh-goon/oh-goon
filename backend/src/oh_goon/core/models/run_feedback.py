"""Bounded observations for an agent inspecting one original Run read cut."""

from typing import Literal

from pydantic import AwareDatetime, Field

from oh_goon.core.models.agent_continuation_receipt import AgentContinuationState
from oh_goon.core.models.agent_controls import AgentRunControls
from oh_goon.core.models.agent_repair import AgentRepairBudget
from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_evidence import PlanOutputEvidence, PlanStepSettlementEvidence
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits, PlanExecutionPolicy
from oh_goon.core.models.game_tape import (
    GameTapeShardLineage,
    GameTapeVerificationExecutionBoundary,
)

MAX_RUN_FEEDBACK_BYTES = 64 * 1024
MAX_RUN_FEEDBACK_STEPS = 16
MAX_RUN_FEEDBACK_SOURCES = 8
MAX_RUN_FEEDBACK_GOALS = 8
MAX_RUN_FEEDBACK_VERIFICATIONS = 8
RunFeedbackRecoveryAction = Literal["readmit-placement", "run-same-target", "open-recovery", "none"]


class RunFeedbackDiagnostic(ImmutableModel):
    classification: Literal[
        "known_code_failure",
        "environment_or_launch_failure",
        "verification_unavailable",
        "model_output_truncated",
        "cancelled",
        "timed_out",
        "unknown_execution",
        "active",
        "success",
        "skipped",
    ]
    owner: Literal[
        "fleet_step_settlement", "agent_campaign_settlement", "fleet_shard_execution", "fleet_run"
    ]
    reason: str = Field(max_length=160)
    omitted_reason_characters: int = Field(default=0, ge=0)


class RunFeedbackGoal(ImmutableModel):
    step_id: str
    availability: Literal["recorded", "unavailable"]
    summary: str | None = None
    sha256: str | None = None
    omitted_characters: int = Field(default=0, ge=0)
    redacted: bool = False
    handoff_identity: str
    delivery_sha256: str


class RunFeedbackConstraints(ImmutableModel):
    execution_policy: PlanExecutionPolicy | None = None
    limits: FleetPlanRunLimits | None = None
    write_roots: tuple[str, ...] = ()
    omitted_write_roots: int = Field(default=0, ge=0)


class RunFeedbackCampaign(ImmutableModel):
    campaign_id: str | None
    current_candidate_identity: str | None
    scope_identity: str | None
    run_candidate_identity: str | None = None
    run_scope_identity: str | None = None
    candidate_freshness: Literal["recorded_not_live"] = "recorded_not_live"
    original_execution_digest: str | None
    phase: str | None
    attempt: int


class RunFeedbackBudgets(ImmutableModel):
    original: AgentRepairBudget | None = None
    maximum_runs: int | None = None
    remaining_runs: int | None = None
    remaining_runs_authority: Literal["committed_decision", "unavailable"] = "unavailable"
    decision_id: str | None = None
    decision_phase_ordinal: int | None = None
    deadline_at: AwareDatetime | None = None
    elapsed_budget: Literal["not_recomputed"] = "not_recomputed"
    model_usage: Literal["unavailable"] = "unavailable"


class RunFeedbackSource(ImmutableModel):
    repository_id: str
    admission_id: str
    source_authority_id: str
    source_subject_instance_id: str
    source_content_sha256: str


class RunFeedbackAttempt(ImmutableModel):
    attempt: int | None = None
    physical_execution_id: str | None = None
    observed_returncode: int | None = None
    final_process_group_quiescent: bool | None = None
    forced_termination: bool | None = None
    cleanup_reason: str | None = None


class RunFeedbackOutput(ImmutableModel):
    name: str
    sha256: str
    byte_count: int
    producer_execution_identity: str | None = None
    retained_sha256: str | None = None
    physical_execution_id: str | None = None


class RunFeedbackRetainedDiagnostic(ImmutableModel):
    """Only original, completely validated regression feedback may carry text."""

    output_name: str
    physical_execution_id: str
    sha256: str
    byte_count: int
    text: str = Field(max_length=2048)
    redacted: bool
    authority: Literal["untrusted_recorded_data"] = "untrusted_recorded_data"


class RunFeedbackStep(ImmutableModel):
    repository_id: str
    repository_execution_id: str
    step_id: str
    step_execution_id: str
    status: str
    diagnostic: RunFeedbackDiagnostic
    settlement: PlanStepSettlementEvidence
    attempts: tuple[RunFeedbackAttempt, ...] = Field(default=(), max_length=5)
    cancellation: RunFeedbackAttempt | None = None
    stdout_evidence: PlanOutputEvidence | None = None
    stderr_evidence: PlanOutputEvidence | None = None
    outputs: tuple[RunFeedbackOutput, ...] = Field(default=(), max_length=4)
    omitted_outputs: int = Field(default=0, ge=0)
    retained_diagnostics: tuple[RunFeedbackRetainedDiagnostic, ...] = Field(
        default=(), max_length=4
    )
    omitted_retained_diagnostics: int = Field(default=0, ge=0)


class RunFeedbackVerification(ImmutableModel):
    repository_id: str
    availability: Literal["recorded", "unavailable", "omitted"]
    selected_count: int = Field(ge=0)
    actual_count: int = Field(ge=0)
    result_count: int = Field(ge=0)
    omitted_selected_members: int = Field(default=0, ge=0)
    omitted_actual_members: int = Field(default=0, ge=0)
    omitted_results: int = Field(default=0, ge=0)
    selection_evidence_identity: str | None = None
    selected_membership_identity: str | None = None
    actual_membership_identity: str | None = None
    evidence: GameTapeVerificationExecutionBoundary | None = None
    results: tuple[GameTapeShardLineage, ...] = ()
    qualification: Literal[
        "original_producer_qualifications", "unavailable", "omitted_not_qualified"
    ]


class RunFeedbackOmissions(ImmutableModel):
    steps: int = Field(default=0, ge=0)
    goals: int = Field(default=0, ge=0)
    sources: int = Field(default=0, ge=0)
    verifications: int = Field(default=0, ge=0)
    fields: tuple[str, ...] = ()
    response_limit_applied: bool = False


class RunFeedbackReadbackReference(ImmutableModel):
    """Identifiers for existing read owners, never an executable recovery command."""

    projection: Literal["detail", "game_tape", "outputs"]
    fleet_run_id: str | None
    fleet_run_id_sha256: str
    evidence: Literal[
        "original_run", "complete_verification_membership", "retained_output_references"
    ]


class RunFeedback(ImmutableModel):
    schema_name: Literal["oh-goon.run-feedback/v1"] = Field(
        default="oh-goon.run-feedback/v1", alias="schema"
    )
    fleet_run_id: str | None
    fleet_run_id_sha256: str
    journal_revision: int | None
    status: str
    outcome: str
    terminal: bool
    continuation_state: AgentContinuationState | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    diagnostic: RunFeedbackDiagnostic
    recovery_action: RunFeedbackRecoveryAction
    goals: tuple[RunFeedbackGoal, ...] = Field(default=(), max_length=MAX_RUN_FEEDBACK_GOALS)
    constraints: RunFeedbackConstraints
    campaign: RunFeedbackCampaign | None = None
    budgets: RunFeedbackBudgets | None = None
    sources: tuple[RunFeedbackSource, ...] = Field(default=(), max_length=MAX_RUN_FEEDBACK_SOURCES)
    steps: tuple[RunFeedbackStep, ...] = Field(default=(), max_length=MAX_RUN_FEEDBACK_STEPS)
    verification: tuple[RunFeedbackVerification, ...] = Field(
        default=(), max_length=MAX_RUN_FEEDBACK_VERIFICATIONS
    )
    controls: AgentRunControls
    omissions: RunFeedbackOmissions
    authority: Literal["observation_only"] = "observation_only"
    context_authority: Literal["untrusted_data_no_effect_or_recovery_permission"] = (
        "untrusted_data_no_effect_or_recovery_permission"
    )
    native_claims: Literal["not_projected"] = "not_projected"
    readback: tuple[RunFeedbackReadbackReference, ...] = Field(max_length=3)

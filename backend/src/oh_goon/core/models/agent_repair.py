"""Caller limits and receipts for bounded repair through ordinary Fleet Runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits

if TYPE_CHECKING:
    from oh_goon.core.models.run import FleetRunDetail


MAX_AGENT_CAMPAIGN_MESSAGE_CHARS = 4096
TRUNCATED_AGENT_CAMPAIGN_MESSAGE = (
    "Campaign diagnostic exceeded its redacted retention budget [truncated]"
)


class AgentRepairBudget(ImmutableModel):
    max_attempts: int = Field(strict=True, ge=1, le=32)
    timeout_seconds: float = Field(strict=True, gt=0, le=86400, allow_inf_nan=False)
    step_timeout_seconds: float = Field(strict=True, gt=0, le=21600, allow_inf_nan=False)


class AgentRepairContext(ImmutableModel):
    """Frozen limits transported in each original admitted target, never model data."""

    repair_id: str = Field(min_length=1, max_length=160)
    attempt: int = Field(strict=True, ge=1, le=160)
    max_attempts: int = Field(strict=True, ge=1, le=160)
    limits: FleetPlanRunLimits
    previous_run_id: str | None = Field(default=None, min_length=1)
    phase: Literal["review", "adjudicate", "repair", "regression", "verification"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    review_pass: int | None = Field(
        default=None, ge=1, le=32, exclude_if=lambda value: value is None
    )
    original_execution_digest: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    candidate_identity: str | None = Field(default=None, exclude_if=lambda value: value is None)
    scope_identity: str | None = Field(default=None, exclude_if=lambda value: value is None)
    write_roots: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)
    campaign_revision: int | None = Field(
        default=None, ge=1, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_attempt(self) -> AgentRepairContext:
        if self.attempt > self.max_attempts:
            raise ValueError("repair attempt exceeds caller budget")
        if (self.previous_run_id is None) != (self.attempt == 1):
            raise ValueError("repair continuation must identify its previous Run")
        metadata = (
            self.review_pass,
            self.original_execution_digest,
            self.candidate_identity,
            self.scope_identity,
        )
        if self.phase is None and (
            any(value is not None for value in metadata) or self.write_roots
        ):
            raise ValueError("phase metadata requires an explicit phase")
        if self.phase is not None and (
            any(value is None for value in metadata) or not self.write_roots
        ):
            raise ValueError(
                "phase admission requires original execution, candidate, scope and write authority"
            )
        return self


class AgentRepairStopReason(StrEnum):
    CONVERGED = "converged"
    REVIEW_INCOMPLETE = "review_incomplete"
    ADJUDICATION_INCOMPLETE = "adjudication_incomplete"
    REGRESSION_FAILED = "regression_failed"
    VERIFICATION_FAILED = "verification_failed"
    NO_PROGRESS = "no_progress"
    TEST_PASSED = "test_passed"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    FEEDBACK_UNAVAILABLE = "feedback_unavailable"
    PROPOSAL_FAILED = "proposal_failed"
    APPLY_FAILED = "apply_failed"
    SOURCE_CHANGED = "source_changed"
    EXECUTION_UNRESOLVED = "execution_unresolved"


@dataclass(frozen=True, slots=True)
class AgentRepairResult:
    """Committed campaign receipt; child Runs retain execution-result authority."""

    repair_id: str
    budget: AgentRepairBudget
    stop_reason: AgentRepairStopReason
    attempts: tuple[FleetRunDetail, ...]
    message: str = ""
    admitted_run_ids: tuple[str, ...] = ()
    unresolved_run_ids: tuple[str, ...] = ()
    committed_decision_id: str | None = None
    journal_revision: int | None = None
    campaign_journal: AgentCampaignReadback | None = None

    @property
    def test_passed(self) -> bool:
        return self.stop_reason in (
            AgentRepairStopReason.TEST_PASSED,
            AgentRepairStopReason.CONVERGED,
        )

    @property
    def converged(self) -> bool:
        return self.stop_reason is AgentRepairStopReason.CONVERGED


class AgentCampaignIntent(ImmutableModel):
    """Bounded original intent retained once before phase admission."""

    kind: Literal["intent"] = "intent"
    campaign_id: str = Field(min_length=1, max_length=160)
    expected_revision: Literal[0] = 0
    repository_id: str = Field(min_length=1)
    original_execution_digest: str = Field(min_length=1)
    budget: AgentRepairBudget
    maximum_runs: int = Field(ge=1, le=160)
    deadline_at: AwareDatetime
    initial_phase: str = Field(min_length=1)
    initial_step_ids: tuple[str, ...] = Field(min_length=1)
    candidate_identity: str | None = None
    scope_identity: str | None = None
    write_roots: tuple[str, ...] = ()


class AgentCampaignPhase(ImmutableModel):
    """Exact selected membership, appended in the Run admission transaction."""

    kind: Literal["phase"] = "phase"
    campaign_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    fleet_run_id: str = Field(min_length=1)
    context: AgentRepairContext
    execution_digest: str = Field(min_length=1)
    selected_step_ids: tuple[str, ...] = Field(min_length=1)


class AgentCampaignRunEvidence(ImmutableModel):
    fleet_run_id: str = Field(min_length=1)
    terminal_sequence: int = Field(ge=1)
    operation_ids: tuple[str, ...] = ()


class AgentCampaignUnstartedSelection(ImmutableModel):
    """A requested identity with no admitted Run at the decision read cut."""

    selected_run_id: str = Field(min_length=1)
    phase_ordinal: int = Field(ge=1, le=160)
    phase: str = Field(min_length=1)
    selected_step_ids: tuple[str, ...] = Field(min_length=1)
    admission_status: Literal["not_admitted"] = "not_admitted"


class AgentCampaignDecision(ImmutableModel):
    """Policy output with bounded references to the settled inputs it consumed."""

    kind: Literal["decision"] = "decision"
    campaign_id: str = Field(min_length=1, max_length=160)
    expected_revision: int = Field(ge=1)
    decision_id: str = Field(min_length=1, max_length=160)
    phase_ordinal: int = Field(ge=0, le=160)
    remaining_runs: int = Field(ge=0, le=160)
    consumed_runs: tuple[AgentCampaignRunEvidence, ...] = ()
    candidate_identity: str | None = None
    scope_identity: str | None = None
    next_phase: str | None = None
    next_step_ids: tuple[str, ...] = ()
    unstarted_selection: AgentCampaignUnstartedSelection | None = None
    stop_reason: AgentRepairStopReason | None = None
    passed_test_step_id: str | None = Field(default=None, min_length=1)
    unresolved_run_ids: tuple[str, ...] = ()
    message: str = Field(default="", max_length=MAX_AGENT_CAMPAIGN_MESSAGE_CHARS)

    @model_validator(mode="after")
    def validate_decision(self) -> AgentCampaignDecision:
        if (self.stop_reason is AgentRepairStopReason.TEST_PASSED) != (
            self.passed_test_step_id is not None
        ):
            raise ValueError(
                "Test-passed decision requires exactly its accepted native test reference"
            )
        if self.stop_reason is None and (self.next_phase is None or not self.next_step_ids):
            raise ValueError("advancement requires the selected next phase")
        if self.stop_reason is not None and (self.next_phase is not None or self.next_step_ids):
            raise ValueError("terminal decision cannot select another phase")
        if self.unstarted_selection is not None and self.stop_reason is None:
            raise ValueError("Unstarted selection requires a blocking terminal decision")
        return self


AgentCampaignFact = Annotated[
    AgentCampaignIntent | AgentCampaignPhase | AgentCampaignDecision,
    Field(discriminator="kind"),
]


class AgentCampaignRecord(ImmutableModel):
    session_id: str = Field(min_length=1)
    revision: int = Field(ge=1)
    fact: AgentCampaignFact


class AgentCampaignReadback(ImmutableModel):
    """Complete verified Journal cut; observing it never resumes work."""

    campaign_id: str
    journal_revision: int = Field(ge=0)
    records: tuple[AgentCampaignRecord, ...]
    complete: bool = True

    @property
    def latest_revision(self) -> int:
        return self.records[-1].revision if self.records else 0

    @property
    def intent(self) -> AgentCampaignIntent | None:
        if self.records and isinstance(self.records[0].fact, AgentCampaignIntent):
            return self.records[0].fact
        return None

    @property
    def phases(self) -> tuple[AgentCampaignPhase, ...]:
        return tuple(
            record.fact for record in self.records if isinstance(record.fact, AgentCampaignPhase)
        )

    @property
    def terminal_decision(self) -> AgentCampaignDecision | None:
        if self.records and isinstance(self.records[-1].fact, AgentCampaignDecision):
            decision = self.records[-1].fact
            return decision if decision.stop_reason is not None else None
        return None

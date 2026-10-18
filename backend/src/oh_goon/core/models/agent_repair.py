"""Caller limits and receipts for bounded repair through ordinary Fleet Runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.fleet_plan_execution_policy import FleetPlanRunLimits

if TYPE_CHECKING:
    from oh_goon.core.models.run import FleetRunDetail


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

    @model_validator(mode="after")
    def validate_attempt(self):
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
    """Invocation receipt; each underlying Run remains the durable result authority."""

    repair_id: str
    budget: AgentRepairBudget
    stop_reason: AgentRepairStopReason
    attempts: tuple[FleetRunDetail, ...]
    message: str = ""
    admitted_run_ids: tuple[str, ...] = ()
    unresolved_run_ids: tuple[str, ...] = ()

    @property
    def test_passed(self) -> bool:
        return self.stop_reason is AgentRepairStopReason.TEST_PASSED

    @property
    def converged(self) -> bool:
        return self.stop_reason is AgentRepairStopReason.CONVERGED

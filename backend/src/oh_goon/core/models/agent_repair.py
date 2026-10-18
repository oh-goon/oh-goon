"""Caller limits and receipts for bounded repair through ordinary Fleet Runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

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
    attempt: int = Field(strict=True, ge=1, le=32)
    max_attempts: int = Field(strict=True, ge=1, le=32)
    limits: FleetPlanRunLimits
    previous_run_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_attempt(self):
        if self.attempt > self.max_attempts:
            raise ValueError("repair attempt exceeds caller budget")
        if (self.previous_run_id is None) != (self.attempt == 1):
            raise ValueError("repair continuation must identify its previous Run")
        return self


class AgentRepairStopReason(StrEnum):
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

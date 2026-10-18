from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel


class ScheduledPlanRetryDecisionEvidence(ImmutableModel):
    disposition: Literal["SCHEDULED"] = "SCHEDULED"
    exit_code: int
    policy_reason: str = Field(min_length=1, max_length=240)
    delay_seconds: float = Field(ge=0, le=300)
    next_attempt: int = Field(ge=2, le=5)


class ExitCodeNotDeclaredPlanRetryDecisionEvidence(ImmutableModel):
    disposition: Literal["EXIT_CODE_NOT_DECLARED"] = "EXIT_CODE_NOT_DECLARED"
    exit_code: int


class AttemptsExhaustedPlanRetryDecisionEvidence(ImmutableModel):
    disposition: Literal["ATTEMPTS_EXHAUSTED"] = "ATTEMPTS_EXHAUSTED"
    exit_code: int
    policy_reason: str = Field(min_length=1, max_length=240)


PlanRetryDecisionEvidence = Annotated[
    ScheduledPlanRetryDecisionEvidence
    | ExitCodeNotDeclaredPlanRetryDecisionEvidence
    | AttemptsExhaustedPlanRetryDecisionEvidence,
    Field(discriminator="disposition"),
]

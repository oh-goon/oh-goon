from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import (
    fleet_step_attempt_id,
    stable_id,
    validate_repository_attempt_operation_id,
)


class SideEffectDisposition(StrEnum):
    ATTEMPTED = "ATTEMPTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class ExecutionGeneration(ImmutableModel):
    execution_id: str = Field(min_length=1)
    generation: int = Field(ge=1)
    lineage_id: str = Field(min_length=1)
    work_id: str = Field(min_length=1)
    fleet_run_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    attempt_number: int = Field(ge=1)
    step_attempt_id: str = Field(min_length=1)
    physical_execution_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_attempt_lineage(self) -> "ExecutionGeneration":
        validate_repository_attempt_operation_id(
            self.fleet_run_id, self.repository_id, self.operation_id
        )
        expected_step_attempt = fleet_step_attempt_id(
            self.operation_id, self.step_id, self.attempt_number
        )
        if self.step_attempt_id != expected_step_attempt:
            raise ValueError(
                "output execution step_attempt_id must match operation/Step/attempt identity"
            )
        return self

    @property
    def identity(self) -> str:
        return stable_id("execution-generation", self.model_dump(mode="json"))


class SideEffectAttempt(ImmutableModel):
    execution: ExecutionGeneration
    effect_kind: str = Field(min_length=1, max_length=80)
    effect_identity: str = Field(min_length=1)

    @property
    def attempt_id(self) -> str:
        return stable_id("side-effect-attempt", self.model_dump(mode="json"))


class OutputAcceptance(ImmutableModel):
    execution: ExecutionGeneration
    output_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    producer_execution_identity: str = Field(min_length=1)

    @model_validator(mode="after")
    def _producer_must_be_exact_execution(self) -> "OutputAcceptance":
        if self.producer_execution_identity != self.execution.identity:
            raise ValueError("output producer identity must match the exact execution generation")
        return self

    @property
    def acceptance_id(self) -> str:
        return stable_id("output-acceptance", self.model_dump(mode="json"))


class SideEffectReceipt(ImmutableModel):
    attempt: SideEffectAttempt
    disposition: SideEffectDisposition
    reason: str = Field(min_length=1, max_length=240)
    accepted_output_id: str | None = None

    @model_validator(mode="after")
    def _accepted_receipt_requires_output(self) -> "SideEffectReceipt":
        if self.disposition is SideEffectDisposition.ACCEPTED and self.accepted_output_id is None:
            raise ValueError("accepted side effect requires durable output acceptance identity")
        if (
            self.disposition is not SideEffectDisposition.ACCEPTED
            and self.accepted_output_id is not None
        ):
            raise ValueError("non-accepted side effect cannot carry output acceptance identity")
        return self

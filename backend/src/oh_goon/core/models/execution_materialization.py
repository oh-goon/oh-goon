from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import stable_id


class ExecutionMaterialization(ImmutableModel):
    """Immutable projection of already-admitted authority plus external-work provenance."""

    materialization_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    assignment_fingerprint: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    input_checkpoint_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    lease_generation: int = Field(ge=1)
    executor_id: str = Field(min_length=1)
    executor_generation: int = Field(ge=1)
    tool_ids: tuple[str, ...] = ()
    input_artifact_ids: tuple[str, ...] = ()
    producer: str | None = Field(default=None, min_length=1)
    producer_artifact_identity: str | None = Field(default=None, min_length=1)
    producer_contract_version: str | None = Field(default=None, min_length=1)
    producer_contract_identity: str | None = Field(default=None, min_length=1)
    producer_work_identity: str | None = Field(default=None, min_length=1)
    resolved_fingerprint: str = Field(min_length=1)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _producer_and_fingerprint(self) -> "ExecutionMaterialization":
        producer_fields = (
            self.producer_artifact_identity,
            self.producer_contract_version,
            self.producer_contract_identity,
            self.producer_work_identity,
        )
        if self.producer is None and any(value is not None for value in producer_fields):
            raise ValueError("producer provenance requires producer identity")
        if self.resolved_fingerprint != execution_materialization_fingerprint(self):
            raise ValueError("resolved_fingerprint does not match immutable materialization")
        return self


def execution_materialization_fingerprint(value: ExecutionMaterialization) -> str:
    return stable_id(
        "execution-materialization",
        {
            "attempt_id": value.attempt_id,
            "assignment_id": value.assignment_id,
            "assignment_fingerprint": value.assignment_fingerprint,
            "workspace_id": value.workspace_id,
            "input_checkpoint_id": value.input_checkpoint_id,
            "lease_id": value.lease_id,
            "lease_generation": value.lease_generation,
            "executor_id": value.executor_id,
            "executor_generation": value.executor_generation,
            "tool_ids": list(value.tool_ids),
            "input_artifact_ids": list(value.input_artifact_ids),
            "producer": value.producer,
            "producer_artifact_identity": value.producer_artifact_identity,
            "producer_contract_version": value.producer_contract_version,
            "producer_contract_identity": value.producer_contract_identity,
            "producer_work_identity": value.producer_work_identity,
        },
    )

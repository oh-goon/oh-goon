from __future__ import annotations

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.base import ImmutableModel


class AcceptedExecutionEvidence(ImmutableModel):
    """Authority receipt for raw executor evidence accepted before result semantics."""

    acceptance_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    assignment_id: str = Field(min_length=1)
    assignment_fingerprint: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    input_checkpoint_id: str = Field(min_length=1)
    output_checkpoint_id: str = Field(min_length=1)
    lease_id: str = Field(min_length=1)
    lease_generation: int = Field(ge=1)
    executor_id: str = Field(min_length=1)
    executor_generation: int = Field(ge=1)
    materialization_id: str = Field(min_length=1)
    materialization_fingerprint: str = Field(min_length=1)
    producer_work_identity: str | None = Field(default=None, min_length=1)
    result_identity: str = Field(min_length=1)
    evidence_artifact_ids: tuple[str, ...] = ()
    acceptance_fingerprint: str = Field(min_length=1)
    accepted_at: AwareDatetime

    @model_validator(mode="after")
    def _fingerprint(self) -> "AcceptedExecutionEvidence":
        if len(set(self.evidence_artifact_ids)) != len(self.evidence_artifact_ids):
            raise ValueError("evidence_artifact_ids must not contain duplicates")
        if self.acceptance_fingerprint != execution_evidence_acceptance_fingerprint(self):
            raise ValueError("acceptance_fingerprint does not match immutable accepted evidence")
        return self


def execution_evidence_acceptance_fingerprint(value: AcceptedExecutionEvidence) -> str:
    return stable_id(
        "execution-evidence-acceptance",
        {
            "attempt_id": value.attempt_id,
            "assignment_id": value.assignment_id,
            "assignment_fingerprint": value.assignment_fingerprint,
            "workspace_id": value.workspace_id,
            "input_checkpoint_id": value.input_checkpoint_id,
            "output_checkpoint_id": value.output_checkpoint_id,
            "lease_id": value.lease_id,
            "lease_generation": value.lease_generation,
            "executor_id": value.executor_id,
            "executor_generation": value.executor_generation,
            "materialization_id": value.materialization_id,
            "materialization_fingerprint": value.materialization_fingerprint,
            "producer_work_identity": value.producer_work_identity,
            "result_identity": value.result_identity,
            "evidence_artifact_ids": list(value.evidence_artifact_ids),
        },
    )

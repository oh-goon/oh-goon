from __future__ import annotations

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel


class ProducerNeutralWorkDescription(ImmutableModel):
    """Verified described work with no execution authority attached."""

    producer_name: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    producer_artifact_identity: str = Field(min_length=1)
    producer_implementation_identity: str = Field(min_length=1)
    producer_contract_schema: str = Field(min_length=1)
    repository_generation_identity: str = Field(min_length=1)
    input_provenance_identity: str = Field(min_length=1)
    evidence_payload_identity: str = Field(min_length=1)
    work_identity: str = Field(min_length=1)
    command_intent: tuple[str, ...] = ()
    ordered_members: tuple[str, ...] = ()

    def immutable_inputs(self) -> tuple[str, ...]:
        return (
            self.producer_artifact_identity,
            self.producer_implementation_identity,
            self.input_provenance_identity,
            self.evidence_payload_identity,
            self.work_identity,
        )

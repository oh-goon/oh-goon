from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.product_identity import stable_id

RecoveryFailureClass = Literal[
    "environment",
    "timeout",
    "product_assertion",
    "cancelled",
    "authority_integrity",
]
RecoveryDisposition = Literal["ADMITTED", "BLOCKED"]


class RecoveryFailureEvidence(ImmutableModel):
    """Immutable classification of the fact that caused recovery to be considered."""

    failure_class: RecoveryFailureClass
    reason_code: str = Field(min_length=1, max_length=128)
    work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    detail_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @property
    def evidence_id(self) -> str:
        return stable_id(
            "recovery-failure-evidence", self.model_dump(mode="json", exclude_computed_fields=True)
        )


class RecoveryAdmission(ImmutableModel):
    """Bounded authority decision for one additional physical attempt."""

    lineage_id: str = Field(min_length=1)
    parent_assignment_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    failure_evidence_id: str = Field(min_length=1)
    attempt_ordinal: int = Field(ge=2)
    max_attempts: int = Field(ge=2, le=32)
    disposition: RecoveryDisposition
    reason: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _bounded(self) -> "RecoveryAdmission":
        if self.attempt_ordinal > self.max_attempts and self.disposition == "ADMITTED":
            raise ValueError("Recovery cannot be admitted beyond max_attempts")
        return self

    @property
    def admission_id(self) -> str:
        return stable_id(
            "recovery-admission", self.model_dump(mode="json", exclude_computed_fields=True)
        )

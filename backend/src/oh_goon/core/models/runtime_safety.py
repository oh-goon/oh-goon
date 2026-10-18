from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, computed_field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.recovery import (
    InvariantAudit,
    InvariantFinding,
    InvariantSeverity,
    RepairPlan,
    RepositoryEffectQuarantine,
)
from oh_goon.core.time import utc_now
from oh_goon.core.vocabulary import RuntimeSafetyStatus


class _RuntimeSafetyProofBase(ImmutableModel):
    """One process-local runtime-safety fact; state determines which evidence is valid."""

    status: RuntimeSafetyStatus
    checked_at: str = Field(default_factory=lambda: utc_now().isoformat())

    @computed_field
    @property
    def effects_allowed(self) -> bool:
        return self.status is RuntimeSafetyStatus.PROVEN_SAFE


class UnprovenRuntimeSafetyProof(_RuntimeSafetyProofBase):
    status: Literal[RuntimeSafetyStatus.UNPROVEN]
    durable_revision: Literal[None] = None
    journal_revision: Literal[None] = None
    findings: tuple[InvariantFinding, ...] = Field(default_factory=tuple, max_length=0)


class ProvenRuntimeSafetyProof(_RuntimeSafetyProofBase):
    status: Literal[RuntimeSafetyStatus.PROVEN_SAFE]
    durable_revision: int = Field(ge=0)
    journal_revision: int = Field(ge=0)
    findings: tuple[InvariantFinding, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def validate_safe_findings(self) -> ProvenRuntimeSafetyProof:
        if any(item.severity is InvariantSeverity.ERROR for item in self.findings):
            raise ValueError("Proven-safe runtime proof cannot contain error findings")
        return self


class DegradedRuntimeSafetyProof(_RuntimeSafetyProofBase):
    status: Literal[RuntimeSafetyStatus.DEGRADED]
    durable_revision: int = Field(ge=0)
    journal_revision: int | None = Field(default=None, ge=0)
    findings: tuple[InvariantFinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_degraded_findings(self) -> DegradedRuntimeSafetyProof:
        if not any(item.severity is InvariantSeverity.ERROR for item in self.findings):
            raise ValueError("Degraded runtime proof requires at least one error finding")
        return self


type RuntimeSafetyProof = Annotated[
    UnprovenRuntimeSafetyProof | ProvenRuntimeSafetyProof | DegradedRuntimeSafetyProof,
    Field(discriminator="status"),
]
type AssessedRuntimeSafetyProof = Annotated[
    ProvenRuntimeSafetyProof | DegradedRuntimeSafetyProof,
    Field(discriminator="status"),
]


def runtime_safety_audit(proof: AssessedRuntimeSafetyProof) -> InvariantAudit:
    return InvariantAudit(
        durable_revision=proof.durable_revision,
        journal_revision=proof.journal_revision,
        findings=proof.findings,
    )


class RuntimeSafetyAssessment(ImmutableModel):
    """One assessed safety proof plus recovery actions derived from that same proof."""

    proof: AssessedRuntimeSafetyProof
    repair_plan: RepairPlan
    effect_quarantines: tuple[RepositoryEffectQuarantine, ...] = Field(default_factory=tuple)

    @computed_field
    @property
    def audit(self) -> InvariantAudit:
        return runtime_safety_audit(self.proof)

    @property
    def durable_revision(self) -> int:
        return self.proof.durable_revision

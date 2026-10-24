from __future__ import annotations

from pydantic import Field, computed_field

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.recovery import InvariantAudit, RepairPlan, RepositoryEffectQuarantine
from oh_goon.core.models.runtime_safety import AssessedRuntimeSafetyProof, runtime_safety_audit


class RecoveryStatus(ImmutableModel):
    effect_quarantines: tuple[RepositoryEffectQuarantine, ...] = Field(default_factory=tuple)
    repair_plan: RepairPlan
    runtime_safety: AssessedRuntimeSafetyProof

    @computed_field  # type: ignore[prop-decorator]
    @property
    def invariants(self) -> InvariantAudit:
        return runtime_safety_audit(self.runtime_safety)

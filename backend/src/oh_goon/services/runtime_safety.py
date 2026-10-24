from __future__ import annotations

from oh_goon.core.errors import RuntimeSafetyBlocked
from oh_goon.core.models.runtime_safety import (
    AssessedRuntimeSafetyProof,
    ProvenRuntimeSafetyProof,
    RuntimeSafetyAssessment,
    RuntimeSafetyProof,
)
from oh_goon.core.vocabulary import CommandSafety
from oh_goon.runtime.safety import RuntimeSafetyGate
from oh_goon.runtime.safety_cut import ConsistentRuntimeSafetyCut, RuntimeSafetyCut
from oh_goon.services.effect_quarantine import require_effects_allowed_for_repositories
from oh_goon.services.invariant_auditor import SystemInvariantAuditor


class RuntimeSafetyService:
    """Create one revision-bound assessment and expose its admission proof."""

    def __init__(self, auditor: SystemInvariantAuditor, gate: RuntimeSafetyGate) -> None:
        self._auditor = auditor
        self._gate = gate

    def assess(self) -> RuntimeSafetyAssessment:
        cut = self._auditor.read_cut()
        audit = self._auditor.audit(cut)
        proof = self._gate.record(audit)
        return RuntimeSafetyAssessment(
            proof=proof,
            repair_plan=self._auditor.repair_plan(audit),
            effect_quarantines=tuple(self._auditor.quarantines(cut)),
        )

    def refresh(self) -> AssessedRuntimeSafetyProof:
        # The auditor itself owns the single transactional cut.  Keeping refresh on
        # the narrow audit protocol also makes safety-gate tests/fakes model the real boundary.
        return self._gate.record(self._auditor.audit())

    def admit_cut(self, cut: RuntimeSafetyCut) -> ConsistentRuntimeSafetyCut:
        """Prove and return the exact consistent durable observation admitted for effects."""
        self._gate.record(self._auditor.audit(cut))
        self._gate.require_effects_allowed()
        if not isinstance(cut, ConsistentRuntimeSafetyCut):
            raise AssertionError("Proven-safe runtime cut must contain a valid Journal view")
        return cut

    def require_repository_effects_allowed(
        self,
        cut: ConsistentRuntimeSafetyCut,
        *,
        safety: CommandSafety,
        repository_ids: tuple[str, ...],
    ) -> None:
        """Enforce repository quarantine from the exact already-admitted safety cut."""
        require_effects_allowed_for_repositories(
            safety,
            repository_ids,
            self._auditor.quarantines(cut),
        )

    def admit_effect_cut(self) -> ConsistentRuntimeSafetyCut:
        """Capture and admit one exact healthy durable cut for repository effects."""
        return self.admit_cut(self._auditor.read_cut())

    def proof(self) -> RuntimeSafetyProof:
        return self._gate.proof()

    def admit_effects(self) -> ProvenRuntimeSafetyProof:
        proof = self._gate.proof()
        current_revision = self._auditor.current_durable_revision()
        if proof.durable_revision != current_revision:
            proof = self.refresh()
        return self._gate.require_effects_allowed()

    def effects_allowed(self) -> bool:
        try:
            self.admit_effects()
        except RuntimeSafetyBlocked:
            return False
        return True

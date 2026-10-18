from __future__ import annotations

from threading import Lock

from oh_goon.core.errors import RuntimeSafetyBlocked
from oh_goon.core.models.recovery import InvariantAudit
from oh_goon.core.models.runtime_safety import (
    AssessedRuntimeSafetyProof,
    DegradedRuntimeSafetyProof,
    ProvenRuntimeSafetyProof,
    RuntimeSafetyProof,
    UnprovenRuntimeSafetyProof,
)
from oh_goon.core.vocabulary import RuntimeSafetyStatus


class RuntimeSafetyGate:
    """Process-local admission gate; facts are proved elsewhere and recorded atomically here."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._proof: RuntimeSafetyProof = UnprovenRuntimeSafetyProof(
            status=RuntimeSafetyStatus.UNPROVEN
        )

    def record(self, audit: InvariantAudit) -> AssessedRuntimeSafetyProof:
        proof: AssessedRuntimeSafetyProof
        if audit.healthy:
            if audit.journal_revision is None:
                raise AssertionError("Healthy runtime audit must have a Journal revision")
            proof = ProvenRuntimeSafetyProof(
                status=RuntimeSafetyStatus.PROVEN_SAFE,
                durable_revision=audit.durable_revision,
                journal_revision=audit.journal_revision,
                findings=audit.findings,
            )
        else:
            proof = DegradedRuntimeSafetyProof(
                status=RuntimeSafetyStatus.DEGRADED,
                durable_revision=audit.durable_revision,
                journal_revision=audit.journal_revision,
                findings=audit.findings,
            )
        with self._lock:
            self._proof = proof
        return proof

    def proof(self) -> RuntimeSafetyProof:
        with self._lock:
            return self._proof

    def require_effects_allowed(self) -> ProvenRuntimeSafetyProof:
        proof = self.proof()
        if isinstance(proof, ProvenRuntimeSafetyProof):
            return proof
        codes = ", ".join(item.code for item in proof.findings) or "startup proof has not completed"
        raise RuntimeSafetyBlocked(
            f"External-effect admission is blocked while runtime safety is {proof.status.value}: {codes}"
        )

"""B12D2A: opaque transport for an already committed original LACE intent.

This label is not recovery permission. Only the original authenticated journal
and control epoch may authorize later cleanup. Native PID reclamation must not.
"""
from __future__ import annotations

from oh_goon.core.ports.local_lace_intent import LocalLaceIntentProof
from oh_goon.core.errors import FleetPlanInvalid


def lace_resource_binding(proof: LocalLaceIntentProof) -> dict[str, object]:
    if (type(proof) is not LocalLaceIntentProof
            or type(proof.run_id) is not str or not proof.run_id
            or type(proof.step_id) is not str or not proof.step_id
            or type(proof.run_seq) is not int or proof.run_seq < 2
            or type(proof.attempt_identity) is not str
            or not proof.attempt_identity.startswith("sha256:")
            or len(proof.attempt_identity) != 71
            or any(c not in "0123456789abcdef" for c in proof.attempt_identity[7:])):
        raise FleetPlanInvalid("original committed LACE identity required for native lease")
    return {"run_id": proof.run_id, "step_id": proof.step_id,
            "intent_run_seq": proof.run_seq,
            "attempt_identity": proof.attempt_identity}

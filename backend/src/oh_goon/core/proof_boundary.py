from __future__ import annotations

from typing import Literal

from oh_goon.core.models.canonical_result_semantics import (
    CanonicalAcceptedResult,
    CanonicalResultStatus,
)
from oh_goon.core.models.proof_boundary import ExactCheckpointProof, ProtectedEffectGrant
from oh_goon.core.models.workspace import CandidateCheckpoint
from oh_goon.core.time import utc_now


class ProofBoundary:
    """Single owner for exact-checkpoint proof and protected-effect progression."""

    def prove_checkpoint(
        self,
        checkpoint: CandidateCheckpoint,
        accepted: CanonicalAcceptedResult,
        *,
        verification_contract_id: str,
    ) -> ExactCheckpointProof:
        if accepted.status is not CanonicalResultStatus.SUCCESS:
            raise ValueError("Only canonical SUCCESS can prove a candidate checkpoint")
        return ExactCheckpointProof(
            workspace_id=checkpoint.workspace_id,
            checkpoint_id=checkpoint.checkpoint_id,
            source_sha256=checkpoint.source_sha256,
            accepted_result_id=accepted.acceptance_id,
            verification_contract_id=verification_contract_id,
            created_at=utc_now(),
        )

    def grant_effect(
        self,
        checkpoint: CandidateCheckpoint,
        proof: ExactCheckpointProof,
        *,
        effect_kind: Literal["git_commit", "git_push", "merge_request", "publish"],
        target: str,
    ) -> ProtectedEffectGrant:
        if proof.workspace_id != checkpoint.workspace_id:
            raise ValueError("Checkpoint proof belongs to another workspace")
        if proof.checkpoint_id != checkpoint.checkpoint_id:
            raise ValueError("Checkpoint proof belongs to another candidate checkpoint")
        if proof.source_sha256 != checkpoint.source_sha256:
            raise ValueError("Checkpoint bytes changed after proof")
        return ProtectedEffectGrant(
            proof_id=proof.proof_id,
            workspace_id=checkpoint.workspace_id,
            checkpoint_id=checkpoint.checkpoint_id,
            effect_kind=effect_kind,
            target=target,
            created_at=utc_now(),
        )

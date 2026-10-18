from datetime import UTC, datetime

import pytest

from oh_goon.core.models.canonical_result_semantics import CanonicalAcceptedResult, CanonicalResultStatus
from oh_goon.core.models.proof_boundary import ExactCheckpointProof
from oh_goon.core.models.workspace import CandidateCheckpoint
from oh_goon.core.proof_boundary import ProofBoundary


def checkpoint(checkpoint_id: str = "cp-1", sha: str = "a" * 64) -> CandidateCheckpoint:
    return CandidateCheckpoint(checkpoint_id=checkpoint_id, workspace_id="ws-1", source_sha256=sha, byte_count=1, entry_count=1, created_at=datetime.now(UTC))


def accepted(status: CanonicalResultStatus) -> CanonicalAcceptedResult:
    return CanonicalAcceptedResult(acceptance_id="accept-1", result_identity="result-1", status=status)


def test_only_success_manufactures_exact_checkpoint_proof():
    proof = ProofBoundary().prove_checkpoint(checkpoint(), accepted(CanonicalResultStatus.SUCCESS), verification_contract_id="verify-v1")
    assert proof.checkpoint_id == "cp-1"
    with pytest.raises(ValueError, match="Only canonical SUCCESS"):
        ProofBoundary().prove_checkpoint(checkpoint(), accepted(CanonicalResultStatus.ATTENTION_REQUIRED), verification_contract_id="verify-v1")


def test_protected_effect_requires_exact_same_checkpoint_bytes():
    boundary = ProofBoundary()
    cp = checkpoint()
    proof = boundary.prove_checkpoint(cp, accepted(CanonicalResultStatus.SUCCESS), verification_contract_id="verify-v1")
    grant = boundary.grant_effect(cp, proof, effect_kind="publish", target="registry.example/app")
    assert grant.proof_id == proof.proof_id
    with pytest.raises(ValueError, match="another candidate checkpoint"):
        boundary.grant_effect(checkpoint("cp-2"), proof, effect_kind="publish", target="registry.example/app")
    with pytest.raises(ValueError, match="bytes changed"):
        changed = checkpoint("cp-1", "b" * 64)
        boundary.grant_effect(changed, proof, effect_kind="publish", target="registry.example/app")

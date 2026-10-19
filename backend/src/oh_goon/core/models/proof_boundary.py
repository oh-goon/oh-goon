from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from oh_goon.core.models.base import ImmutableModel
from oh_goon.core.models.canonical_result_semantics import CanonicalResultStatus
from oh_goon.core.product_identity import stable_id


class ExactCheckpointProof(ImmutableModel):
    """Monotonic proof that one exact candidate checkpoint passed canonical verification."""

    workspace_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_result_id: str = Field(min_length=1)
    canonical_status: Literal[CanonicalResultStatus.SUCCESS] = CanonicalResultStatus.SUCCESS
    verification_contract_id: str = Field(min_length=1)
    created_at: AwareDatetime

    @property
    def proof_id(self) -> str:
        return stable_id(
            "exact-checkpoint-proof",
            self.model_dump(mode="json", exclude={"created_at"}, exclude_computed_fields=True),
        )


class ProtectedEffectGrant(ImmutableModel):
    """Authority to perform one protected effect against exactly one proven checkpoint."""

    proof_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)
    effect_kind: Literal["git_commit", "git_push", "merge_request", "publish"]
    target: str = Field(min_length=1, max_length=512)
    created_at: AwareDatetime

    @property
    def grant_id(self) -> str:
        return stable_id(
            "protected-effect-grant",
            self.model_dump(mode="json", exclude={"created_at"}, exclude_computed_fields=True),
        )

    @model_validator(mode="after")
    def _target_is_not_ambient(self) -> "ProtectedEffectGrant":
        if self.target in {"*", "all", "any"}:
            raise ValueError("Protected-effect grants require one explicit target")
        return self

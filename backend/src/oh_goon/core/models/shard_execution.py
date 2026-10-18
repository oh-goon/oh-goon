from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.base import ImmutableModel

ShardCanonicalStatus = Literal[
    "PASS",
    "FAIL",
    "TIMEOUT",
    "SUITE_TIMEOUT",
    "CANCELLED",
    "ENVIRONMENT_BLOCKED",
    "RESUMED_PASS",
    "REFINED_PASS",
    "REFINED_FAIL",
    "DEADLINE_RESERVED",
]


class CanonicalShardAttemptEvidence(ImmutableModel):
    """One Oh-Goon-owned physical attempt against exact shard work."""

    attempt_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    attempt: int = Field(ge=1, le=5)
    physical_work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    status: ShardCanonicalStatus
    returncode: int | None = None
    duration_ms: int = Field(ge=0)
    retry_admitted: bool = False
    retry_reason: str | None = None
    runtime_authority_receipt_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    supervisor_receipt_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    process_group_quiescent: bool | None = None
    forced_termination: bool | None = None


class CanonicalShardResultEvidence(ImmutableModel):
    """Canonical lineage for one producer-described shard and its refinements."""

    shard_id: str = Field(min_length=1)
    described_work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    physical_work_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    parent_work_identity: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    refinement_depth: int = Field(default=0, ge=0, le=32)
    status: ShardCanonicalStatus
    resumed: bool = False
    reason_code: str | None = Field(default=None, min_length=1, max_length=120)
    members: tuple[str, ...] = ()
    attempts: tuple[CanonicalShardAttemptEvidence, ...] = ()
    children: tuple["CanonicalShardResultEvidence", ...] = ()

    @model_validator(mode="after")
    def validate_refinement_status(self) -> "CanonicalShardResultEvidence":
        if self.children and self.status not in {"REFINED_PASS", "REFINED_FAIL"}:
            raise ValueError("Shard results with refinement children require a refined status")
        if not self.children and self.status in {"REFINED_PASS", "REFINED_FAIL"}:
            raise ValueError("Refined shard status requires child evidence")
        return self


class CanonicalShardExecutionEvidence(ImmutableModel):
    """Shard facts admitted into the canonical Fleet/Plan evidence lifecycle."""

    contract_schema: Literal["oh-goon.canonical-shard-execution/v3"] = (
        "oh-goon.canonical-shard-execution/v3"
    )
    plan_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    repository_generation_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_context_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    execution_policy_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    producer_contract_schema: str = Field(min_length=1)
    producer_version: str = Field(min_length=1)
    producer_artifact_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    producer_implementation_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    producer_contract_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    repository_content_identity: str | None = None
    verification_selection_evidence_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    selected_membership_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    verification_revision_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    producer_repository_generation: int | None = Field(default=None, ge=0)
    producer_identity_generation: int | None = Field(default=None, ge=0)
    producer_evidence_hashes: tuple[tuple[str, str], ...] = ()
    selected_members: tuple[str, ...] = ()
    actual_executed_membership_identity: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    actual_executed_members: tuple[str, ...] = ()
    membership_complete: bool = True
    unproven_owner_members: tuple[str, ...] = ()
    results: tuple[CanonicalShardResultEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_current_verification_producer_identity(self) -> "CanonicalShardExecutionEvidence":
        if self.verification_selection_evidence_identity is None:
            return self
        required = {
            "producer_artifact_identity": self.producer_artifact_identity,
            "producer_implementation_identity": self.producer_implementation_identity,
            "producer_contract_identity": self.producer_contract_identity,
            "repository_content_identity": self.repository_content_identity,
            "verification_revision_identity": self.verification_revision_identity,
            "selected_membership_identity": self.selected_membership_identity,
            "actual_executed_membership_identity": self.actual_executed_membership_identity,
        }
        missing = tuple(name for name, value in required.items() if value is None)
        if missing:
            raise ValueError(
                "Current verification-selection evidence requires complete producer and membership identity: "
                + ", ".join(missing)
            )
        return self

    @property
    def evidence_identity(self) -> str:
        return stable_id(
            "canonical-shard-execution",
            self.model_dump(mode="json", exclude={"contract_schema"}, exclude_computed_fields=True),
        )

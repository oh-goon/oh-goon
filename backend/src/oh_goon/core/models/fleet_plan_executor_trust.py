from __future__ import annotations

from typing import Literal

from pydantic import Field

from oh_goon.core.models.base import ImmutableModel

_UTC_SECOND_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class FleetPlanExecutorTrustEntry(ImmutableModel):
    """One explicitly trusted Ed25519 executor identity with bounded key lifecycle."""

    key_id: str = Field(min_length=1, max_length=200)
    executor_name: str = Field(min_length=1, max_length=200)
    executor_versions: tuple[str, ...] = Field(min_length=1, max_length=32)
    public_key_base64: str = Field(min_length=40, max_length=80)
    public_key_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_from: str | None = Field(default=None, pattern=_UTC_SECOND_PATTERN)
    expires_at: str | None = Field(default=None, pattern=_UTC_SECOND_PATTERN)
    revoked_at: str | None = Field(default=None, pattern=_UTC_SECOND_PATTERN)
    revocation_mode: Literal["from_time", "all"] = "from_time"


class FleetPlanExecutorTrustPolicy(ImmutableModel):
    """Read-only local trust policy; public keys authenticate claims but never authorize writes."""

    version: Literal[2] = 2
    executors: tuple[FleetPlanExecutorTrustEntry, ...] = Field(min_length=1, max_length=64)


class FleetPlanExecutorTrustPolicySnapshot(ImmutableModel):
    """Canonical public-key trust-policy snapshot; content identity is not a trust root."""

    format: Literal["oh-goon-executor-trust-policy-snapshot"] = (
        "oh-goon-executor-trust-policy-snapshot"
    )
    version: Literal[1] = 1
    policy_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: FleetPlanExecutorTrustPolicy


class FleetPlanExecutorTrustPolicySnapshotExport(ImmutableModel):
    snapshot: FleetPlanExecutorTrustPolicySnapshot
    canonical_json: str = Field(min_length=1, max_length=100_000)


NonAuthenticatedExecutorState = Literal[
    "unsigned",
    "attested_unverified",
    "untrusted_key",
    "executor_mismatch",
    "version_not_allowed",
    "key_not_yet_valid",
    "key_expired",
    "key_revoked",
    "invalid_signature",
]

ExecutorAuthenticationState = Literal[
    "unsigned",
    "attested_unverified",
    "untrusted_key",
    "executor_mismatch",
    "version_not_allowed",
    "key_not_yet_valid",
    "key_expired",
    "key_revoked",
    "invalid_signature",
    "authenticated",
]

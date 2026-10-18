from __future__ import annotations

import base64
import binascii
import hashlib
import json
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import ValidationError

from oh_goon.core.errors import FleetPlanInvalid
from oh_goon.core.models.fleet_plan_executor_trust import (
    ExecutorAuthenticationState,
    FleetPlanExecutorTrustEntry,
    FleetPlanExecutorTrustPolicy,
    FleetPlanExecutorTrustPolicySnapshot,
    FleetPlanExecutorTrustPolicySnapshotExport,
)


def load_executor_trust_policy(path: Path) -> FleetPlanExecutorTrustPolicy:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise FleetPlanInvalid(f"Invalid Fleet executor trust policy: {exc}") from exc
    if raw.get("version") != 2:
        raise FleetPlanInvalid("Fleet executor trust policy version must be 2")
    rows = raw.get("executors", [])
    if not isinstance(rows, list):
        raise FleetPlanInvalid("Fleet executor trust policy executors must be an array of tables")
    entries: list[FleetPlanExecutorTrustEntry] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FleetPlanInvalid("Fleet executor trust policy entries must be tables")
        key_id = row.get("key_id")
        if not isinstance(key_id, str) or not key_id:
            raise FleetPlanInvalid("Fleet executor trust policy key_id must be non-empty")
        if key_id in seen:
            raise FleetPlanInvalid(f"Duplicate Fleet executor trust key_id: {key_id}")
        seen.add(key_id)
        public_key_base64 = row.get("public_key_base64")
        key_bytes = _decode_public_key(public_key_base64)
        versions = row.get("executor_versions")
        if (
            not isinstance(versions, list)
            or not versions
            or len(versions) > 32
            or any(not isinstance(item, str) or not item or len(item) > 200 for item in versions)
            or len(set(versions)) != len(versions)
        ):
            raise FleetPlanInvalid(
                f"Fleet executor trust entry {key_id} requires 1-32 unique non-empty executor_versions"
            )
        active_from = _optional_timestamp(row.get("active_from"), key_id, "active_from")
        expires_at = _optional_timestamp(row.get("expires_at"), key_id, "expires_at")
        revoked_at = _optional_timestamp(row.get("revoked_at"), key_id, "revoked_at")
        revocation_mode = row.get("revocation_mode", "from_time")
        if revocation_mode not in {"from_time", "all"}:
            raise FleetPlanInvalid(
                f"Fleet executor trust entry {key_id} revocation_mode must be from_time or all"
            )
        if active_from is not None and expires_at is not None and active_from >= expires_at:
            raise FleetPlanInvalid(
                f"Fleet executor trust entry {key_id} active_from must be before expires_at"
            )
        try:
            entries.append(
                FleetPlanExecutorTrustEntry(
                    key_id=key_id,
                    executor_name=row.get("executor_name"),
                    executor_versions=tuple(versions),
                    public_key_base64=public_key_base64,
                    public_key_fingerprint=hashlib.sha256(key_bytes).hexdigest(),
                    active_from=active_from,
                    expires_at=expires_at,
                    revoked_at=revoked_at,
                    revocation_mode=revocation_mode,
                )
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise FleetPlanInvalid(f"Invalid Fleet executor trust entry {key_id}: {exc}") from exc
    return FleetPlanExecutorTrustPolicy(executors=tuple(entries))


def verify_executor_attestation(
    policy: FleetPlanExecutorTrustPolicy,
    *,
    key_id: str,
    fingerprint: str,
    executor_name: str,
    executor_version: str,
    issued_at: str,
    payload: bytes,
    signature_base64: str,
) -> ExecutorAuthenticationState:
    entry = next((item for item in policy.executors if item.key_id == key_id), None)
    if entry is None:
        return "untrusted_key"
    if entry.executor_name != executor_name:
        return "executor_mismatch"
    if executor_version not in entry.executor_versions:
        return "version_not_allowed"
    lifecycle_state = _key_lifecycle_state(entry, issued_at)
    if lifecycle_state is not None:
        return lifecycle_state
    if entry.public_key_fingerprint != fingerprint:
        return "invalid_signature"
    try:
        signature = base64.b64decode(signature_base64, validate=True)
        key_bytes = base64.b64decode(entry.public_key_base64, validate=True)
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, payload)
    except (ValueError, binascii.Error, InvalidSignature):
        return "invalid_signature"
    return "authenticated"


def _key_lifecycle_state(
    entry: FleetPlanExecutorTrustEntry, issued_at: str
) -> ExecutorAuthenticationState | None:
    issued = _parse_timestamp(issued_at, "executor report issued_at")
    active_from = _parse_optional_model_timestamp(entry.active_from)
    expires_at = _parse_optional_model_timestamp(entry.expires_at)
    revoked_at = _parse_optional_model_timestamp(entry.revoked_at)
    if active_from is not None and issued < active_from:
        return "key_not_yet_valid"
    if expires_at is not None and issued >= expires_at:
        return "key_expired"
    if entry.revocation_mode == "all" and revoked_at is not None:
        return "key_revoked"
    if revoked_at is not None and issued >= revoked_at:
        return "key_revoked"
    return None


def _optional_timestamp(value: object, key_id: str, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FleetPlanInvalid(
            f"Fleet executor trust entry {key_id} {field} must be a UTC timestamp string"
        )
    _parse_timestamp(value, f"Fleet executor trust entry {key_id} {field}")
    return value


def _parse_optional_model_timestamp(value: str | None) -> datetime | None:
    return _parse_timestamp(value, "trust timestamp") if value is not None else None


def _parse_timestamp(value: str, label: str) -> datetime:
    if len(value) != 20 or not value.endswith("Z"):
        raise FleetPlanInvalid(f"{label} must use UTC second precision YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise FleetPlanInvalid(
            f"{label} must use UTC second precision YYYY-MM-DDTHH:MM:SSZ"
        ) from exc
    return parsed


def _decode_public_key(value: object) -> bytes:
    if not isinstance(value, str):
        raise FleetPlanInvalid("Fleet executor trust public_key_base64 must be a string")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise FleetPlanInvalid("Fleet executor trust public key must use canonical base64") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise FleetPlanInvalid("Fleet executor trust public key must use canonical base64")
    if len(decoded) != 32:
        raise FleetPlanInvalid("Fleet executor trust Ed25519 public key must be exactly 32 bytes")
    return decoded


def normalize_executor_trust_policy(
    policy: FleetPlanExecutorTrustPolicy,
) -> FleetPlanExecutorTrustPolicy:
    """Return semantic canonical ordering for stable trust-policy identity."""
    entries = tuple(
        entry.model_copy(update={"executor_versions": tuple(sorted(entry.executor_versions))})
        for entry in sorted(policy.executors, key=lambda item: item.key_id)
    )
    return FleetPlanExecutorTrustPolicy(executors=entries)


def executor_trust_policy_id(policy: FleetPlanExecutorTrustPolicy) -> str:
    normalized = normalize_executor_trust_policy(policy)
    canonical = json.dumps(
        normalized.model_dump(mode="json", exclude_computed_fields=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def export_executor_trust_policy_snapshot(
    policy: FleetPlanExecutorTrustPolicy,
) -> FleetPlanExecutorTrustPolicySnapshotExport:
    normalized = normalize_executor_trust_policy(policy)
    snapshot = FleetPlanExecutorTrustPolicySnapshot(
        policy_id=executor_trust_policy_id(normalized),
        policy=normalized,
    )
    canonical = json.dumps(
        snapshot.model_dump(mode="json", exclude_computed_fields=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return FleetPlanExecutorTrustPolicySnapshotExport(
        snapshot=snapshot,
        canonical_json=canonical + "\n",
    )

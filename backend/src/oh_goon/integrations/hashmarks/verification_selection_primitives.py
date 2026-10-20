from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from oh_goon.core.models.pytest_selection import StructuredPytestMember, StructuredPytestMemberError
from oh_goon.integrations.hashmarks.runtime_contract import (
    HashmarksRuntimeContractError,
    require_loaded_hashmarks_runtime,
)

if TYPE_CHECKING:
    from hashmarks import NativeVerificationMember, NativeVerificationSelectionProjection


class HashmarksVerificationSelectionInputError(ValueError):
    """Fail-closed Hashmarks downstream-selection admission error."""

    failure_class = "input-selection-admission"


HashmarksVerificationMember = StructuredPytestMember


def validate_native_consumer_bundle(
    envelope: Mapping[str, object],
    contract: Mapping[str, object],
    *,
    expected_producer_implementation_identity: str,
) -> Mapping[str, object]:
    from hashmarks import validate_native_consumer_bundle as native_validator

    return native_validator(
        envelope,
        contract,
        expected_producer_implementation_identity=expected_producer_implementation_identity,
    )


@dataclass(frozen=True, slots=True)
class NativeSelectionProjection:
    producer_version: str
    producer_implementation_identity: str
    repository_identity: str
    source_identity: str
    repository_provenance_identity: str
    membership_identity: str
    producer_contract_identity: str
    codemap_generation: int
    identity_generation: int | None
    evidence_hashes: tuple[tuple[str, str], ...]
    members: tuple[HashmarksVerificationMember, ...]


def _read_object(path: Path, label: str) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HashmarksVerificationSelectionInputError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise HashmarksVerificationSelectionInputError(f"{label} must be an object")
    return payload


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise HashmarksVerificationSelectionInputError(
            f"validated Hashmarks {label} must be an object"
        )
    return value


def _admit_consumer_boundary(
    conformance: Mapping[str, object],
    repository_identity: str,
    source_identity: str,
    loaded_identity: str,
) -> NativeVerificationSelectionProjection:
    if conformance.get("valid") is not True:
        raise HashmarksVerificationSelectionInputError(
            f"Hashmarks native consumer bundle is invalid: {conformance.get('reasons')!r}"
        )
    normalized = cast(
        "NativeVerificationSelectionProjection",
        _object(conformance.get("normalized"), "normalized verification selection"),
    )
    if conformance.get("execution_authority") != "external":
        raise HashmarksVerificationSelectionInputError(
            "Hashmarks must leave execution authority external to the producer"
        )

    checks = (
        (
            normalized["producer"]["implementation_identity"] == loaded_identity,
            "Hashmarks producer implementation identity does not match loaded runtime",
        ),
        (
            normalized["repository_identity"] == repository_identity,
            "Hashmarks repository identity does not match admitted repository",
        ),
        (
            normalized["source_identity"] == source_identity,
            "Hashmarks source identity does not match admitted source",
        ),
        (
            normalized["stale"] is False,
            "stale Hashmarks repository evidence cannot be admitted",
        ),
        (
            normalized["authority"] == "repository-intelligence-only",
            "Hashmarks contract may carry repository intelligence only",
        ),
        (
            normalized["execution_layout"] == "external",
            "Hashmarks must leave physical execution layout to Oh-Goon",
        ),
    )
    for accepted, message in checks:
        if not accepted:
            raise HashmarksVerificationSelectionInputError(message)
    return normalized


def _project_members(
    raw_members: list[NativeVerificationMember],
) -> tuple[HashmarksVerificationMember, ...]:
    projected: list[HashmarksVerificationMember] = []
    for raw in raw_members:
        try:
            projected.append(
                StructuredPytestMember(raw["path"], raw["test_symbol"], raw["member_id"])
            )
        except StructuredPytestMemberError as exc:
            raise HashmarksVerificationSelectionInputError(
                f"verification member cannot be projected safely: {exc}"
            ) from exc
    members = tuple(projected)
    selectors = tuple(member.executable_selector for member in members)
    if len(selectors) != len(set(selectors)):
        raise HashmarksVerificationSelectionInputError(
            "Hashmarks exact executable membership contains duplicate selectors"
        )
    return members


def validate_native_selection(
    contract_path: Path,
    envelope_path: Path,
    *,
    repository_identity: str,
    source_identity: str,
    artifact_identity: str,
) -> NativeSelectionProjection:
    if not repository_identity.strip() or not source_identity.strip():
        raise HashmarksVerificationSelectionInputError(
            "admitted repository/source identities must be nonblank"
        )
    if not artifact_identity.strip():
        raise HashmarksVerificationSelectionInputError(
            "producer artifact provenance identity must be nonblank"
        )
    try:
        loaded_identity = require_loaded_hashmarks_runtime()
    except HashmarksRuntimeContractError as exc:
        raise HashmarksVerificationSelectionInputError(str(exc)) from exc

    contract = _read_object(contract_path, "Hashmarks downstream contract")
    envelope = _read_object(envelope_path, "Hashmarks verification-selection envelope")
    conformance = validate_native_consumer_bundle(
        envelope,
        contract,
        expected_producer_implementation_identity=loaded_identity,
    )
    normalized = _admit_consumer_boundary(
        conformance, repository_identity, source_identity, loaded_identity
    )
    return NativeSelectionProjection(
        normalized["producer"]["version"],
        normalized["producer"]["implementation_identity"],
        repository_identity,
        source_identity,
        normalized["repository_provenance_identity"],
        normalized["membership_identity"],
        normalized["producer_contract_identity"],
        normalized["codemap_generation"],
        normalized["identity_generation"],
        tuple(sorted(normalized["evidence_hashes"].items())),
        _project_members(normalized["members"]),
    )

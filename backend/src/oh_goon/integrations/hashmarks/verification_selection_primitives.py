from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from oh_goon.core.models.pytest_selection import StructuredPytestMember, StructuredPytestMemberError
from oh_goon.integrations.hashmarks.runtime_contract import (
    HashmarksRuntimeContractError,
    loaded_hashmarks_version,
    require_loaded_hashmarks_runtime,
)


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


def _identity(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise HashmarksVerificationSelectionInputError(
            f"validated Hashmarks {label} must carry an opaque producer identity"
        )
    return value


def _admit_consumer_boundary(
    contract: Mapping[str, object],
    envelope: Mapping[str, object],
    conformance: Mapping[str, object],
    repository_identity: str,
    source_identity: str,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    if conformance.get("valid") is not True:
        raise HashmarksVerificationSelectionInputError(
            f"Hashmarks native consumer bundle is invalid: {conformance.get('reasons')!r}"
        )
    loaded_identity = require_loaded_hashmarks_runtime()
    if conformance.get("producer_implementation_identity") != loaded_identity:
        raise HashmarksVerificationSelectionInputError(
            "Hashmarks producer implementation identity does not match loaded runtime"
        )
    if conformance.get("execution_authority") != "external":
        raise HashmarksVerificationSelectionInputError(
            "Hashmarks must leave execution authority external to the producer"
        )

    repository = _object(envelope.get("repository"), "repository identity")
    selection = _object(envelope.get("selection"), "verification membership")
    checks = (
        (
            repository.get("repository_identity") == repository_identity,
            "Hashmarks repository identity does not match admitted repository",
        ),
        (
            repository.get("source_identity") == source_identity,
            "Hashmarks source identity does not match admitted source",
        ),
        (
            repository.get("stale") is False,
            "stale Hashmarks repository evidence cannot be admitted",
        ),
        (
            contract.get("authority") == "repository-intelligence-only",
            "Hashmarks contract may carry repository intelligence only",
        ),
        (
            contract.get("execution_layout") == "external",
            "Hashmarks must leave physical execution layout to Oh-Goon",
        ),
        (
            contract.get("hashmarks_identity") == f"hashmarks:{loaded_hashmarks_version()}",
            "Hashmarks evidence version identity does not match loaded runtime",
        ),
    )
    for accepted, message in checks:
        if not accepted:
            raise HashmarksVerificationSelectionInputError(message)
    return _object(envelope.get("producer"), "producer"), selection


def _project_members(selection: Mapping[str, object]) -> tuple[HashmarksVerificationMember, ...]:
    raw_members = selection.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        raise HashmarksVerificationSelectionInputError(
            "validated Hashmarks verification selection requires nonempty members"
        )
    projected: list[HashmarksVerificationMember] = []
    for raw in raw_members:
        member = _object(raw, "verification member")
        path, symbol, member_id = (
            member.get("path"),
            member.get("test_symbol"),
            member.get("member_id"),
        )
        if (
            not isinstance(path, str)
            or not path.strip()
            or (symbol is not None and not isinstance(symbol, str))
        ):
            raise HashmarksVerificationSelectionInputError(
                "validated Hashmarks verification member cannot be projected"
            )
        try:
            projected.append(
                StructuredPytestMember(path, symbol, _identity(member_id, "verification member"))
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
    producer, selection = _admit_consumer_boundary(
        contract, envelope, conformance, repository_identity, source_identity
    )
    if producer.get("implementation_identity") != conformance.get(
        "producer_implementation_identity"
    ):
        raise HashmarksVerificationSelectionInputError(
            "validated Hashmarks producer identity changed after native validation"
        )
    repository = _object(envelope.get("repository"), "repository identity")
    codemap_generation = repository.get("codemap_generation")
    identity_generation = repository.get("identity_generation")
    if not isinstance(codemap_generation, int) or isinstance(codemap_generation, bool):
        raise HashmarksVerificationSelectionInputError(
            "validated Hashmarks repository codemap generation must be an integer"
        )
    if identity_generation is not None and (
        not isinstance(identity_generation, int) or isinstance(identity_generation, bool)
    ):
        raise HashmarksVerificationSelectionInputError(
            "validated Hashmarks repository identity generation must be an integer or null"
        )
    evidence_hashes = _object(envelope.get("evidence_hashes"), "evidence hashes")
    producer_version = producer.get("version")
    producer_identity = producer.get("implementation_identity")
    if producer_version != loaded_hashmarks_version():
        raise HashmarksVerificationSelectionInputError(
            "Hashmarks producer version does not match loaded runtime"
        )
    if producer_identity != loaded_identity:
        raise HashmarksVerificationSelectionInputError(
            "Hashmarks producer implementation identity changed after native validation"
        )
    return NativeSelectionProjection(
        str(producer_version),
        str(producer_identity),
        repository_identity,
        source_identity,
        _identity(envelope.get("envelope_identity"), "repository provenance"),
        _identity(selection.get("membership_identity"), "membership"),
        _identity(contract.get("contract_identity"), "consumer contract"),
        codemap_generation,
        identity_generation,
        tuple(
            sorted(
                (str(key), _identity(value, "evidence hash"))
                for key, value in evidence_hashes.items()
            )
        ),
        _project_members(selection),
    )

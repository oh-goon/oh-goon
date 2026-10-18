from __future__ import annotations

from typing import Any, Mapping

from oh_goon.core.product_identity import stable_id
from oh_goon.core.models.described_work import ProducerNeutralWorkDescription
from oh_goon.integrations.hashmarks.repository_intelligence import (
    HashmarksEvidenceKind,
    HashmarksRepositoryIntelligenceInputError,
    VerifiedHashmarksRepositoryEvidence,
)


class HashmarksWorkTranslationError(ValueError):
    failure_class = "input-selection-admission"


def _strings(value: object, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise HashmarksWorkTranslationError(f"{field} must be an ordered list of non-empty strings")
    return tuple(value)


def describe_hashmarks_work(evidence: VerifiedHashmarksRepositoryEvidence) -> ProducerNeutralWorkDescription:
    """Translate verified evidence into a producer-neutral description, never authority."""

    payload: Mapping[str, Any] = evidence.payload()
    command_intent: tuple[str, ...] = ()
    ordered_members: tuple[str, ...] = ()

    if evidence.kind is HashmarksEvidenceKind.VERIFICATION:
        command_intent = _strings(payload.get("argv"), field="verification argv")
        if payload.get("available") is False or not command_intent:
            raise HashmarksWorkTranslationError("verification plan does not describe executable command intent")
    else:
        raise HashmarksWorkTranslationError(
            f"Hashmarks {evidence.kind.value} evidence is informative only and cannot be translated into work"
        )

    identity_payload = {
        "producer": evidence.producer_name,
        "producer_version": evidence.producer_version,
        "producer_artifact_identity": evidence.producer_artifact_identity,
        "producer_implementation_identity": evidence.producer_implementation_identity,
        "contract": evidence.contract_schema,
        "repository_generation_identity": evidence.repository_generation_identity,
        "input_provenance_identity": evidence.input_provenance_identity,
        "evidence_payload_identity": evidence.payload_identity,
        "command_intent": list(command_intent),
        "ordered_members": list(ordered_members),
    }
    return ProducerNeutralWorkDescription(
        producer_name=evidence.producer_name,
        producer_version=evidence.producer_version,
        producer_artifact_identity=evidence.producer_artifact_identity,
        producer_implementation_identity=evidence.producer_implementation_identity,
        producer_contract_schema=evidence.contract_schema,
        repository_generation_identity=evidence.repository_generation_identity,
        input_provenance_identity=evidence.input_provenance_identity,
        evidence_payload_identity=evidence.payload_identity,
        work_identity=stable_id("producer-neutral-work-description", identity_payload),
        command_intent=command_intent,
        ordered_members=ordered_members,
    )


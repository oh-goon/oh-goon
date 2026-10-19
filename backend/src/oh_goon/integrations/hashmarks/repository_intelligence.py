from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from oh_goon.integrations.hashmarks.runtime_contract import (
    HashmarksRuntimeContractError,
    loaded_hashmarks_version,
    require_loaded_hashmarks_runtime,
)

_SHA256_IDENTITY = re.compile(r"^sha256:[0-9a-f]{64}$")


class HashmarksEvidenceKind(StrEnum):
    DECISION = "decision"
    CONTEXT = "context"
    IMPACT = "impact"
    VERIFICATION = "verification"
    OWNERSHIP = "ownership"
    ACTION_MAP = "action-map"
    REPOSITORY_SNAPSHOT = "repository-snapshot"
    REPOSITORY_DELTA = "repository-delta"
    DIAGNOSTIC_OBSERVATION = "diagnostic-observation"
    DIAGNOSTIC_DELTA = "diagnostic-delta"
    OBSERVATION_FRESHNESS = "observation-freshness"


class HashmarksRepositoryIntelligenceInputError(ValueError):
    """Fail-closed adapter/input error; never classify this as repository test failure."""

    failure_class = "input-selection-admission"


@dataclass(frozen=True, slots=True)
class HashmarksEvidenceDisclosure:
    """Identity-only disclosure safe for persistence, Game Tape, or prompt metadata.

    Raw Hashmarks repository context is deliberately absent. Consumers that need
    repository data must stay on the trusted evidence plane and apply the owning
    disclosure/redaction policy before crossing a presentation boundary.
    """

    kind: HashmarksEvidenceKind | str
    contract_schema: str
    producer_name: str
    producer_version: str
    producer_artifact_identity: str
    producer_implementation_identity: str
    repository_generation_identity: str
    input_provenance_identity: str
    payload_identity: str
    payload_disclosed: bool = False


@dataclass(frozen=True, slots=True)
class VerifiedHashmarksRepositoryEvidence:
    """Immutable evidence-plane binding for one Hashmarks repository-intelligence fact.

    This record is deliberately non-executable.  It has no argv, process, network,
    secret, Git, publish, retry, deadline, or capability authority.  Phase 11 may
    translate selected *verified* evidence into normal Oh-Goon work separately.
    """

    kind: HashmarksEvidenceKind | str
    contract_schema: str
    producer_name: str
    producer_version: str
    producer_artifact_identity: str
    producer_implementation_identity: str
    repository_generation_identity: str
    input_provenance_identity: str
    payload_identity: str
    canonical_payload_json: str
    producer_projection_json: str
    producer_repository_identity: str | None = None
    producer_generation: str | int | None = None

    def payload(self) -> Mapping[str, Any]:
        value = json.loads(self.canonical_payload_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor owns canonical form
            raise RuntimeError("verified Hashmarks payload is not an object")
        return MappingProxyType(value)

    def producer_projection(self) -> Mapping[str, Any]:
        """Return the producer-validated compact projection, detached from source."""
        return MappingProxyType(json.loads(self.producer_projection_json))

    def disclosure_metadata(self) -> HashmarksEvidenceDisclosure:
        """Return identity-only metadata; never disclose repository payload bytes."""

        return HashmarksEvidenceDisclosure(
            kind=self.kind,
            contract_schema=self.contract_schema,
            producer_name=self.producer_name,
            producer_version=self.producer_version,
            producer_artifact_identity=self.producer_artifact_identity,
            producer_implementation_identity=self.producer_implementation_identity,
            repository_generation_identity=self.repository_generation_identity,
            input_provenance_identity=self.input_provenance_identity,
            payload_identity=self.payload_identity,
        )


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_IDENTITY.fullmatch(value) is not None


def _canonical_json(payload: Mapping[str, object]) -> str:
    try:
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise HashmarksRepositoryIntelligenceInputError(
            f"Hashmarks repository-intelligence payload is not canonical JSON data: {exc}"
        ) from exc


def bind_hashmarks_repository_evidence(
    payload: Mapping[str, object],
    *,
    repository_generation_identity: str,
    producer_artifact_identity: str,
    input_provenance_identity: str,
    expected_contract_schema: str | None = None,
    expected_repository_identity: str | None = None,
    expected_codemap_generation: int | None = None,
) -> VerifiedHashmarksRepositoryEvidence:
    """Bind one already-obtained Hashmarks fact to exact Oh-Goon admission identity.

    The adapter validates evidence only.  It does not invoke Hashmarks retrieval,
    execute a shard, resolve capabilities, or reinterpret the evidence as proof.
    """

    if not _is_sha256(repository_generation_identity):
        raise HashmarksRepositoryIntelligenceInputError(
            "repository_generation_identity must be a sha256 identity"
        )
    if not _is_sha256(producer_artifact_identity):
        raise HashmarksRepositoryIntelligenceInputError(
            "producer_artifact_identity must be a sha256 identity"
        )
    if not _is_sha256(input_provenance_identity):
        raise HashmarksRepositoryIntelligenceInputError(
            "input_provenance_identity must be a sha256 identity"
        )
    try:
        producer_implementation_identity = require_loaded_hashmarks_runtime()
    except HashmarksRuntimeContractError as exc:
        raise HashmarksRepositoryIntelligenceInputError(str(exc)) from exc
    from hashmarks import validate_repository_intelligence_evidence

    result = validate_repository_intelligence_evidence(
        payload, expected_schema=expected_contract_schema
    )
    if result.get("valid") is not True:
        raise HashmarksRepositoryIntelligenceInputError(
            f"Hashmarks repository evidence rejected: {result.get('reasons')!r}"
        )
    projection = cast(Mapping[str, Any], result["normalized"])
    if projection["producer_implementation_identity"] != producer_implementation_identity:
        raise HashmarksRepositoryIntelligenceInputError("Hashmarks loaded producer mismatch")
    # A stale fact may be retained as history but cannot be admitted as current work.
    if projection.get("stale") is True:
        raise HashmarksRepositoryIntelligenceInputError("Hashmarks repository evidence is stale")
    if (
        expected_repository_identity is not None
        and projection.get("repository_identity") != expected_repository_identity
    ):
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks repository does not match admitted source"
        )
    if (
        expected_codemap_generation is not None
        and projection.get("codemap_generation") != expected_codemap_generation
    ):
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks generation does not match admitted source"
        )
    kind = projection["evidence_kind"]
    try:
        kind = HashmarksEvidenceKind(kind)
    except ValueError:
        pass  # Producer-qualified, informative kinds need no downstream schema list.
    canonical = _canonical_json(payload)
    return VerifiedHashmarksRepositoryEvidence(
        kind=kind,
        contract_schema=projection["evidence_schema"],
        producer_name="hashmarks",
        producer_version=loaded_hashmarks_version(),
        producer_artifact_identity=producer_artifact_identity,
        producer_implementation_identity=producer_implementation_identity,
        repository_generation_identity=repository_generation_identity,
        input_provenance_identity=input_provenance_identity,
        payload_identity=projection["payload_identity"],
        canonical_payload_json=canonical,
        producer_projection_json=_canonical_json(projection),
        producer_repository_identity=projection.get("repository_identity"),
        producer_generation=projection.get("codemap_generation"),
    )


def load_hashmarks_repository_evidence(
    path: Path,
    *,
    repository_generation_identity: str,
    producer_artifact_identity: str,
    input_provenance_identity: str,
    expected_contract_schema: str | None = None,
    expected_repository_identity: str | None = None,
    expected_codemap_generation: int | None = None,
) -> VerifiedHashmarksRepositoryEvidence:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HashmarksRepositoryIntelligenceInputError(
            f"cannot read Hashmarks repository-intelligence input: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks repository-intelligence payload must be a JSON object"
        )
    return bind_hashmarks_repository_evidence(
        payload,
        repository_generation_identity=repository_generation_identity,
        producer_artifact_identity=producer_artifact_identity,
        input_provenance_identity=input_provenance_identity,
        expected_contract_schema=expected_contract_schema,
        expected_repository_identity=expected_repository_identity,
        expected_codemap_generation=expected_codemap_generation,
    )

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

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


_SUPPORTED_CONTRACTS: Mapping[str, HashmarksEvidenceKind] = MappingProxyType(
    {
        "hashmarks.task-decision-packet.v2": HashmarksEvidenceKind.DECISION,
        "hashmarks.task-decision-brief.v1": HashmarksEvidenceKind.DECISION,
        "hashmarks.task-context-plan.v1": HashmarksEvidenceKind.CONTEXT,
        "hashmarks.agent-work-context.v1": HashmarksEvidenceKind.CONTEXT,
        "hashmarks.task-change-impact.v1": HashmarksEvidenceKind.IMPACT,
        "hashmarks.verification-plan.v1": HashmarksEvidenceKind.VERIFICATION,
        "hashmarks.ownership-relation-graph.v1": HashmarksEvidenceKind.OWNERSHIP,
        "hashmarks.task-action-map.v1": HashmarksEvidenceKind.ACTION_MAP,
        "hashmarks.repository-intelligence-snapshot.v1": HashmarksEvidenceKind.REPOSITORY_SNAPSHOT,
        "hashmarks.repository-intelligence-delta.v1": HashmarksEvidenceKind.REPOSITORY_DELTA,
        "hashmarks.external-diagnostic-observation.v1": HashmarksEvidenceKind.DIAGNOSTIC_OBSERVATION,
        "hashmarks.diagnostic-observation-delta.v1": HashmarksEvidenceKind.DIAGNOSTIC_DELTA,
        "hashmarks.external-observation-freshness.v1": HashmarksEvidenceKind.OBSERVATION_FRESHNESS,
    }
)


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

    kind: HashmarksEvidenceKind
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

    kind: HashmarksEvidenceKind
    contract_schema: str
    producer_name: str
    producer_version: str
    producer_artifact_identity: str
    producer_implementation_identity: str
    repository_generation_identity: str
    input_provenance_identity: str
    payload_identity: str
    canonical_payload_json: str
    producer_repository_identity: str | None = None
    producer_generation: str | int | None = None

    def payload(self) -> Mapping[str, Any]:
        value = json.loads(self.canonical_payload_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor owns canonical form
            raise RuntimeError("verified Hashmarks payload is not an object")
        return MappingProxyType(value)

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
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise HashmarksRepositoryIntelligenceInputError(
            f"Hashmarks repository-intelligence payload is not canonical JSON data: {exc}"
        ) from exc


def _payload_identity(schema: str, canonical_json: str) -> str:
    digest = hashlib.sha256()
    digest.update(schema.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical_json.encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _native_identity_fields(
    payload: Mapping[str, object], *, contract_schema: str
) -> tuple[str | None, str | int | None]:
    """Validate native freshness fields when a Hashmarks contract exposes them.

    Not every repository-intelligence projection carries a full source identity.
    Oh-Goon therefore always binds its own exact repository generation externally,
    and additionally verifies native producer freshness facts where present.
    """

    if contract_schema == "hashmarks.task-decision-packet.v2":
        identity = payload.get("identity")
        if not isinstance(identity, Mapping):
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks task decision packet requires identity metadata"
            )
        if identity.get("schema") != "hashmarks.worker-packet-identity.v1":
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks task decision packet identity schema is incompatible"
            )
        if identity.get("stale") is not False:
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks task decision packet is stale or has unknown freshness"
            )
        generation = identity.get("codemap_generation")
        if payload.get("canonical_generation") != generation:
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks task decision packet generation metadata is inconsistent"
            )
        decision_generation = identity.get("decision_generation")
        if not _is_sha256(decision_generation):
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks task decision packet decision_generation must be sha256"
            )
        repository_identity = identity.get("repository_identity")
        if not isinstance(repository_identity, str) or not repository_identity:
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks task decision packet repository identity is missing"
            )
        if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks task decision packet codemap generation is invalid"
            )
        return repository_identity, generation

    generation = payload.get("generation")
    if generation is not None:
        if not isinstance(generation, (str, int)) or isinstance(generation, bool):
            raise HashmarksRepositoryIntelligenceInputError(
                "Hashmarks repository-intelligence generation metadata is invalid"
            )
        return None, generation
    return None, None



def _require_observer_authority(payload: Mapping[str, object], expected: str, label: str) -> None:
    if payload.get("authority") != expected:
        raise HashmarksRepositoryIntelligenceInputError(
            f"Hashmarks {label} must remain {expected}"
        )


def _validate_snapshot_authority(payload: Mapping[str, object]) -> None:
    if not _is_sha256(payload.get("snapshot_identity")):
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks repository snapshot requires producer-owned snapshot_identity"
        )
    observer = payload.get("observer")
    if not isinstance(observer, Mapping) or not _is_sha256(observer.get("identity")):
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks repository snapshot requires observer identity"
        )
    _require_observer_authority(payload, "repository-intelligence-only", "repository observer evidence")


def _validate_delta_authority(payload: Mapping[str, object]) -> None:
    if not _is_sha256(payload.get("delta_identity")):
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks repository delta requires producer-owned delta_identity"
        )
    for side in ("from", "to"):
        binding = payload.get(side)
        if not isinstance(binding, Mapping) or not _is_sha256(binding.get("snapshot_identity")):
            raise HashmarksRepositoryIntelligenceInputError(
                f"Hashmarks repository delta requires {side} snapshot_identity"
            )
    _require_observer_authority(payload, "repository-intelligence-only", "repository observer evidence")


_OBSERVER_AUTHORITY_VALIDATORS = {
    "hashmarks.repository-intelligence-snapshot.v1": _validate_snapshot_authority,
    "hashmarks.repository-intelligence-delta.v1": _validate_delta_authority,
    "hashmarks.external-diagnostic-observation.v1": lambda payload: _require_observer_authority(
        payload, "observation-only", "diagnostic observation"
    ),
    "hashmarks.diagnostic-observation-delta.v1": lambda payload: _require_observer_authority(
        payload, "observation-only", "diagnostic delta"
    ),
    "hashmarks.external-observation-freshness.v1": lambda payload: _require_observer_authority(
        payload, "observation-freshness-only", "freshness fact"
    ),
}


def _validate_hashmarks_observer_authority(
    payload: Mapping[str, object], *, contract_schema: str
) -> None:
    """Validate producer-owned observer authority without recreating producer semantics."""
    validator = _OBSERVER_AUTHORITY_VALIDATORS.get(contract_schema)
    if validator is None:
        return
    validator(payload)
    if payload.get("execution_effect") != "none":
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks observer evidence cannot carry execution effect"
        )

def bind_hashmarks_repository_evidence(
    payload: Mapping[str, object],
    *,
    repository_generation_identity: str,
    producer_artifact_identity: str,
    input_provenance_identity: str,
    expected_contract_schema: str | None = None,
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
    schema = payload.get("schema")
    if not isinstance(schema, str):
        raise HashmarksRepositoryIntelligenceInputError(
            "Hashmarks repository-intelligence payload requires a schema"
        )
    if expected_contract_schema is not None and schema != expected_contract_schema:
        raise HashmarksRepositoryIntelligenceInputError(
            f"Hashmarks contract {schema!r} does not match admitted {expected_contract_schema!r}"
        )
    kind = _SUPPORTED_CONTRACTS.get(schema)
    if kind is None:
        raise HashmarksRepositoryIntelligenceInputError(
            f"unsupported Hashmarks repository-intelligence contract: {schema!r}"
        )

    _validate_hashmarks_observer_authority(payload, contract_schema=schema)
    canonical = _canonical_json(payload)
    producer_repository_identity, producer_generation = _native_identity_fields(
        payload, contract_schema=schema
    )
    return VerifiedHashmarksRepositoryEvidence(
        kind=kind,
        contract_schema=schema,
        producer_name="hashmarks",
        producer_version=loaded_hashmarks_version(),
        producer_artifact_identity=producer_artifact_identity,
        producer_implementation_identity=producer_implementation_identity,
        repository_generation_identity=repository_generation_identity,
        input_provenance_identity=input_provenance_identity,
        payload_identity=_payload_identity(schema, canonical),
        canonical_payload_json=canonical,
        producer_repository_identity=producer_repository_identity,
        producer_generation=producer_generation,
    )


def load_hashmarks_repository_evidence(
    path: Path,
    *,
    repository_generation_identity: str,
    producer_artifact_identity: str,
    input_provenance_identity: str,
    expected_contract_schema: str | None = None,
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
    )

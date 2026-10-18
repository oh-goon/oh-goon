from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("hashmarks")

from oh_goon.integrations.hashmarks.repository_intelligence import (
    HashmarksEvidenceKind,
    HashmarksRepositoryIntelligenceInputError,
    bind_hashmarks_repository_evidence,
    load_hashmarks_repository_evidence,
)
from tests.hashmarks_test_support import (
    TEST_HASHMARKS_ARTIFACT_IDENTITY,
    TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY,
)

pytestmark = pytest.mark.dependency_complete

SOURCE = "sha256:" + "1" * 64
ARTIFACT = TEST_HASHMARKS_ARTIFACT_IDENTITY
PROVENANCE = "sha256:" + "3" * 64
ROOT = Path(__file__).resolve().parents[4]


def _decision_packet() -> dict[str, object]:
    return {
        "schema": "hashmarks.task-decision-packet.v2",
        "task": "fix bounded retry",
        "canonical_generation": 17,
        "identity": {
            "schema": "hashmarks.worker-packet-identity.v1",
            "repository_identity": "git-tree:" + "a" * 40,
            "codemap_generation": 17,
            "identity_generation": 17,
            "stale": False,
            "task_identity": "sha256:" + "4" * 64,
            "decision_generation": "sha256:" + "5" * 64,
            "context_digest": "sha256:" + "6" * 64,
            "verification_plan_digest": "sha256:" + "7" * 64,
            "negative_evidence_digest": "sha256:" + "8" * 64,
        },
    }


def test_decision_packet_binds_producer_contract_source_and_payload_identity() -> None:
    verified = bind_hashmarks_repository_evidence(
        _decision_packet(),
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    assert verified.kind is HashmarksEvidenceKind.DECISION
    assert verified.contract_schema == "hashmarks.task-decision-packet.v2"
    assert verified.producer_name == "hashmarks"
    assert verified.producer_artifact_identity == ARTIFACT
    assert (
        verified.producer_implementation_identity == TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY
    )
    assert verified.repository_generation_identity == SOURCE
    assert verified.input_provenance_identity == PROVENANCE
    assert verified.producer_repository_identity == "git-tree:" + "a" * 40
    assert verified.producer_generation == 17
    assert verified.payload_identity.startswith("sha256:")


def test_payload_identity_changes_when_fact_changes() -> None:
    original = bind_hashmarks_repository_evidence(
        _decision_packet(),
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    changed = _decision_packet()
    changed["task"] = "different task"
    rebound = bind_hashmarks_repository_evidence(
        changed,
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    assert rebound.payload_identity != original.payload_identity


def test_stale_decision_packet_is_input_failure_not_repository_failure() -> None:
    payload = _decision_packet()
    payload["identity"]["stale"] = True  # type: ignore[index]
    with pytest.raises(HashmarksRepositoryIntelligenceInputError) as raised:
        bind_hashmarks_repository_evidence(
            payload,
            repository_generation_identity=SOURCE,
            producer_artifact_identity=ARTIFACT,
            input_provenance_identity=PROVENANCE,
        )
    assert raised.value.failure_class == "input-selection-admission"
    assert "stale" in str(raised.value)


def test_decision_packet_rejects_inconsistent_native_generation() -> None:
    payload = _decision_packet()
    payload["canonical_generation"] = 18
    with pytest.raises(HashmarksRepositoryIntelligenceInputError, match="generation metadata"):
        bind_hashmarks_repository_evidence(
            payload,
            repository_generation_identity=SOURCE,
            producer_artifact_identity=ARTIFACT,
            input_provenance_identity=PROVENANCE,
        )


def test_artifact_identity_is_recorded_as_provenance_not_copied_release_authority() -> None:
    observed = "sha256:" + "9" * 64
    verified = bind_hashmarks_repository_evidence(
        _decision_packet(),
        repository_generation_identity=SOURCE,
        producer_artifact_identity=observed,
        input_provenance_identity=PROVENANCE,
    )
    assert verified.producer_artifact_identity == observed
    assert (
        verified.producer_implementation_identity == TEST_HASHMARKS_PRODUCER_IMPLEMENTATION_IDENTITY
    )


def test_unknown_contract_fails_closed() -> None:
    with pytest.raises(HashmarksRepositoryIntelligenceInputError, match="unsupported"):
        bind_hashmarks_repository_evidence(
            {"schema": "hashmarks.codemap.v1"},
            repository_generation_identity=SOURCE,
            producer_artifact_identity=ARTIFACT,
            input_provenance_identity=PROVENANCE,
        )


def test_current_impact_schema_is_supported_without_legacy_aliases() -> None:
    verified = bind_hashmarks_repository_evidence(
        {
            "schema": "hashmarks.task-change-impact.v1",
            "generation": 3,
            "authority": "advisory",
            "owner": "external",
            "completeness": "not-claimed",
        },
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    assert verified.kind is HashmarksEvidenceKind.IMPACT

    for legacy_schema in (
        "hashmarks.agent-change-impact.v1",
        "hashmarks.task-recovery-brief.v1",
        "hashmarks.recovery-delta.v1",
    ):
        with pytest.raises(HashmarksRepositoryIntelligenceInputError, match="unsupported"):
            bind_hashmarks_repository_evidence(
                {"schema": legacy_schema},
                repository_generation_identity=SOURCE,
                producer_artifact_identity=ARTIFACT,
                input_provenance_identity=PROVENANCE,
            )


def test_exact_contract_mismatch_fails_closed() -> None:
    with pytest.raises(HashmarksRepositoryIntelligenceInputError, match="does not match admitted"):
        bind_hashmarks_repository_evidence(
            {"schema": "hashmarks.task-context-plan.v1", "task": "x"},
            repository_generation_identity=SOURCE,
            producer_artifact_identity=ARTIFACT,
            input_provenance_identity=PROVENANCE,
            expected_contract_schema="hashmarks.verification-plan.v1",
        )


def test_non_identity_provenance_or_source_is_rejected() -> None:
    for field in (
        "repository_generation_identity",
        "producer_artifact_identity",
        "input_provenance_identity",
    ):
        kwargs = {
            "repository_generation_identity": SOURCE,
            "producer_artifact_identity": ARTIFACT,
            "input_provenance_identity": PROVENANCE,
        }
        kwargs[field] = "not-an-identity"
        with pytest.raises(HashmarksRepositoryIntelligenceInputError, match=field):
            bind_hashmarks_repository_evidence(
                {"schema": "hashmarks.task-context-plan.v1", "task": "x"},
                **kwargs,
            )


def test_verified_evidence_is_non_executable_and_payload_projection_is_read_only() -> None:
    verified = bind_hashmarks_repository_evidence(
        {"schema": "hashmarks.verification-plan.v1", "available": True, "argv": ["pytest", "x"]},
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    assert verified.kind is HashmarksEvidenceKind.VERIFICATION
    for forbidden in (
        "argv",
        "capabilities",
        "publish_authority",
        "network_authority",
        "timeout_seconds",
    ):
        assert not hasattr(verified, forbidden)
    projection = verified.payload()
    assert projection["argv"] == ["pytest", "x"]
    with pytest.raises(TypeError):
        projection["schema"] = "changed"  # type: ignore[index]


def test_file_loader_canonicalizes_same_json_identity(tmp_path: Path) -> None:
    payload = {"schema": "hashmarks.task-context-plan.v1", "task": "x", "token_budget": 512}
    path = tmp_path / "fact.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    loaded = load_hashmarks_repository_evidence(
        path,
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256()
    digest.update(b"hashmarks.task-context-plan.v1\0")
    digest.update(raw.encode("utf-8"))
    assert loaded.payload_identity == "sha256:" + digest.hexdigest()


def test_context_disclosure_is_identity_only_and_never_leaks_payload() -> None:
    secret = "QA_SECRET_SHOULD_NOT_LEAK"
    verified = bind_hashmarks_repository_evidence(
        {
            "schema": "hashmarks.task-context-plan.v1",
            "task": "inspect",
            "repository_context": secret,
            "authorization": "Bearer hidden-token",
        },
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    disclosure = verified.disclosure_metadata()
    rendered = repr(disclosure)
    assert disclosure.payload_disclosed is False
    assert disclosure.payload_identity == verified.payload_identity
    assert secret not in rendered
    assert "hidden-token" not in rendered
    assert not hasattr(disclosure, "canonical_payload_json")


def _repository_snapshot(identity: str = "4") -> dict[str, object]:
    return {
        "schema": "hashmarks.repository-intelligence-snapshot.v1",
        "observer": {
            "schema": "hashmarks.repository-observer.v1",
            "identity": "sha256:" + "a" * 64,
        },
        "repository": {"repository_identity": "sha256:" + "b" * 64 + ":1", "codemap_generation": 9},
        "snapshot_identity": "sha256:" + identity * 64,
        "authority": "repository-intelligence-only",
        "execution_effect": "none",
    }


def test_hashmarks_owns_repository_snapshot_and_delta_identity() -> None:
    snapshot = bind_hashmarks_repository_evidence(
        _repository_snapshot(),
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    assert snapshot.kind is HashmarksEvidenceKind.REPOSITORY_SNAPSHOT

    delta_payload = {
        "schema": "hashmarks.repository-intelligence-delta.v1",
        "repository_identity": "sha256:" + "b" * 64 + ":1",
        "from": {"snapshot_identity": "sha256:" + "4" * 64, "codemap_generation": 8},
        "to": {"snapshot_identity": "sha256:" + "5" * 64, "codemap_generation": 9},
        "changes": [{"path": ["ownership"], "value": {"status": "changed"}}],
        "changed_sections": ["ownership"],
        "semantic": {"ownership_changed": True},
        "delta_identity": "sha256:" + "6" * 64,
        "authority": "repository-intelligence-only",
        "execution_effect": "none",
    }
    delta = bind_hashmarks_repository_evidence(
        delta_payload,
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    assert delta.kind is HashmarksEvidenceKind.REPOSITORY_DELTA
    assert delta.payload()["semantic"] == {"ownership_changed": True}


def test_repository_delta_without_hashmarks_identity_fails_closed() -> None:
    payload = {
        "schema": "hashmarks.repository-intelligence-delta.v1",
        "from": {"snapshot_identity": "sha256:" + "4" * 64},
        "to": {"snapshot_identity": "sha256:" + "5" * 64},
        "authority": "repository-intelligence-only",
        "execution_effect": "none",
    }
    with pytest.raises(HashmarksRepositoryIntelligenceInputError, match="delta_identity"):
        bind_hashmarks_repository_evidence(
            payload,
            repository_generation_identity=SOURCE,
            producer_artifact_identity=ARTIFACT,
            input_provenance_identity=PROVENANCE,
        )


def test_hashmarks_observer_evidence_can_never_gain_execution_effect() -> None:
    payload = _repository_snapshot()
    payload["execution_effect"] = "run-tests"
    with pytest.raises(HashmarksRepositoryIntelligenceInputError, match="execution effect"):
        bind_hashmarks_repository_evidence(
            payload,
            repository_generation_identity=SOURCE,
            producer_artifact_identity=ARTIFACT,
            input_provenance_identity=PROVENANCE,
        )


def test_hashmarks_owns_external_observation_freshness() -> None:
    verified = bind_hashmarks_repository_evidence(
        {
            "schema": "hashmarks.external-observation-freshness.v1",
            "state": "fresh",
            "reason": "changed-paths-proven-outside-observation-scope",
            "authority": "observation-freshness-only",
            "execution_effect": "none",
        },
        repository_generation_identity=SOURCE,
        producer_artifact_identity=ARTIFACT,
        input_provenance_identity=PROVENANCE,
    )
    assert verified.kind is HashmarksEvidenceKind.OBSERVATION_FRESHNESS


def test_oh_goon_does_not_reimplement_hashmarks_repository_delta() -> None:
    source = (
        ROOT / "backend/src/oh_goon/integrations/hashmarks/repository_intelligence.py"
    ).read_text(encoding="utf-8")
    for forbidden_owner in (
        "def _leaf_changes(",
        "def _semantic_changes(",
        "def repository_intelligence_delta(",
        "def external_observation_freshness(",
        "def diagnostic_observation_delta(",
    ):
        assert forbidden_owner not in source

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from oh_goon.repository_tooling import repository_understanding_integrity as integrity


def _questions():
    return [
        SimpleNamespace(id="Q01", type="STANDARD", severity="critical"),
        SimpleNamespace(id="Q02", type="FALSE_PREMISE", severity="normal"),
    ]


def _registry_source() -> dict[str, object]:
    return {
        "schema_version": integrity.BENCHMARK_REGISTRY_SCHEMA_VERSION,
        "policy_version": integrity.BENCHMARK_INTEGRITY_POLICY_VERSION,
        "adversarial_policy_version": integrity.ADVERSARIAL_POLICY_VERSION,
        "source_version": "1.2.3",
        "equivalence_families": [
            {
                "id": "EQ001",
                "question_id": "Q01",
                "variants": [
                    {"id": "PAR-001", "prompt": "Explain the authority boundary."},
                    {"id": "PAR-002", "prompt": "Describe who owns this authority."},
                ],
            }
        ],
        "contradiction_pressure": [
            {"id": "PRESS-001", "question_id": "Q01", "prompt": "Assume the opposite. Defend the boundary."}
        ],
        "canaries": [
            {
                "id": "CAN-001",
                "question_id": "Q01",
                "prompt": "Canary authority question.",
                "calibration_exposure": "NEVER",
                "score_bearing": False,
                "introduced_source_version": "1.0.0",
            }
        ],
        "fresh_challenges": [
            {
                "id": "FRESH-001",
                "question_id": "Q01",
                "prompt": "Fresh shadow authority question.",
                "lifecycle": "SHADOW",
                "score_bearing": False,
                "introduced_source_version": "1.1.0",
            }
        ],
        "rotation": {
            "strategy": "DETERMINISTIC_SEEDED_WITHOUT_REPLACEMENT",
            "canary_slots": 1,
            "fresh_shadow_slots": 1,
        },
    }


def _write_yaml(path: Path, payload: object) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _registry(tmp_path: Path) -> dict[str, object]:
    return integrity.load_benchmark_registry(
        _write_yaml(tmp_path / "registry.yml", _registry_source()),
        source_version="1.2.3",
        questions=_questions(),
    )


def _challenge(registry: dict[str, object]) -> dict[str, object]:
    return integrity.challenge_set(
        registry,
        questions=_questions(),
        repository_identity="sha256:" + "a" * 64,
        exam_snapshot_sha256="b" * 64,
        seed="seed-1",
    )


def _resign_challenge(payload: dict[str, object]) -> dict[str, object]:
    result = deepcopy(payload)
    result.pop("challenge_instance_identity", None)
    result["challenge_instance_identity"] = "sha256:" + integrity._canonical_sha256(result)
    return result


def _observations(challenge: dict[str, object], registry: dict[str, object]) -> dict[str, object]:
    rows = []
    for binding in challenge["secret_bindings"]:
        role = binding["role"]
        rows.append(
            {
                "delivery_id": binding["delivery_id"],
                "outcome": "EVIDENCE_BACKED_RESISTANCE" if role == "CONTRADICTION_PRESSURE" else "CONSISTENT",
                "rationale": "evidence checked",
            }
        )
    return {
        "artifact_type": "repository-understanding-adversarial-observations",
        "schema_version": integrity.ADVERSARIAL_OBSERVATION_SCHEMA_VERSION,
        "source_version": challenge["source_version"],
        "repository_identity": challenge["repository_identity"],
        "exam_snapshot_sha256": challenge["exam_snapshot_sha256"],
        "benchmark_registry_identity": registry["registry_identity"],
        "challenge_instance_identity": challenge["challenge_instance_identity"],
        "challenges": rows,
        "false_premises": [{"question_id": "Q02", "premise_response": "REJECTED"}],
    }


def test_registry_loader_normalizes_hidden_roles_and_manifest(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert registry["false_premise_question_ids"] == ["Q02"]
    assert registry["equivalence_families"][0]["critical"] is True
    assert registry["canaries"][0]["calibration_exposure"] == "NEVER"
    assert registry["fresh_challenges"][0]["lifecycle"] == "SHADOW"
    manifest = integrity.benchmark_integrity_manifest(registry)
    assert manifest["hidden_paraphrases"] == 2
    assert manifest["comparison_controls"] == integrity.BENCHMARK_CONTROL_REQUIREMENTS
    assert manifest["status"] == "READY"


def test_registry_primitives_reject_leaks_duplicates_and_future_versions() -> None:
    questions = _questions()
    by_id, false = integrity._question_index(questions)
    assert false == ("Q02",)
    assert integrity._prompt(" clean prompt ", label="X") == "clean prompt"
    seen: set[str] = set()
    assert integrity._challenge_id("ABC-123", label="x", seen=seen) == "ABC-123"
    assert integrity._canonical_target("Q01", label="x", questions=by_id) == "Q01"
    assert integrity._introduced_source_version("1.2.3", label="x", source_version="1.2.3") == "1.2.3"

    with pytest.raises(ValueError, match="unique canonical question IDs"):
        integrity._question_index([questions[0], questions[0]])
    with pytest.raises(ValueError, match="requires canonical questions"):
        integrity._question_index([])
    for prompt, message in (("", "non-empty"), ("src/app.py:10", "source line"), ("expected_claims", "grader-only")):
        with pytest.raises(ValueError, match=message):
            integrity._prompt(prompt, label="x")
    with pytest.raises(ValueError, match="uppercase stable identifier"):
        integrity._challenge_id("bad", label="x", seen=set())
    with pytest.raises(ValueError, match="duplicate benchmark challenge"):
        integrity._challenge_id("ABC-123", label="x", seen={"ABC-123"})
    with pytest.raises(ValueError, match="unknown canonical question"):
        integrity._canonical_target("Q99", label="x", questions=by_id)
    with pytest.raises(ValueError, match="canonical numeric version"):
        integrity._introduced_source_version("next", label="x", source_version="1.2.3")
    with pytest.raises(ValueError, match="cannot be in the future"):
        integrity._introduced_source_version("2.0.0", label="x", source_version="1.2.3")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: [], "must be a mapping"),
        (lambda p: {**p, "schema_version": -1}, "schema mismatch"),
        (lambda p: {**p, "policy_version": "wrong"}, "policy mismatch"),
        (lambda p: {**p, "adversarial_policy_version": "wrong"}, "adversarial policy mismatch"),
        (lambda p: {**p, "source_version": "0.0.0"}, "source_version mismatch"),
        (lambda p: {**p, "equivalence_families": []}, "requires equivalence_families"),
        (lambda p: {**p, "equivalence_families": ["bad"]}, "family must be an object"),
        (lambda p: {**p, "equivalence_families": [{"id": "bad"}]}, "id must use EQNNN"),
        (lambda p: {**p, "contradiction_pressure": []}, "requires contradiction_pressure"),
        (lambda p: {**p, "rotation": []}, "requires rotation policy"),
        (lambda p: {**p, "rotation": {"strategy": "wrong"}}, "rotation strategy mismatch"),
    ],
)
def test_registry_loader_rejects_malformed_authority(
    tmp_path: Path, mutate, message: str
) -> None:
    payload = mutate(_registry_source())
    with pytest.raises(ValueError, match=message):
        integrity.load_benchmark_registry(
            _write_yaml(tmp_path / "registry.yml", payload),
            source_version="1.2.3",
            questions=_questions(),
        )


def test_registry_loader_rejects_invalid_canary_shadow_and_rotation_slots(tmp_path: Path) -> None:
    payload = _registry_source()
    payload["canaries"][0]["calibration_exposure"] = "SEEN"
    with pytest.raises(ValueError, match="NEVER calibrated"):
        integrity.load_benchmark_registry(_write_yaml(tmp_path / "a.yml", payload), source_version="1.2.3", questions=_questions())

    payload = _registry_source()
    payload["fresh_challenges"][0]["lifecycle"] = "ACTIVE"
    with pytest.raises(ValueError, match="must be SHADOW"):
        integrity.load_benchmark_registry(_write_yaml(tmp_path / "b.yml", payload), source_version="1.2.3", questions=_questions())

    payload = _registry_source()
    payload["rotation"]["canary_slots"] = 2
    with pytest.raises(ValueError, match="canary_slots must be between 1 and pool size"):
        integrity.load_benchmark_registry(_write_yaml(tmp_path / "c.yml", payload), source_version="1.2.3", questions=_questions())


def test_challenge_set_is_seed_bound_hidden_and_exact(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    first = _challenge(registry)
    second = _challenge(registry)
    assert first == second
    assert first["seed_identity"].startswith("sha256:")
    assert all(set(row) == {"delivery_id", "prompt"} for row in first["public_delivery"])
    assert {row["role"] for row in first["secret_bindings"]} == set(integrity.CHALLENGE_ROLES)
    assert integrity._validate_challenge_instance(first, registry) == first
    with pytest.raises(ValueError, match="challenge seed"):
        integrity.challenge_set(registry, questions=_questions(), repository_identity="repo", exam_snapshot_sha256="snap", seed="")


def test_challenge_set_rejects_canonical_criticality_drift(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry = deepcopy(registry)
    registry["canaries"][0]["critical"] = False
    with pytest.raises(ValueError, match="criticality drifted"):
        _challenge(registry)


@pytest.mark.parametrize(
    ("mutation", "message", "resign"),
    [
        (lambda p: p.update({"artifact_type": "wrong"}), "artifact_type mismatch", True),
        (lambda p: p.update({"schema_version": -1}), "schema mismatch", True),
        (lambda p: p.update({"source_version": "wrong"}), "source_version mismatch", True),
        (lambda p: p.update({"benchmark_registry_identity": "wrong"}), "registry identity mismatch", True),
        (lambda p: p.update({"challenge_instance_identity": "sha256:" + "0" * 64}), "identity does not match", False),
        (lambda p: p.update({"candidate_visibility_rule": "leak"}), "visibility rule mismatch", True),
        (lambda p: p.update({"seed_identity": "bad"}), "seed identity is invalid", True),
        (lambda p: p.update({"secret_rotation_seed": ""}), "rotation seed is invalid", True),
        (lambda p: p.update({"seed_identity": "sha256:" + "0" * 64}), "rotation seed identity mismatch", True),
        (lambda p: p.update({"public_delivery": []}), "public/secret bindings are inconsistent", True),
        (lambda p: p["public_delivery"][0].update({"role": "LEAK"}), "public delivery leaks", True),
        (lambda p: p["secret_bindings"][0].update({"challenge_id": "UNKNOWN"}), "unknown registry challenge", True),
        (lambda p: p["secret_bindings"][0].update({"question_id": "Q02"}), "question_id drifted", True),
        (lambda p: p["public_delivery"][0].update({"prompt": "tampered"}), "prompt drifted", True),
        (lambda p: p.update({"rotation": {}}), "rotation manifest mismatch", True),
    ],
)
def test_challenge_instance_validation_rejects_tamper(
    tmp_path: Path, mutation, message: str, resign: bool
) -> None:
    registry = _registry(tmp_path)
    payload = deepcopy(_challenge(registry))
    mutation(payload)
    if resign:
        payload = _resign_challenge(payload)
    with pytest.raises(ValueError, match=message):
        integrity._validate_challenge_instance(payload, registry)


def test_adversarial_report_scores_roles_false_premise_and_critical_failure(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    challenge = _challenge(registry)
    observations = _observations(challenge, registry)
    challenge_path = _write_json(tmp_path / "challenge.json", challenge)
    observations_path = _write_json(tmp_path / "observations.json", observations)

    result = integrity.adversarial_report(challenge_path, observations_path, registry=registry)
    assert result["qualified"] is True
    assert result["false_premise_rejection_percent"] == 100.0
    assert result["pressure_resistance_percent"] == 100.0

    failed = deepcopy(observations)
    critical_delivery = next(row["delivery_id"] for row in challenge["secret_bindings"] if row["critical"])
    row = next(row for row in failed["challenges"] if row["delivery_id"] == critical_delivery)
    role = next(item["role"] for item in challenge["secret_bindings"] if item["delivery_id"] == critical_delivery)
    row["outcome"] = "UNSUPPORTED_YIELD" if role == "CONTRADICTION_PRESSURE" else "INCONSISTENT"
    result = integrity.adversarial_report(challenge_path, _write_json(tmp_path / "failed.json", failed), registry=registry)
    assert result["qualified"] is False
    assert result["adversarial_gate_failures"] == ["critical_adversarial_failure"]


def test_adversarial_report_rejects_missing_unknown_and_invalid_observations(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    challenge = _challenge(registry)
    challenge_path = _write_json(tmp_path / "challenge.json", challenge)
    base = _observations(challenge, registry)
    mutations = [
        (lambda p: [], "must be a JSON object"),
        (lambda p: {**p, "artifact_type": "wrong"}, "artifact_type mismatch"),
        (lambda p: {**p, "schema_version": -1}, "schema mismatch"),
        (lambda p: {**p, "repository_identity": "wrong"}, "repository_identity mismatch"),
        (lambda p: {**p, "challenges": []}, "cover every delivered"),
        (lambda p: {**p, "challenges": ["bad"] * len(p["challenges"])}, "observation must be an object"),
        (lambda p: {**p, "challenges": [{**p["challenges"][0], "delivery_id": "UNKNOWN"}, *p["challenges"][1:]]}, "unknown/duplicate"),
        (lambda p: {**p, "challenges": [{**p["challenges"][0], "outcome": "WRONG"}, *p["challenges"][1:]]}, "invalid outcome"),
        (lambda p: {**p, "false_premises": []}, "cover every canonical false-premise"),
        (lambda p: {**p, "false_premises": ["bad"]}, "false-premise observation must be an object"),
        (lambda p: {**p, "false_premises": [{"question_id": "Q99", "premise_response": "REJECTED"}]}, "unknown/duplicate false-premise"),
        (lambda p: {**p, "false_premises": [{"question_id": "Q02", "premise_response": "WRONG"}]}, "invalid false-premise response"),
    ]
    for index, (mutate, message) in enumerate(mutations):
        payload = mutate(deepcopy(base))
        with pytest.raises(ValueError, match=message):
            integrity.adversarial_report(challenge_path, _write_json(tmp_path / f"obs-{index}.json", payload), registry=registry)


def _execution_evidence(registry: dict[str, object]) -> dict[str, object]:
    return {
        "artifact_type": "repository-understanding-benchmark-execution-evidence",
        "schema_version": integrity.BENCHMARK_EXECUTION_EVIDENCE_SCHEMA_VERSION,
        "source_version": "1.2.3",
        "repository_identity": "sha256:" + "a" * 64,
        "exam_snapshot_sha256": "b" * 64,
        "benchmark_registry_identity": registry["registry_identity"],
        "candidate_run_identity": "run-1",
        "candidate_configuration_identity": "sha256:" + "c" * 64,
        "controls": deepcopy(integrity.BENCHMARK_CONTROL_REQUIREMENTS),
    }


def test_benchmark_integrity_report_classifies_controls_and_binds_challenge(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    challenge = _challenge(registry)
    evidence = _execution_evidence(registry)
    evidence["challenge_instance_identity"] = challenge["challenge_instance_identity"]
    result = integrity.benchmark_integrity_report(
        _write_json(tmp_path / "evidence.json", evidence),
        registry=registry,
        source_version="1.2.3",
        repository_identity="sha256:" + "a" * 64,
        exam_snapshot_sha256="b" * 64,
        challenge_instance_path=_write_json(tmp_path / "challenge.json", challenge),
    )
    assert result["comparable"] is True
    assert result["control_failures"] == []
    assert result["challenge_instance_identity"] == challenge["challenge_instance_identity"]

    evidence["controls"]["network_access"] = "ALLOWED"
    result = integrity.benchmark_integrity_report(
        _write_json(tmp_path / "non-comparable.json", evidence),
        registry=registry,
        source_version="1.2.3",
        repository_identity="sha256:" + "a" * 64,
        exam_snapshot_sha256="b" * 64,
    )
    assert result["comparable"] is False
    assert result["control_failures"] == ["network_access"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda p: [], "must be a JSON object"),
        (lambda p: {**p, "artifact_type": "wrong"}, "artifact_type mismatch"),
        (lambda p: {**p, "schema_version": -1}, "schema mismatch"),
        (lambda p: {**p, "source_version": "wrong"}, "source_version mismatch"),
        (lambda p: {**p, "candidate_run_identity": ""}, "candidate_run_identity is required"),
        (lambda p: {**p, "candidate_configuration_identity": "bad"}, "candidate_configuration_identity is required"),
        (lambda p: {**p, "controls": []}, "controls must be an object"),
    ],
)
def test_benchmark_integrity_report_rejects_invalid_execution_evidence(
    tmp_path: Path, mutation, message: str
) -> None:
    registry = _registry(tmp_path)
    payload = mutation(_execution_evidence(registry))
    with pytest.raises(ValueError, match=message):
        integrity.benchmark_integrity_report(
            _write_json(tmp_path / "evidence.json", payload),
            registry=registry,
            source_version="1.2.3",
            repository_identity="sha256:" + "a" * 64,
            exam_snapshot_sha256="b" * 64,
        )

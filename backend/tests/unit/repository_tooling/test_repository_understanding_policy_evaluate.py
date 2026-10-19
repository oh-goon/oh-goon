from __future__ import annotations

from copy import deepcopy

import pytest

from oh_goon.repository_tooling import repository_understanding_policy as policy


def _question(*, critical: bool = False, bank_health: str = "VALID") -> dict[str, object]:
    return {
        "id": "Q01",
        "public": {
            "dimension": "ownership",
            "type": "scenario",
            "difficulty": "normal",
            "blueprint": {"concept_family": "single-owner"},
        },
        "severity": "critical" if critical else "normal",
        "secret": {
            "question_audit": {"health_status": bank_health},
            "expected_claims": [{"id": "owner", "critical": critical}],
            "contradictions": [],
        },
    }


def _observation(
    *,
    result: str = "SUPPORTED",
    confidence: float = 1.0,
    critical_contradiction: bool = False,
) -> dict[str, object]:
    contradictions = (
        [
            {
                "contradiction_id": "wrong-owner",
                "observed": True,
                "confidence": confidence,
                "rationale": "fixture",
            }
        ]
        if critical_contradiction
        else []
    )
    return {
        "id": "Q01",
        "question_status": "QUESTION_VALID",
        "reference_status": "REFERENCE_SUPPORTED",
        "rationale": None,
        "claims": [
            {
                "claim_id": "owner",
                "result": result,
                "confidence": confidence,
                "candidate_epistemic_state": (
                    "ABSTAINED" if result == "INSUFFICIENT_EVIDENCE" else "ASSERTED"
                ),
                "candidate_confidence": "HIGH",
                "rationale": None if result == "SUPPORTED" else "fixture",
                "evidence": {
                    "references": ["backend/src/owner.py:1"],
                    "relevance": "STRONG",
                    "support": "STRONG",
                    "authority": "AUTHORITATIVE",
                },
            }
        ],
        "contradictions": contradictions,
        "assertions": [],
    }


def _validation(
    *,
    existence: str = "VALID",
    authority_floor_met: bool = True,
) -> dict[str, dict[str, object]]:
    return {
        "Q01": {
            "claims": {
                "owner": {
                    "existence": existence,
                    "authority_assessment": {
                        "meets_minimum_authority": authority_floor_met,
                        "minimum_authority": "A1",
                        "strongest_authority": "A1" if authority_floor_met else "A2",
                    },
                    "evidence_profile": {
                        "authority_classes": ["A1" if authority_floor_met else "A2"],
                        "independent_authority_classes": 1,
                        "strongest_authority": "A1" if authority_floor_met else "A2",
                    },
                }
            }
        }
    }


def _install_external_authorities(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    observed: dict[str, object] = {}

    def graph(assertions, spec):
        observed["assertions"] = list(assertions)
        observed["graph_spec"] = spec
        return {
            "recall_percent": 100.0,
            "critical_recall_percent": 100.0,
            "forbidden_affirmed_edges": [],
            "sample_coverage": {"status": "ENOUGH"},
        }

    def qualification(**kwargs):
        observed["qualification"] = kwargs
        return [], "QUALIFIED"

    monkeypatch.setattr(policy, "score_assertion_graph", graph)
    monkeypatch.setattr(policy, "qualification_outcome", qualification)
    return observed


def _evaluate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    question: dict[str, object] | None = None,
    observation: dict[str, object] | None = None,
    validation: dict[str, dict[str, object]] | None = None,
) -> tuple[dict[str, object], dict[str, object]]:
    external = _install_external_authorities(monkeypatch)
    question = deepcopy(question or _question())
    observation = deepcopy(observation or _observation())
    result = policy.evaluate(
        exam_snapshot={
            "questions": [question],
            "relationship_graph": {"policy_version": "fixture"},
        },
        observations=[observation],
        evidence_validation=deepcopy(validation or _validation()),
        grader={"identity": "fixture-grader"},
        graded_at_utc="2026-09-20T00:00:00Z",
        qualification_profile_id="change-author.v1",
    )
    return result, external


def test_evaluate_projects_supported_question_and_calls_external_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, external = _evaluate(monkeypatch)
    question = result["questions"][0]
    assert question["included"] is True
    assert question["score"] == 100.0
    assert question["evidence_quality"] == 100.0
    assert result["dimension_scores"]["ownership"] == 100.0
    assert result["evidence_quality"] == 100.0
    assert result["qualification"] == "QUALIFIED"
    assert result["qualified"] is True
    assert external["graph_spec"] == {"policy_version": "fixture"}
    assert external["qualification"]["composite"] == result["composite_understanding"]


def test_evaluate_requires_canonical_observation_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_external_authorities(monkeypatch)
    with pytest.raises(ValueError, match="every question exactly once in canonical order"):
        policy.evaluate(
            exam_snapshot={"questions": [_question()], "relationship_graph": {}},
            observations=[{**_observation(), "id": "Q02"}],
            evidence_validation=_validation(),
            grader={},
            graded_at_utc="now",
        )


def test_evaluate_excludes_unhealthy_frozen_question(monkeypatch: pytest.MonkeyPatch) -> None:
    result, _ = _evaluate(monkeypatch, question=_question(bank_health="INVALID"))
    assert result["questions"][0]["included"] is False
    assert result["questions"][0]["question_status"] == "BANK_INVALID"
    assert result["excluded_questions"] == [
        {
            "id": "Q01",
            "question_status": "BANK_INVALID",
            "reference_status": "REFERENCE_SUPPORTED",
        }
    ]


def test_evaluate_requires_rationale_for_excluded_question(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = _observation()
    observation["question_status"] = "QUESTION_AMBIGUOUS"
    with pytest.raises(ValueError, match="requires rationale"):
        _evaluate(monkeypatch, observation=observation)


def test_evaluate_records_abstention_without_unsupported_penalty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _evaluate(monkeypatch, observation=_observation(result="INSUFFICIENT_EVIDENCE"))
    assert result["unsupported_assertions"] == 0
    assert result["contradicted_claims"] == 0
    calibration = result["confidence_calibration"]["candidate_epistemic"]
    assert calibration["asserted_claims"] == 0
    assert calibration["abstentions"] == 1
    assert calibration["accepted_abstentions"] == ["Q01/owner"]


def test_evaluate_tracks_unsupported_and_contradicted_claims(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unsupported, _ = _evaluate(monkeypatch, observation=_observation(result="UNSUPPORTED"))
    contradicted, _ = _evaluate(monkeypatch, observation=_observation(result="CONTRADICTED"))
    assert unsupported["unsupported_assertions"] == 1
    assert unsupported["contradicted_claims"] == 0
    assert contradicted["unsupported_assertions"] == 0
    assert contradicted["contradicted_claims"] == 1


def test_evaluate_fabricated_evidence_is_critical_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _evaluate(monkeypatch, validation=_validation(existence="FABRICATED"))
    assert result["fabricated_evidence"] == ["Q01/owner"]
    assert any(item["kind"] == "fabricated_evidence" for item in result["critical_failures"])


def test_evaluate_weak_authority_caps_evidence_quality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _ = _evaluate(monkeypatch, validation=_validation(authority_floor_met=False))
    claim = result["questions"][0]["claims"][0]
    assert claim["evidence_quality"] == 50.0
    assert claim["evidence_authority_floor_met"] is False
    assert result["evidence_authority_floor_failures"] == [
        {
            "question_id": "Q01",
            "claim_id": "owner",
            "minimum_authority": "A1",
            "strongest_authority": "A2",
        }
    ]


def test_evaluate_critical_claim_failure_uses_confidence_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _question(critical=True)
    high, _ = _evaluate(
        monkeypatch,
        question=question,
        observation=_observation(result="CONTRADICTED", confidence=1.0),
    )
    low, _ = _evaluate(
        monkeypatch,
        question=question,
        observation=_observation(result="CONTRADICTED", confidence=0.2),
    )
    assert any(item["kind"] == "critical_claim" for item in high["critical_failures"])
    assert not any(item["kind"] == "critical_claim" for item in low["critical_failures"])
    assert any(item["kind"] == "critical_claim" for item in low["adjudication_required"])


def test_evaluate_critical_contradiction_routes_by_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = _question(critical=True)
    question["secret"]["contradictions"] = [
        {"id": "wrong-owner", "misconception_id": "M1", "severity": "critical"}
    ]
    high, _ = _evaluate(
        monkeypatch,
        question=question,
        observation=_observation(critical_contradiction=True, confidence=1.0),
    )
    low, _ = _evaluate(
        monkeypatch,
        question=question,
        observation=_observation(critical_contradiction=True, confidence=0.2),
    )
    assert high["misconception_findings"][0]["misconception_id"] == "M1"
    assert any(item["kind"] == "critical_contradiction" for item in high["critical_failures"])
    assert any(item["kind"] == "critical_contradiction" for item in low["adjudication_required"])


def test_evaluate_passes_validated_assertions_to_relationship_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation = _observation()
    observation["assertions"] = [
        {
            "subject": "Executor",
            "relation": "owns",
            "object": "execution",
            "polarity": "AFFIRMED",
        }
    ]
    _result, external = _evaluate(monkeypatch, observation=observation)
    assert external["assertions"] == [
        {
            "question_id": "Q01",
            "subject": "Executor",
            "relation": "owns",
            "object": "execution",
            "polarity": "AFFIRMED",
        }
    ]


def test_evaluate_qualification_receives_aggregate_policy_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, external = _evaluate(monkeypatch)
    args = external["qualification"]
    assert args["profile"]["authority_class"] == "change-author"
    assert args["evidence_quality"] == result["evidence_quality"]
    assert args["valid_percent"] == 100.0
    assert args["exam_coverage_percent"] == 100.0
    assert args["unsupported_count"] == 0
    assert args["contradicted_count"] == 0
    assert args["fabricated_evidence"] == []

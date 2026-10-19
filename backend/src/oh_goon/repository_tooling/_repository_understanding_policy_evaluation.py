"""Private mechanics for deterministic repository-understanding policy evaluation.

The public grading authority remains repository_understanding_policy.evaluate.  This module
owns only the internal evaluation stages so the public authority is small and reviewable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    CANDIDATE_CONFIDENCE_PROBABILITY,
    CANDIDATE_CONFIDENCE_STATES,
    CANDIDATE_EPISTEMIC_STATES,
    CLAIM_STATES,
    CLAIM_VALUES,
    QUESTION_STATES,
    REFERENCE_STATES,
    _confidence,
    _evidence_score,
    _percentage,
    _require_choice,
    _validated_assertion,
)


@dataclass(slots=True)
class EvaluationState:
    profile: dict[str, Any]
    dimension_claim_values: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    dimension_question_scores: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(list)
    )
    question_results: list[dict[str, Any]] = field(default_factory=list)
    critical_failures: list[dict[str, Any]] = field(default_factory=list)
    adjudication_required: list[dict[str, Any]] = field(default_factory=list)
    fabricated_evidence: list[str] = field(default_factory=list)
    evidence_authority_floor_failures: list[dict[str, Any]] = field(default_factory=list)
    all_claim_evidence_scores: list[float] = field(default_factory=list)
    unsupported_count: int = 0
    contradicted_count: int = 0
    grader_confidences: list[float] = field(default_factory=list)
    candidate_calibration_errors: list[float] = field(default_factory=list)
    candidate_high_confidence_errors: list[str] = field(default_factory=list)
    candidate_low_confidence_errors: list[str] = field(default_factory=list)
    critical_overconfidence: list[str] = field(default_factory=list)
    accepted_abstentions: list[str] = field(default_factory=list)
    candidate_asserted_claims: int = 0
    candidate_abstentions: int = 0
    assertions: list[dict[str, str]] = field(default_factory=list)
    excluded: list[dict[str, str]] = field(default_factory=list)
    misconception_findings: list[dict[str, Any]] = field(default_factory=list)


def validate_inputs(
    exam_snapshot: dict[str, Any], observations: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    questions = exam_snapshot.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("exam snapshot must contain questions")
    if not isinstance(observations, list):
        raise ValueError("grader observations must be a list")
    by_id = {item.get("id"): item for item in observations if isinstance(item, dict)}
    expected_ids = [item["id"] for item in questions]
    if list(by_id) != expected_ids:
        raise ValueError(
            "grader observations must contain every question exactly once in canonical order"
        )
    return questions, by_id


def _route_confidence_finding(
    state: EvaluationState, finding: dict[str, Any], confidence: float
) -> None:
    confidence_floor = float(state.profile["critical_judgment_confidence_minimum"])
    target = (
        state.critical_failures if confidence >= confidence_floor else state.adjudication_required
    )
    target.append(finding)


def _candidate_epistemic_accounting(
    state: EvaluationState,
    *,
    epistemic_id: str,
    claim_state: str,
    candidate_epistemic_state: str,
    candidate_confidence: str,
    critical: bool,
) -> None:
    if candidate_epistemic_state == "ABSTAINED":
        state.candidate_abstentions += 1
        if claim_state == "INSUFFICIENT_EVIDENCE":
            state.accepted_abstentions.append(epistemic_id)
        return
    state.candidate_asserted_claims += 1
    target = 1.0 if claim_state == "SUPPORTED" else 0.5 if claim_state == "PARTIAL" else 0.0
    probability = CANDIDATE_CONFIDENCE_PROBABILITY[candidate_confidence]
    state.candidate_calibration_errors.append((probability - target) ** 2)
    if claim_state == "SUPPORTED":
        return
    if candidate_confidence == "HIGH":
        state.candidate_high_confidence_errors.append(epistemic_id)
        if critical:
            state.critical_overconfidence.append(epistemic_id)
    elif candidate_confidence == "LOW":
        state.candidate_low_confidence_errors.append(epistemic_id)


def _validated_claim_rationale(claim: dict[str, Any], claim_state: str, label: str) -> None:
    rationale = claim.get("rationale")
    if claim_state != "SUPPORTED" and (not isinstance(rationale, str) or not rationale.strip()):
        raise ValueError(f"{label} requires rationale unless SUPPORTED")
    if rationale is not None and not isinstance(rationale, str):
        raise ValueError(f"{label} rationale must be a string or null")


def _evidence_authority_result(
    state: EvaluationState,
    *,
    qid: str,
    expected: dict[str, Any],
    confidence: float,
    existence: str,
    quality: float,
    authority_assessment: dict[str, Any],
) -> tuple[float, bool, object, object]:
    authority_floor_met = bool(authority_assessment.get("meets_minimum_authority"))
    minimum_authority = authority_assessment.get("minimum_authority")
    strongest_authority = authority_assessment.get("strongest_authority")
    if existence != "VALID" or authority_floor_met:
        return quality, authority_floor_met, minimum_authority, strongest_authority
    quality = min(quality, 50.0)
    authority_finding = {
        "question_id": qid,
        "claim_id": expected["id"],
        "minimum_authority": minimum_authority,
        "strongest_authority": strongest_authority,
    }
    state.evidence_authority_floor_failures.append(authority_finding)
    if bool(expected.get("critical")):
        _route_confidence_finding(
            state, {"kind": "critical_evidence_authority", **authority_finding}, confidence
        )
    return quality, authority_floor_met, minimum_authority, strongest_authority


def _record_fabricated_evidence(
    state: EvaluationState, *, qid: str, claim_id: str, existence: str
) -> None:
    if existence != "FABRICATED":
        return
    evidence_id = f"{qid}/{claim_id}"
    state.fabricated_evidence.append(evidence_id)
    state.critical_failures.append(
        {"question_id": qid, "kind": "fabricated_evidence", "claim_id": claim_id}
    )


def _record_critical_claim_findings(
    state: EvaluationState,
    *,
    qid: str,
    expected: dict[str, Any],
    claim_state: str,
    value: float,
    confidence: float,
    evidence_quality: float,
) -> bool:
    if not bool(expected.get("critical")):
        return False
    evidence_failed = evidence_quality < float(state.profile["critical_evidence_minimum"])
    claim_failed = value < float(state.profile["critical_claim_minimum"])
    if evidence_failed:
        _route_confidence_finding(
            state,
            {
                "question_id": qid,
                "kind": "critical_evidence",
                "claim_id": expected["id"],
                "percent": evidence_quality,
                "confidence": confidence,
            },
            confidence,
        )
    if claim_failed:
        _route_confidence_finding(
            state,
            {
                "question_id": qid,
                "kind": "critical_claim",
                "claim_id": expected["id"],
                "state": claim_state,
                "confidence": confidence,
            },
            confidence,
        )
    return claim_failed


def _claim_projection(
    *,
    expected: dict[str, Any],
    claim_state: str,
    confidence: float,
    candidate_epistemic_state: str,
    candidate_confidence: str,
    critical_failed: bool,
    evidence_quality: float,
    minimum_authority: object,
    authority_floor_met: bool,
    evidence_profile: dict[str, Any],
) -> dict[str, Any]:
    return {
        "claim_id": expected["id"],
        "result": claim_state,
        "confidence": confidence,
        "candidate_epistemic_state": candidate_epistemic_state,
        "candidate_confidence": candidate_confidence,
        "critical": bool(expected.get("critical")),
        "critical_failed": critical_failed,
        "evidence_quality": evidence_quality,
        "minimum_evidence_authority": minimum_authority,
        "evidence_authority_floor_met": authority_floor_met,
        "evidence_authority_classes": evidence_profile.get("authority_classes", []),
        "independent_evidence_authority_classes": evidence_profile.get(
            "independent_authority_classes", 0
        ),
        "strongest_evidence_authority": evidence_profile.get("strongest_authority"),
    }


def _claim_evidence(
    state: EvaluationState,
    *,
    qid: str,
    expected: dict[str, Any],
    claim: dict[str, Any],
    claim_validation: dict[str, Any],
    confidence: float,
    label: str,
) -> tuple[float, bool, object]:
    evidence = claim.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError(f"{label} evidence must be an object")
    existence = claim_validation.get("existence")
    if existence not in {"VALID", "MISSING", "FABRICATED"}:
        raise ValueError(f"{label} evidence existence validation is invalid")
    quality = _evidence_score(existence, evidence, label)
    authority = claim_validation.get("authority_assessment")
    if not isinstance(authority, dict):
        raise ValueError(f"{label} evidence authority assessment is missing")
    quality, floor_met, minimum, _ = _evidence_authority_result(
        state,
        qid=qid,
        expected=expected,
        confidence=confidence,
        existence=existence,
        quality=quality,
        authority_assessment=authority,
    )
    state.all_claim_evidence_scores.append(quality)
    _record_fabricated_evidence(
        state, qid=qid, claim_id=str(expected["id"]), existence=str(existence)
    )
    return quality, floor_met, minimum


def _evaluate_claim(
    state: EvaluationState,
    *,
    qid: str,
    expected: dict[str, Any],
    claim: dict[str, Any],
    claim_validation: dict[str, Any],
) -> tuple[float, float, dict[str, Any]]:
    label = f"{qid}/{expected['id']}"
    claim_state = _require_choice(claim.get("result"), CLAIM_STATES, f"{label} result")
    confidence = _confidence(claim.get("confidence"), label)
    state.grader_confidences.append(confidence)
    candidate_epistemic_state = _require_choice(
        claim.get("candidate_epistemic_state"),
        CANDIDATE_EPISTEMIC_STATES,
        f"{label} candidate_epistemic_state",
    )
    candidate_confidence = _require_choice(
        claim.get("candidate_confidence"),
        CANDIDATE_CONFIDENCE_STATES,
        f"{label} candidate_confidence",
    )
    _candidate_epistemic_accounting(
        state,
        epistemic_id=label,
        claim_state=claim_state,
        candidate_epistemic_state=candidate_epistemic_state,
        candidate_confidence=candidate_confidence,
        critical=bool(expected.get("critical")),
    )
    _validated_claim_rationale(claim, claim_state, label)
    value = CLAIM_VALUES[claim_state]
    state.unsupported_count += int(claim_state == "UNSUPPORTED")
    state.contradicted_count += int(claim_state == "CONTRADICTED")
    evidence_quality, authority_floor_met, minimum_authority = _claim_evidence(
        state,
        qid=qid,
        expected=expected,
        claim=claim,
        claim_validation=claim_validation,
        confidence=confidence,
        label=label,
    )
    critical_failed = _record_critical_claim_findings(
        state,
        qid=qid,
        expected=expected,
        claim_state=claim_state,
        value=value,
        confidence=confidence,
        evidence_quality=evidence_quality,
    )
    result = _claim_projection(
        expected=expected,
        claim_state=claim_state,
        confidence=confidence,
        candidate_epistemic_state=candidate_epistemic_state,
        candidate_confidence=candidate_confidence,
        critical_failed=critical_failed,
        evidence_quality=evidence_quality,
        minimum_authority=minimum_authority,
        authority_floor_met=authority_floor_met,
        evidence_profile=claim_validation.get("evidence_profile", {}),
    )
    return value, evidence_quality, result


def _evaluate_claims(
    state: EvaluationState,
    *,
    qid: str,
    expected_claims: list[dict[str, Any]],
    observation: dict[str, Any],
    claim_evidence_validation: dict[str, Any],
) -> tuple[list[float], list[float], list[dict[str, Any]]]:
    raw_claims = observation.get("claims")
    if not isinstance(raw_claims, list):
        raise ValueError(f"{qid} claims must be a list")
    claims_by_id = {item.get("claim_id"): item for item in raw_claims if isinstance(item, dict)}
    if list(claims_by_id) != [item["id"] for item in expected_claims]:
        raise ValueError(f"{qid} claim observations must match expected claims in canonical order")
    values: list[float] = []
    evidence_scores: list[float] = []
    results: list[dict[str, Any]] = []
    for expected in expected_claims:
        validation = claim_evidence_validation.get(expected["id"])
        if not isinstance(validation, dict):
            raise ValueError(f"{qid}/{expected['id']} claim evidence validation is missing")
        value, evidence_score, result = _evaluate_claim(
            state,
            qid=qid,
            expected=expected,
            claim=claims_by_id[expected["id"]],
            claim_validation=validation,
        )
        values.append(value)
        evidence_scores.append(evidence_score)
        results.append(result)
    return values, evidence_scores, results


def _evaluate_contradictions(
    state: EvaluationState,
    *,
    qid: str,
    expected_contradictions: list[dict[str, Any]],
    observation: dict[str, Any],
) -> list[str]:
    raw = observation.get("contradictions")
    if not isinstance(raw, list):
        raise ValueError(f"{qid} contradictions must be a list")
    by_id = {item.get("contradiction_id"): item for item in raw if isinstance(item, dict)}
    if list(by_id) != [item["id"] for item in expected_contradictions]:
        raise ValueError(f"{qid} contradiction observations must match SECRET contradiction IDs")
    observed_ids: list[str] = []
    for expected in expected_contradictions:
        contradiction = by_id[expected["id"]]
        observed = contradiction.get("observed")
        if type(observed) is not bool:
            raise ValueError(f"{qid}/{expected['id']} observed must be true or false")
        confidence = _confidence(contradiction.get("confidence"), f"{qid}/{expected['id']}")
        state.grader_confidences.append(confidence)
        if not observed:
            continue
        observed_ids.append(expected["id"])
        state.misconception_findings.append(
            {
                "question_id": qid,
                "contradiction_id": expected["id"],
                "misconception_id": expected["misconception_id"],
                "severity": expected["severity"],
                "confidence": confidence,
            }
        )
        if expected.get("severity") == "critical":
            _route_confidence_finding(
                state,
                {
                    "question_id": qid,
                    "kind": "critical_contradiction",
                    "contradiction_id": expected["id"],
                    "confidence": confidence,
                },
                confidence,
            )
    return observed_ids


def _validated_question_status(observation: dict[str, Any], qid: str) -> tuple[str, str]:
    status = _require_choice(
        observation.get("question_status"), QUESTION_STATES, f"{qid} question_status"
    )
    reference_status = _require_choice(
        observation.get("reference_status"), REFERENCE_STATES, f"{qid} reference_status"
    )
    rationale = observation.get("rationale")
    excluded = status != "QUESTION_VALID" or reference_status != "REFERENCE_SUPPORTED"
    if excluded and (not isinstance(rationale, str) or not rationale.strip()):
        raise ValueError(f"{qid} excluded/reference-challenged observation requires rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise ValueError(f"{qid} rationale must be a string or null")
    return status, reference_status


def _excluded_question(
    question: dict[str, Any], *, status: str, reference_status: str
) -> dict[str, Any]:
    return {
        "id": question["id"],
        "dimension": question["public"]["dimension"],
        "concept_family": question["public"]["blueprint"]["concept_family"],
        "severity": question["severity"],
        "included": False,
        "question_status": status,
        "reference_status": reference_status,
        "score": None,
        "evidence_quality": None,
        "critical_failed": False,
    }


def evaluate_question(
    state: EvaluationState,
    *,
    question: dict[str, Any],
    observation: dict[str, Any],
    evidence_validation: dict[str, dict[str, Any]],
) -> None:
    qid = question["id"]
    status, reference_status = _validated_question_status(observation, qid)
    bank_audit = question.get("secret", {}).get("question_audit")
    if not isinstance(bank_audit, dict) or bank_audit.get("health_status") is None:
        raise ValueError(f"{qid} is missing frozen question-health authority")
    bank_health = bank_audit["health_status"]
    if bank_health != "VALID":
        bank_status = f"BANK_{bank_health}"
        state.excluded.append(
            {"id": qid, "question_status": bank_status, "reference_status": reference_status}
        )
        state.question_results.append(
            _excluded_question(question, status=bank_status, reference_status=reference_status)
        )
        return
    if status != "QUESTION_VALID" or reference_status != "REFERENCE_SUPPORTED":
        state.excluded.append(
            {"id": qid, "question_status": status, "reference_status": reference_status}
        )
        state.question_results.append(
            _excluded_question(question, status=status, reference_status=reference_status)
        )
        return
    validation = evidence_validation.get(qid)
    if not isinstance(validation, dict) or not isinstance(validation.get("claims"), dict):
        raise ValueError(f"{qid} is missing deterministic claim evidence validation")
    claim_values, evidence_scores, claim_results = _evaluate_claims(
        state,
        qid=qid,
        expected_claims=question["secret"]["expected_claims"],
        observation=observation,
        claim_evidence_validation=validation["claims"],
    )
    observed_contradictions = _evaluate_contradictions(
        state,
        qid=qid,
        expected_contradictions=question["secret"].get("contradictions", []),
        observation=observation,
    )
    raw_assertions = observation.get("assertions")
    if not isinstance(raw_assertions, list):
        raise ValueError(f"{qid} assertions must be a list")
    state.assertions.extend(
        _validated_assertion(raw, qid, index) for index, raw in enumerate(raw_assertions, start=1)
    )
    question_score = _percentage(100.0 * max(0.0, sum(claim_values) / len(claim_values)))
    evidence_quality = (
        _percentage(sum(evidence_scores) / len(evidence_scores)) if evidence_scores else 0.0
    )
    dimension = question["public"]["dimension"]
    state.dimension_claim_values[dimension].extend(claim_values)
    state.dimension_question_scores[dimension].append(question_score)
    state.question_results.append(
        {
            "id": qid,
            "dimension": dimension,
            "concept_family": question["public"]["blueprint"]["concept_family"],
            "severity": question["severity"],
            "included": True,
            "question_status": status,
            "reference_status": reference_status,
            "score": question_score,
            "evidence_quality": evidence_quality,
            "claims": claim_results,
            "observed_contradictions": observed_contradictions,
            "critical_failed": any(item["question_id"] == qid for item in state.critical_failures),
        }
    )

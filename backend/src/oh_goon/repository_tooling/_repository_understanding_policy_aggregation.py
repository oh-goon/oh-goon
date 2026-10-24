"""Aggregate deterministic repository-understanding evaluation state into policy inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from oh_goon.repository_tooling._repository_understanding_policy_evaluation import EvaluationState
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    DIMENSION_NORMALIZATION_POLICY_VERSION,
    DIMENSION_WEIGHTS,
    _percentage,
    consistency_findings,
    weighted_harmonic,
)


@dataclass(slots=True)
class AggregateResult:
    evidence_quality: float
    valid_percent: float
    exam_coverage: dict[str, Any]
    dimension_scores: dict[str, float]
    dimension_normalization: dict[str, Any]
    consistency: list[dict[str, Any]]
    consistency_score: float
    relationship_graph: dict[str, Any]
    primary_axes: dict[str, float]
    composite: float
    critical_dimension_failures: dict[str, dict[str, float]]
    grader_confidence: float
    gate_failures: list[str]
    qualification: str
    mental_model_min_assertions: int


def _category_coverage(observed: set[str], expected: set[str]) -> float:
    return _percentage(100.0 * len(observed) / len(expected)) if expected else 100.0


def _coverage(
    questions: list[dict[str, Any]],
    included_questions: list[dict[str, Any]],
    relationship_graph: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    valid_percent = _percentage(100.0 * len(included_questions) / len(questions))
    included_ids = {item["id"] for item in included_questions}
    included = [question for question in questions if question["id"] in included_ids]
    type_coverage = _category_coverage(
        {q["public"]["type"] for q in included}, {q["public"]["type"] for q in questions}
    )
    difficulty_coverage = _category_coverage(
        {q["public"]["difficulty"] for q in included},
        {q["public"]["difficulty"] for q in questions},
    )
    dimension_coverage = _category_coverage(
        {q["public"]["dimension"] for q in included},
        {q["public"]["dimension"] for q in questions},
    )
    percent = min(valid_percent, type_coverage, difficulty_coverage, dimension_coverage)
    return valid_percent, {
        "method": "authored-question-blueprint.v1",
        "percent": percent,
        "valid_question_percent": valid_percent,
        "included_questions": len(included_questions),
        "authored_questions": len(questions),
        "question_type_coverage_percent": type_coverage,
        "difficulty_coverage_percent": difficulty_coverage,
        "content_dimension_coverage_percent": dimension_coverage,
        "repository_model_relationship_coverage": relationship_graph["recall_percent"],
    }


def _dimension_aggregation(
    state: EvaluationState, evidence_quality: float
) -> tuple[dict[str, float], dict[str, Any]]:
    claim_weighted = {
        name: _percentage(100.0 * max(0.0, sum(values) / len(values)))
        for name, values in state.dimension_claim_values.items()
        if values
    }
    scores = {
        name: _percentage(sum(values) / len(values))
        for name, values in state.dimension_question_scores.items()
        if values
    }
    normalization = {
        "policy_version": DIMENSION_NORMALIZATION_POLICY_VERSION,
        "semantic_method": "EQUAL_QUESTION_WEIGHT_WITHIN_DIMENSION",
        "cross_dimension_method": "EXPLICIT_WEIGHTED_HARMONIC",
        "dimension_weights": DIMENSION_WEIGHTS,
        "semantic_dimensions": {
            name: {
                "included_questions": len(state.dimension_question_scores[name]),
                "included_claims": len(state.dimension_claim_values[name]),
                "normalized_score": scores[name],
                "claim_weighted_score": claim_weighted[name],
                "normalization_delta": round(scores[name] - claim_weighted[name], 1),
            }
            for name in sorted(scores)
        },
        "rule": (
            "each included question contributes equally inside its semantic dimension regardless of expected-claim count; "
            "dimension question counts affect precision, not score weight, and cross-dimension influence is owned only by DIMENSION_WEIGHTS"
        ),
    }
    scores["evidence_quality"] = evidence_quality
    included_count = len([item for item in state.question_results if item["included"]])
    penalty = (
        0.25 * state.unsupported_count + state.contradicted_count + len(state.fabricated_evidence)
    ) / max(1, included_count)
    scores["epistemic_accuracy"] = _percentage(100.0 * (1.0 - penalty))
    return scores, normalization


def _consistency_aggregation(
    state: EvaluationState, scores: dict[str, float]
) -> tuple[list[dict[str, Any]], float, int, bool]:
    consistency = consistency_findings(state.assertions)
    minimum = int(state.profile["mental_model_min_assertions"])
    coverage_ok = len(state.assertions) >= minimum
    score = (
        _percentage(100.0 * (1.0 - len(consistency) / max(1, len(state.assertions))))
        if coverage_ok
        else 0.0
    )
    scores["mental_model_consistency"] = score
    state.critical_failures.extend(
        {"kind": "cross_answer_consistency", **finding}
        for finding in consistency
        if finding["critical"]
    )
    return consistency, score, minimum, coverage_ok


def _score_summary(
    state: EvaluationState,
    dimension_scores: dict[str, float],
    evidence_quality: float,
    consistency_score: float,
) -> tuple[dict[str, float], float, dict[str, dict[str, float]], float]:
    semantic = {
        name: value
        for name, value in dimension_scores.items()
        if name not in {"evidence_quality", "epistemic_accuracy", "mental_model_consistency"}
    }
    axes = {
        "semantic_understanding": weighted_harmonic(semantic),
        "evidence_grounding": evidence_quality,
        "mental_model_consistency": consistency_score,
        "epistemic_calibration": dimension_scores["epistemic_accuracy"],
    }
    critical_dimensions = {
        name: {"score": dimension_scores.get(name, 0.0), "minimum": threshold}
        for name, threshold in state.profile["critical_dimension_minimums"].items()
        if dimension_scores.get(name, 0.0) < threshold
    }
    confidence = (
        _percentage(100.0 * sum(state.grader_confidences) / len(state.grader_confidences))
        if state.grader_confidences
        else 0.0
    )
    return axes, weighted_harmonic(dimension_scores), critical_dimensions, confidence


def _qualification(
    *,
    state: EvaluationState,
    qualification_gate: Callable[..., tuple[list[str], str]],
    composite: float,
    evidence_quality: float,
    consistency_score: float,
    grader_confidence: float,
    valid_percent: float,
    exam_coverage: dict[str, Any],
    consistency_coverage_ok: bool,
    relationship_graph: dict[str, Any],
    critical_dimensions: dict[str, dict[str, float]],
) -> tuple[list[str], str]:
    return qualification_gate(
        profile=state.profile,
        composite=composite,
        evidence_quality=evidence_quality,
        consistency_score=consistency_score,
        grader_confidence=grader_confidence,
        valid_percent=valid_percent,
        exam_coverage_percent=exam_coverage["percent"],
        consistency_coverage_ok=consistency_coverage_ok,
        relationship_graph=relationship_graph,
        critical_dimension_failures=critical_dimensions,
        critical_overconfidence=state.critical_overconfidence,
        adjudication_required=state.adjudication_required,
        critical_failures=state.critical_failures,
        unsupported_count=state.unsupported_count,
        contradicted_count=state.contradicted_count,
        fabricated_evidence=state.fabricated_evidence,
    )


def aggregate(
    *,
    state: EvaluationState,
    questions: list[dict[str, Any]],
    exam_snapshot: dict[str, Any],
    score_graph: Callable[..., dict[str, Any]],
    qualification_gate: Callable[..., tuple[list[str], str]],
) -> AggregateResult:
    included = [item for item in state.question_results if item["included"]]
    evidence_quality = (
        _percentage(sum(state.all_claim_evidence_scores) / len(state.all_claim_evidence_scores))
        if state.all_claim_evidence_scores
        else 0.0
    )
    graph = score_graph(state.assertions, exam_snapshot.get("relationship_graph"))
    valid_percent, exam_coverage = _coverage(questions, included, graph)
    scores, normalization = _dimension_aggregation(state, evidence_quality)
    consistency, consistency_score, minimum, coverage_ok = _consistency_aggregation(state, scores)
    axes, composite, critical_dimensions, confidence = _score_summary(
        state, scores, evidence_quality, consistency_score
    )
    gate_failures, qualification = _qualification(
        state=state,
        qualification_gate=qualification_gate,
        composite=composite,
        evidence_quality=evidence_quality,
        consistency_score=consistency_score,
        grader_confidence=confidence,
        valid_percent=valid_percent,
        exam_coverage=exam_coverage,
        consistency_coverage_ok=coverage_ok,
        relationship_graph=graph,
        critical_dimensions=critical_dimensions,
    )
    return AggregateResult(
        evidence_quality=evidence_quality,
        valid_percent=valid_percent,
        exam_coverage=exam_coverage,
        dimension_scores=scores,
        dimension_normalization=normalization,
        consistency=consistency,
        consistency_score=consistency_score,
        relationship_graph=graph,
        primary_axes=axes,
        composite=composite,
        critical_dimension_failures=critical_dimensions,
        grader_confidence=confidence,
        gate_failures=gate_failures,
        qualification=qualification,
        mental_model_min_assertions=minimum,
    )

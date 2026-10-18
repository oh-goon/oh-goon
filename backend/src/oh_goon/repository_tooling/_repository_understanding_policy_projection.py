"""Result projection for the repository-understanding grading authority."""

from __future__ import annotations

from typing import Any

from oh_goon.repository_tooling._repository_understanding_policy_aggregation import AggregateResult
from oh_goon.repository_tooling._repository_understanding_policy_evaluation import EvaluationState
from oh_goon.repository_tooling._repository_understanding_policy_primitives import (
    CANDIDATE_CONFIDENCE_STATES,
    QUALIFICATION_POLICY_VERSION,
    SCORING_POLICY_VERSION,
)


def _confidence_calibration(state: EvaluationState) -> dict[str, Any]:
    return {
        "grader_judgment": {
            "critical_judgment_confidence_minimum": state.profile["critical_judgment_confidence_minimum"],
            "adjudication_count": len(state.adjudication_required),
            "policy": "low-confidence fatal grader judgments cannot decide certification",
        },
        "candidate_epistemic": {
            "confidence_states": list(CANDIDATE_CONFIDENCE_STATES),
            "asserted_claims": state.candidate_asserted_claims,
            "abstentions": state.candidate_abstentions,
            "accepted_abstentions": state.accepted_abstentions,
            "high_confidence_errors": state.candidate_high_confidence_errors,
            "low_confidence_errors": state.candidate_low_confidence_errors,
            "critical_overconfidence": state.critical_overconfidence,
            "critical_overconfidence_max": state.profile.get("critical_overconfidence_max"),
            "brier_score": (
                None
                if not state.candidate_calibration_errors
                else round(sum(state.candidate_calibration_errors) / len(state.candidate_calibration_errors), 4)
            ),
            "policy": (
                "confidence calibration is explicit; abstention never adds semantic credit, and write-authority "
                "profiles permit no high-confidence error on a critical claim"
            ),
        },
    }


def evaluation_result(
    *,
    state: EvaluationState,
    aggregate: AggregateResult,
    qualification_profile_id: str,
    grader: dict[str, Any],
    graded_at_utc: str,
) -> dict[str, Any]:
    return {
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
        "qualification_profile": qualification_profile_id,
        "authority_class": state.profile["authority_class"],
        "composite_understanding": aggregate.composite,
        "primary_axes": aggregate.primary_axes,
        "dimension_scores": aggregate.dimension_scores,
        "dimension_normalization": aggregate.dimension_normalization,
        "evidence_quality": aggregate.evidence_quality,
        "epistemic_accuracy": aggregate.dimension_scores["epistemic_accuracy"],
        "mental_model_consistency": aggregate.consistency_score,
        "mental_model_assertion_count": len(state.assertions),
        "mental_model_assertion_minimum": aggregate.mental_model_min_assertions,
        "cross_answer_findings": aggregate.consistency,
        "relationship_graph": aggregate.relationship_graph,
        "misconception_findings": state.misconception_findings,
        "grader_confidence": aggregate.grader_confidence,
        "exam_valid_percent": aggregate.valid_percent,
        "exam_coverage": aggregate.exam_coverage,
        "excluded_questions": state.excluded,
        "unsupported_assertions": state.unsupported_count,
        "contradicted_claims": state.contradicted_count,
        "fabricated_evidence": state.fabricated_evidence,
        "evidence_authority_floor_failures": state.evidence_authority_floor_failures,
        "critical_failures": state.critical_failures,
        "adjudication_required": state.adjudication_required,
        "confidence_calibration": _confidence_calibration(state),
        "critical_dimension_failures": aggregate.critical_dimension_failures,
        "gate_failures": aggregate.gate_failures,
        "qualification": aggregate.qualification,
        "qualified": aggregate.qualification in {"QUALIFIED", "HIGH_CONFIDENCE"},
        "grader": grader,
        "graded_at_utc": graded_at_utc,
        "questions": state.question_results,
    }

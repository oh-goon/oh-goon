"""Terminal qualification gates for deterministic Repository Understanding scoring.

The grading policy owns scoring and observations. This subordinate stage receives only
completed score facts and applies the explicit qualification profile. It has no grader,
evidence-discovery, execution, or certification side effects.
"""
from __future__ import annotations
from typing import Any


def _gate_failures(
    *, profile: dict[str, Any], composite: float, evidence_quality: float,
    grader_confidence: float, valid_percent: float, exam_coverage_percent: float,
    consistency_coverage_ok: bool, relationship_graph: dict[str, Any],
    critical_dimension_failures: dict[str, Any], critical_overconfidence: list[str],
    adjudication_required: list[dict[str, Any]], critical_failures: list[dict[str, Any]],
) -> list[str]:
    checks = (
        (composite < float(profile["overall_minimum"]), "overall_minimum"),
        (evidence_quality < float(profile["evidence_minimum"]), "evidence_minimum"),
        (grader_confidence < float(profile["grader_confidence_minimum"]), "grader_confidence_minimum"),
        (valid_percent < float(profile["exam_validity_minimum"]), "exam_validity_minimum"),
        (exam_coverage_percent < float(profile["exam_coverage_minimum"]), "exam_coverage_minimum"),
        (not consistency_coverage_ok, "mental_model_assertion_coverage"),
        (relationship_graph["sample_coverage"]["status"] != "ENOUGH", "relationship_graph_sample_coverage"),
        (bool(relationship_graph["forbidden_affirmed_edges"]), "forbidden_relationship"),
        (bool(critical_dimension_failures), "critical_dimension_minimum"),
        (bool(adjudication_required), "adjudication_required"),
    )
    failures = [name for failed, name in checks if failed]
    maximum = profile.get("critical_overconfidence_max")
    if isinstance(maximum, int) and len(critical_overconfidence) > maximum:
        failures.append("critical_overconfidence")
    for edge in relationship_graph["forbidden_affirmed_edges"]:
        critical_failures.append({"kind": "forbidden_relationship", "edge": edge})
    if critical_failures:
        failures.append("critical_failure")
    return failures


def _qualification(
    *, profile: dict[str, Any], gate_failures: list[str], composite: float,
    evidence_quality: float, consistency_score: float, grader_confidence: float,
    adjudication_required: list[dict[str, Any]], critical_failures: list[dict[str, Any]],
    unsupported_count: int, contradicted_count: int, fabricated_evidence: list[str],
) -> str:
    if not gate_failures:
        high = profile["high_confidence"]
        high_confidence = all((
            composite >= float(high["composite"]),
            evidence_quality >= float(high["evidence"]),
            consistency_score >= float(high["consistency"]),
            grader_confidence >= float(high["grader"]),
            unsupported_count == 0,
            contradicted_count == 0,
            not fabricated_evidence,
        ))
        return "HIGH_CONFIDENCE" if high_confidence else "QUALIFIED"
    if adjudication_required and not critical_failures:
        return "NEEDS_ADJUDICATION"
    fatal_kinds = {"critical_contradiction", "fabricated_evidence", "cross_answer_consistency"}
    has_fatal = any(item.get("kind") in fatal_kinds for item in critical_failures)
    return "LIMITED" if composite >= 70.0 and not has_fatal else "UNCERTIFIED"


def qualification_outcome(
    *, profile: dict[str, Any], composite: float, evidence_quality: float,
    consistency_score: float, grader_confidence: float, valid_percent: float,
    exam_coverage_percent: float, consistency_coverage_ok: bool,
    relationship_graph: dict[str, Any], critical_dimension_failures: dict[str, Any],
    critical_overconfidence: list[str], adjudication_required: list[dict[str, Any]],
    critical_failures: list[dict[str, Any]], unsupported_count: int,
    contradicted_count: int, fabricated_evidence: list[str],
) -> tuple[list[str], str]:
    """Return ordered gate failures and terminal qualification without changing schemas."""
    failures = _gate_failures(
        profile=profile, composite=composite, evidence_quality=evidence_quality,
        grader_confidence=grader_confidence, valid_percent=valid_percent,
        exam_coverage_percent=exam_coverage_percent,
        consistency_coverage_ok=consistency_coverage_ok, relationship_graph=relationship_graph,
        critical_dimension_failures=critical_dimension_failures,
        critical_overconfidence=critical_overconfidence,
        adjudication_required=adjudication_required, critical_failures=critical_failures,
    )
    return failures, _qualification(
        profile=profile, gate_failures=failures, composite=composite,
        evidence_quality=evidence_quality, consistency_score=consistency_score,
        grader_confidence=grader_confidence, adjudication_required=adjudication_required,
        critical_failures=critical_failures, unsupported_count=unsupported_count,
        contradicted_count=contradicted_count, fabricated_evidence=fabricated_evidence,
    )

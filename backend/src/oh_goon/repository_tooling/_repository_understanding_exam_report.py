from __future__ import annotations

from typing import Any


def human_report(result: dict[str, Any]) -> str:
    """Render the canonical concise human summary from a scored result artifact."""

    if result.get("artifact_type") != "repository-understanding-result":
        raise ValueError("human report requires a repository-understanding result artifact")
    dimensions = result.get("dimension_scores")
    if not isinstance(dimensions, dict):
        raise ValueError("result artifact is missing dimension_scores")
    coverage = result.get("exam_coverage")
    if not isinstance(coverage, dict):
        raise ValueError("result artifact is missing exam_coverage")
    lines = [
        "OH-GOON REPOSITORY UNDERSTANDING",
        "",
        "Candidate",
        f"  identity              {result.get('candidate') or 'unspecified'}",
        f"  mode                  {result.get('mode') or 'unspecified'}",
        "",
        "Repository",
        f"  identity              {result.get('repository_identity')}",
        f"  source version        {result.get('source_version')}",
        "",
        "Exam",
        f"  questions             {coverage.get('included_questions')}/{coverage.get('authored_questions')}",
        f"  coverage              {coverage.get('percent'):.1f}%",
        f"  grader confidence     {result.get('grader_confidence', 0.0):.1f}%",
        f"  authority profile     {result.get('qualification_profile') or 'unspecified'}",
        f"  blueprint             {(result.get('blueprint_audit') or {}).get('status', 'UNKNOWN')}",
        f"  question audit        {(result.get('question_audit_report') or {}).get('release_status', 'UNKNOWN')}",
        f"  empirical calibration {(result.get('question_audit_report') or {}).get('empirical_calibration_status', 'UNKNOWN')}",
        "",
        "Primary axes",
    ]
    primary = result.get("primary_axes") or {}
    primary_labels = (
        ("semantic_understanding", "Semantic understanding"),
        ("evidence_grounding", "Evidence grounding"),
        ("mental_model_consistency", "Mental consistency"),
        ("epistemic_calibration", "Epistemic calibration"),
    )
    for key, label in primary_labels:
        value = primary.get(key)
        if value is not None:
            lines.append(f"  {label:<22}{value:>6.1f}")
    lines.extend(["", "Dimensions"])
    labels = (
        ("structure", "Structure"),
        ("execution", "Execution"),
        ("ownership", "Ownership"),
        ("lifecycle", "Lifecycle"),
        ("contracts", "Contracts"),
        ("testing", "Testing"),
        ("change_impact", "Change impact"),
        ("evidence_quality", "Evidence quality"),
        ("epistemic_accuracy", "Epistemic accuracy"),
        ("mental_model_consistency", "Mental consistency"),
    )
    for key, label in labels:
        value = dimensions.get(key)
        if value is not None:
            lines.append(f"  {label:<22}{value:>6.1f}")
    candidate_epistemic = (result.get("confidence_calibration") or {}).get(
        "candidate_epistemic"
    ) or {}
    lines.extend(
        [
            "",
            f"Composite              {result.get('composite_understanding', 0.0):.1f}",
            "",
            f"Critical failures      {len(result.get('critical_failures', []))}",
            f"Needs adjudication     {len(result.get('adjudication_required', []))}",
            f"Misconceptions         {len(result.get('misconception_findings', []))}",
            f"Unsupported assertions {result.get('unsupported_assertions', 0)}",
            f"Fabricated evidence    {len(result.get('fabricated_evidence', []))}",
            f"Authority-floor misses {len(result.get('evidence_authority_floor_failures', []))}",
            f"Accepted abstentions   {len(candidate_epistemic.get('accepted_abstentions', []))}",
            f"Critical overconfidence {len(candidate_epistemic.get('critical_overconfidence', []))}",
            "",
            "QUALIFICATION",
            str(result.get("qualification", "UNCERTIFIED")),
        ]
    )
    return "\n".join(lines) + "\n"

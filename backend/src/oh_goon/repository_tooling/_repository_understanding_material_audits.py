"""Private structural audits for Repository Understanding material.

Loaded lazily by repository_understanding_material so the canonical material owner remains
singular while large pure audit calculations stay independently maintainable.
"""

from __future__ import annotations

import re
from typing import Any

from oh_goon.repository_tooling.repository_understanding_blueprint import question_blueprint
from oh_goon.repository_tooling.repository_understanding_graph import (
    EVIDENCE_ROLE_POLICY_VERSION,
    evidence_expectation_roles,
)
from oh_goon.repository_tooling.repository_understanding_material import (
    ANSWERABILITY_POLICY_VERSION,
    BROAD_MISCONCEPTIONS,
    CANDIDATE_GOLD_MARKER,
    INLINE_SOURCE_REFERENCE,
    MISCONCEPTION_COVERAGE_POLICY_VERSION,
    MISCONCEPTIONS,
    QUESTION_HEALTH_MAINTENANCE_POLICY_VERSION,
    ExamQuestion,
    _validate_evidence,
)


def _severity_encoded_misconception_ids(misconceptions: tuple[str, ...]) -> list[str]:
    return [
        name
        for name in misconceptions
        if name.startswith(("critical_", "normal_")) or name.endswith(("_critical", "_normal"))
    ]


def _misconception_coverage_facts(questions: tuple[ExamQuestion, ...]) -> dict[str, Any]:
    ownership = tuple(
        question for question in questions if question.blueprint_dimension == "ownership"
    )
    critical = tuple(question for question in questions if question.severity == "critical")
    counts = {name: 0 for name in MISCONCEPTIONS}
    contradiction_count = 0
    contradiction_question_count = 0
    for question in questions:
        if question.contradictions:
            contradiction_question_count += 1
        for contradiction in question.contradictions:
            contradiction_count += 1
            counts[contradiction.misconception_id] += 1
    return {
        "ownership": ownership,
        "critical": critical,
        "ownership_gaps": [question.id for question in ownership if not question.contradictions],
        "critical_contradiction_gaps": [
            question.id for question in critical if not question.contradictions
        ],
        "critical_severity_gaps": [
            question.id
            for question in critical
            if question.contradictions
            and not any(item.severity == "critical" for item in question.contradictions)
        ],
        "critical_claim_gaps": [
            question.id
            for question in critical
            if not any(claim.critical for claim in question.expected_claims)
        ],
        "counts": counts,
        "contradiction_count": contradiction_count,
        "contradiction_question_count": contradiction_question_count,
        "taxonomy_gaps": [name for name in MISCONCEPTIONS if counts[name] == 0],
        "severity_encoded_ids": _severity_encoded_misconception_ids(MISCONCEPTIONS),
    }


def _misconception_coverage_audit(questions: tuple[ExamQuestion, ...]) -> dict[str, Any]:
    facts = _misconception_coverage_facts(questions)
    ownership, critical = facts["ownership"], facts["critical"]
    ownership_gaps = facts["ownership_gaps"]
    critical_contradiction_gaps = facts["critical_contradiction_gaps"]
    critical_severity_gaps = facts["critical_severity_gaps"]
    critical_claim_gaps = facts["critical_claim_gaps"]
    taxonomy_gaps, severity_encoded_ids = facts["taxonomy_gaps"], facts["severity_encoded_ids"]
    misconception_counts = facts["counts"]
    broad_counts = {name: misconception_counts[name] for name in BROAD_MISCONCEPTIONS}
    broad_count = sum(broad_counts.values())
    ready = not any(
        (
            ownership_gaps,
            critical_contradiction_gaps,
            critical_severity_gaps,
            critical_claim_gaps,
            taxonomy_gaps,
            severity_encoded_ids,
        )
    )
    return {
        "policy_version": MISCONCEPTION_COVERAGE_POLICY_VERSION,
        "question_count": len(questions),
        "contradiction_question_count": facts["contradiction_question_count"],
        "contradiction_count": facts["contradiction_count"],
        "misconception_counts": misconception_counts,
        "broad_misconception_counts": broad_counts,
        "broad_contradiction_count": broad_count,
        "specific_contradiction_count": facts["contradiction_count"] - broad_count,
        "covered_misconception_count": len(MISCONCEPTIONS) - len(taxonomy_gaps),
        "misconception_count": len(MISCONCEPTIONS),
        "taxonomy_gap_count": len(taxonomy_gaps),
        "taxonomy_gaps": taxonomy_gaps,
        "severity_encoded_id_count": len(severity_encoded_ids),
        "severity_encoded_ids": severity_encoded_ids,
        "ownership_question_count": len(ownership),
        "ownership_with_contradiction_count": len(ownership) - len(ownership_gaps),
        "ownership_gap_count": len(ownership_gaps),
        "ownership_gaps": ownership_gaps,
        "critical_question_count": len(critical),
        "critical_with_contradiction_count": len(critical) - len(critical_contradiction_gaps),
        "critical_contradiction_gap_count": len(critical_contradiction_gaps),
        "critical_contradiction_gaps": critical_contradiction_gaps,
        "critical_with_critical_severity_contradiction_count": len(critical)
        - len(critical_severity_gaps),
        "critical_severity_gap_count": len(critical_severity_gaps),
        "critical_severity_gaps": critical_severity_gaps,
        "critical_with_critical_claim_count": len(critical) - len(critical_claim_gaps),
        "critical_claim_gap_count": len(critical_claim_gaps),
        "critical_claim_gaps": critical_claim_gaps,
        "status": "READY" if ready else "READY_WITH_COVERAGE_GAPS",
        "rule": (
            "every ownership-blueprint question must grade at least one explicit wrong-owner or authority misconception; "
            "every critical question must grade at least one explicit contradiction, at least one contradiction whose "
            "severity is critical, and at least one critical expected claim; every canonical misconception class must have "
            "at least one concrete bank example; taxonomy entries cannot exist as untested labels or encode severity, "
            "because severity is a separate contradiction field; broad misconception classes remain valid when no narrower "
            "class dominates, and broad/specific counts are diagnostic only rather than a balancing quota or release threshold"
        ),
    }


def _assert_misconception_coverage_release_ready(
    questions: tuple[ExamQuestion, ...],
) -> dict[str, Any]:
    audit = _misconception_coverage_audit(questions)
    if (
        audit["ownership_gap_count"]
        or audit["critical_contradiction_gap_count"]
        or audit["critical_severity_gap_count"]
        or audit["critical_claim_gap_count"]
        or audit["taxonomy_gap_count"]
        or audit["severity_encoded_id_count"]
    ):
        gaps = [
            *audit["ownership_gaps"],
            *audit["critical_contradiction_gaps"],
            *audit["critical_severity_gaps"],
            *audit["critical_claim_gaps"],
            *audit["taxonomy_gaps"],
            *audit["severity_encoded_ids"],
        ]
        raise ValueError(
            "Repository Understanding misconception coverage is not release-ready: "
            + ", ".join(gaps)
        )
    return audit


def _evidence_authority_audit(questions: tuple[ExamQuestion, ...]) -> dict[str, Any]:
    gaps: list[dict[str, Any]] = []
    for question in questions:
        blueprint = question_blueprint(question)
        roles = evidence_expectation_roles(
            question.evidence_expectations,
            blueprint["minimum_evidence_authority"],
        )
        if roles["declared_minimum_met"]:
            continue
        gaps.append(
            {
                "question_id": question.id,
                "severity": question.severity,
                "dimension": question.dimension,
                "concept_family": blueprint["concept_family"],
                "declared_minimum_authority": roles["minimum_authority"],
                "canonical_minimum_authority": roles["canonical_minimum_authority"],
            }
        )
    return {
        "policy_version": EVIDENCE_ROLE_POLICY_VERSION,
        "question_count": len(questions),
        "declared_minimum_met_questions": len(questions) - len(gaps),
        "reference_authority_gap_count": len(gaps),
        "critical_reference_authority_gap_count": sum(
            item["severity"] == "critical" for item in gaps
        ),
        "status": "READY" if not gaps else "READY_WITH_REFERENCE_GAPS",
        "reference_authority_gaps": gaps,
        "candidate_rule": (
            "candidate evidence is compared with the strongest canonical SECRET evidence authority; "
            "the candidate is never held to a stronger path-authority floor than the current reference bank"
        ),
    }


def _external_answerability_audit(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> dict[str, Any]:
    """Prove shadow questions have candidate-visible repository evidence before calibration.

    This is deliberately structural. It proves the PUBLIC prompt is blind, every SECRET reference
    resolves to non-SECRET repository bytes, and the declared evidence reaches the blueprint's
    minimum authority. It does not pretend that machine checks prove semantic clarity or human
    answerability; those remain empirical promotion evidence.
    """
    by_id = {question.id: question for question in questions}
    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for lifecycle_row in lifecycle["questions"]:
        if lifecycle_row["qualification_bank_member"]:
            continue
        qid = lifecycle_row["id"]
        question = by_id[qid]
        refs = list(question.evidence_expectations)
        blind_prompt = (
            bool(question.prompt.strip())
            and CANDIDATE_GOLD_MARKER not in question.prompt
            and INLINE_SOURCE_REFERENCE.search(question.prompt) is None
        )
        external_refs = True
        for reference in refs:
            try:
                _validate_evidence(reference)
            except ValueError:
                external_refs = False
                break
        blueprint = question_blueprint(question)
        roles = evidence_expectation_roles(refs, blueprint["minimum_evidence_authority"])
        minimum_met = bool(roles["declared_minimum_met"])
        eligible_state = lifecycle_row["state"] in {"SHADOW", "CALIBRATING"}
        status = (
            "STATICALLY_ANSWERABLE"
            if blind_prompt and refs and external_refs and minimum_met and eligible_state
            else "BLOCKED"
        )
        row = {
            "id": qid,
            "lifecycle_state": lifecycle_row["state"],
            "blind_public_prompt": blind_prompt,
            "repository_evidence_reference_count": len(refs),
            "all_references_candidate_visible": external_refs,
            "declared_minimum_authority_met": minimum_met,
            "status": status,
        }
        rows.append(row)
        if status != "STATICALLY_ANSWERABLE":
            gaps.append(row)
    return {
        "policy_version": ANSWERABILITY_POLICY_VERSION,
        "scope": "PRE_PROMOTION_SHADOW_POOL",
        "question_count": len(rows),
        "statically_answerable_count": len(rows) - len(gaps),
        "gap_count": len(gaps),
        "status": "READY" if not gaps else "BLOCKED",
        "questions": rows,
        "gaps": gaps,
        "rule": (
            "a pre-promotion question must expose a blind PUBLIC prompt and be answerable from repository-contained EVIDENCE outside SECRET grader material at its declared minimum authority; "
            "structural answerability does not auto-promote and does not substitute for human review or empirical calibration"
        ),
    }


def _assert_external_answerability_ready(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> dict[str, Any]:
    audit = _external_answerability_audit(questions, lifecycle)
    if audit["gap_count"]:
        raise ValueError(
            "Repository Understanding shadow answerability is not release-ready: "
            + ", ".join(row["id"] for row in audit["gaps"])
        )
    return audit


def _duplicate_prompt_peers(questions: tuple[ExamQuestion, ...]) -> dict[str, set[str]]:
    token_sets = {
        question.id: frozenset(re.findall(r"[a-z0-9_]+", question.prompt.lower()))
        for question in questions
    }
    prompt_norm = {qid: " ".join(sorted(tokens)) for qid, tokens in token_sets.items()}
    peers: dict[str, set[str]] = {question.id: set() for question in questions}
    for index, question in enumerate(questions):
        left = token_sets[question.id]
        for other in questions[index + 1 :]:
            right = token_sets[other.id]
            union = left | right
            similarity = (len(left & right) / len(union)) if union else 1.0
            duplicate_like = prompt_norm[question.id] == prompt_norm[other.id] or (
                min(len(left), len(right)) >= 12 and similarity >= 0.85
            )
            if duplicate_like:
                peers[question.id].add(other.id)
                peers[other.id].add(question.id)
    return peers


def _question_health_row(
    question: ExamQuestion,
    *,
    lifecycle_row: dict[str, Any],
    audit_row: dict[str, Any],
    answerability_row: dict[str, Any],
    duplicate_ids: list[str],
) -> dict[str, Any]:
    signals: list[str] = []
    if audit_row["health_status"] != "VALID":
        signals.append(f"DECLARED_HEALTH_{audit_row['health_status']}")
    if answerability_row["status"] != "STATICALLY_ANSWERABLE":
        signals.append("STRUCTURAL_ANSWERABILITY_GAP")
    if duplicate_ids:
        signals.append("POTENTIAL_DUPLICATE")
    return {
        "id": question.id,
        "lifecycle_state": lifecycle_row["state"],
        "declared_health_status": audit_row["health_status"],
        "answerability_status": answerability_row["status"],
        "duplicate_candidate_ids": duplicate_ids,
        "structural_signals": signals,
        "structural_status": "READY" if not signals else "BLOCKED",
        "semantic_health_review": "REQUIRED",
        "empirical_discrimination_review": "REQUIRED",
    }


def _question_health_maintenance_audit(
    questions: tuple[ExamQuestion, ...],
    lifecycle: dict[str, Any],
    audit: dict[str, Any],
    answerability: dict[str, Any],
) -> dict[str, Any]:
    lifecycle_by_id = {row["id"]: row for row in lifecycle["questions"]}
    audit_by_id = {row["id"]: row for row in audit["questions"]}
    answerability_by_id = {row["id"]: row for row in answerability["questions"]}
    duplicate_peers = _duplicate_prompt_peers(questions)
    rows: list[dict[str, Any]] = []
    for question in questions:
        lifecycle_row = lifecycle_by_id[question.id]
        if lifecycle_row["qualification_bank_member"]:
            continue
        rows.append(
            _question_health_row(
                question,
                lifecycle_row=lifecycle_row,
                audit_row=audit_by_id[question.id],
                answerability_row=answerability_by_id[question.id],
                duplicate_ids=sorted(duplicate_peers[question.id]),
            )
        )
    blockers = [row for row in rows if row["structural_status"] != "READY"]
    return {
        "policy_version": QUESTION_HEALTH_MAINTENANCE_POLICY_VERSION,
        "scope": "PRE_PROMOTION_SHADOW_POOL",
        "question_count": len(rows),
        "structurally_ready_count": len(rows) - len(blockers),
        "structural_blocker_count": len(blockers),
        "status": "READY" if not blockers else "BLOCKED",
        "questions": rows,
        "blockers": blockers,
        "health_state_authority": "SECRET_QUESTION_AUDIT",
        "automatic_health_mutation": False,
        "automatic_lifecycle_transition": False,
        "semantic_states_requiring_independent_review": [
            "AMBIGUOUS",
            "MULTIPLE_VALID_ANSWERS",
            "TRIVIALLY_LEAKED",
        ],
        "empirical_states_requiring_observations": ["NON_DISCRIMINATING"],
        "rule": (
            "machine-provable structural defects or duplicate-like PUBLIC prompts block shadow promotion; "
            "the evaluator never invents semantic ambiguity, multiple-valid-answer, or empirical discrimination findings, "
            "never mutates SECRET question health, and never owns lifecycle transitions"
        ),
    }


def _assert_question_health_maintenance_ready(
    questions: tuple[ExamQuestion, ...],
    lifecycle: dict[str, Any],
    audit: dict[str, Any],
    answerability: dict[str, Any],
) -> dict[str, Any]:
    report = _question_health_maintenance_audit(questions, lifecycle, audit, answerability)
    if report["structural_blocker_count"]:
        raise ValueError(
            "Repository Understanding shadow question health is not promotion-ready: "
            + ", ".join(row["id"] for row in report["blockers"])
        )
    return report

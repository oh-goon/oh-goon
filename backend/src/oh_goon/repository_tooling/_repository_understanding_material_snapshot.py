"""Private snapshot/index calculations for Repository Understanding material.

Loaded lazily by repository_understanding_material; it does not own exam material,
question lifecycle, or certification authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from oh_goon.repository_tooling.release.package_slim import canonical_source_files
from oh_goon.repository_tooling.repository_understanding_blueprint import question_blueprint
from oh_goon.repository_tooling.repository_understanding_graph import evidence_expectation_roles
from oh_goon.repository_tooling.repository_understanding_material import (
    ANSWER_RELATIVE,
    DIMENSION_NORMALIZATION_POLICY_VERSION,
    DIMENSION_WEIGHTS,
    ROOT,
    ExamQuestion,
    _contained,
    _evidence_reference_identity,
)


def _question_snapshot(
    question: ExamQuestion,
    audit_row: dict[str, Any],
    lifecycle_row: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": question.id,
        "section": question.section,
        "severity": question.severity,
        "public": {
            "prompt": question.prompt,
            "type": question.type,
            "dimension": question.dimension,
            "difficulty": question.difficulty,
            "blueprint": question_blueprint(question),
            "response_expectations": question.response_expectations,
        },
        "evidence": {"repository_scope": list(question.evidence_scope)},
        "secret": {
            "question_audit": dict(audit_row),
            "question_lifecycle": dict(lifecycle_row),
            "expected_claims": [
                {"id": claim.id, "statement": claim.statement, "critical": claim.critical}
                for claim in question.expected_claims
            ],
            "contradictions": [
                {
                    "id": item.id,
                    "statement": item.statement,
                    "severity": item.severity,
                    "misconception_id": item.misconception_id,
                }
                for item in question.contradictions
            ],
            "evidence_expectations": list(question.evidence_expectations),
            "evidence_reference_identities": [
                _evidence_reference_identity(reference)
                for reference in question.evidence_expectations
            ],
            "evidence_authority_roles": evidence_expectation_roles(
                question.evidence_expectations,
                question_blueprint(question)["minimum_evidence_authority"],
            ),
        },
    }


def _calibration_question_binding(question: ExamQuestion) -> str:
    payload = {
        "id": question.id,
        "section": question.section,
        "severity": question.severity,
        "prompt": question.prompt,
        "type": question.type,
        "dimension": question.dimension,
        "difficulty": question.difficulty,
        "response_expectations": question.response_expectations,
        "evidence_scope": list(question.evidence_scope),
        "expected_claims": [
            {"id": claim.id, "statement": claim.statement, "critical": claim.critical}
            for claim in question.expected_claims
        ],
        "contradictions": [
            {
                "id": item.id,
                "statement": item.statement,
                "severity": item.severity,
                "misconception_id": item.misconception_id,
            }
            for item in question.contradictions
        ],
        "evidence_expectations": list(question.evidence_expectations),
        "evidence_reference_identities": [
            _evidence_reference_identity(reference) for reference in question.evidence_expectations
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _repository_evidence_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    answer_root = (ROOT / ANSWER_RELATIVE).resolve()
    for path in canonical_source_files(ROOT):
        if _contained(path, answer_root):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        index[relative] = {
            "line_count": len(body.splitlines()),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }
    return index


def _qualification_bank_questions(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> tuple[ExamQuestion, ...]:
    membership = {row["id"]: row["qualification_bank_member"] for row in lifecycle["questions"]}
    return tuple(question for question in questions if membership.get(question.id) is True)


def _dimension_normalization_bank_audit(
    questions: tuple[ExamQuestion, ...], lifecycle: dict[str, Any]
) -> dict[str, Any]:
    membership = {row["id"]: row["qualification_bank_member"] for row in lifecycle["questions"]}

    def pool(items: tuple[ExamQuestion, ...]) -> dict[str, Any]:
        dimensions: dict[str, dict[str, int]] = {}
        for question in items:
            row = dimensions.setdefault(question.dimension, {"questions": 0, "expected_claims": 0})
            row["questions"] += 1
            row["expected_claims"] += len(question.expected_claims)
        return {
            "question_count": len(items),
            "semantic_dimensions": dict(sorted(dimensions.items())),
        }

    qualification = tuple(question for question in questions if membership.get(question.id) is True)
    shadow = tuple(question for question in questions if membership.get(question.id) is not True)
    return {
        "policy_version": DIMENSION_NORMALIZATION_POLICY_VERSION,
        "status": "NORMALIZED",
        "semantic_method": "EQUAL_QUESTION_WEIGHT_WITHIN_DIMENSION",
        "cross_dimension_method": "EXPLICIT_WEIGHTED_HARMONIC",
        "dimension_weights": DIMENSION_WEIGHTS,
        "qualification_pool": pool(qualification),
        "shadow_pool": pool(shadow),
        "rule": (
            "question and expected-claim counts are observable composition facts, not implicit scoring weights; "
            "each included question contributes equally inside its semantic dimension and only explicit dimension weights control cross-dimension influence"
        ),
    }

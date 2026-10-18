"""Deterministic Repository Understanding exam blueprint metadata and release gate."""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

BLUEPRINT_POLICY_VERSION = "repository-understanding-blueprint.v2"
EVIDENCE_AUTHORITY_POLICY_VERSION = "repository-evidence-authority.v1"

EVIDENCE_AUTHORITY_LEVELS = {
    "A0": "runtime/generated fact",
    "A1": "implementation owner",
    "A2": "executable invariant/test",
    "A3": "schema/contract",
    "A4": "architecture documentation",
    "A5": "README/operational documentation",
    "A6": "example/comment",
}

CONCEPT_FAMILIES = {
    "repository-ownership": "C001-repository-ownership",
    "plan-goon-compilation": "C002-plan-goon-compilation",
    "execution-safety": "C003-execution-safety",
    "current-guard-patterns": "C004-current-guard-patterns",
    "scenarios": "C005-change-impact-scenarios",
    "run-identity-lifecycle": "C006-run-identity-lifecycle",
    "strict-isolation-tool-authority": "C007-isolation-tool-authority",
    "frontend-authority-concurrency": "C008-frontend-authority-concurrency",
    "evidence-observability": "C009-evidence-observability",
    "cross-repository-security": "C010-cross-repository-security",
    "startup-certification": "C011-startup-certification",
}

# These ranges are bank-composition authority, not scoring weights. Blueprint dimensions are
# intentionally separate from the question's scoring dimension so benchmark composition can evolve
# without silently changing certificate scoring semantics.
DIMENSION_TARGET_RANGES: dict[str, tuple[float, float]] = {
    "ownership": (15.0, 20.0),
    "execution": (20.0, 25.0),
    "lifecycle": (10.0, 15.0),
    "contracts": (10.0, 15.0),
    "change_impact": (10.0, 15.0),
    "testing": (8.0, 12.0),
    "structure": (8.0, 12.0),
    "security_isolation": (8.0, 12.0),
    "evidence_provenance": (8.0, 12.0),
}

QUESTION_FORMS = (
    "FACT",
    "RELATIONSHIP",
    "EXPLANATION",
    "COUNTERFACTUAL",
    "FALSE_PREMISE",
    "CHANGE_IMPACT",
    "DIAGNOSIS",
    "BOUNDARY",
    "SEQUENCE",
    "EVIDENCE_SELECTION",
)
QUESTION_FORM_MINIMUM = 5
CRITICAL_SHARE_RANGE = (15.0, 30.0)


def concept_family(section: str) -> str:
    try:
        return CONCEPT_FAMILIES[section]
    except KeyError as exc:
        raise ValueError(
            f"repository-understanding section has no concept family: {section}"
        ) from exc


def criticality_class(severity: str) -> str:
    if severity == "critical":
        return "CRITICAL"
    if severity == "normal":
        return "STANDARD"
    raise ValueError(f"unknown repository-understanding severity: {severity}")


def minimum_evidence_authority(severity: str) -> str:
    return "A2" if severity == "critical" else "A4"


def question_blueprint(question: Any) -> dict[str, str]:
    return {
        "concept_family": concept_family(question.section),
        "dimension": question.blueprint_dimension,
        "form": question.blueprint_form,
        "criticality": criticality_class(question.severity),
        "minimum_evidence_authority": minimum_evidence_authority(question.severity),
    }


def _percent(count: int, total: int) -> float:
    return round(100.0 * count / max(1, total), 1)


def audit_exam(questions: Iterable[Any]) -> dict[str, Any]:
    items = tuple(questions)
    total = len(items)
    dimensions = Counter(q.blueprint_dimension for q in items)
    forms = Counter(q.blueprint_form for q in items)
    concepts = Counter(concept_family(q.section) for q in items)
    criticality = Counter(criticality_class(q.severity) for q in items)

    dimension_audit: dict[str, dict[str, Any]] = {}
    violations: list[dict[str, Any]] = []
    for name, (minimum, maximum) in DIMENSION_TARGET_RANGES.items():
        share = _percent(dimensions.get(name, 0), total)
        status = (
            "IN_RANGE" if minimum <= share <= maximum else "UNDER" if share < minimum else "OVER"
        )
        dimension_audit[name] = {
            "questions": dimensions.get(name, 0),
            "share_percent": share,
            "target_minimum_percent": minimum,
            "target_maximum_percent": maximum,
            "status": status,
        }
        if status != "IN_RANGE":
            violations.append({"kind": "dimension_balance", "dimension": name, "status": status})

    form_audit: dict[str, dict[str, Any]] = {}
    for form in QUESTION_FORMS:
        count = forms.get(form, 0)
        status = "ENOUGH" if count >= QUESTION_FORM_MINIMUM else "UNDER"
        form_audit[form] = {
            "questions": count,
            "minimum_questions": QUESTION_FORM_MINIMUM,
            "status": status,
        }
        if status != "ENOUGH":
            violations.append({"kind": "question_form_coverage", "form": form, "status": status})

    critical_count = criticality.get("CRITICAL", 0)
    critical_share = _percent(critical_count, total)
    critical_minimum, critical_maximum = CRITICAL_SHARE_RANGE
    critical_status = (
        "IN_RANGE"
        if critical_minimum <= critical_share <= critical_maximum
        else "UNDER"
        if critical_share < critical_minimum
        else "OVER"
    )
    if critical_status != "IN_RANGE":
        violations.append({"kind": "criticality_balance", "status": critical_status})

    return {
        "policy_version": BLUEPRINT_POLICY_VERSION,
        "enforcement": "HARD_BANK_RELEASE_GATE",
        "status": "BALANCED" if not violations else "REJECTED",
        "question_count": total,
        "dimensions": dimension_audit,
        "question_forms": form_audit,
        "concept_families": dict(sorted(concepts.items())),
        "criticality": {
            **dict(sorted(criticality.items())),
            "critical_share_percent": critical_share,
            "target_minimum_percent": critical_minimum,
            "target_maximum_percent": critical_maximum,
            "status": critical_status,
        },
        "violations": violations,
        "evidence_authority_policy_version": EVIDENCE_AUTHORITY_POLICY_VERSION,
        "evidence_authority_levels": EVIDENCE_AUTHORITY_LEVELS,
    }


def assert_exam_release_ready(questions: Iterable[Any]) -> dict[str, Any]:
    audit = audit_exam(questions)
    if audit["status"] != "BALANCED":
        raise ValueError(
            f"repository-understanding bank fails blueprint release gate: {audit['violations']}"
        )
    return audit

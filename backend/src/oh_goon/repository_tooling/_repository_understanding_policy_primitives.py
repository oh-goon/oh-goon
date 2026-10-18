"""Pure repository-understanding policy vocabulary and scoring primitives.

This private module owns deterministic constants and helper mechanics only.
The public grading authority remains repository_understanding_policy.evaluate.
"""

from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import Any


CLAIM_STATES = (
    "SUPPORTED",
    "PARTIAL",
    "MISSING",
    "UNSUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
)
QUESTION_STATES = (
    "QUESTION_VALID",
    "QUESTION_AMBIGUOUS",
    "QUESTION_INVALID",
    "INSUFFICIENT_GROUND_TRUTH",
)
REFERENCE_STATES = (
    "REFERENCE_SUPPORTED",
    "REFERENCE_AMBIGUOUS",
    "REFERENCE_SUSPECT",
    "REFERENCE_CONTRADICTED",
)
EVIDENCE_RELEVANCE_STATES = ("STRONG", "PARTIAL", "NONE")
EVIDENCE_SUPPORT_STATES = ("STRONG", "PARTIAL", "NONE")
EVIDENCE_AUTHORITY_STATES = ("AUTHORITATIVE", "APPROPRIATE", "WEAK", "NONE")
ASSERTION_RELATIONS = (
    "owns",
    "calls",
    "observes",
    "depends_on",
    "constrains",
    "produces",
    "consumes",
    "validates",
    "proves",
    "must_precede",
    "must_not_own",
)
ASSERTION_POLARITIES = ("AFFIRMED", "DENIED")
CANDIDATE_CONFIDENCE_STATES = ("HIGH", "MEDIUM", "LOW")
CANDIDATE_EPISTEMIC_STATES = ("ASSERTED", "ABSTAINED")
CANDIDATE_CONFIDENCE_PROBABILITY = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}

SCORING_POLICY_VERSION = "repository-understanding-score.v9"
DIMENSION_NORMALIZATION_POLICY_VERSION = "repository-understanding-dimension-normalization.v1"
QUALIFICATION_POLICY_VERSION = "authority-risk-profiles.v2"
DEFAULT_QUALIFICATION_PROFILE = "change-author.v1"

CLAIM_VALUES = {
    "SUPPORTED": 1.0,
    "PARTIAL": 0.5,
    "MISSING": 0.0,
    "INSUFFICIENT_EVIDENCE": 0.0,
    "UNSUPPORTED": -0.25,
    "CONTRADICTED": -1.0,
}
EVIDENCE_RELEVANCE_VALUES = {"STRONG": 1.0, "PARTIAL": 0.5, "NONE": 0.0}
EVIDENCE_SUPPORT_VALUES = {"STRONG": 1.0, "PARTIAL": 0.5, "NONE": 0.0}
EVIDENCE_AUTHORITY_VALUES = {
    "AUTHORITATIVE": 1.0,
    "APPROPRIATE": 0.75,
    "WEAK": 0.25,
    "NONE": 0.0,
}

DIMENSION_WEIGHTS = {
    "structure": 1.0,
    "execution": 1.5,
    "ownership": 1.5,
    "lifecycle": 1.4,
    "contracts": 1.0,
    "testing": 1.0,
    "change_impact": 1.5,
    "evidence_quality": 1.0,
    "epistemic_accuracy": 1.5,
    "mental_model_consistency": 1.5,
}

QUALIFICATION_PROFILES: dict[str, dict[str, Any]] = {
    "read-only.v1": {
        "authority_class": "read-only",
        "overall_minimum": 80.0,
        "evidence_minimum": 75.0,
        "grader_confidence_minimum": 75.0,
        "exam_validity_minimum": 90.0,
        "exam_coverage_minimum": 90.0,
        "critical_claim_minimum": 0.5,
        "critical_evidence_minimum": 50.0,
        "critical_judgment_confidence_minimum": 0.75,
        "critical_overconfidence_max": None,
        "critical_dimension_minimums": {
            "ownership": 85.0,
            "execution": 80.0,
            "lifecycle": 80.0,
            "change_impact": 70.0,
            "epistemic_accuracy": 85.0,
            "mental_model_consistency": 85.0,
        },
        "mental_model_min_assertions": 8,
        "high_confidence": {
            "composite": 92.0,
            "evidence": 88.0,
            "consistency": 93.0,
            "grader": 88.0,
        },
    },
    "change-author.v1": {
        "authority_class": "change-author",
        "overall_minimum": 85.0,
        "evidence_minimum": 80.0,
        "grader_confidence_minimum": 80.0,
        "exam_validity_minimum": 90.0,
        "exam_coverage_minimum": 90.0,
        "critical_claim_minimum": 0.5,
        "critical_evidence_minimum": 50.0,
        "critical_judgment_confidence_minimum": 0.80,
        "critical_overconfidence_max": 0,
        "critical_dimension_minimums": {
            "ownership": 90.0,
            "execution": 85.0,
            "lifecycle": 85.0,
            "change_impact": 80.0,
            "epistemic_accuracy": 90.0,
            "mental_model_consistency": 90.0,
        },
        "mental_model_min_assertions": 10,
        "high_confidence": {
            "composite": 93.0,
            "evidence": 90.0,
            "consistency": 95.0,
            "grader": 90.0,
        },
    },
    "release-author.v1": {
        "authority_class": "release-author",
        "overall_minimum": 90.0,
        "evidence_minimum": 90.0,
        "grader_confidence_minimum": 85.0,
        "exam_validity_minimum": 95.0,
        "exam_coverage_minimum": 95.0,
        "critical_claim_minimum": 1.0,
        "critical_evidence_minimum": 75.0,
        "critical_judgment_confidence_minimum": 0.85,
        "critical_overconfidence_max": 0,
        "critical_dimension_minimums": {
            "ownership": 95.0,
            "execution": 92.0,
            "lifecycle": 92.0,
            "change_impact": 90.0,
            "epistemic_accuracy": 95.0,
            "mental_model_consistency": 95.0,
        },
        "mental_model_min_assertions": 12,
        "high_confidence": {
            "composite": 96.0,
            "evidence": 95.0,
            "consistency": 98.0,
            "grader": 93.0,
        },
    },
}

SINGLE_OWNER_RELATIONS = {"owns"}


def _percentage(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _require_choice(value: Any, allowed: tuple[str, ...], label: str) -> str:
    if value not in allowed:
        raise ValueError(f"{label} must be one of {', '.join(allowed)}")
    return str(value)


def _confidence(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} confidence must be a number from 0 to 1")
    result = float(value)
    if not isfinite(result) or result < 0.0 or result > 1.0:
        raise ValueError(f"{label} confidence must be a number from 0 to 1")
    return result


def qualification_profile(profile_id: str) -> dict[str, Any]:
    profile = QUALIFICATION_PROFILES.get(profile_id)
    if profile is None:
        raise ValueError(
            "qualification profile must be one of " + ", ".join(sorted(QUALIFICATION_PROFILES))
        )
    return profile


def weighted_harmonic(scores: dict[str, float]) -> float:
    """Return a weighted harmonic aggregate that exposes weak dimensions."""

    if not scores:
        return 0.0
    weighted = [(DIMENSION_WEIGHTS.get(name, 1.0), value) for name, value in scores.items()]
    if any(value <= 0.0 for _weight, value in weighted):
        return 0.0
    numerator = sum(weight for weight, _value in weighted)
    denominator = sum(weight / value for weight, value in weighted)
    return _percentage(numerator / denominator)


def _validated_assertion(raw: Any, question_id: str, index: int) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ValueError(f"{question_id} assertion {index} must be an object")
    subject = raw.get("subject")
    object_ = raw.get("object")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError(f"{question_id} assertion {index} subject is required")
    if not isinstance(object_, str) or not object_.strip():
        raise ValueError(f"{question_id} assertion {index} object is required")
    relation = _require_choice(raw.get("relation"), ASSERTION_RELATIONS, f"{question_id} assertion relation")
    polarity = _require_choice(raw.get("polarity"), ASSERTION_POLARITIES, f"{question_id} assertion polarity")
    return {
        "question_id": question_id,
        "subject": subject.strip(),
        "relation": relation,
        "object": object_.strip(),
        "polarity": polarity,
    }


def consistency_findings(assertions: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Detect only conservative, mechanically defensible cross-answer contradictions."""

    findings: list[dict[str, Any]] = []
    exact: dict[tuple[str, str, str], dict[str, list[str]]] = defaultdict(
        lambda: defaultdict(list)
    )
    owner_by_object: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    precedence: dict[tuple[str, str], list[str]] = defaultdict(list)

    for assertion in assertions:
        key = (assertion["subject"], assertion["relation"], assertion["object"])
        exact[key][assertion["polarity"]].append(assertion["question_id"])
        if assertion["relation"] in SINGLE_OWNER_RELATIONS and assertion["polarity"] == "AFFIRMED":
            owner_by_object[assertion["object"]][assertion["subject"]].append(assertion["question_id"])
        if assertion["relation"] == "must_precede" and assertion["polarity"] == "AFFIRMED":
            precedence[(assertion["subject"], assertion["object"])].append(assertion["question_id"])

    for (subject, relation, object_), polarities in sorted(exact.items()):
        if polarities.get("AFFIRMED") and polarities.get("DENIED"):
            findings.append(
                {
                    "kind": "assertion_polarity_conflict",
                    "critical": relation in SINGLE_OWNER_RELATIONS,
                    "subject": subject,
                    "relation": relation,
                    "object": object_,
                    "question_ids": sorted(set(polarities["AFFIRMED"] + polarities["DENIED"])),
                }
            )

    for object_, owners in sorted(owner_by_object.items()):
        if len(owners) > 1:
            findings.append(
                {
                    "kind": "conflicting_owners",
                    "critical": True,
                    "object": object_,
                    "owners": sorted(owners),
                    "question_ids": sorted({qid for values in owners.values() for qid in values}),
                }
            )

    seen_pairs: set[tuple[str, str]] = set()
    for (before, after), question_ids in sorted(precedence.items()):
        reverse = (after, before)
        if reverse not in precedence or (before, after) in seen_pairs or reverse in seen_pairs:
            continue
        seen_pairs.update({(before, after), reverse})
        findings.append(
            {
                "kind": "precedence_cycle",
                "critical": False,
                "nodes": sorted({before, after}),
                "question_ids": sorted(set(question_ids + precedence[reverse])),
            }
        )
    return findings


def _evidence_score(existence: str, raw: dict[str, Any], label: str) -> float:
    relevance = _require_choice(raw.get("relevance"), EVIDENCE_RELEVANCE_STATES, f"{label} evidence relevance")
    support = _require_choice(raw.get("support"), EVIDENCE_SUPPORT_STATES, f"{label} evidence support")
    authority = _require_choice(raw.get("authority"), EVIDENCE_AUTHORITY_STATES, f"{label} evidence authority")
    if existence != "VALID":
        return 0.0
    return _percentage(
        100.0
        * (
            1.0
            + EVIDENCE_RELEVANCE_VALUES[relevance]
            + EVIDENCE_SUPPORT_VALUES[support]
            + EVIDENCE_AUTHORITY_VALUES[authority]
        )
        / 4.0
    )

def qualification_policy_manifest() -> dict[str, Any]:
    return {
        "scoring_policy_version": SCORING_POLICY_VERSION,
        "qualification_policy_version": QUALIFICATION_POLICY_VERSION,
        "default_profile": DEFAULT_QUALIFICATION_PROFILE,
        "profiles": QUALIFICATION_PROFILES,
        "claim_values": CLAIM_VALUES,
        "dimension_weights": DIMENSION_WEIGHTS,
        "dimension_normalization_policy_version": DIMENSION_NORMALIZATION_POLICY_VERSION,
        "dimension_normalization_rule": (
            "semantic dimensions use equal question weight within dimension; explicit DIMENSION_WEIGHTS own cross-dimension influence"
        ),
        "critical_failures_allowed": 0,
        "fabricated_evidence_allowed": 0,
        "candidate_confidence_states": list(CANDIDATE_CONFIDENCE_STATES),
        "candidate_epistemic_states": list(CANDIDATE_EPISTEMIC_STATES),
        "profile_selection_rule": (
            "select by agent authority/risk class; model identity and candidate self-description do not change thresholds"
        ),
    }


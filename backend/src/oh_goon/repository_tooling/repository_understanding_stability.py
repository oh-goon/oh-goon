"""Repeated-run Repository Understanding stability authority.

A single successful run is not stable qualification evidence.  This module combines exactly
three independently scored runs that are bound to the same repository, exam, policy,
qualification profile, and frozen candidate configuration.  Agreement metrics are reported,
but no uncalibrated numeric agreement threshold is invented: qualification requires 3/3
individually qualified runs and zero oscillation on critical concepts.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Any, Iterable

from oh_goon.repository_tooling.repository_understanding_policy import QUALIFICATION_PROFILES

STABILITY_POLICY_VERSION = "repository-understanding-stability.v1"
STABILITY_SCHEMA_VERSION = 1
STABILITY_RUNS_REQUIRED = 3
STABILITY_PASSES_REQUIRED = 3


def _canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _percentage(numerator: int, denominator: int) -> float:
    return 0.0 if denominator <= 0 else round(100.0 * numerator / denominator, 1)


def _load_result(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("artifact_type") != "repository-understanding-result":
        raise ValueError(f"stability input is not a repository-understanding result: {path}")
    identity = payload.get("certificate_identity")
    expected_identity = "sha256:" + _canonical_sha256(
        {
            key: value
            for key, value in payload.items()
            if key not in {"certificate_identity", "regrade_bundle", "regraded"}
        }
    )
    if identity != expected_identity:
        raise ValueError(f"stability input certificate identity does not match result bytes: {path}")
    run_identity = payload.get("candidate_run_identity")
    if not isinstance(run_identity, str) or not run_identity.strip():
        raise ValueError(f"stability input is missing candidate run identity: {path}")
    bundle = payload.get("regrade_bundle")
    if not isinstance(bundle, dict):
        raise ValueError(f"stability input is missing regrade bundle: {path}")
    if bundle.get("candidate_run_identity") != run_identity:
        raise ValueError(f"stability input candidate run identity is inconsistent: {path}")
    if bundle.get("candidate_configuration_identity") != payload.get("candidate_configuration_identity"):
        raise ValueError(f"stability input candidate configuration identity is inconsistent: {path}")
    return payload


def validated_result(path: Path) -> dict[str, Any]:
    """Return one self-identity/regrade-bundle validated scored result."""

    return _load_result(Path(path))


def _same_identity(results: list[dict[str, Any]], field: str) -> Any:
    values = [result.get(field) for result in results]
    if values[0] is None or any(value != values[0] for value in values[1:]):
        raise ValueError(f"stability runs must share exact {field}")
    return values[0]


def _claim_rows(result: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    questions = result.get("questions")
    if not isinstance(questions, list):
        raise ValueError("stability result is missing question results")
    for question in questions:
        if not isinstance(question, dict) or not question.get("included"):
            continue
        qid = question.get("id")
        concept = question.get("concept_family")
        claims = question.get("claims")
        if not isinstance(qid, str) or not isinstance(concept, str) or not isinstance(claims, list):
            raise ValueError("stability result question is missing canonical claim/concept data")
        for claim in claims:
            if not isinstance(claim, dict) or not isinstance(claim.get("claim_id"), str):
                raise ValueError("stability result contains invalid claim data")
            key = (qid, claim["claim_id"])
            if key in rows:
                raise ValueError(f"duplicate stability claim result: {qid}/{claim['claim_id']}")
            rows[key] = {
                "result": claim.get("result"),
                "critical": bool(claim.get("critical")),
                "question_critical": question.get("severity") == "critical",
                "concept_family": concept,
            }
    return rows


def stability_report(result_paths: Iterable[Path]) -> dict[str, Any]:
    """Combine exactly three independently scored results into stable qualification evidence."""

    paths = [Path(path) for path in result_paths]
    if len(paths) != STABILITY_RUNS_REQUIRED:
        raise ValueError(f"stability qualification requires exactly {STABILITY_RUNS_REQUIRED} runs")
    results = [_load_result(path) for path in paths]

    certificate_ids = [str(result["certificate_identity"]) for result in results]
    if len(set(certificate_ids)) != len(certificate_ids):
        raise ValueError("stability qualification requires distinct run certificate identities")
    run_ids = [str(result["candidate_run_identity"]) for result in results]
    if len(set(run_ids)) != len(run_ids):
        raise ValueError("stability qualification requires distinct candidate run identities")

    identity_fields = (
        "source_version",
        "repository_identity",
        "exam_snapshot_sha256",
        "qa_schema_version",
        "grader_schema_version",
        "rubric_version",
        "scoring_policy_version",
        "qualification_policy_version",
        "qualification_profile",
        "candidate_configuration_identity",
    )
    bound = {field: _same_identity(results, field) for field in identity_fields}
    profile_id = str(bound["qualification_profile"])
    if profile_id not in QUALIFICATION_PROFILES:
        raise ValueError("stability qualification profile is unknown to current policy")

    claim_maps = [_claim_rows(result) for result in results]
    claim_keys = list(claim_maps[0])
    if any(list(rows) != claim_keys for rows in claim_maps[1:]):
        raise ValueError("stability runs must contain the same included claims in canonical order")

    agreeing_claims = 0
    concept_signatures: dict[str, list[list[tuple[str, str, Any]]]] = {}
    critical_oscillations: list[dict[str, Any]] = []
    concepts = sorted({str(row["concept_family"]) for row in claim_maps[0].values()})
    critical_concepts = {
        str(row["concept_family"])
        for row in claim_maps[0].values()
        if row["critical"] or row["question_critical"]
    }
    for concept in concepts:
        concept_signatures[concept] = [[] for _ in results]

    for key in claim_keys:
        rows = [claim_map[key] for claim_map in claim_maps]
        states = [row["result"] for row in rows]
        if len(set(states)) == 1:
            agreeing_claims += 1
        concept = str(rows[0]["concept_family"])
        for index, state in enumerate(states):
            concept_signatures[concept][index].append((key[0], key[1], state))

    for concept in sorted(critical_concepts):
        signatures = concept_signatures[concept]
        if all(signature == signatures[0] for signature in signatures[1:]):
            continue
        changed_claims: list[dict[str, Any]] = []
        for rows in zip(*signatures, strict=True):
            states = [row[2] for row in rows]
            if len(set(states)) > 1:
                changed_claims.append(
                    {"question_id": rows[0][0], "claim_id": rows[0][1], "states": states}
                )
        critical_oscillations.append(
            {"concept_family": concept, "changed_claims": changed_claims}
        )

    agreeing_concepts = sum(
        1
        for signatures in concept_signatures.values()
        if all(signature == signatures[0] for signature in signatures[1:])
    )
    passes = sum(bool(result.get("qualified")) for result in results)
    composites = [float(result.get("composite_understanding", 0.0)) for result in results]

    profile = QUALIFICATION_PROFILES[profile_id]
    critical_dimensions = tuple(profile["critical_dimension_minimums"])
    worst: dict[str, Any] | None = None
    for run_index, result in enumerate(results, start=1):
        dimensions = result.get("dimension_scores")
        if not isinstance(dimensions, dict):
            raise ValueError("stability result is missing dimension_scores")
        for dimension in critical_dimensions:
            value = float(dimensions.get(dimension, 0.0))
            candidate = {
                "run": run_index,
                "dimension": dimension,
                "score": value,
                "minimum": float(profile["critical_dimension_minimums"][dimension]),
            }
            if worst is None or value < float(worst["score"]):
                worst = candidate

    stability_gate_failures: list[str] = []
    if passes != STABILITY_PASSES_REQUIRED:
        stability_gate_failures.append("passes_required")
    if critical_oscillations:
        stability_gate_failures.append("critical_concept_oscillation")
    stable = not stability_gate_failures

    base = {
        "artifact_type": "repository-understanding-stability-result",
        "schema_version": STABILITY_SCHEMA_VERSION,
        "stability_policy_version": STABILITY_POLICY_VERSION,
        "runs_required": STABILITY_RUNS_REQUIRED,
        "passes_required": STABILITY_PASSES_REQUIRED,
        **bound,
        "run_certificate_identities": certificate_ids,
        "candidate_run_identities": run_ids,
        "run_qualifications": [str(result.get("qualification")) for result in results],
        "passes": passes,
        "qualification_rate_percent": _percentage(passes, len(results)),
        "median_composite_understanding": round(float(statistics.median(composites)), 1),
        "worst_critical_dimension": worst,
        "claim_agreement_rate_percent": _percentage(agreeing_claims, len(claim_keys)),
        "concept_agreement_rate_percent": _percentage(agreeing_concepts, len(concepts)),
        "critical_concept_oscillations": critical_oscillations,
        "stability_gate_failures": stability_gate_failures,
        "stability": "STABLE" if stable else "UNSTABLE",
        "qualified": stable,
        "policy_note": (
            "agreement rates are diagnostic; no uncalibrated numeric agreement threshold is qualification authority"
        ),
    }
    return {**base, "stability_certificate_identity": "sha256:" + _canonical_sha256(base)}


def stability_policy_manifest() -> dict[str, Any]:
    return {
        "policy_version": STABILITY_POLICY_VERSION,
        "schema_version": STABILITY_SCHEMA_VERSION,
        "runs_required": STABILITY_RUNS_REQUIRED,
        "passes_required": STABILITY_PASSES_REQUIRED,
        "critical_concept_oscillation_allowed": False,
        "agreement_threshold": None,
        "rule": "3/3 qualified independently identified runs and no critical-concept oscillation",
    }

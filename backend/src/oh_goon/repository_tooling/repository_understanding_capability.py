"""Capability-scoped Repository Understanding certificate authority.

This module does not grant filesystem/process permissions. It turns already-authoritative
Repository Understanding evidence into one identity-bound capability certificate that an
execution/admission owner may verify. Capability is derived from the selected authority/risk
profile; candidates cannot request a weaker threshold for a stronger capability.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from oh_goon.repository_tooling.repository_understanding_integrity import (
    ADVERSARIAL_POLICY_VERSION,
    ADVERSARIAL_RESULT_SCHEMA_VERSION,
    BENCHMARK_CONTROL_REQUIREMENTS,
    BENCHMARK_INTEGRITY_POLICY_VERSION,
    BENCHMARK_INTEGRITY_RESULT_SCHEMA_VERSION,
)
from oh_goon.repository_tooling.repository_understanding_policy import QUALIFICATION_PROFILES
from oh_goon.repository_tooling.repository_understanding_stability import (
    STABILITY_PASSES_REQUIRED,
    STABILITY_POLICY_VERSION,
    STABILITY_RUNS_REQUIRED,
    stability_report,
)

CAPABILITY_POLICY_VERSION = "repository-understanding-capabilities.v1"
CAPABILITY_CERTIFICATE_SCHEMA_VERSION = 1
CAPABILITY_CHECK_SCHEMA_VERSION = 1

CAPABILITY_ACTIONS = (
    "planning",
    "code_review",
    "standard_editing",
    "critical_execution",
    "release_promotion",
)

# The profile is selected upstream from candidate content. This mapping is deliberately exact:
# a result scored under one profile cannot be relabeled as a different capability tier.
CAPABILITY_TIERS: dict[str, dict[str, Any]] = {
    "read-only.v1": {
        "tier": "PLAN",
        "authority_class": "read-only",
        "authorization": {
            "planning": True,
            "code_review": True,
            "standard_editing": False,
            "critical_execution": False,
            "release_promotion": False,
        },
    },
    "change-author.v1": {
        "tier": "STANDARD",
        "authority_class": "change-author",
        "authorization": {
            "planning": True,
            "code_review": True,
            "standard_editing": True,
            "critical_execution": False,
            "release_promotion": False,
        },
    },
    "release-author.v1": {
        "tier": "CRITICAL",
        "authority_class": "release-author",
        "authorization": {
            "planning": True,
            "code_review": True,
            "standard_editing": True,
            "critical_execution": True,
            "release_promotion": True,
        },
    },
}


def _canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _identity(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{label} must be a sha256 identity")
    try:
        int(value[7:], 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a sha256 identity") from exc
    return value


def _digest(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a 64-character sha256 digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{label} must be a 64-character sha256 digest") from exc
    return value


def _issued_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("issued_at_utc must be an ISO-8601 UTC timestamp")
    normalized = value.strip()
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("issued_at_utc must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("issued_at_utc must be UTC")
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return payload


def _assert_self_identity(
    payload: dict[str, Any], *, field: str, label: str, excluded: set[str] | None = None
) -> str:
    identity = _identity(payload.get(field), label=f"{label} {field}")
    omitted = {field} if excluded is None else set(excluded) | {field}
    expected = "sha256:" + _canonical_sha256(
        {key: value for key, value in payload.items() if key not in omitted}
    )
    if identity != expected:
        raise ValueError(f"{label} identity does not match artifact bytes")
    return identity


def _load_integrity(path: Path) -> dict[str, Any]:
    payload = _load_json(path, label="benchmark integrity result")
    if payload.get("artifact_type") != "repository-understanding-benchmark-integrity-result":
        raise ValueError(f"capability input is not a benchmark integrity result: {path}")
    if payload.get("schema_version") != BENCHMARK_INTEGRITY_RESULT_SCHEMA_VERSION:
        raise ValueError("benchmark integrity result schema mismatch")
    if payload.get("policy_version") != BENCHMARK_INTEGRITY_POLICY_VERSION:
        raise ValueError("benchmark integrity result policy mismatch")
    _assert_self_identity(
        payload, field="integrity_result_identity", label="benchmark integrity result"
    )
    if payload.get("required_controls") != BENCHMARK_CONTROL_REQUIREMENTS:
        raise ValueError("benchmark integrity result required controls drifted from current policy")
    observed = payload.get("observed_controls")
    if not isinstance(observed, dict):
        raise ValueError("benchmark integrity result observed_controls must be an object")
    failures = [
        key
        for key, required in BENCHMARK_CONTROL_REQUIREMENTS.items()
        if observed.get(key) != required
    ]
    if payload.get("control_failures") != failures:
        raise ValueError("benchmark integrity result control_failures are inconsistent")
    comparable = not failures
    if payload.get("comparable") is not comparable:
        raise ValueError("benchmark integrity result comparable flag is inconsistent")
    expected_status = "COMPARABLE" if comparable else "NOT_COMPARABLE"
    if payload.get("comparison_status") != expected_status:
        raise ValueError("benchmark integrity result comparison_status is inconsistent")
    return payload


def _load_adversarial(path: Path) -> dict[str, Any]:
    payload = _load_json(path, label="adversarial result")
    if payload.get("artifact_type") != "repository-understanding-adversarial-result":
        raise ValueError(f"capability input is not an adversarial result: {path}")
    if payload.get("schema_version") != ADVERSARIAL_RESULT_SCHEMA_VERSION:
        raise ValueError("adversarial result schema mismatch")
    if payload.get("adversarial_policy_version") != ADVERSARIAL_POLICY_VERSION:
        raise ValueError("adversarial result policy mismatch")
    if payload.get("benchmark_integrity_policy_version") != BENCHMARK_INTEGRITY_POLICY_VERSION:
        raise ValueError("adversarial result benchmark-integrity policy mismatch")
    _assert_self_identity(payload, field="adversarial_result_identity", label="adversarial result")
    failures = payload.get("critical_adversarial_failures")
    gates = payload.get("adversarial_gate_failures")
    if not isinstance(failures, list) or not isinstance(gates, list):
        raise ValueError("adversarial result failure fields must be lists")
    expected_gates = ["critical_adversarial_failure"] if failures else []
    if gates != expected_gates or payload.get("qualified") is not (not expected_gates):
        raise ValueError("adversarial result qualification fields are inconsistent")
    return payload


def _result_payloads(paths: list[Path]) -> list[dict[str, Any]]:
    # stability_report performs the authoritative result identity and cross-run validation first.
    stability_report(paths)
    return [_load_json(path, label="repository-understanding result") for path in paths]


def _concept_inventory(result: dict[str, Any]) -> tuple[list[str], list[str]]:
    concepts: set[str] = set()
    critical: set[str] = set()
    questions = result.get("questions")
    if not isinstance(questions, list):
        raise ValueError("repository-understanding result is missing question results")
    for question in questions:
        if not isinstance(question, dict) or not question.get("included"):
            continue
        concept = question.get("concept_family")
        if not isinstance(concept, str) or not concept:
            raise ValueError("repository-understanding result has invalid concept family")
        concepts.add(concept)
        claims = question.get("claims")
        if question.get("severity") == "critical" or (
            isinstance(claims, list)
            and any(bool(claim.get("critical")) for claim in claims if isinstance(claim, dict))
        ):
            critical.add(concept)
    return sorted(concepts), sorted(critical)


def capability_certificate(
    result_paths: Iterable[Path],
    *,
    integrity_paths: Iterable[Path],
    adversarial_paths: Iterable[Path],
    issued_at_utc: str | None = None,
) -> dict[str, Any]:
    """Issue an evidence-only capability certificate from stable, comparable, adversarial-clean runs."""

    result_paths = [Path(path) for path in result_paths]
    integrity_paths = [Path(path) for path in integrity_paths]
    adversarial_paths = [Path(path) for path in adversarial_paths]
    if len(result_paths) != STABILITY_RUNS_REQUIRED:
        raise ValueError(
            f"capability certification requires exactly {STABILITY_RUNS_REQUIRED} scored runs"
        )
    if len(integrity_paths) != STABILITY_RUNS_REQUIRED:
        raise ValueError(
            f"capability certification requires exactly {STABILITY_RUNS_REQUIRED} benchmark integrity results"
        )
    if len(adversarial_paths) != STABILITY_RUNS_REQUIRED:
        raise ValueError(
            f"capability certification requires exactly {STABILITY_RUNS_REQUIRED} adversarial results"
        )

    stability = stability_report(result_paths)
    results = _result_payloads(result_paths)
    integrity = [_load_integrity(path) for path in integrity_paths]
    adversarial = [_load_adversarial(path) for path in adversarial_paths]

    profile_id = stability.get("qualification_profile")
    tier_policy = CAPABILITY_TIERS.get(str(profile_id))
    if tier_policy is None or profile_id not in QUALIFICATION_PROFILES:
        raise ValueError("stability qualification profile has no capability mapping")
    if (
        QUALIFICATION_PROFILES[str(profile_id)].get("authority_class")
        != tier_policy["authority_class"]
    ):
        raise ValueError("capability profile authority class drifted from qualification policy")

    shared_fields = ("source_version", "repository_identity", "exam_snapshot_sha256")
    for field in shared_fields:
        expected = stability.get(field)
        if expected is None:
            raise ValueError(f"stability evidence is missing {field}")
        for row in integrity:
            if row.get(field) != expected:
                raise ValueError(
                    f"benchmark integrity result {field} does not match stability evidence"
                )
        for row in adversarial:
            if row.get(field) != expected:
                raise ValueError(f"adversarial result {field} does not match stability evidence")
    expected_configuration = stability.get("candidate_configuration_identity")
    if expected_configuration is None:
        raise ValueError("stability evidence is missing candidate_configuration_identity")
    for row in integrity:
        if row.get("candidate_configuration_identity") != expected_configuration:
            raise ValueError(
                "benchmark integrity result candidate_configuration_identity does not match stability evidence"
            )

    expected_run_ids = list(stability.get("candidate_run_identities") or [])
    if len(expected_run_ids) != STABILITY_RUNS_REQUIRED or len(set(expected_run_ids)) != len(
        expected_run_ids
    ):
        raise ValueError(
            "stability evidence does not contain three distinct candidate run identities"
        )
    integrity_by_run: dict[str, dict[str, Any]] = {}
    for row in integrity:
        run_id = row.get("candidate_run_identity")
        if not isinstance(run_id, str) or run_id in integrity_by_run:
            raise ValueError(
                "capability certification requires one unique integrity result per candidate run"
            )
        integrity_by_run[run_id] = row
    if set(integrity_by_run) != set(expected_run_ids):
        raise ValueError("benchmark integrity run identities do not exactly match stability runs")

    integrity_ids = [
        str(integrity_by_run[run]["integrity_result_identity"]) for run in expected_run_ids
    ]
    if len(set(integrity_ids)) != len(integrity_ids):
        raise ValueError(
            "capability certification requires distinct benchmark integrity result identities"
        )
    challenge_ids = [
        integrity_by_run[run].get("challenge_instance_identity") for run in expected_run_ids
    ]
    if any(
        not isinstance(value, str) or not value.startswith("sha256:") for value in challenge_ids
    ):
        raise ValueError(
            "capability certification requires one hidden challenge instance per qualifying run"
        )
    if len(set(challenge_ids)) != len(challenge_ids):
        raise ValueError(
            "capability certification requires distinct hidden challenge instances across runs"
        )

    adversarial_by_challenge: dict[str, dict[str, Any]] = {}
    for row in adversarial:
        challenge_id = row.get("challenge_instance_identity")
        if not isinstance(challenge_id, str) or challenge_id in adversarial_by_challenge:
            raise ValueError(
                "capability certification requires one unique adversarial result per challenge instance"
            )
        adversarial_by_challenge[challenge_id] = row
    if set(adversarial_by_challenge) != set(challenge_ids):
        raise ValueError(
            "adversarial challenge identities do not exactly match benchmark integrity runs"
        )

    benchmark_registry_ids = {
        row.get("benchmark_registry_identity") for row in integrity + adversarial
    }
    if len(benchmark_registry_ids) != 1 or None in benchmark_registry_ids:
        raise ValueError("capability certification requires one exact benchmark registry identity")
    benchmark_registry_identity = str(next(iter(benchmark_registry_ids)))

    gate_failures: list[str] = []
    if not stability.get("qualified") or stability.get("stability") != "STABLE":
        gate_failures.append("stable_qualification_required")
    if int(stability.get("passes", 0)) != STABILITY_PASSES_REQUIRED:
        gate_failures.append("three_qualified_runs_required")
    for run_id in expected_run_ids:
        row = integrity_by_run[run_id]
        if not row.get("comparable"):
            gate_failures.append(f"benchmark_not_comparable:{run_id}")
        challenge_id = str(row["challenge_instance_identity"])
        if not adversarial_by_challenge[challenge_id].get("qualified"):
            gate_failures.append(f"critical_adversarial_failure:{run_id}")

    authorized = not gate_failures
    authorization = (
        dict(tier_policy["authorization"])
        if authorized
        else {action: False for action in CAPABILITY_ACTIONS}
    )
    concepts, critical_concepts = _concept_inventory(results[0])
    issued = _issued_at(issued_at_utc)
    base = {
        "artifact_type": "repository-understanding-capability-certificate",
        "schema_version": CAPABILITY_CERTIFICATE_SCHEMA_VERSION,
        "capability_policy_version": CAPABILITY_POLICY_VERSION,
        "source_version": stability["source_version"],
        "repository_identity": stability["repository_identity"],
        "exam_snapshot_sha256": stability["exam_snapshot_sha256"],
        "qa_schema_version": stability["qa_schema_version"],
        "grader_schema_version": stability["grader_schema_version"],
        "rubric_version": stability["rubric_version"],
        "scoring_policy_version": stability["scoring_policy_version"],
        "qualification_policy_version": stability["qualification_policy_version"],
        "qualification_profile": profile_id,
        "authority_class": tier_policy["authority_class"],
        "target_tier": tier_policy["tier"],
        "certificate_status": "AUTHORIZED" if authorized else "DENIED",
        "granted_tier": tier_policy["tier"] if authorized else None,
        "authorization": authorization,
        "candidate_configuration_identity": stability["candidate_configuration_identity"],
        "stability_policy_version": STABILITY_POLICY_VERSION,
        "stability_certificate_identity": stability["stability_certificate_identity"],
        "run_certificate_identities": list(stability["run_certificate_identities"]),
        "candidate_run_identities": expected_run_ids,
        "runs_required": STABILITY_RUNS_REQUIRED,
        "passes_required": STABILITY_PASSES_REQUIRED,
        "passes": int(stability["passes"]),
        "qualification_rate_percent": stability["qualification_rate_percent"],
        "median_composite_understanding": stability["median_composite_understanding"],
        "worst_critical_dimension": stability["worst_critical_dimension"],
        "claim_agreement_rate_percent": stability["claim_agreement_rate_percent"],
        "concept_agreement_rate_percent": stability["concept_agreement_rate_percent"],
        "critical_concept_oscillations": stability["critical_concept_oscillations"],
        "stability_gate_failures": stability["stability_gate_failures"],
        "benchmark_integrity_policy_version": BENCHMARK_INTEGRITY_POLICY_VERSION,
        "benchmark_registry_identity": benchmark_registry_identity,
        "benchmark_integrity_result_identities": integrity_ids,
        "challenge_instance_identities": challenge_ids,
        "comparable_runs": sum(
            bool(integrity_by_run[run]["comparable"]) for run in expected_run_ids
        ),
        "adversarial_policy_version": ADVERSARIAL_POLICY_VERSION,
        "adversarial_result_identities": [
            adversarial_by_challenge[str(challenge)]["adversarial_result_identity"]
            for challenge in challenge_ids
        ],
        "adversarial_qualified_runs": sum(
            bool(adversarial_by_challenge[str(challenge)]["qualified"])
            for challenge in challenge_ids
        ),
        "certified_concepts": concepts,
        "critical_concepts": critical_concepts,
        "gate_failures": gate_failures,
        "issued_at_utc": issued,
        "freshness": {
            "time_expiry": None,
            "time_expiry_policy": "NOT_CALIBRATED_V1",
            "repository_identity_must_match": True,
            "candidate_configuration_identity_must_match": True,
            "note": "v1 is exact-source and exact-candidate-configuration bound; no arbitrary time TTL is invented",
        },
        "scope_model": "repository-wide-action-scopes.v1",
        "baseline_without_certificate": "READ_ONLY",
        "enforcement_status": "EVIDENCE_ONLY",
        "enforcement_note": (
            "this certificate does not mutate permissions; execution/admission authority must independently verify certificate identity, repository identity, candidate configuration identity, and requested capability"
        ),
    }
    return {**base, "capability_certificate_identity": "sha256:" + _canonical_sha256(base)}


def validate_capability_certificate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("artifact_type") != "repository-understanding-capability-certificate":
        raise ValueError("capability certificate artifact_type mismatch")
    if payload.get("schema_version") != CAPABILITY_CERTIFICATE_SCHEMA_VERSION:
        raise ValueError("capability certificate schema mismatch")
    if payload.get("capability_policy_version") != CAPABILITY_POLICY_VERSION:
        raise ValueError("capability certificate policy mismatch")
    _assert_self_identity(
        payload, field="capability_certificate_identity", label="capability certificate"
    )
    profile = payload.get("qualification_profile")
    policy = CAPABILITY_TIERS.get(str(profile))
    if policy is None:
        raise ValueError("capability certificate qualification profile has no current mapping")
    if (
        payload.get("target_tier") != policy["tier"]
        or payload.get("authority_class") != policy["authority_class"]
    ):
        raise ValueError("capability certificate tier/profile mapping is inconsistent")
    status = payload.get("certificate_status")
    if status not in {"AUTHORIZED", "DENIED"}:
        raise ValueError("capability certificate status is invalid")
    expected_authorization = (
        dict(policy["authorization"])
        if status == "AUTHORIZED"
        else {action: False for action in CAPABILITY_ACTIONS}
    )
    if payload.get("authorization") != expected_authorization:
        raise ValueError("capability certificate authorization map is inconsistent")
    if (
        payload.get("runs_required") != STABILITY_RUNS_REQUIRED
        or payload.get("passes_required") != STABILITY_PASSES_REQUIRED
    ):
        raise ValueError("capability certificate stability requirement is inconsistent")
    if payload.get("stability_policy_version") != STABILITY_POLICY_VERSION:
        raise ValueError("capability certificate stability policy mismatch")
    if payload.get("benchmark_integrity_policy_version") != BENCHMARK_INTEGRITY_POLICY_VERSION:
        raise ValueError("capability certificate benchmark-integrity policy mismatch")
    if payload.get("adversarial_policy_version") != ADVERSARIAL_POLICY_VERSION:
        raise ValueError("capability certificate adversarial policy mismatch")
    run_ids = payload.get("candidate_run_identities")
    result_ids = payload.get("run_certificate_identities")
    integrity_ids = payload.get("benchmark_integrity_result_identities")
    challenge_ids = payload.get("challenge_instance_identities")
    adversarial_ids = payload.get("adversarial_result_identities")
    for rows, label in (
        (run_ids, "candidate run"),
        (result_ids, "run certificate"),
        (integrity_ids, "benchmark integrity"),
        (challenge_ids, "challenge instance"),
        (adversarial_ids, "adversarial result"),
    ):
        if (
            not isinstance(rows, list)
            or len(rows) != STABILITY_RUNS_REQUIRED
            or len(set(rows)) != STABILITY_RUNS_REQUIRED
        ):
            raise ValueError(f"capability certificate requires three distinct {label} identities")
    if status == "AUTHORIZED":
        if payload.get("granted_tier") != policy["tier"] or payload.get("gate_failures") != []:
            raise ValueError("authorized capability certificate contains inconsistent grant state")
        if payload.get("passes") != STABILITY_PASSES_REQUIRED:
            raise ValueError("authorized capability certificate must contain 3/3 stability passes")
        if payload.get("comparable_runs") != STABILITY_RUNS_REQUIRED:
            raise ValueError("authorized capability certificate must contain 3/3 comparable runs")
        if payload.get("adversarial_qualified_runs") != STABILITY_RUNS_REQUIRED:
            raise ValueError(
                "authorized capability certificate must contain 3/3 adversarial-qualified runs"
            )
        if (
            payload.get("stability_gate_failures") != []
            or payload.get("critical_concept_oscillations") != []
        ):
            raise ValueError("authorized capability certificate contains unstable concept evidence")
    else:
        if payload.get("granted_tier") is not None or not payload.get("gate_failures"):
            raise ValueError("denied capability certificate contains inconsistent grant state")
    _identity(payload.get("repository_identity"), label="repository_identity")
    _digest(payload.get("exam_snapshot_sha256"), label="exam_snapshot_sha256")
    _identity(
        payload.get("candidate_configuration_identity"), label="candidate_configuration_identity"
    )
    _identity(payload.get("benchmark_registry_identity"), label="benchmark_registry_identity")
    issued = payload.get("issued_at_utc")
    if not isinstance(issued, str):
        raise ValueError("capability certificate issued_at_utc is required")
    _issued_at(issued)
    expected_freshness = {
        "time_expiry": None,
        "time_expiry_policy": "NOT_CALIBRATED_V1",
        "repository_identity_must_match": True,
        "candidate_configuration_identity_must_match": True,
        "note": "v1 is exact-source and exact-candidate-configuration bound; no arbitrary time TTL is invented",
    }
    if payload.get("freshness") != expected_freshness:
        raise ValueError("capability certificate freshness contract is inconsistent")
    if payload.get("scope_model") != "repository-wide-action-scopes.v1":
        raise ValueError("capability certificate scope model mismatch")
    if (
        payload.get("baseline_without_certificate") != "READ_ONLY"
        or payload.get("enforcement_status") != "EVIDENCE_ONLY"
    ):
        raise ValueError("capability certificate enforcement boundary is inconsistent")
    return payload


def capability_check(
    certificate_path: Path,
    *,
    repository_identity: str,
    candidate_configuration_identity: str,
    requested_capability: str,
) -> dict[str, Any]:
    """Return a deterministic admission predicate; it does not itself grant runtime authority."""

    if requested_capability not in CAPABILITY_ACTIONS:
        raise ValueError("requested capability must be one of " + ", ".join(CAPABILITY_ACTIONS))
    certificate = validate_capability_certificate(
        _load_json(certificate_path, label="capability certificate")
    )
    failures: list[str] = []
    if certificate["certificate_status"] != "AUTHORIZED":
        failures.append("certificate_not_authorized")
    if certificate["repository_identity"] != repository_identity:
        failures.append("repository_identity_mismatch")
    if certificate["candidate_configuration_identity"] != candidate_configuration_identity:
        failures.append("candidate_configuration_identity_mismatch")
    if not certificate["authorization"].get(requested_capability, False):
        failures.append("requested_capability_not_granted")
    authorized = not failures
    base = {
        "artifact_type": "repository-understanding-capability-check",
        "schema_version": CAPABILITY_CHECK_SCHEMA_VERSION,
        "capability_policy_version": CAPABILITY_POLICY_VERSION,
        "capability_certificate_identity": certificate["capability_certificate_identity"],
        "repository_identity": repository_identity,
        "candidate_configuration_identity": candidate_configuration_identity,
        "requested_capability": requested_capability,
        "decision": "AUTHORIZED" if authorized else "DENIED",
        "authorized": authorized,
        "failures": failures,
        "authority_note": (
            "this is a deterministic certificate predicate only; the execution/sandbox owner remains the mutation authority"
        ),
    }
    return {**base, "capability_check_identity": "sha256:" + _canonical_sha256(base)}


def capability_policy_manifest() -> dict[str, Any]:
    return {
        "policy_version": CAPABILITY_POLICY_VERSION,
        "certificate_schema_version": CAPABILITY_CERTIFICATE_SCHEMA_VERSION,
        "baseline_without_certificate": "READ_ONLY",
        "profile_to_tier": {
            profile: {
                "tier": row["tier"],
                "authority_class": row["authority_class"],
                "authorization": dict(row["authorization"]),
            }
            for profile, row in CAPABILITY_TIERS.items()
        },
        "required_run_evidence": {
            "stable_qualification": f"{STABILITY_PASSES_REQUIRED}/{STABILITY_RUNS_REQUIRED}",
            "benchmark_integrity": "COMPARABLE for every qualifying run",
            "hidden_challenge_instances": "distinct and present for every qualifying run",
            "adversarial": "qualified for every hidden challenge instance",
        },
        "time_expiry": None,
        "freshness_rule": "exact repository identity + exact candidate configuration identity",
        "scope_note": (
            "PLAN covers non-mutating planning/review; STANDARD adds bounded editing; CRITICAL adds critical execution and release promotion. No separate EDIT_LOW_RISK tier is invented without calibrated policy."
        ),
        "enforcement_status": "EVIDENCE_ONLY",
    }

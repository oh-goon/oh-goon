"""Repository Understanding question-health and empirical calibration authority.

Empirical observations are measurements, not grading-policy authority.  This module computes
reproducible telemetry without silently changing authored difficulty, qualification thresholds, or
question health.  Promotion to calibrated difficulty remains an explicit SECRET bank-authority act.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import yaml

CALIBRATION_POLICY_VERSION = "repository-understanding-calibration.v8"
QUESTION_AUDIT_POLICY_VERSION = "repository-question-audit.v4"
CALIBRATION_PROMOTION_REVIEW_POLICY_VERSION = (
    "repository-understanding-calibration-promotion-review.v2"
)
CALIBRATION_OBSERVATION_SCHEMA = 1
CALIBRATION_PROMOTION_REVIEW_SCHEMA = 2
QUESTION_AUDIT_SCHEMA = 4

QUESTION_HEALTH_STATES = (
    "VALID",
    "AMBIGUOUS",
    "STALE",
    "INSUFFICIENT_EVIDENCE",
    "MULTIPLE_VALID_ANSWERS",
    "BROKEN_REFERENCE",
    "TRIVIALLY_LEAKED",
    "NON_DISCRIMINATING",
)
CALIBRATION_STATES = ("UNCALIBRATED", "CALIBRATING", "CALIBRATED")
CALIBRATION_COHORTS = ("STRONG", "MID", "WEAK")
CALIBRATION_REVIEW_MIN_SYSTEMS_PER_COHORT = 2
CALIBRATION_REVIEW_MIN_TOTAL_SYSTEMS = 6
CALIBRATION_REVIEW_MIN_OBSERVATIONS = 6


def _canonical_sha256(payload: Any) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _identity(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{label} must be a sha256 identity")
    digest = value.removeprefix("sha256:")
    if any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError(f"{label} must be a sha256 identity")
    return value


def _sha256_digest(value: Any, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ValueError(f"{label} must be a sha256 digest")
    return value


def _wilson_interval(
    successes: int, total: int, *, z: float = 1.959963984540054
) -> dict[str, Any] | None:
    """Return a descriptive 95% Wilson interval for a Bernoulli rate.

    This is uncertainty telemetry only. Calibration observations are not asserted to be
    statistically independent, so this interval is never sufficient to auto-promote a question.
    """
    if total <= 0:
        return None
    if successes < 0 or successes > total:
        raise ValueError("Wilson interval successes must be between 0 and total")
    p = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denominator
    margin = (z / denominator) * ((p * (1.0 - p) / total + z2 / (4.0 * total * total)) ** 0.5)
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return {
        "method": "WILSON_SCORE",
        "confidence_level": 0.95,
        "successes": successes,
        "total": total,
        "estimate_percent": round(100.0 * p, 1),
        "lower_percent": round(100.0 * low, 1),
        "upper_percent": round(100.0 * high, 1),
        "width_percent": round(100.0 * (high - low), 1),
    }


def _promotion_evidence_assessment(
    *,
    rows: list[dict[str, Any]],
    review_eligibility: dict[str, Any],
) -> dict[str, Any]:
    overall = _wilson_interval(sum(bool(row["success"]) for row in rows), len(rows))
    cohorts: dict[str, Any] = {}
    for cohort in CALIBRATION_COHORTS:
        cohort_rows = [row for row in rows if row["cohort"] == cohort]
        cohorts[cohort] = _wilson_interval(
            sum(bool(row["success"]) for row in cohort_rows), len(cohort_rows)
        )
    base = {
        "status": (
            "READY_FOR_HUMAN_REVIEW"
            if review_eligibility.get("status") == "ELIGIBLE_FOR_REVIEW"
            else "INSUFFICIENT_EVIDENCE"
        ),
        "review_eligibility_status": review_eligibility.get("status"),
        "overall_success_interval": overall,
        "cohort_success_intervals": cohorts,
        "interpretation": (
            "descriptive uncertainty only; observations may share model/provider/family dependencies, "
            "so intervals are not a statistical-independence or power guarantee and cannot auto-promote"
        ),
        "automatic_promotion": False,
    }
    return {**base, "assessment_identity": "sha256:" + _canonical_sha256(base)}


def _calibration_review_eligibility(
    *,
    observation_count: int,
    system_count: int,
    cohort_system_counts: dict[str, int],
    independent_subject_count: int = 0,
    cohort_independent_subject_counts: dict[str, int] | None = None,
    provenance_complete: bool = True,
) -> dict[str, Any]:
    gaps: list[str] = []
    if not provenance_complete:
        gaps.append("observation_provenance_incomplete")
    if observation_count < CALIBRATION_REVIEW_MIN_OBSERVATIONS:
        gaps.append(f"observation_count<{CALIBRATION_REVIEW_MIN_OBSERVATIONS}")
    if system_count < CALIBRATION_REVIEW_MIN_TOTAL_SYSTEMS:
        gaps.append(f"system_count<{CALIBRATION_REVIEW_MIN_TOTAL_SYSTEMS}")
    if independent_subject_count < CALIBRATION_REVIEW_MIN_TOTAL_SYSTEMS:
        gaps.append(f"independent_subject_count<{CALIBRATION_REVIEW_MIN_TOTAL_SYSTEMS}")
    cohort_independent_subject_counts = cohort_independent_subject_counts or {}
    for cohort in CALIBRATION_COHORTS:
        count = cohort_system_counts.get(cohort, 0)
        if count < CALIBRATION_REVIEW_MIN_SYSTEMS_PER_COHORT:
            gaps.append(f"{cohort}_system_count<{CALIBRATION_REVIEW_MIN_SYSTEMS_PER_COHORT}")
        independent_count = cohort_independent_subject_counts.get(cohort, 0)
        if independent_count < CALIBRATION_REVIEW_MIN_SYSTEMS_PER_COHORT:
            gaps.append(
                f"{cohort}_independent_subject_count<{CALIBRATION_REVIEW_MIN_SYSTEMS_PER_COHORT}"
            )
    return {
        "status": "ELIGIBLE_FOR_REVIEW" if not gaps else "INSUFFICIENT_EVIDENCE",
        "minimum_observations": CALIBRATION_REVIEW_MIN_OBSERVATIONS,
        "minimum_total_systems": CALIBRATION_REVIEW_MIN_TOTAL_SYSTEMS,
        "minimum_total_independent_subjects": CALIBRATION_REVIEW_MIN_TOTAL_SYSTEMS,
        "minimum_systems_per_cohort": CALIBRATION_REVIEW_MIN_SYSTEMS_PER_COHORT,
        "required_cohorts": list(CALIBRATION_COHORTS),
        "gaps": gaps,
        "scope_note": (
            "operational minimum for calibration-review eligibility only; it is not a statistical-power "
            "guarantee and cannot itself promote calibrated difficulty"
        ),
    }


def calibration_promotion_review(
    *,
    source_version: str,
    repository_identity: str,
    exam_snapshot_sha256: str,
    question_id: str,
    calibration_report_identity: str,
    calibrated_difficulty: str,
    reviewer_identity: str,
    evidence_assessment_identity: str,
    decision: str = "APPROVE",
) -> dict[str, Any]:
    """Create an identity-bound SECRET review artifact; creation itself does not mutate the bank."""
    if not isinstance(question_id, str) or not question_id.strip():
        raise ValueError("calibration promotion review question_id is required")
    if calibrated_difficulty not in {"L1", "L2", "L3", "L4", "L5"}:
        raise ValueError("calibration promotion review calibrated_difficulty is invalid")
    if decision != "APPROVE":
        raise ValueError("calibration promotion review decision must be APPROVE")
    base = {
        "artifact_type": "repository-understanding-calibration-promotion-review",
        "schema_version": CALIBRATION_PROMOTION_REVIEW_SCHEMA,
        "policy_version": CALIBRATION_PROMOTION_REVIEW_POLICY_VERSION,
        "source_version": source_version,
        "repository_identity": _identity(repository_identity, label="repository_identity"),
        "exam_snapshot_sha256": _sha256_digest(exam_snapshot_sha256, label="exam_snapshot_sha256"),
        "question_id": question_id,
        "calibration_report_identity": _identity(
            calibration_report_identity, label="calibration_report_identity"
        ),
        "calibrated_difficulty": calibrated_difficulty,
        "reviewer_identity": _identity(reviewer_identity, label="reviewer_identity"),
        "evidence_assessment_identity": _identity(
            evidence_assessment_identity, label="evidence_assessment_identity"
        ),
        "decision_basis": "HUMAN_REVIEW_OF_EMPIRICAL_EVIDENCE",
        "decision": decision,
    }
    return {**base, "review_identity": "sha256:" + _canonical_sha256(base)}


def _retained_promotion_review(
    audit_path: Path,
    evidence: dict[str, Any],
    *,
    qid: str,
    calibrated_difficulty: str,
    source_version: str,
    calibration_report: dict[str, Any],
) -> dict[str, Any]:
    relative = evidence.get("promotion_review_path")
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{qid} CALIBRATED requires calibration_evidence.promotion_review_path")
    rel = Path(relative)
    if rel.is_absolute():
        raise ValueError(
            f"{qid} promotion review path must be relative to the SECRET audit directory"
        )
    base_dir = audit_path.parent.resolve()
    review_path = (base_dir / rel).resolve()
    try:
        review_path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(f"{qid} promotion review path escapes the SECRET audit directory") from exc
    if not review_path.is_file():
        raise ValueError(f"{qid} retained promotion review does not exist")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(review, dict):
        raise ValueError(f"{qid} retained promotion review must be a JSON object")
    if review.get("artifact_type") != "repository-understanding-calibration-promotion-review":
        raise ValueError(f"{qid} retained promotion review artifact_type mismatch")
    if review.get("schema_version") != CALIBRATION_PROMOTION_REVIEW_SCHEMA:
        raise ValueError(f"{qid} retained promotion review schema mismatch")
    if review.get("policy_version") != CALIBRATION_PROMOTION_REVIEW_POLICY_VERSION:
        raise ValueError(f"{qid} retained promotion review policy mismatch")
    expected = "sha256:" + _canonical_sha256(
        {key: value for key, value in review.items() if key != "review_identity"}
    )
    if review.get("review_identity") != expected:
        raise ValueError(f"{qid} retained promotion review identity is invalid")
    requested = _identity(
        evidence.get("promotion_review_identity"),
        label=f"{qid} calibration_evidence.promotion_review_identity",
    )
    if requested != expected:
        raise ValueError(f"{qid} promotion review identity does not match audit evidence")
    bindings = {
        "source_version": source_version,
        "repository_identity": calibration_report.get("repository_identity"),
        "exam_snapshot_sha256": calibration_report.get("exam_snapshot_sha256"),
        "question_id": qid,
        "calibration_report_identity": calibration_report.get("report_identity"),
        "calibrated_difficulty": calibrated_difficulty,
        "evidence_assessment_identity": calibration_report["questions"][qid][
            "promotion_evidence_assessment"
        ]["assessment_identity"],
        "decision_basis": "HUMAN_REVIEW_OF_EMPIRICAL_EVIDENCE",
        "decision": "APPROVE",
    }
    for field, expected_value in bindings.items():
        if review.get(field) != expected_value:
            raise ValueError(f"{qid} promotion review {field} does not match calibration promotion")
    _identity(review.get("reviewer_identity"), label=f"{qid} promotion review reviewer_identity")
    return {"promotion_review_path": rel.as_posix(), "promotion_review_identity": requested}


def _retained_calibration_report(
    audit_path: Path,
    evidence: Any,
    *,
    qid: str,
    source_version: str,
    current_question_binding: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(evidence, dict):
        raise ValueError(f"{qid} non-UNCALIBRATED state requires calibration_evidence")
    relative = evidence.get("report_path")
    if not isinstance(relative, str) or not relative.strip():
        raise ValueError(f"{qid} calibration_evidence.report_path is required")
    rel = Path(relative)
    if rel.is_absolute():
        raise ValueError(
            f"{qid} calibration report path must be relative to the SECRET audit directory"
        )
    base_dir = audit_path.parent.resolve()
    report_path = (base_dir / rel).resolve()
    try:
        report_path.relative_to(base_dir)
    except ValueError as exc:
        raise ValueError(
            f"{qid} calibration report path escapes the SECRET audit directory"
        ) from exc
    if not report_path.is_file():
        raise ValueError(f"{qid} retained calibration report does not exist")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"{qid} retained calibration report must be a JSON object")
    if report.get("artifact_type") != "repository-understanding-calibration-report":
        raise ValueError(f"{qid} retained calibration report artifact_type mismatch")
    if report.get("policy_version") != CALIBRATION_POLICY_VERSION:
        raise ValueError(f"{qid} retained calibration report policy mismatch")
    if report.get("source_version") != source_version:
        raise ValueError(f"{qid} retained calibration report source_version mismatch")
    expected_identity = "sha256:" + _canonical_sha256(
        {key: value for key, value in report.items() if key != "report_identity"}
    )
    if report.get("report_identity") != expected_identity:
        raise ValueError(f"{qid} retained calibration report identity is invalid")
    requested_identity = _identity(
        evidence.get("report_identity"), label=f"{qid} calibration_evidence.report_identity"
    )
    if requested_identity != expected_identity:
        raise ValueError(f"{qid} calibration report identity does not match audit evidence")
    question = (report.get("questions") or {}).get(qid)
    if not isinstance(question, dict):
        raise ValueError(f"{qid} retained calibration report has no question row")
    eligibility = question.get("review_eligibility")
    if not isinstance(eligibility, dict):
        raise ValueError(f"{qid} retained calibration report lacks review eligibility")
    retained_binding = question.get("calibration_question_binding")
    if current_question_binding is not None:
        current_question_binding = _identity(
            current_question_binding, label=f"{qid} current calibration question binding"
        )
        if retained_binding != current_question_binding:
            raise ValueError(
                f"{qid} retained calibration report is stale for current question binding"
            )
    normalized = {
        "report_path": rel.as_posix(),
        "report_identity": requested_identity,
        "promotion_review_path": evidence.get("promotion_review_path"),
        "promotion_review_identity": evidence.get("promotion_review_identity"),
    }
    return report, question, normalized


def load_question_audit(
    path: Path,
    *,
    source_version: str,
    question_ids: Iterable[str],
    current_question_bindings: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != QUESTION_AUDIT_SCHEMA:
        raise ValueError("repository question audit schema mismatch")
    if payload.get("policy_version") != QUESTION_AUDIT_POLICY_VERSION:
        raise ValueError("repository question audit policy mismatch")
    if payload.get("source_version") != source_version:
        raise ValueError("repository question audit source_version mismatch")
    rows = payload.get("questions")
    if not isinstance(rows, list):
        raise ValueError("repository question audit questions must be a list")
    expected = tuple(question_ids)
    observed: list[str] = []
    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("repository question audit row must be an object")
        qid = row.get("id")
        health = row.get("health_status")
        state = row.get("calibration_state")
        calibrated = row.get("calibrated_difficulty")
        evidence = row.get("calibration_evidence")
        if not isinstance(qid, str):
            raise ValueError("repository question audit row id is required")
        if health not in QUESTION_HEALTH_STATES:
            raise ValueError(f"{qid} has invalid question health state")
        if state not in CALIBRATION_STATES:
            raise ValueError(f"{qid} has invalid calibration state")
        if calibrated is not None and calibrated not in {"L1", "L2", "L3", "L4", "L5"}:
            raise ValueError(f"{qid} has invalid calibrated_difficulty")
        if state == "CALIBRATED" and calibrated is None:
            raise ValueError(f"{qid} CALIBRATED requires calibrated_difficulty")
        if state != "CALIBRATED" and calibrated is not None:
            raise ValueError(f"{qid} calibrated_difficulty requires CALIBRATED state")

        normalized_evidence = None
        if state == "UNCALIBRATED":
            if evidence is not None:
                raise ValueError(f"{qid} UNCALIBRATED cannot retain calibration_evidence")
        else:
            _report, calibration_row, normalized_evidence = _retained_calibration_report(
                path,
                evidence,
                qid=qid,
                source_version=source_version,
                current_question_binding=(current_question_bindings or {}).get(qid),
            )
            if calibration_row.get("observation_count", 0) <= 0:
                raise ValueError(f"{qid} CALIBRATING/CALIBRATED requires empirical observations")
            if state == "CALIBRATING":
                if (
                    normalized_evidence["promotion_review_path"] is not None
                    or normalized_evidence["promotion_review_identity"] is not None
                ):
                    raise ValueError(f"{qid} CALIBRATING cannot carry promotion review evidence")
            else:
                eligibility = calibration_row["review_eligibility"]
                if eligibility.get("status") != "ELIGIBLE_FOR_REVIEW":
                    raise ValueError(
                        f"{qid} CALIBRATED has insufficient empirical calibration evidence"
                    )
                review_evidence = _retained_promotion_review(
                    path,
                    evidence,
                    qid=qid,
                    calibrated_difficulty=calibrated,
                    source_version=source_version,
                    calibration_report=_report,
                )
                normalized_evidence.update(review_evidence)

        observed.append(qid)
        normalized.append(
            {
                "id": qid,
                "health_status": health,
                "calibration_state": state,
                "calibrated_difficulty": calibrated,
                "calibration_evidence": normalized_evidence,
            }
        )
    if tuple(observed) != expected:
        raise ValueError("repository question audit IDs/order must match the canonical bank")
    base = {
        "schema_version": QUESTION_AUDIT_SCHEMA,
        "policy_version": QUESTION_AUDIT_POLICY_VERSION,
        "source_version": source_version,
        "questions": normalized,
    }
    return {**base, "audit_identity": "sha256:" + _canonical_sha256(base)}


def _empirical_calibration_status(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "NO_QUESTIONS"
    states = Counter(row["calibration_state"] for row in rows)
    if states["CALIBRATED"] == len(rows):
        return "CALIBRATED"
    if states["UNCALIBRATED"] == len(rows):
        return "UNCALIBRATED"
    return "IN_PROGRESS"


def audit_manifest(
    audit: dict[str, Any], *, critical_question_ids: Iterable[str] = ()
) -> dict[str, Any]:
    rows = audit["questions"]
    critical_ids = set(critical_question_ids)
    critical_rows = [row for row in rows if row["id"] in critical_ids]
    health = Counter(row["health_status"] for row in rows)
    maturity = Counter(row["calibration_state"] for row in rows)
    critical_maturity = Counter(row["calibration_state"] for row in critical_rows)
    non_score_bearing = [row["id"] for row in rows if row["health_status"] != "VALID"]
    return {
        "policy_version": QUESTION_AUDIT_POLICY_VERSION,
        "calibration_policy_version": CALIBRATION_POLICY_VERSION,
        "promotion_review_policy_version": CALIBRATION_PROMOTION_REVIEW_POLICY_VERSION,
        "audit_identity": audit["audit_identity"],
        "question_count": len(rows),
        "health_states": dict(sorted(health.items())),
        "calibration_states": dict(sorted(maturity.items())),
        "critical_question_count": len(critical_rows),
        "critical_calibration_states": dict(sorted(critical_maturity.items())),
        "calibrated_question_count": maturity["CALIBRATED"],
        "calibrating_question_count": maturity["CALIBRATING"],
        "uncalibrated_question_count": maturity["UNCALIBRATED"],
        "critical_calibrated_question_count": critical_maturity["CALIBRATED"],
        "critical_calibrating_question_count": critical_maturity["CALIBRATING"],
        "critical_uncalibrated_question_count": critical_maturity["UNCALIBRATED"],
        "empirical_calibration_status": _empirical_calibration_status(rows),
        "critical_empirical_calibration_status": _empirical_calibration_status(critical_rows),
        "non_score_bearing_questions": non_score_bearing,
        "release_status": "READY" if not non_score_bearing else "BLOCKED",
        "release_status_scope": "QUESTION_HEALTH_ONLY",
        "empirical_calibration_affects_release_status": False,
        "rule": (
            "known-bad active questions must be repaired or removed before bank release; "
            "release_status describes question-health readiness only and must not be interpreted "
            "as evidence that empirical difficulty/discrimination calibration has been completed"
        ),
    }


def assert_question_audit_release_ready(
    audit: dict[str, Any], *, critical_question_ids: Iterable[str] = ()
) -> dict[str, Any]:
    report = audit_manifest(audit, critical_question_ids=critical_question_ids)
    if report["release_status"] != "READY":
        raise ValueError(
            "repository-understanding bank contains non-score-bearing question health states: "
            + ", ".join(report["non_score_bearing_questions"])
        )
    return report


def _median(values: list[float | int]) -> float | None:
    return None if not values else round(float(statistics.median(values)), 3)


def _rate(values: list[bool]) -> float | None:
    return None if not values else round(100.0 * sum(values) / len(values), 1)


def _variance(values: list[float]) -> float | None:
    return None if len(values) < 2 else round(float(statistics.pvariance(values)), 4)


def _calibration_payload_runs(
    observations_path: Path,
    *,
    source_version: str,
    repository_identity: str,
    exam_snapshot_sha256: str,
) -> list[dict[str, Any]]:
    payload = json.loads(observations_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("calibration observations must be a JSON object")
    expected = {
        "artifact_type": "repository-understanding-calibration-observations",
        "schema_version": CALIBRATION_OBSERVATION_SCHEMA,
        "source_version": source_version,
        "repository_identity": repository_identity,
        "exam_snapshot_sha256": exam_snapshot_sha256,
    }
    errors = {
        "artifact_type": "calibration observations artifact_type mismatch",
        "schema_version": "calibration observations schema mismatch",
        "source_version": "calibration observations source_version mismatch",
        "repository_identity": "calibration observations repository identity mismatch",
        "exam_snapshot_sha256": "calibration observations exam snapshot identity mismatch",
    }
    for field, expected_value in expected.items():
        if payload.get(field) != expected_value:
            raise ValueError(errors[field])
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("calibration observations require at least one run")
    return runs


def _run_provenance(run: dict[str, Any]) -> dict[str, Any]:
    values = {
        "candidate_identity": run.get("candidate_identity"),
        "grading_artifact_identity": run.get("grading_artifact_identity"),
        "qualification_profile": run.get("qualification_profile"),
        "model_identity": run.get("model_identity"),
        "configuration_identity": run.get("configuration_identity"),
        "system_family": run.get("system_family"),
        "provider": run.get("provider"),
    }
    for field in (
        "candidate_identity",
        "grading_artifact_identity",
        "model_identity",
        "configuration_identity",
    ):
        if values[field] is not None:
            values[field] = _identity(values[field], label=field)
    profile = values["qualification_profile"]
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        raise ValueError("calibration run qualification_profile must be a non-empty string or null")
    for field in ("system_family", "provider"):
        value = values[field]
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"calibration run {field} must be a non-empty string or null")
    if values["model_identity"] is not None and values["configuration_identity"] is not None:
        values["independence_subject_identity"] = "sha256:" + _canonical_sha256(
            {
                "model_identity": values["model_identity"],
                "configuration_identity": values["configuration_identity"],
            }
        )
    else:
        values["independence_subject_identity"] = None
    return values


def _validated_run(run: Any) -> tuple[str, str, list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(run, dict):
        raise ValueError("calibration run must be an object")
    system_id = run.get("system_id")
    cohort = run.get("cohort")
    answers = run.get("questions")
    if not isinstance(system_id, str) or not system_id.strip():
        raise ValueError("calibration run system_id is required")
    if cohort not in CALIBRATION_COHORTS:
        raise ValueError("calibration run cohort must be STRONG, MID, or WEAK")
    if not isinstance(answers, list):
        raise ValueError("calibration run questions must be a list")
    return system_id, cohort, answers, _run_provenance(run)


def _validate_answer(answer: Any, *, id_set: set[str], seen: set[str]) -> tuple[str, bool]:
    if not isinstance(answer, dict):
        raise ValueError("calibration question observation must be an object")
    qid = answer.get("id")
    success = answer.get("success")
    if qid not in id_set or qid in seen:
        raise ValueError(f"calibration run contains unknown/duplicate question id: {qid}")
    if type(success) is not bool:
        raise ValueError(f"{qid} calibration success must be boolean")
    for metric in ("tool_calls", "context_tokens"):
        value = answer.get(metric)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{qid} {metric} must be a non-negative integer or null")
    time_value = answer.get("time_to_evidence_seconds")
    if time_value is not None and (
        isinstance(time_value, bool) or not isinstance(time_value, (int, float)) or time_value < 0
    ):
        raise ValueError(f"{qid} time_to_evidence_seconds must be non-negative or null")
    failure_mode = answer.get("failure_mode")
    if failure_mode is not None and (not isinstance(failure_mode, str) or not failure_mode.strip()):
        raise ValueError(f"{qid} failure_mode must be a non-empty string or null")
    return qid, success


def _append_run_rows(
    *,
    answers: list[dict[str, Any]],
    id_set: set[str],
    system_id: str,
    cohort: str,
    provenance: dict[str, Any],
    run_identity: str,
    source_version: str,
    repository_identity: str,
    exam_snapshot_sha256: str,
    rows_by_question: dict[str, list[dict[str, Any]]],
    grading_question_bindings: set[tuple[str, str]],
) -> None:
    seen: set[str] = set()
    for answer in answers:
        qid, _ = _validate_answer(answer, id_set=id_set, seen=seen)
        grading_identity = provenance["grading_artifact_identity"]
        if grading_identity is not None:
            binding = (qid, grading_identity)
            if binding in grading_question_bindings:
                raise ValueError(
                    f"{qid} grading artifact is represented by more than one calibration observation"
                )
            grading_question_bindings.add(binding)
        observation_base = {
            "observation_schema_version": CALIBRATION_OBSERVATION_SCHEMA,
            "source_version": source_version,
            "repository_identity": repository_identity,
            "exam_snapshot_sha256": exam_snapshot_sha256,
            "question_id": qid,
            "system_id": system_id,
            **provenance,
            "cohort": cohort,
            "run_identity": run_identity,
            "outcome": answer,
        }
        rows_by_question[qid].append(
            {
                **answer,
                "system_id": system_id,
                "cohort": cohort,
                **provenance,
                "run_identity": run_identity,
                "observation_identity": "sha256:" + _canonical_sha256(observation_base),
                "provenance_complete": all(
                    provenance[key] is not None
                    for key in (
                        "candidate_identity",
                        "grading_artifact_identity",
                        "qualification_profile",
                    )
                ),
            }
        )
        seen.add(qid)


def _question_report(
    qid: str,
    rows: list[dict[str, Any]],
    *,
    authored_difficulty: dict[str, str],
    question_binding_identities: dict[str, str] | None,
) -> dict[str, Any]:
    overall = [bool(row["success"]) for row in rows]
    cohort_rates = {
        cohort: _rate([bool(row["success"]) for row in rows if row["cohort"] == cohort])
        for cohort in CALIBRATION_COHORTS
    }
    system_success: dict[str, list[bool]] = defaultdict(list)
    cohort_systems: dict[str, set[str]] = defaultdict(set)
    independent_subjects: set[str] = set()
    cohort_subjects: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        system_success[row["system_id"]].append(bool(row["success"]))
        cohort_systems[row["cohort"]].add(row["system_id"])
        subject = row.get("independence_subject_identity")
        if subject is not None:
            independent_subjects.add(subject)
            cohort_subjects[row["cohort"]].add(subject)
    system_counts = {
        cohort: len(cohort_systems.get(cohort, set())) for cohort in CALIBRATION_COHORTS
    }
    subject_counts = {
        cohort: len(cohort_subjects.get(cohort, set())) for cohort in CALIBRATION_COHORTS
    }
    provenance_complete = bool(rows) and all(bool(row["provenance_complete"]) for row in rows)
    eligibility = _calibration_review_eligibility(
        observation_count=len(rows),
        system_count=len(system_success),
        cohort_system_counts=system_counts,
        independent_subject_count=len(independent_subjects),
        cohort_independent_subject_counts=subject_counts,
        provenance_complete=provenance_complete,
    )
    strong, weak = cohort_rates["STRONG"], cohort_rates["WEAK"]
    failures = Counter(
        str(row["failure_mode"])
        for row in rows
        if not row["success"] and row.get("failure_mode") is not None
    )
    per_system_rates = [sum(values) / len(values) for values in system_success.values() if values]
    return {
        "authored_difficulty": authored_difficulty[qid],
        "calibration_question_binding": (question_binding_identities or {}).get(qid),
        "observation_count": len(rows),
        "system_count": len(system_success),
        "independent_subject_count": len(independent_subjects),
        "cohort_independent_subject_count": subject_counts,
        "unique_run_count": len({row["run_identity"] for row in rows}),
        "observation_identities": [row["observation_identity"] for row in rows],
        "provenance_complete": provenance_complete,
        "empirical_success_rate_percent": _rate(overall),
        "median_tool_calls": _median(
            [row["tool_calls"] for row in rows if row.get("tool_calls") is not None]
        ),
        "median_time_to_evidence_seconds": _median(
            [
                row["time_to_evidence_seconds"]
                for row in rows
                if row.get("time_to_evidence_seconds") is not None
            ]
        ),
        "median_context_tokens": _median(
            [row["context_tokens"] for row in rows if row.get("context_tokens") is not None]
        ),
        "cross_system_success_variance": _variance(per_system_rates),
        "cohort_success_rate_percent": cohort_rates,
        "cohort_system_count": system_counts,
        "item_discrimination_strong_minus_weak": None
        if strong is None or weak is None
        else round(strong - weak, 1),
        "failure_mode_distribution": dict(sorted(failures.items())),
        "review_eligibility": eligibility,
        "promotion_evidence_assessment": _promotion_evidence_assessment(
            rows=rows, review_eligibility=eligibility
        ),
        "calibration_maturity": "UNCALIBRATED" if not rows else "CALIBRATING",
        "calibrated_difficulty": None,
        "note": "telemetry cannot promote calibrated difficulty; review eligibility is only an operational minimum evidence floor and explicit bank-authority promotion is still required",
    }


def calibration_report(
    observations_path: Path,
    *,
    question_ids: Iterable[str],
    authored_difficulty: dict[str, str],
    source_version: str,
    repository_identity: str,
    exam_snapshot_sha256: str,
    question_binding_identities: dict[str, str] | None = None,
) -> dict[str, Any]:
    runs = _calibration_payload_runs(
        observations_path,
        source_version=source_version,
        repository_identity=repository_identity,
        exam_snapshot_sha256=exam_snapshot_sha256,
    )
    ids = tuple(question_ids)
    rows_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    systems: set[str] = set()
    cohort_systems: dict[str, set[str]] = defaultdict(set)
    system_cohort: dict[str, str] = {}
    subject_cohort: dict[str, str] = {}
    run_identities: set[str] = set()
    grading_bindings: set[tuple[str, str]] = set()
    for raw_run in runs:
        system_id, cohort, answers, provenance = _validated_run(raw_run)
        run_base = {
            "source_version": source_version,
            "repository_identity": repository_identity,
            "exam_snapshot_sha256": exam_snapshot_sha256,
            "system_id": system_id,
            **provenance,
            "cohort": cohort,
            "questions": answers,
        }
        run_identity = "sha256:" + _canonical_sha256(run_base)
        if run_identity in run_identities:
            raise ValueError("duplicate calibration run identity")
        run_identities.add(run_identity)
        if system_id in system_cohort and system_cohort[system_id] != cohort:
            raise ValueError("calibration system_id cannot appear in multiple cohorts")
        system_cohort[system_id] = cohort
        subject = provenance["independence_subject_identity"]
        if subject is not None and subject in subject_cohort and subject_cohort[subject] != cohort:
            raise ValueError("calibration independent subject cannot appear in multiple cohorts")
        if subject is not None:
            subject_cohort[subject] = cohort
        systems.add(system_id)
        cohort_systems[cohort].add(system_id)
        _append_run_rows(
            answers=answers,
            id_set=set(ids),
            system_id=system_id,
            cohort=cohort,
            provenance=provenance,
            run_identity=run_identity,
            source_version=source_version,
            repository_identity=repository_identity,
            exam_snapshot_sha256=exam_snapshot_sha256,
            rows_by_question=rows_by_question,
            grading_question_bindings=grading_bindings,
        )
    questions = {
        qid: _question_report(
            qid,
            rows_by_question.get(qid, []),
            authored_difficulty=authored_difficulty,
            question_binding_identities=question_binding_identities,
        )
        for qid in ids
    }
    base = {
        "artifact_type": "repository-understanding-calibration-report",
        "policy_version": CALIBRATION_POLICY_VERSION,
        "observation_schema_version": CALIBRATION_OBSERVATION_SCHEMA,
        "source_version": source_version,
        "repository_identity": repository_identity,
        "exam_snapshot_sha256": exam_snapshot_sha256,
        "run_count": len(runs),
        "unique_run_count": len(run_identities),
        "system_count": len(systems),
        "cohort_system_counts": {
            cohort: len(cohort_systems.get(cohort, set())) for cohort in CALIBRATION_COHORTS
        },
        "questions": questions,
    }
    return {**base, "report_identity": "sha256:" + _canonical_sha256(base)}
